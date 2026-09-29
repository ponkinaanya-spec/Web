"""Research-only hip silhouette and landmark *candidates* for DXA images.

No returned point is a verified anatomical landmark. The method deliberately
abstains where the connected pelvis/femur silhouette cannot be separated.
Input coordinates and outputs always refer to the original DICOM pixel grid.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    padded = np.pad(row.astype(np.int8), (1, 1))
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return [(int(a), int(b)) for a, b in zip(starts, ends)]


def _largest_usable_component(binary: np.ndarray) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    h, w = binary.shape
    candidates = [i for i in range(1, n)
                  if stats[i, cv2.CC_STAT_AREA] >= 0.003 * h * w]
    if not candidates:
        return np.zeros_like(binary)
    # The central hip complex is usually the largest component. This is not
    # semantic femur segmentation: the pelvis can belong to the same component.
    index = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    return np.uint8(labels == index) * 255


def _shaft_rows(mask: np.ndarray) -> list[tuple[int, float, int, int]]:
    h, w = mask.shape
    rows = []
    previous_center = None
    for y in range(int(0.94 * h), int(0.62 * h), -1):
        intervals = [(a, b) for a, b in _runs(mask[y] > 0)
                     if b - a >= max(5, int(0.04 * w))]
        if not intervals:
            continue
        if previous_center is None:
            # The shaft is expected to dominate near the lower image border.
            a, b = max(intervals, key=lambda pair: pair[1] - pair[0])
        else:
            a, b = min(intervals, key=lambda pair:
                       abs((pair[0] + pair[1]) / 2 - previous_center)
                       - 0.05 * (pair[1] - pair[0]))
        center = (a + b - 1) / 2
        rows.append((y, center, a, b))
        previous_center = center
    return rows


def _extreme_point(mask: np.ndarray, x_min: int, x_max: int,
                   y_min: int, y_max: int, *, top: bool, lateral: str) -> list[int] | None:
    h, w = mask.shape
    x_min, x_max = max(0, x_min), min(w, x_max)
    y_min, y_max = max(0, y_min), min(h, y_max)
    if x_min >= x_max or y_min >= y_max:
        return None
    patch = mask[y_min:y_max, x_min:x_max] > 0
    ys, xs = np.where(patch)
    if len(xs) < 8:
        return None
    ys = ys + y_min
    xs = xs + x_min
    target_y = int(ys.min() if top else ys.max())
    band = np.abs(ys - target_y) <= 2
    target_x = int(xs[band].min() if lateral == "left" else xs[band].max())
    return [target_x, target_y]


def propose_hip_geometry(gray: np.ndarray) -> dict:
    """Return silhouette, tentative geometry, and abstention diagnostics.

    The `bone_complex_mask` may contain both femur and pelvis. Candidate point
    names are hypotheses for visual review, not validated anatomical labels.
    """
    image = np.asarray(gray)
    if image.ndim != 2 or image.dtype != np.uint8:
        raise ValueError("Expected a native-resolution uint8 grayscale image")
    h, w = image.shape
    if h < 80 or w < 80:
        raise ValueError("Image is too small for this prototype")
    blur = cv2.GaussianBlur(image, (5, 5), 0)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(blur)
    threshold, binary = cv2.threshold(
        enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    complex_mask = _largest_usable_component(binary)
    contours, _ = cv2.findContours(complex_mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea) if contours else None
    rows = _shaft_rows(complex_mask)
    warnings = ["bone_complex_is_not_semantic_femur_mask"]
    candidates = {
        "lateral_tip_unverified": None,
        "medial_edge_extreme_unidentified": None,
        "femoral_head_center": None,
        "lesser_trochanter": None,
        "ischial_bone": None,
    }
    features = {
        "bone_complex_fraction": float(np.count_nonzero(complex_mask) / (h * w)),
        "otsu_threshold": float(threshold),
        "shaft_width_fraction": math.nan,
        "shaft_tilt_deg": math.nan,
        "medial_prominence_ratio": math.nan,
        "lateral_prominence_ratio": math.nan,
        "top_margin_fraction": math.nan,
        "bottom_margin_fraction": math.nan,
    }
    if features["bone_complex_fraction"] > 0.70:
        warnings.append("silhouette_covers_most_image")
        return {"bone_complex_mask": complex_mask, "contour": contour,
                "shaft_axis": None, "shaft_side_on_image": None,
                "candidates": candidates, "features": features,
                "warnings": warnings}
    if contour is not None:
        x, y, cw, ch = cv2.boundingRect(contour)
        features["top_margin_fraction"] = float(y / h)
        features["bottom_margin_fraction"] = float((h - y - ch) / h)
    if len(rows) < max(8, int(0.07 * h)):
        warnings.append("shaft_not_found")
        return {"bone_complex_mask": complex_mask, "contour": contour,
                "shaft_axis": None, "shaft_side_on_image": None,
                "candidates": candidates, "features": features,
                "warnings": warnings}

    sample = np.asarray(rows, dtype=np.float64)
    y_values = sample[:, 0]
    x_centers = sample[:, 1]
    slope, intercept = np.polyfit(y_values, x_centers, 1)
    shaft_width = float(np.median(sample[:, 3] - sample[:, 2]))
    if shaft_width > 0.45 * w:
        warnings.append("shaft_width_unreliable")
        return {"bone_complex_mask": complex_mask, "contour": contour,
                "shaft_axis": None, "shaft_side_on_image": None,
                "candidates": candidates, "features": features,
                "warnings": warnings}
    shaft_center = float(slope * 0.78 * h + intercept)
    side = "left" if shaft_center < w / 2 else "right"
    medial_sign = 1 if side == "left" else -1
    lateral_sign = -medial_sign
    axis_top_y, axis_bottom_y = int(0.63 * h), int(0.95 * h)
    axis = [[int(round(slope * axis_top_y + intercept)), axis_top_y],
            [int(round(slope * axis_bottom_y + intercept)), axis_bottom_y]]
    features["shaft_width_fraction"] = float(shaft_width / w)
    features["shaft_tilt_deg"] = float(math.degrees(math.atan(slope)))

    # Tentative greater-trochanter tip: earliest lateral silhouette in the
    # upper-middle band, excluding the medial pelvic mass by shaft-side prior.
    center_upper = slope * 0.34 * h + intercept
    if side == "left":
        xlo, xhi = int(center_upper - 2.2 * shaft_width), int(center_upper - 0.2 * shaft_width)
    else:
        xlo, xhi = int(center_upper + 0.2 * shaft_width), int(center_upper + 2.2 * shaft_width)
    candidates["lateral_tip_unverified"] = _extreme_point(
        complex_mask, xlo, xhi, int(0.10 * h), int(0.62 * h),
        top=True, lateral=side)

    # Medial prominence is only a *shape feature*: it may include the neck,
    # lesser trochanter, pelvis, or their projection overlap.
    medial_offsets, lateral_offsets = [], []
    medial_points = []
    for y in range(int(0.48 * h), int(0.73 * h)):
        expected = slope * y + intercept
        intervals = [(a, b) for a, b in _runs(complex_mask[y] > 0)
                     if b - a >= max(4, int(0.02 * w))]
        if not intervals:
            continue
        a, b = min(intervals, key=lambda pair:
                   abs((pair[0] + pair[1]) / 2 - expected))
        medial_x = b - 1 if medial_sign == 1 else a
        lateral_x = a if medial_sign == 1 else b - 1
        medial_offsets.append((medial_sign * (medial_x - expected), y, medial_x))
        lateral_offsets.append(lateral_sign * (lateral_x - expected))
        medial_points.append((y, medial_x))
    if medial_offsets:
        peak = max(medial_offsets, key=lambda item: item[0])
        features["medial_prominence_ratio"] = float(peak[0] / max(shaft_width, 1))
        candidates["medial_edge_extreme_unidentified"] = [int(peak[2]), int(peak[1])]
        # Do not call this the lesser trochanter: the same extreme can be the
        # femoral neck or overlapping pelvis. The point remains unavailable.
        warnings.append("lesser_trochanter_not_localized")
        features["lateral_prominence_ratio"] = float(
            max(lateral_offsets) / max(shaft_width, 1))

    # The head/ischium are not inferred from this connected silhouette. A
    # generic circle/edge maximum would give unjustified anatomical names.
    warnings.extend(["femoral_head_not_localized", "ischial_bone_not_localized"])
    if candidates["lateral_tip_unverified"] is None:
        warnings.append("lateral_tip_not_localized")
    return {"bone_complex_mask": complex_mask, "contour": contour,
            "shaft_axis": axis, "shaft_side_on_image": side,
            "candidates": candidates, "features": features,
            "warnings": warnings}

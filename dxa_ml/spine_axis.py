"""Experimental OpenCV estimate of the visible lumbar spine's image-plane axis.

This is a visualisation aid, not a validated clinical measurement. In particular,
it does not diagnose scoliosis or replace the expert's DXA positioning assessment.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def _uint8(image: np.ndarray) -> np.ndarray:
    image = np.asarray(image)
    if image.ndim != 2 or min(image.shape) < 100:
        raise ValueError("Expected a two-dimensional spine image of at least 100x100 pixels")
    if not np.isfinite(image).all():
        raise ValueError("Image contains non-finite pixels")
    values = image.astype(np.float32)
    if values.max() <= 1.0 and values.min() >= 0.0:
        values *= 255
    elif values.min() < 0 or values.max() > 255:
        low, high = np.percentile(values, [1, 99])
        if high <= low:
            raise ValueError("Image has insufficient contrast")
        values = (values - low) * (255.0 / (high - low))
    return np.uint8(np.clip(values, 0, 255))


def estimate_spine_axis_v1(image: np.ndarray) -> dict:
    """Estimate a straight axis from bright midline bands in ORIGINAL pixels.

    All cutoffs below are engineering heuristics, not clinical thresholds. A result
    with ``status='estimated'`` still requires visual verification. ``review``
    means that even the heuristic's image-quality checks failed.
    """
    gray = _uint8(image)
    height, width = gray.shape
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    enhanced = cv2.GaussianBlur(enhanced, (5, 5), 0)

    # Find a bright central column, keeping ribs/iliac crests outside the search.
    y0, y1 = round(0.10 * height), round(0.76 * height)
    x0, x1 = round(0.30 * width), round(0.70 * width)
    central = enhanced[y0:y1, x0:x1].astype(np.float32)
    column = np.maximum(central - np.percentile(central, 55), 0).mean(axis=0)
    column = cv2.GaussianBlur(column.reshape(1, -1), (0, 0), 8).ravel()
    anchor = x0 + int(np.argmax(column))

    # Broad bands suppress vertebral-endplate texture; a continuity window
    # prevents a bright pelvic rim or text annotation from taking over.
    band_height = max(9, round(0.055 * height))
    radius = max(18, round(0.19 * width))
    points: list[tuple[float, float]] = []
    for top in range(y0, y1 - band_height + 1, band_height):
        bottom = top + band_height
        left, right = max(x0, anchor - radius), min(x1, anchor + radius)
        band = enhanced[top:bottom, left:right].astype(np.float32)
        threshold = float(np.percentile(band, 68))
        weights = np.maximum(band - threshold, 0)
        profile = weights.sum(axis=0)
        if profile.sum() < 1 or np.count_nonzero(profile) < 5:
            continue
        # Median of bright-mass x coordinates is less sensitive than argmax to
        # one bright spinous process or a narrow horizontal annotation.
        mass = np.cumsum(profile)
        center = left + float(np.searchsorted(mass, mass[-1] / 2))
        points.append((center, (top + bottom - 1) / 2))

    result = {
        "status": "review",
        "angle_deg": None,
        "threshold_deg_from_tz": 5.0,
        "line_endpoints_xy": None,
        "trace_xy": [[round(x, 2), round(y, 2)] for x, y in points],
        "bands_found": len(points),
        "bands_expected": max(1, (y1 - y0) // band_height),
        "median_residual_px": None,
        "reason": None,
        "method": "opencv_bright_midline_heuristic_v1",
    }
    if len(points) < 7:
        result["reason"] = "too_few_visible_midline_bands"
        return result

    xy = np.asarray(points, dtype=np.float32)
    # Iteratively discard bands distant from the main line. Coordinates are in
    # original pixel space; no anisotropic resize is applied before geometry.
    inliers = np.ones(len(xy), dtype=bool)
    for _ in range(3):
        slope, intercept = np.polyfit(xy[inliers, 1], xy[inliers, 0], 1)
        residual = np.abs(xy[:, 0] - (slope * xy[:, 1] + intercept))
        tolerance = max(5.0, 0.025 * width)
        updated = residual <= tolerance
        if updated.sum() < 6 or np.array_equal(updated, inliers):
            break
        inliers = updated

    if inliers.sum() < 6:
        result["reason"] = "unstable_midline"
        return result
    slope, intercept = np.polyfit(xy[inliers, 1], xy[inliers, 0], 1)
    median_residual = float(np.median(np.abs(
        xy[inliers, 0] - (slope * xy[inliers, 1] + intercept))))
    angle = math.degrees(math.atan(float(slope)))
    start_y, end_y = float(xy[inliers, 1].min()), float(xy[inliers, 1].max())
    start_x, end_x = slope * start_y + intercept, slope * end_y + intercept
    result["angle_deg"] = round(abs(angle), 2)
    result["signed_angle_deg"] = round(angle, 2)
    result["median_residual_px"] = round(median_residual, 2)
    result["inlier_bands"] = int(inliers.sum())
    result["line_endpoints_xy"] = [
        [round(float(start_x), 2), round(start_y, 2)],
        [round(float(end_x), 2), round(end_y, 2)],
    ]
    if inliers.sum() < 0.65 * result["bands_expected"]:
        result["reason"] = "insufficient_axis_coverage"
    elif median_residual > max(4.0, 0.018 * width):
        result["reason"] = "curved_or_ambiguous_midline"
    elif abs(angle) > 22:
        result["reason"] = "implausible_axis_angle"
    else:
        result["status"] = "estimated"
    return result


def estimate_spine_axis(image: np.ndarray) -> dict:
    """Find paired left/right bright-bone borders and fit their midpoints.

    This replaces the v1 bright-mass median, which shifted towards an
    asymmetrically bright pedicle or osteophyte. All coordinates stay in the
    original image. It remains an exploratory, non-clinical estimate.
    """
    gray = _uint8(image)
    height, width = gray.shape
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    enhanced = cv2.GaussianBlur(enhanced, (5, 5), 0)
    y0, y1 = round(.12 * height), round(.78 * height)
    x0, x1 = round(.25 * width), round(.75 * width)
    central = enhanced[y0:y1, x0:x1].astype(np.float32)
    column = np.maximum(central - np.percentile(central, 55), 0).mean(axis=0)
    column = cv2.GaussianBlur(column.reshape(1, -1), (0, 0), 9).ravel()
    anchor = x0 + int(np.argmax(column))

    band_height = max(11, round(.06 * height))
    points: list[tuple[float, float]] = []
    borders: list[list[float]] = []
    for top in range(y0, y1 - band_height + 1, band_height):
        bottom = top + band_height
        profile = enhanced[top:bottom, x0:x1].mean(axis=0).astype(np.float32)
        profile = cv2.GaussianBlur(profile.reshape(1, -1), (0, 0), 2).ravel()
        margin = max(8, round(.10 * len(profile)))
        background = float(np.median(np.r_[profile[:margin], profile[-margin:]]))
        peak = float(profile.max())
        if peak - background < 13:
            continue
        threshold = max(background + .40 * (peak - background),
                        float(np.percentile(profile, 57)))
        bright = np.uint8(profile >= threshold).reshape(1, -1)
        # Bridge the dim spinous centre without joining remote ribs/pelvis.
        closing_width = max(7, round(.035 * width) | 1)
        closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE,
                                  np.ones((1, closing_width), np.uint8)).ravel()
        transitions = np.diff(np.r_[0, closed.astype(np.int16), 0])
        starts, stops = np.where(transitions == 1)[0], np.where(transitions == -1)[0]
        candidates = []
        for start, stop in zip(starts, stops):
            span = stop - start
            if not (.07 * width <= span <= .34 * width):
                continue
            left, right = x0 + int(start), x0 + int(stop) - 1
            center = (left + right) / 2
            if abs(center - anchor) > .17 * width:
                continue
            contrast = float(np.mean(profile[start:stop]) - background)
            # Favor wide, contrast-rich vertebral bodies near the central axis.
            score = contrast * np.sqrt(span) - 2.0 * abs(center - anchor)
            candidates.append((score, left, right, center))
        if not candidates:
            continue
        _, left, right, center = max(candidates)
        cy = (top + bottom - 1) / 2
        points.append((center, cy))
        borders.append([float(left), float(right), float(cy)])

    expected = max(1, (y1 - y0) // band_height)
    result = {
        "status": "review", "angle_deg": None, "threshold_deg_from_tz": 5.0,
        "line_endpoints_xy": None,
        "trace_xy": [[round(x, 2), round(y, 2)] for x, y in points],
        "borders_lry": borders,
        "bands_found": len(points), "bands_expected": expected,
        "median_residual_px": None, "reason": None,
        "method": "opencv_paired_borders_heuristic_v2",
    }
    if len(points) < 6:
        result["reason"] = "too_few_paired_borders"
        return result

    xy = np.asarray(points, dtype=np.float32)
    inliers = np.ones(len(xy), dtype=bool)
    tolerance = max(4., .02 * width)
    for _ in range(3):
        slope, intercept = np.polyfit(xy[inliers, 1], xy[inliers, 0], 1)
        residual = np.abs(xy[:, 0] - (slope * xy[:, 1] + intercept))
        updated = residual <= tolerance
        if updated.sum() < 5 or np.array_equal(updated, inliers):
            break
        inliers = updated
    if inliers.sum() < 5:
        result["reason"] = "unstable_border_midpoints"
        return result
    slope, intercept = np.polyfit(xy[inliers, 1], xy[inliers, 0], 1)
    residual = np.abs(xy[inliers, 0] - (slope * xy[inliers, 1] + intercept))
    median_residual = float(np.median(residual))
    angle = math.degrees(math.atan(float(slope)))
    start_y, end_y = float(xy[inliers, 1].min()), float(xy[inliers, 1].max())
    start_x, end_x = slope * start_y + intercept, slope * end_y + intercept
    result.update({
        "angle_deg": round(abs(angle), 2), "signed_angle_deg": round(angle, 2),
        "median_residual_px": round(median_residual, 2),
        "inlier_bands": int(inliers.sum()),
        "line_endpoints_xy": [[round(float(start_x), 2), round(start_y, 2)],
                              [round(float(end_x), 2), round(end_y, 2)]],
    })
    if inliers.sum() < max(6, .65 * expected):
        result["reason"] = "insufficient_border_coverage"
    elif median_residual > max(3., .013 * width):
        result["reason"] = "curved_or_ambiguous_borders"
    elif abs(angle) > 22:
        result["reason"] = "implausible_axis_angle"
    else:
        result["status"] = "estimated"
    return result


def draw_spine_axis(image: np.ndarray, estimate: dict) -> np.ndarray:
    """Return RGB overlay: green borders, cyan midpoints, yellow fit, red vertical."""
    gray = _uint8(image)
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    for left, right, y in estimate.get("borders_lry", []):
        cv2.circle(overlay, (round(left), round(y)), 2, (80, 255, 80), -1)
        cv2.circle(overlay, (round(right), round(y)), 2, (80, 255, 80), -1)
    for x, y in estimate["trace_xy"]:
        cv2.circle(overlay, (round(x), round(y)), 2, (0, 255, 255), -1)
    endpoints = estimate["line_endpoints_xy"]
    if endpoints:
        (xa, ya), (xb, yb) = endpoints
        center = round((xa + xb) / 2)
        cv2.line(overlay, (center, round(ya)), (center, round(yb)), (255, 70, 70), 1)
        cv2.line(overlay, (round(xa), round(ya)), (round(xb), round(yb)),
                 (255, 220, 0), 2)
    text = (f"estimate {estimate['angle_deg']:.1f} deg; verify visually"
            if estimate["angle_deg"] is not None else "axis unavailable; review")
    cv2.putText(overlay, text, (5, min(17, overlay.shape[0] - 2)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)
    return overlay

"""Unvalidated landmark proposals for DXA spine positioning review.

These points are visual aids. In particular, the upper point is NOT an
automatic identification of T12, and the lower points are NOT verified iliac
crests. No clinical correct/incorrect judgment is made by this module.
"""

from __future__ import annotations

import cv2
import numpy as np


LANDMARKS = ("upper_spine", "left_iliac", "right_iliac")


def _gray8(image: np.ndarray) -> np.ndarray:
    image = np.asarray(image)
    if image.ndim != 2 or min(image.shape) < 100:
        raise ValueError("Expected a two-dimensional image at least 100x100 pixels")
    if not np.isfinite(image).all():
        raise ValueError("Non-finite pixels")
    values = image.astype(np.float32)
    if values.min() < 0 or values.max() > 255:
        low, high = np.percentile(values, [1, 99])
        if high <= low:
            raise ValueError("Insufficient contrast")
        values = np.clip((values - low) / (high - low) * 255, 0, 255)
    return values.astype(np.uint8)


def _upper_spine_proposal(enhanced: np.ndarray) -> tuple[list[int], float]:
    height, width = enhanced.shape
    y_start, y_stop = round(.03 * height), round(.24 * height)
    x_start, x_stop = round(.30 * width), round(.70 * width)
    region = enhanced[y_start:y_stop, x_start:x_stop].astype(np.float32)
    if region.size == 0:
        return [width // 2, y_start], 0.0
    background = np.percentile(region, 55)
    prominence = np.maximum(region - background, 0)
    column = cv2.GaussianBlur(prominence.mean(axis=0).reshape(1, -1), (0, 0), 6).ravel()
    x = x_start + int(np.argmax(column))
    # This is simply the strongest upper-midline region, not a vertebral label.
    neighborhood = enhanced[y_start:y_stop, max(0, x - 12):min(width, x + 13)].astype(np.float32)
    row = cv2.GaussianBlur(neighborhood.mean(axis=1).reshape(-1, 1), (0, 0), 4).ravel()
    y = y_start + int(np.argmax(row))
    contrast = max(0.0, float(column.max() - np.median(column)))
    return [x, y], round(min(1.0, contrast / 35.0), 3)


def _iliac_proposal(enhanced: np.ndarray, side: str) -> tuple[list[int], float]:
    height, width = enhanced.shape
    # The first prototype selected bright lumbar structures near the midline.
    # Constrain proposals to the outer lower scan area where the crests appear.
    x_start, x_stop = ((round(.02 * width), round(.30 * width)) if side == "left"
                       else (round(.70 * width), round(.98 * width)))
    y_start, y_stop = round(.68 * height), round(.98 * height)
    region = enhanced[y_start:y_stop, x_start:x_stop].astype(np.float32)
    if region.size == 0:
        return [round((x_start + x_stop) / 2), round((y_start + y_stop) / 2)], 0.0
    blurred = cv2.GaussianBlur(region, (7, 7), 0)
    vertical_edge = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    # Favor the upper rim of a bright structure, but keep this only a proposal.
    score = np.maximum(vertical_edge, 0)
    score = cv2.GaussianBlur(score, (9, 9), 0)
    row_bias = np.linspace(1.1, .8, score.shape[0], dtype=np.float32)[:, None]
    score *= row_bias
    cy, cx = np.unravel_index(int(np.argmax(score)), score.shape)
    peak = float(score[cy, cx])
    typical = float(np.percentile(score, 90))
    confidence = min(1.0, max(0.0, (peak - typical) / 55.0))
    return [int(x_start + cx), int(y_start + cy)], round(confidence, 3)


def propose_positioning_landmarks(image: np.ndarray) -> dict:
    """Return three image-coordinate proposals; none is clinically identified."""
    gray = _gray8(image)
    height, width = gray.shape
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    upper, upper_score = _upper_spine_proposal(enhanced)
    left, left_score = _iliac_proposal(enhanced, "left")
    right, right_score = _iliac_proposal(enhanced, "right")
    return {
        "method": "opencv_positioning_proposals_v1",
        "image_size_wh": [width, height],
        "status": "review_required",
        "points_xy": {"upper_spine": upper, "left_iliac": left, "right_iliac": right},
        "image_contrast_scores_not_probabilities": {
            "upper_spine": upper_score, "left_iliac": left_score, "right_iliac": right_score},
        "warning": "upper_spine is not identified T12; iliac proposals require visual review",
    }


def validate_points(points: dict, image_shape: tuple[int, int]) -> dict:
    """Validate and integer-round user-edited pixel coordinates."""
    height, width = image_shape
    result = {}
    for name in LANDMARKS:
        if name not in points or len(points[name]) != 2:
            raise ValueError(f"Missing landmark: {name}")
        x, y = map(float, points[name])
        if not np.isfinite([x, y]).all() or not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"Out-of-bounds landmark: {name}")
        result[name] = [round(x), round(y)]
    return result


def draw_positioning_proposals(image: np.ndarray, points: dict) -> np.ndarray:
    """RGB image with numbered points 1=upper, 2=left, 3=right."""
    gray = _gray8(image)
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    height, width = gray.shape
    checked = validate_points(points, (height, width))
    for number, (name, color) in enumerate(
        [("upper_spine", (255, 220, 0)),
         ("left_iliac", (50, 255, 100)),
         ("right_iliac", (50, 180, 255))], start=1
    ):
        x, y = checked[name]
        radius = max(5, round(min(height, width) * .014))
        cv2.circle(overlay, (x, y), radius, color, 2)
        cv2.putText(overlay, str(number), (min(width - 12, x + radius + 2), max(12, y - radius)),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1, cv2.LINE_AA)
    return overlay

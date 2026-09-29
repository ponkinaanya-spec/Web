from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pydicom
from PIL import Image

from dxa_ml.hip_geometry import propose_hip_geometry
from dxa_ml.spine_axis import draw_spine_axis, estimate_spine_axis

from .config import RESULTS_DIR
from .dicom_preview import _normalize_pixels


REGION_TO_ANATOMY = {
    "Поясничный отдел позвоночника": "spine",
    "Проксимальный отдел бедра": "hip",
}


def read_dicom_gray(dicom_path: str | Path) -> np.ndarray:
    ds = pydicom.dcmread(str(dicom_path), force=True)
    pixels = np.asarray(ds.pixel_array)
    if pixels.ndim > 2:
        pixels = pixels[0]
    if pixels.ndim != 2:
        raise ValueError("Only 2D DICOM images can be visualized")
    return _normalize_pixels(ds, pixels)


def detect_anatomy(result: dict[str, Any] | None, anatomical_region: str | None = None) -> str:
    selected = (result or {}).get("selected_anatomy")
    if selected in {"spine", "hip"}:
        return selected
    if anatomical_region:
        for label, anatomy in REGION_TO_ANATOMY.items():
            if label in anatomical_region:
                return anatomy
        if "позвоноч" in anatomical_region.lower():
            return "spine"
        if "бед" in anatomical_region.lower():
            return "hip"
    return "unknown"


def _draw_label(image: np.ndarray, text: str, y: int = 18) -> None:
    cv2.putText(
        image,
        text,
        (8, min(y, image.shape[0] - 4)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )


def _hip_overlay(gray: np.ndarray) -> np.ndarray:
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    geometry = propose_hip_geometry(gray)
    contour = geometry.get("contour")
    if contour is not None:
        cv2.drawContours(overlay, [contour], -1, (255, 220, 0), 2)

    axis = geometry.get("shaft_axis")
    if axis:
        (xa, ya), (xb, yb) = axis
        cv2.line(overlay, (xa, ya), (xb, yb), (255, 70, 70), 2)

    colors = {
        "lateral_tip_unverified": (80, 255, 80),
        "medial_edge_extreme_unidentified": (0, 255, 255),
        "femoral_head_center": (255, 120, 220),
        "lesser_trochanter": (120, 180, 255),
        "ischial_bone": (255, 160, 80),
    }
    for name, point in (geometry.get("candidates") or {}).items():
        if point is None:
            continue
        x, y = int(point[0]), int(point[1])
        color = colors.get(name, (255, 255, 255))
        cv2.circle(overlay, (x, y), 4, color, -1)
        cv2.putText(overlay, name[:12], (x + 6, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1, cv2.LINE_AA)

    _draw_label(overlay, "bone contour + shaft axis; verify visually")
    return overlay


def _point(x: float, y: float, width: int, height: int) -> dict[str, float]:
    return {
        "x": round(float(np.clip(x / max(width - 1, 1), 0, 1)), 6),
        "y": round(float(np.clip(y / max(height - 1, 1), 0, 1)), 6),
    }


def _spine_markup(gray: np.ndarray, anatomical_region: str | None) -> dict[str, Any]:
    height, width = gray.shape
    estimate = estimate_spine_axis(gray)
    objects: list[dict[str, Any]] = []
    endpoints = estimate.get("line_endpoints_xy")
    trace = estimate.get("trace_xy") or []
    axis_points = [_point(x, y, width, height) for x, y in (endpoints or trace)]
    border_rows = estimate.get("borders_lry", [])
    left_points = [_point(left, y, width, height) for left, _, y in border_rows]
    right_points = [_point(right, y, width, height) for _, right, y in border_rows]
    mid_points = [_point(x, y, width, height) for x, y in trace]

    if left_points:
        objects.append(
            {
                "id": "spine-left-border-points",
                "label": "Левые точки границы",
                "type": "points",
                "color": "#50ff50",
                "radius_ratio": 2 / max(height, 1),
                "points": left_points,
            }
        )
    if right_points:
        objects.append(
            {
                "id": "spine-right-border-points",
                "label": "Правые точки границы",
                "type": "points",
                "color": "#50ff50",
                "radius_ratio": 2 / max(height, 1),
                "points": right_points,
            }
        )
    if mid_points:
        objects.append(
            {
                "id": "spine-midpoints",
                "label": "Срединные точки",
                "type": "points",
                "color": "#00ffff",
                "radius_ratio": 2 / max(height, 1),
                "points": mid_points,
            }
        )
    if axis_points:
        if endpoints:
            (xa, ya), (xb, yb) = endpoints
            center = (float(xa) + float(xb)) / 2
            objects.append(
                {
                    "id": "spine-reference-vertical",
                    "label": "Вертикаль",
                    "type": "polyline",
                    "color": "#ff4646",
                    "line_width_ratio": 1 / max(height, 1),
                    "points": [_point(center, ya, width, height), _point(center, yb, width, height)],
                }
            )
        objects.append(
            {
                "id": "spine-axis",
                "label": "Срединная линия",
                "type": "polyline",
                "color": "#ffe600",
                "line_width_ratio": 2 / max(height, 1),
                "points": axis_points,
            }
        )

    if not objects:
        objects.append(
            {
                "id": "spine-axis",
                "label": "Срединная линия",
                "type": "polyline",
                "color": "#ffe600",
                "points": [{"x": 0.5, "y": 0.18}, {"x": 0.5, "y": 0.82}],
            }
        )

    return {
        "schema": "dxa-quality-contour-v2",
        "region": anatomical_region or "Поясничный отдел позвоночника",
        "coordinate_space": "normalized_preview",
        "source": "ml_spine_axis",
        "objects": objects,
        "markers": [],
    }


def _hip_markup(gray: np.ndarray, anatomical_region: str | None) -> dict[str, Any]:
    height, width = gray.shape
    geometry = propose_hip_geometry(gray)
    objects: list[dict[str, Any]] = []
    contour = geometry.get("contour")
    if contour is not None:
        contour_points = [_point(point[0][0], point[0][1], width, height) for point in contour]
        if contour_points:
            objects.append(
                {
                    "id": "hip-bone-contour",
                    "label": "Костный контур",
                    "type": "polygon",
                    "color": "#ffe600",
                    "line_width_ratio": 2 / max(height, 1),
                    "points": contour_points,
                }
            )

    axis = geometry.get("shaft_axis")
    if axis:
        objects.append(
            {
                "id": "hip-shaft-axis",
                "label": "Ось бедра",
                "type": "polyline",
                "color": "#ff4646",
                "line_width_ratio": 2 / max(height, 1),
                "points": [_point(x, y, width, height) for x, y in axis],
            }
        )

    markers = []
    for name, point in (geometry.get("candidates") or {}).items():
        if point is None:
            continue
        markers.append(
            {
                "id": name,
                "type": "point",
                "label": name,
                "color": "#00e5ff" if "medial" in name else "#50ff50",
                **_point(point[0], point[1], width, height),
            }
        )

    if not objects:
        objects.append(
            {
                "id": "hip-bone-contour",
                "label": "Костный контур",
                "type": "polyline",
                "color": "#ffe600",
                "points": [
                    {"x": 0.73, "y": 0.22},
                    {"x": 0.68, "y": 0.25},
                    {"x": 0.63, "y": 0.31},
                    {"x": 0.59, "y": 0.40},
                    {"x": 0.56, "y": 0.52},
                    {"x": 0.54, "y": 0.66},
                    {"x": 0.53, "y": 0.82},
                ],
            }
        )

    return {
        "schema": "dxa-quality-contour-v2",
        "region": anatomical_region or "Проксимальный отдел бедра",
        "coordinate_space": "normalized_preview",
        "source": "ml_hip_geometry",
        "objects": objects,
        "markers": markers,
    }


def create_editable_markup(
    dicom_path: str | Path,
    result: dict[str, Any] | None = None,
    anatomical_region: str | None = None,
) -> dict[str, Any]:
    gray = read_dicom_gray(dicom_path)
    anatomy = detect_anatomy(result, anatomical_region)
    if anatomy == "spine":
        return _spine_markup(gray, anatomical_region)
    if anatomy == "hip":
        return _hip_markup(gray, anatomical_region)
    return {
        "schema": "dxa-quality-contour-v2",
        "region": anatomical_region or "Не определено",
        "coordinate_space": "normalized_preview",
        "source": "fallback",
        "objects": [
            {
                "id": "fallback-line",
                "label": "Разметка",
                "type": "polyline",
                "color": "#ffe600",
                "points": [{"x": 0.5, "y": 0.25}, {"x": 0.5, "y": 0.75}],
            }
        ],
        "markers": [],
    }


def create_analysis_overlay(
    dicom_path: str | Path,
    study_id: str,
    result: dict[str, Any] | None = None,
    anatomical_region: str | None = None,
    max_side: int = 1200,
) -> Path:
    gray = read_dicom_gray(dicom_path)
    anatomy = detect_anatomy(result, anatomical_region)

    if anatomy == "spine":
        estimate = estimate_spine_axis(gray)
        overlay = draw_spine_axis(gray, estimate)
    elif anatomy == "hip":
        overlay = _hip_overlay(gray)
    else:
        overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        _draw_label(overlay, "overlay unavailable: anatomy not selected")

    output_dir = RESULTS_DIR / "overlays"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{study_id}.png"
    image = Image.fromarray(overlay, mode="RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    image.save(output_path)
    return output_path


def create_markup_overlay(
    dicom_path: str | Path,
    study_id: str,
    markup: dict[str, Any],
    suffix: str = "edited",
    max_side: int = 1200,
) -> Path:
    gray = read_dicom_gray(dicom_path)
    height, width = gray.shape
    overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

    machine_layer = np.zeros((height, width, 4), dtype=np.uint8)
    for item in markup.get("objects") or []:
        points = item.get("points") or []
        if str(item.get("type")) == "points":
            color_hex = str(item.get("color") or "#50ff50").lstrip("#")
            if len(color_hex) == 6:
                color = tuple(int(color_hex[index:index + 2], 16) for index in (0, 2, 4))
            else:
                color = (80, 255, 80)
            radius = max(1, int(round(float(item.get("radius_ratio") or 0) * height))) or 2
            for point in points:
                x = round(float(point.get("x", 0)) * (width - 1))
                y = round(float(point.get("y", 0)) * (height - 1))
                cv2.circle(machine_layer, (x, y), radius, (*color, 255), -1, cv2.LINE_AA)
            continue
        if len(points) < 2:
            continue
        color_hex = str(item.get("color") or "#ffe600").lstrip("#")
        if len(color_hex) == 6:
            color = tuple(int(color_hex[index:index + 2], 16) for index in (0, 2, 4))
        else:
            color = (255, 230, 0)
        pixel_points = np.array(
            [
                [
                    round(float(point.get("x", 0)) * (width - 1)),
                    round(float(point.get("y", 0)) * (height - 1)),
                ]
                for point in points
            ],
            dtype=np.int32,
        )
        line_width = max(1, int(round(float(item.get("line_width_ratio") or 0) * height))) or 2
        if str(item.get("type")) == "polygon" and len(pixel_points) > 2:
            cv2.polylines(machine_layer, [pixel_points], True, (*color, 255), line_width, cv2.LINE_AA)
        else:
            cv2.polylines(machine_layer, [pixel_points], False, (*color, 255), line_width, cv2.LINE_AA)

    for marker in markup.get("markers") or []:
        color_hex = str(marker.get("color") or "#50ff50").lstrip("#")
        if len(color_hex) == 6:
            color = tuple(int(color_hex[index:index + 2], 16) for index in (0, 2, 4))
        else:
            color = (80, 255, 80)
        x = round(float(marker.get("x", 0)) * (width - 1))
        y = round(float(marker.get("y", 0)) * (height - 1))
        cv2.circle(machine_layer, (x, y), 4, (*color, 255), -1, cv2.LINE_AA)

    def apply_strokes(layer: np.ndarray, strokes: list[dict[str, Any]]) -> None:
        for stroke in strokes:
            points = stroke.get("points") or []
            if not points:
                continue
            if stroke.get("size_ratio") is not None:
                size = max(1, int(round(float(stroke.get("size_ratio")) * height)))
            else:
                size = max(1, int(round(float(stroke.get("size", 6)))))
            pixel_points = [
                (
                    round(float(point.get("x", 0)) * (width - 1)),
                    round(float(point.get("y", 0)) * (height - 1)),
                )
                for point in points
            ]
            color = (255, 230, 0, 255)
            is_eraser = stroke.get("tool") == "eraser"
            target = np.zeros(layer.shape[:2], dtype=np.uint8) if is_eraser else layer
            draw_color = 255 if is_eraser else color
            if len(pixel_points) == 1:
                x, y = pixel_points[0]
                if stroke.get("shape") == "square":
                    cv2.rectangle(target, (x - size // 2, y - size // 2), (x + size // 2, y + size // 2), draw_color, -1)
                else:
                    cv2.circle(target, (x, y), size // 2, draw_color, -1, cv2.LINE_AA)
            else:
                line_type = cv2.LINE_8 if stroke.get("shape") == "square" else cv2.LINE_AA
                cv2.polylines(target, [np.array(pixel_points, dtype=np.int32)], False, draw_color, size, line_type)
                if stroke.get("shape") == "square":
                    for x, y in pixel_points:
                        cv2.rectangle(target, (x - size // 2, y - size // 2), (x + size // 2, y + size // 2), draw_color, -1)
            if is_eraser:
                layer[target > 0] = (0, 0, 0, 0)

    apply_strokes(machine_layer, markup.get("machine_eraser_strokes") or [])
    alpha = machine_layer[:, :, 3:4].astype(np.float32) / 255.0
    overlay = np.uint8(overlay.astype(np.float32) * (1 - alpha) + machine_layer[:, :, :3].astype(np.float32) * alpha)

    brush_layer = np.zeros((height, width, 4), dtype=np.uint8)
    apply_strokes(brush_layer, markup.get("brush_strokes") or [])

    alpha = brush_layer[:, :, 3:4].astype(np.float32) / 255.0
    overlay = np.uint8(overlay.astype(np.float32) * (1 - alpha) + brush_layer[:, :, :3].astype(np.float32) * alpha)

    output_dir = RESULTS_DIR / "overlays"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{study_id}_{suffix}.png"
    image = Image.fromarray(overlay, mode="RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    image.save(output_path)
    return output_path

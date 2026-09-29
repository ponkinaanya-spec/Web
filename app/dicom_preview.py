from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
from PIL import Image

from .config import RESULTS_DIR


def _normalize_pixels(ds: pydicom.Dataset, pixels: np.ndarray) -> np.ndarray:
    image = pixels.astype(np.float32)
    image = image * float(ds.get("RescaleSlope", 1)) + float(ds.get("RescaleIntercept", 0))
    if str(ds.get("PhotometricInterpretation", "")) == "MONOCHROME1":
        image = image.max() - image

    finite = image[np.isfinite(image)]
    if finite.size == 0:
        raise ValueError("DICOM pixels are not finite")

    lo, hi = np.percentile(finite, [1, 99])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return np.zeros(image.shape, dtype=np.uint8)

    normalized = np.clip((image - lo) / (hi - lo), 0, 1)
    return (normalized * 255).astype(np.uint8)


def create_dicom_preview(dicom_path: str | Path, study_id: str, max_side: int = 900) -> Path:
    dicom_path = Path(dicom_path)
    ds = pydicom.dcmread(str(dicom_path), force=True)
    pixels = np.asarray(ds.pixel_array)
    if pixels.ndim > 2:
        pixels = pixels[0]
    if pixels.ndim != 2:
        raise ValueError("Only 2D DICOM images can be previewed")

    preview_dir = RESULTS_DIR / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"{study_id}.png"

    image = Image.fromarray(_normalize_pixels(ds, pixels), mode="L")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    image.save(preview_path)
    return preview_path

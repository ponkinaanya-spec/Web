"""Read-only inference for the hip-positioning candidate exported by notebook 19.

This is a review-priority score, not a clinical quality or rotation decision.
The image preprocessing intentionally reproduces notebook 19 rather than the
generic DXA router preprocessor, because changing it would change the model.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pydicom
import torch
from torch import nn
from torchvision import models


FORMAT = "dxa_hip_positioning_densenet121_logreg_v1"
PREPROCESSING = "percentile_1_99_round_uint8_cv2_letterbox_v1"


def preprocess_hip_positioning(ds: pydicom.Dataset, size: int = 224) -> np.ndarray:
    """Match notebook 19 gray() plus letterbox() on valid 2-D DXA pixels."""
    if int(ds.get("SamplesPerPixel", 1)) != 1 or int(ds.get("NumberOfFrames", 1)) != 1:
        raise ValueError("Only single-frame grayscale DICOM is supported")
    pixels = np.asarray(ds.pixel_array)
    if pixels.ndim != 2 or pixels.size > 32_000_000:
        raise ValueError("Invalid DICOM image shape")
    values = pixels.astype(np.float32)
    values = values * float(ds.get("RescaleSlope", 1) or 1)
    values = values + float(ds.get("RescaleIntercept", 0) or 0)
    if not np.isfinite(values).all():
        raise ValueError("Non-finite pixels")
    low, high = np.percentile(values, [1, 99])
    if high <= low:
        raise ValueError("Insufficient image contrast")
    values = np.clip((values - low) / (high - low), 0, 1)
    photo = str(ds.get("PhotometricInterpretation", "")).upper()
    if photo == "MONOCHROME1":
        values = 1 - values
    elif photo != "MONOCHROME2":
        raise ValueError(f"Unsupported photometric interpretation: {photo}")
    gray = np.uint8(np.rint(values * 255))
    height, width = gray.shape
    scale = min(size / height, size / width)
    new_height, new_width = max(1, round(height * scale)), max(1, round(width * scale))
    resized = cv2.resize(
        gray, (new_width, new_height),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
    )
    output = np.zeros((size, size), dtype=np.uint8)
    top, left = (size - new_height) // 2, (size - new_width) // 2
    output[top:top + new_height, left:left + new_width] = resized
    return output


class HipPositioningPredictor:
    """Frozen DenseNet121 + standardized logistic head, loaded once per process."""

    def __init__(self, bundle: dict, device: str = "cpu"):
        if bundle.get("format") != FORMAT or bundle.get("preprocessing") != PREPROCESSING:
            raise ValueError("Unknown hip-positioning bundle format")
        if int(bundle.get("image_size", 0)) != 224:
            raise ValueError("Unsupported image size")
        self.device = torch.device(device)
        self.bundle = bundle
        self.model = models.densenet121(weights=None)
        self.model.classifier = nn.Identity()
        self.model.load_state_dict(bundle["backbone_state"], strict=True)
        self.model = self.model.to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        self.mean = torch.tensor(bundle["normalization_mean"], dtype=torch.float32,
                                 device=self.device)[None, :, None, None]
        self.std = torch.tensor(bundle["normalization_std"], dtype=torch.float32,
                                device=self.device)[None, :, None, None]
        self.scaler_mean = bundle["scaler_mean"].cpu().numpy()
        self.scaler_scale = bundle["scaler_scale"].cpu().numpy()
        self.coefficient = bundle["coefficient"].cpu().numpy()
        self.intercept = float(bundle["intercept"].item())
        self.threshold = float(bundle["threshold"])
        if (not np.isfinite(self.threshold) or not 0 <= self.threshold <= 1
                or np.any(self.scaler_scale <= 0)):
            raise ValueError("Invalid hip-positioning bundle parameters")

    @classmethod
    def from_file(cls, path: str | Path, device: str = "cpu") -> "HipPositioningPredictor":
        bundle = torch.load(path, map_location="cpu", weights_only=True)
        return cls(bundle, device=device)

    @torch.inference_mode()
    def predict_dataset(self, ds: pydicom.Dataset) -> dict:
        image = preprocess_hip_positioning(ds)
        tensor = torch.from_numpy(image.copy()).to(self.device).float()[None, None] / 255
        tensor = (tensor.repeat(1, 3, 1, 1) - self.mean) / self.std
        vector = self.model(tensor).flatten().cpu().numpy().astype(np.float64)
        normalized = (vector - self.scaler_mean) / self.scaler_scale
        logit = float(normalized @ self.coefficient + self.intercept)
        score = float(1 / (1 + np.exp(-np.clip(logit, -30, 30))))
        return {
            "score": score,
            "review_threshold": self.threshold,
            "priority_flag": score >= self.threshold,
            "auto_clear_allowed": False,
            "clinical_decision": None,
            "deployment_status": self.bundle["deployment_status"],
        }

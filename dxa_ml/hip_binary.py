"""Exploratory binary hip-quality inference from the project Excel labels."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import torch
from torch import nn
from torchvision import models

from .hip_positioning import PREPROCESSING, preprocess_hip_positioning


FORMAT = "dxa_hip_binary_densenet121_logreg_v1"


class HipBinaryPredictor:
    def __init__(self, bundle: dict, device: str = "cpu"):
        if bundle.get("format") != FORMAT or bundle.get("task") != "hip_image_incorrect":
            raise ValueError("Unexpected hip-quality checkpoint")
        if bundle.get("preprocessing") != PREPROCESSING or bundle.get("image_size") != 224:
            raise ValueError("Unsupported hip-quality preprocessing")
        self.device = torch.device(device)
        self.model = models.densenet121(weights=None)
        self.model.classifier = nn.Identity()
        self.model.load_state_dict(bundle["backbone_state"], strict=True)
        self.model.to(self.device).eval()
        for param in self.model.parameters():
            param.requires_grad_(False)
        self.mean = torch.tensor(bundle["normalization_mean"], device=self.device)[None, :, None, None]
        self.std = torch.tensor(bundle["normalization_std"], device=self.device)[None, :, None, None]
        self.scaler_mean = bundle["scaler_mean"].numpy().astype(np.float64)
        self.scaler_scale = bundle["scaler_scale"].numpy().astype(np.float64)
        self.coefficient = bundle["coefficient"].numpy().astype(np.float64)
        self.intercept = float(bundle["intercept"])
        self.threshold = float(bundle["threshold"])
        if not 0 <= self.threshold <= 1 or np.any(self.scaler_scale <= 0):
            raise ValueError("Invalid hip-quality threshold or scale")

    @classmethod
    def from_file(cls, path: str | Path, device: str = "cpu") -> "HipBinaryPredictor":
        return cls(torch.load(path, map_location="cpu", weights_only=True), device=device)

    @torch.inference_mode()
    def predict_dataset(self, ds: pydicom.Dataset) -> dict:
        image = preprocess_hip_positioning(ds)
        x = torch.from_numpy(image.copy()).to(self.device).float()[None, None] / 255
        x = (x.repeat(1, 3, 1, 1) - self.mean) / self.std
        vector = self.model(x).flatten().cpu().numpy().astype(np.float64)
        normalized = (vector - self.scaler_mean) / self.scaler_scale
        logit = float(normalized @ self.coefficient + self.intercept)
        probability = float(1 / (1 + np.exp(-np.clip(logit, -30, 30))))
        incorrect = probability >= self.threshold
        return {
            "quality_class": int(incorrect),
            "quality_label": "incorrect" if incorrect else "correct",
            "incorrect_probability": probability,
            "threshold": self.threshold,
            "decision_status": "provisional_requires_review",
        }

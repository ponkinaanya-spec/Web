"""Inference for the frozen-feature spine bundles exported by notebook 13.

The artifact output is a suspicion flag. Axis and positioning heads are
research-only binary scores, not measurements of degrees or landmark geometry.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pydicom
import torch
from PIL import Image
from torch import nn
from torchvision import models


FORMAT = "dxa_spine_frozen_features_logreg_v1"


def _feature_model(name: str) -> nn.Module:
    if name == "resnet18":
        model = models.resnet18(weights=None)
        model.fc = nn.Identity()
    elif name == "resnet50":
        model = models.resnet50(weights=None)
        model.fc = nn.Identity()
    elif name == "densenet121":
        model = models.densenet121(weights=None)
        model.classifier = nn.Identity()
    elif name == "efficientnet_b0":
        model = models.efficientnet_b0(weights=None)
        model.classifier = nn.Identity()
    else:
        raise ValueError(f"Unsupported backbone: {name}")
    return model


def preprocess_spine_dicom(ds: pydicom.Dataset, size: int = 320) -> np.ndarray:
    """Replicate notebook 12/13 pixel preprocessing; return uint8 letterbox."""
    if int(ds.get("SamplesPerPixel", 1)) != 1 or int(ds.get("NumberOfFrames", 1)) != 1:
        raise ValueError("Only single-frame grayscale DICOM is supported")
    raw = np.asarray(ds.pixel_array)
    if raw.ndim != 2 or raw.size > 32_000_000:
        raise ValueError("Invalid DICOM image shape")
    pixels = raw.astype(np.float32) * float(ds.get("RescaleSlope", 1))
    pixels += float(ds.get("RescaleIntercept", 0))
    if not np.isfinite(pixels).all():
        raise ValueError("Non-finite pixels")
    photo = str(ds.get("PhotometricInterpretation", "")).upper()
    if photo == "MONOCHROME1":
        pixels = pixels.max() - pixels
    elif photo != "MONOCHROME2":
        raise ValueError(f"Unsupported photometric interpretation: {photo}")
    low, high = np.percentile(pixels, [1, 99])
    if high <= low:
        raise ValueError("Insufficient image contrast")
    gray = np.uint8(np.clip((pixels - low) / (high - low) * 255, 0, 255))
    height, width = gray.shape
    scale = min(size / width, size / height)
    new_width, new_height = max(1, round(width * scale)), max(1, round(height * scale))
    resized = Image.fromarray(gray).resize((new_width, new_height), Image.Resampling.BILINEAR)
    canvas = Image.new("L", (size, size), 0)
    canvas.paste(resized, ((size - new_width) // 2, (size - new_height) // 2))
    return np.asarray(canvas).copy()


class SpineBundlePredictor:
    """Load a notebook-13 bundle and produce per-label probabilities."""

    def __init__(self, bundle: dict, device: str = "cpu"):
        if bundle.get("format") != FORMAT:
            raise ValueError("Unknown spine bundle format")
        self.bundle = bundle
        self.device = torch.device(device)
        self.mean = torch.tensor(bundle["normalization_mean"], device=self.device)[None, :, None, None]
        self.std = torch.tensor(bundle["normalization_std"], device=self.device)[None, :, None, None]
        self.networks = {}
        for name, state in bundle["backbone_states"].items():
            network = _feature_model(name)
            network.load_state_dict(state, strict=True)
            network = network.to(self.device).eval()
            for parameter in network.parameters():
                parameter.requires_grad_(False)
            self.networks[name] = network

    @classmethod
    def from_file(cls, path: str | Path, device: str = "cpu") -> "SpineBundlePredictor":
        bundle = torch.load(path, map_location="cpu", weights_only=True)
        return cls(bundle, device=device)

    @torch.inference_mode()
    def predict_dataset(self, ds: pydicom.Dataset) -> dict:
        size = int(self.bundle["image_size"])
        gray = preprocess_spine_dicom(ds, size)
        array = torch.from_numpy(gray.copy()).to(self.device).float() / 255
        tensor = (array[None, None].repeat(1, 3, 1, 1) - self.mean) / self.std
        vectors = {name: network(tensor).flatten().cpu().numpy()
                   for name, network in self.networks.items()}
        outputs = {}
        for label in self.bundle["labels"]:
            head = self.bundle["heads"][label]
            vector = vectors[head["backbone"]]
            normalized = (vector - head["scaler_mean"].numpy()) / head["scaler_scale"].numpy()
            logit = float(normalized @ head["coefficient"].numpy() + head["intercept"].item())
            probability = float(1 / (1 + np.exp(-np.clip(logit, -30, 30))))
            threshold = float(head["threshold"])
            outputs[label] = {"probability": probability,
                              "threshold": threshold,
                              "flag_for_review": probability >= threshold}
        return {"scores": outputs, "deployment_status": self.bundle["deployment_status"],
                "clinical_decision": None}

    def predict_bytes(self, dicom_bytes: bytes) -> dict:
        ds = pydicom.dcmread(BytesIO(dicom_bytes))
        return self.predict_dataset(ds)

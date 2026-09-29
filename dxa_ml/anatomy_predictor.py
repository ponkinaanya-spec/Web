"""Inference for the saved three-class DXA anatomy checkpoint.

The output is a routing suggestion, not a clinical determination.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import torch
from PIL import Image
from torch import nn
from torchvision import models

from .imaging import normalize


CLASSES = ["spine", "hip", "unknown"]


class AnatomyPredictor:
    def __init__(self, checkpoint_path: str | Path, device: str = "cpu"):
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        if checkpoint.get("model_name") != "efficientnet_b0":
            raise ValueError("Expected an EfficientNet-B0 anatomy checkpoint")
        if checkpoint.get("class_names") != CLASSES:
            raise ValueError("Expected the trained spine/hip/unknown checkpoint")
        self.size = int(checkpoint["image_size"])
        if self.size != 224:
            raise ValueError("Unexpected anatomy input size")
        self.device = torch.device(device)
        self.mean = torch.tensor(checkpoint["normalization_mean"], device=self.device)[None, :, None, None]
        self.std = torch.tensor(checkpoint["normalization_std"], device=self.device)[None, :, None, None]
        model = models.efficientnet_b0(weights=None)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, len(CLASSES))
        model.load_state_dict(checkpoint["state_dict"], strict=True)
        self.model = model.to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)

    def _tensor(self, ds: pydicom.Dataset, raw: np.ndarray) -> torch.Tensor:
        # Reproduce notebook 03: 1–99 percentile, ToPILImage(float32),
        # uniform letterbox to 224, ToTensor, three channels, ImageNet normalization.
        image = normalize(ds, raw)
        pil = Image.fromarray((image * 255).astype(np.uint8))
        width, height = pil.size
        scale = min(self.size / width, self.size / height)
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = pil.resize(new_size, Image.Resampling.BILINEAR)
        canvas = Image.new("L", (self.size, self.size), 0)
        canvas.paste(resized, ((self.size - new_size[0]) // 2,
                               (self.size - new_size[1]) // 2))
        gray = torch.from_numpy(np.asarray(canvas).copy()).to(self.device).float() / 255
        return (gray[None, None].repeat(1, 3, 1, 1) - self.mean) / self.std

    @torch.inference_mode()
    def predict_dataset(self, ds: pydicom.Dataset, raw: np.ndarray) -> dict:
        probabilities = torch.softmax(self.model(self._tensor(ds, raw))[0], dim=0)
        values = probabilities.detach().cpu().tolist()
        winner = int(torch.argmax(probabilities).item())
        return {
            "anatomy": CLASSES[winner],
            "scores": {name: float(value) for name, value in zip(CLASSES, values)},
            "max_score": float(values[winner]),
            "score_note": "softmax scores are not calibrated clinical probabilities",
        }

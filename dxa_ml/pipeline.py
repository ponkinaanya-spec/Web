"""Local DXA inference orchestration for one already-selected DICOM image.

No input DICOM is modified. Quality labels are provisional research outputs.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np

from .anatomy_predictor import AnatomyPredictor
from .hip_positioning import HipPositioningPredictor
from .hip_binary import HipBinaryPredictor
from .imaging import metadata_hints, normalize, pixel_hash, read_dicom
from .spine_axis import estimate_spine_axis
from .spine_saved_bundle import SpineBundlePredictor


class DxaPipeline:
    def __init__(self, anatomy_model: str | Path, artifact_model: str | Path,
                 positioning_model: str | Path, device: str = "cpu",
                 hip_positioning_model: str | Path | None = None,
                 hip_binary_model: str | Path | None = None):
        # Load once at process startup; many images reuse these instances.
        self.anatomy = AnatomyPredictor(anatomy_model, device=device)
        self.artifact = SpineBundlePredictor.from_file(artifact_model, device=device)
        self.positioning = SpineBundlePredictor.from_file(positioning_model, device=device)
        self.hip_positioning = (HipPositioningPredictor.from_file(hip_positioning_model, device=device)
                                if hip_positioning_model is not None else None)
        self.hip_binary = (HipBinaryPredictor.from_file(hip_binary_model, device=device)
                           if hip_binary_model is not None else None)
        if self.artifact.bundle.get("labels") != ["artifact"]:
            raise ValueError("Artifact checkpoint has the wrong task")
        if self.positioning.bundle.get("labels") != ["positioning"]:
            raise ValueError("Positioning checkpoint has the wrong task")

    def predict_bytes(self, dicom_bytes: bytes, confirmed_anatomy: str | None = None) -> dict:
        if len(dicom_bytes) > 256_000_000:
            raise ValueError("DICOM exceeds the 256 MB input limit")
        if confirmed_anatomy not in (None, "spine", "hip"):
            raise ValueError("confirmed_anatomy must be spine or hip")
        ds, raw = read_dicom(io.BytesIO(dicom_bytes))
        hint = metadata_hints(ds)
        route = self.anatomy.predict_dataset(ds, raw)
        predicted = route["anatomy"]
        conflict = bool(hint["anatomy_hint"] and hint["anatomy_hint"] != predicted)
        selected = confirmed_anatomy or predicted
        output = {
            "schema_version": 1,
            "image_key": hashlib.sha256(dicom_bytes).hexdigest()[:16],
            "pixel_hash": pixel_hash(raw),
            "image_size": [int(raw.shape[1]), int(raw.shape[0])],
            "anatomy": route,
            "metadata_hints": hint,
            "anatomy_conflict": conflict,
            "selected_anatomy": selected,
            "selected_anatomy_source": "user_confirmed" if confirmed_anatomy else "model",
            "spine": None,
            "hip": None,
            "overall_status": "review_required",
        }
        if confirmed_anatomy is None and (predicted == "unknown" or conflict):
            output["routing_status"] = "manual_anatomy_confirmation_required"
            return output
        output["routing_status"] = "routed_with_manual_confirmation" if confirmed_anatomy else "routed_by_model"
        if selected == "hip":
            quality = self.hip_binary.predict_dataset(ds) if self.hip_binary is not None else None
            output["hip"] = {
                "status": ("provisional_binary_classification" if quality is not None
                           else "assisted_review_only" if self.hip_positioning is not None
                           else "hip_quality_models_not_connected"),
                "laterality": hint["laterality_hint"],
                "laterality_source": "dicom_metadata_hint" if hint["laterality_hint"] else None,
                "quality": quality,
                "positioning": (self.hip_positioning.predict_dataset(ds)
                                if self.hip_positioning is not None else None),
                "roi": {"status": "not_separately_predicted"},
                "final_decision": quality["quality_label"] if quality else None,
            }
            output["overall_status"] = quality["decision_status"] if quality else "review_required"
            return output

        # The angle uses ORIGINAL image proportions, not the 224/320 model tensor.
        gray = np.uint8(np.clip(normalize(ds, raw) * 255, 0, 255))
        axis = estimate_spine_axis(gray)
        artifact = self.artifact.predict_dataset(ds)["scores"]["artifact"]
        positioning = self.positioning.predict_dataset(ds)["scores"]["positioning"]
        output["spine"] = {
            "axis": {"status": axis["status"], "angle_deg_estimate": axis["angle_deg"],
                     "over_5_deg_provisional": (axis["angle_deg"] > 5
                                                 if axis["angle_deg"] is not None else None),
                     "method": axis["method"], "reason": axis["reason"]},
            "artifact": {"score": artifact["probability"],
                         "review_threshold": artifact["threshold"],
                         "priority_flag": artifact["flag_for_review"]},
            "positioning": {"score": positioning["probability"],
                            "review_threshold": positioning["threshold"],
                            "priority_flag": positioning["flag_for_review"],
                            "auto_clear_allowed": False},
        }
        return output

    def predict_file(self, path: str | Path, confirmed_anatomy: str | None = None) -> dict:
        path = Path(path)
        if path.stat().st_size > 256_000_000:
            raise ValueError("DICOM exceeds the 256 MB input limit")
        return self.predict_bytes(path.read_bytes(), confirmed_anatomy=confirmed_anatomy)

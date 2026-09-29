"""Map local inference results to the per-image table requested in the brief."""

from __future__ import annotations

import csv
import io
import time
import warnings

import pydicom

from .pipeline import DxaPipeline


REQUIRED_COLUMNS = [
    "path_to_study", "study_uid", "image_uid", "anatomical_region",
    "quality_class", "violation_type", "processing_status", "time_of_processing",
]
EXTRA_COLUMNS = ["review_required", "decision_status", "error_code",
                 "hip_positioning_score", "hip_positioning_review_flag",
                 "hip_incorrect_probability", "hip_quality_threshold"]
ALL_COLUMNS = REQUIRED_COLUMNS + EXTRA_COLUMNS


def _safe_csv_text(value: object) -> str:
    """Do not let an untrusted archive name become an Excel formula."""
    text = str(value) if value is not None else ""
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _dicom_uids(dicom_bytes: bytes) -> tuple[str, str]:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Invalid value for VR UI.*")
        ds = pydicom.dcmread(io.BytesIO(dicom_bytes), stop_before_pixels=True)
    return str(ds.get("StudyInstanceUID", "")).strip(), str(ds.get("SOPInstanceUID", "")).strip()


def predict_row(pipeline: DxaPipeline, dicom_bytes: bytes, path_to_study: str) -> dict:
    started = time.perf_counter()
    row = {column: "" for column in ALL_COLUMNS}
    row["path_to_study"] = _safe_csv_text(path_to_study)
    row["review_required"] = True
    try:
        study_uid, image_uid = _dicom_uids(dicom_bytes)
        row["study_uid"] = _safe_csv_text(study_uid)
        row["image_uid"] = _safe_csv_text(image_uid)
        if not study_uid or not image_uid:
            raise ValueError("missing_required_dicom_uid")
        result = pipeline.predict_bytes(dicom_bytes)
        region = result["selected_anatomy"]
        row["anatomical_region"] = region
        if result["routing_status"] == "manual_anatomy_confirmation_required":
            row["error_code"] = "anatomy_requires_confirmation"
        elif region == "hip":
            hip = result["hip"]
            if hip["quality"] is not None:
                quality = hip["quality"]
                row["quality_class"] = quality["quality_class"]
                row["violation_type"] = "hip_quality_unspecified" if quality["quality_class"] else ""
                row["hip_incorrect_probability"] = round(quality["incorrect_probability"], 6)
                row["hip_quality_threshold"] = round(quality["threshold"], 6)
                row["processing_status"] = "Success"
                row["decision_status"] = quality["decision_status"]
            elif hip["positioning"] is None:
                row["error_code"] = "hip_quality_models_not_connected"
            if hip["positioning"] is not None:
                row["hip_positioning_score"] = round(hip["positioning"]["score"], 6)
                row["hip_positioning_review_flag"] = hip["positioning"]["priority_flag"]
                if hip["quality"] is None:
                    row["processing_status"] = "Success"
                    row["decision_status"] = "partial_hip_review_required"
                    row["error_code"] = "hip_binary_model_not_connected"
        elif region == "spine":
            spine = result["spine"]
            if spine["axis"]["angle_deg_estimate"] is None:
                row["error_code"] = "axis_not_estimated"
            else:
                violations = []
                if spine["axis"]["over_5_deg_provisional"]:
                    violations.append("spine_axis")
                if spine["artifact"]["priority_flag"]:
                    violations.append("spine_artifact")
                if spine["positioning"]["priority_flag"]:
                    violations.append("spine_positioning")
                row["quality_class"] = int(bool(violations))
                row["violation_type"] = ";".join(violations)
                row["processing_status"] = "Success"
                row["decision_status"] = "provisional_requires_review"
        else:
            row["error_code"] = "unsupported_anatomy"
    except Exception as error:
        # Keep one failed row. No exception text: DICOM libraries can include
        # identifying paths or tags in an error message.
        row["error_code"] = "dicom_or_inference_error_" + type(error).__name__
    if row["processing_status"] != "Success":
        row["processing_status"] = "Failure"
        row["quality_class"] = ""
        row["violation_type"] = ""
        row["decision_status"] = "not_classified"
    row["time_of_processing"] = round(time.perf_counter() - started, 4)
    return row


def rows_to_csv(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ALL_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")

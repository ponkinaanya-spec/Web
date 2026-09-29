from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import RESULTS_DIR
from .dicom_overlay import create_analysis_overlay
from .dicom_preview import create_dicom_preview
from .dicom_loader import load_input
from .storage import list_studies, update_job, update_study


STAGES = (
    "Чтение DICOM",
    "Проверка метаданных",
    "Запуск ML-моделей",
    "Интерпретация результата",
    "Определение нарушений",
    "Формирование отчета",
)

REGION_LABELS = {
    "spine": "Поясничный отдел позвоночника",
    "hip": "Проксимальный отдел бедра",
    "unknown": "Не определено",
}

SPINE_VIOLATION_LABELS = {
    "spine_axis": "Не выравнена ось позвоночника",
    "spine_artifact": "Присутствуют посторонние предметы",
    "spine_positioning": "Некорректная укладка",
}

_PIPELINE: Any | None = None


def _pipeline() -> Any:
    global _PIPELINE
    if _PIPELINE is None:
        from dxa_ml.model_paths import DEFAULT_MODEL_DIR, model_paths
        from dxa_ml.pipeline import DxaPipeline

        paths = model_paths(os.environ.get("DXA_MODEL_DIR", DEFAULT_MODEL_DIR))
        _PIPELINE = DxaPipeline(
            paths["anatomy"],
            paths["artifact"],
            paths["positioning"],
            device=os.environ.get("DXA_DEVICE", "cpu"),
            hip_positioning_model=paths.get("hip_positioning"),
            hip_binary_model=paths.get("hip_binary"),
        )
    return _PIPELINE


def _safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _spine_summary(result: dict[str, Any]) -> tuple[int | None, float | None, str]:
    spine = result.get("spine") or {}
    axis = spine.get("axis") or {}
    artifact = spine.get("artifact") or {}
    positioning = spine.get("positioning") or {}

    violations: list[str] = []
    scores: list[float] = []
    if axis.get("over_5_deg_provisional"):
        violations.append(SPINE_VIOLATION_LABELS["spine_axis"])
    artifact_score = _safe_float(artifact.get("score"))
    if artifact_score is not None:
        scores.append(artifact_score)
    if artifact.get("priority_flag"):
        violations.append(SPINE_VIOLATION_LABELS["spine_artifact"])
    positioning_score = _safe_float(positioning.get("score"))
    if positioning_score is not None:
        scores.append(positioning_score)
    if positioning.get("priority_flag"):
        violations.append(SPINE_VIOLATION_LABELS["spine_positioning"])

    return int(bool(violations)), max(scores) if scores else None, "; ".join(violations)


def _hip_summary(result: dict[str, Any]) -> tuple[int | None, float | None, str]:
    hip = result.get("hip") or {}
    quality = hip.get("quality") or {}
    positioning = hip.get("positioning") or {}

    violations: list[str] = []
    quality_class = quality.get("quality_class")
    if quality_class:
        violations.append("Некорректная область интереса")
    if positioning.get("priority_flag"):
        violations.append("Некорректная укладка")

    probability = _safe_float(quality.get("incorrect_probability"))
    if probability is None:
        probability = _safe_float(positioning.get("score"))
    if violations:
        quality_class = 1
    elif quality_class is not None:
        quality_class = 0
    return quality_class, probability, "; ".join(violations)


def _result_to_study_fields(result: dict[str, Any]) -> dict[str, Any]:
    selected = result.get("selected_anatomy") or "unknown"
    region = REGION_LABELS.get(selected, selected)

    if result.get("routing_status") == "manual_anatomy_confirmation_required":
        return {
            "status": "done",
            "anatomical_region": region,
            "quality_class": None,
            "quality_prob": None,
            "violation_type": "Требуется подтверждение анатомической области",
            "processing_status": "ManualReview",
        }

    if selected == "spine":
        quality_class, probability, violations = _spine_summary(result)
    elif selected == "hip":
        quality_class, probability, violations = _hip_summary(result)
    else:
        quality_class, probability, violations = None, None, "Область исследования не поддержана"

    return {
        "status": "done",
        "anatomical_region": region,
        "quality_class": quality_class,
        "quality_prob": probability,
        "violation_type": violations,
        "processing_status": "Success" if quality_class is not None else "ManualReview",
    }


def run_ml_processing(job_id: str) -> None:
    studies = list(reversed(list_studies(job_id)))
    total = len(studies)
    if total == 0:
        update_job(job_id, status="failed", message="Нет файлов для обработки")
        return

    update_job(
        job_id,
        status="processing",
        total_files=total,
        processed_files=0,
        progress=0,
        message="Запущена обработка DICOM и ML-пайплайн",
    )

    try:
        pipeline = _pipeline()
    except Exception as error:
        update_job(job_id, status="failed", progress=100, message=f"ML-пайплайн не запустился: {error}")
        for study in studies:
            metadata = study.get("metadata") or {}
            update_study(
                study["id"],
                status="failed",
                processing_status="Failure",
                violation_type="ML-пайплайн не запустился",
                metadata={**metadata, "ml_error": str(error)},
            )
        return

    for index, study in enumerate(studies, start=1):
        source_path = Path(study["source_path"])
        metadata = study.get("metadata") or {}
        started = time.perf_counter()
        update_study(study["id"], status="processing", processing_status="Processing")

        try:
            for stage_index, stage in enumerate(STAGES, start=1):
                progress = int(((index - 1) + stage_index / len(STAGES)) / total * 100)
                update_job(job_id, current_file=study["display_name"], progress=progress, message=stage)

            loaded = load_input(source_path)
            dicom_metadata = {}
            if not loaded["inventory"].empty:
                dicom_metadata = loaded["inventory"].iloc[0].dropna().to_dict()

            result = pipeline.predict_file(source_path)
            fields = _result_to_study_fields(result)
            processing_time = round(time.perf_counter() - started, 4)
            raw_result_path = RESULTS_DIR / f"{study['id']}_ml_result.json"
            raw_result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            preview_path = None
            overlay_path = None
            try:
                preview_path = create_dicom_preview(source_path, study["id"])
            except Exception as preview_error:
                metadata = {
                    **metadata,
                    "preview_error": type(preview_error).__name__,
                    "preview_error_detail": str(preview_error),
                }
            try:
                overlay_path = create_analysis_overlay(
                    source_path,
                    study["id"],
                    result,
                    fields.get("anatomical_region"),
                )
            except Exception as overlay_error:
                metadata = {
                    **metadata,
                    "overlay_error": type(overlay_error).__name__,
                    "overlay_error_detail": str(overlay_error),
                }

            update_study(
                study["id"],
                **fields,
                time_of_processing=processing_time,
                preview_path=str(preview_path) if preview_path else None,
                overlay_path=str(overlay_path) if overlay_path else None,
                metadata={
                    **metadata,
                    **dicom_metadata,
                    "ml_schema_version": result.get("schema_version"),
                    "image_key": result.get("image_key"),
                    "pixel_hash": result.get("pixel_hash"),
                    "routing_status": result.get("routing_status"),
                    "selected_anatomy_source": result.get("selected_anatomy_source"),
                    "raw_ml_result_path": str(raw_result_path),
                },
            )
        except Exception as error:
            processing_time = round(time.perf_counter() - started, 4)
            update_study(
                study["id"],
                status="failed",
                quality_class=None,
                quality_prob=None,
                violation_type="Ошибка чтения DICOM или инференса",
                processing_status="Failure",
                time_of_processing=processing_time,
                metadata={**metadata, "ml_error": type(error).__name__, "ml_error_detail": str(error)},
            )

        update_job(
            job_id,
            processed_files=index,
            progress=int(index / total * 100),
            message=f"Обработано {index} из {total}",
        )

    update_job(
        job_id,
        status="done",
        progress=100,
        current_file=None,
        message="Обработка завершена. Доступны результаты и экспорт.",
    )

from __future__ import annotations

import random
import time
from pathlib import Path

from .config import ANATOMICAL_REGIONS, RESULTS_DIR, VIOLATIONS
from .storage import add_contour, list_studies, update_job, update_study


STAGES = (
    "Чтение DICOM",
    "Проверка метаданных",
    "Определение анатомической области",
    "Классификация качества",
    "Определение нарушений",
    "Формирование отчета",
)

ERROR_COPY = {
    "not_dicom": {
        "title": "Неправильный формат файла",
        "message": "Файл не открывается или не разбирается как DICOM. В отчете ставится processing_status = Failure.",
    },
    "unsupported_dicom": {
        "title": "Неподходящее исследование",
        "message": "DICOM не похож на денситометрическое исследование позвоночника или бедра. В отчете ставится processing_status = Failure.",
    },
    "low_quality_preview": {
        "title": "Изображение нечеткое",
        "message": "Снимок недостаточно четкий для надежной оценки качества. В отчете ставится processing_status = Failure.",
    },
}


def _demo_contour(region: str) -> dict:
    if region == "Поясничный отдел позвоночника":
        points = [
            {"x": 0.50, "y": 0.18},
            {"x": 0.49, "y": 0.28},
            {"x": 0.50, "y": 0.38},
            {"x": 0.51, "y": 0.48},
            {"x": 0.50, "y": 0.58},
            {"x": 0.49, "y": 0.70},
            {"x": 0.50, "y": 0.82},
        ]
    else:
        points = [
            {"x": 0.73, "y": 0.22},
            {"x": 0.68, "y": 0.25},
            {"x": 0.63, "y": 0.31},
            {"x": 0.59, "y": 0.40},
            {"x": 0.56, "y": 0.52},
            {"x": 0.54, "y": 0.66},
            {"x": 0.53, "y": 0.82},
        ]
    return {
        "schema": "dxa-quality-contour-v1",
        "region": region,
        "coordinate_space": "normalized_preview",
        "objects": [
            {
                "id": "roi-main",
                "label": "Анатомический контур",
                "type": "polyline",
                "points": points,
            }
        ],
        "markers": [
            {"id": "marker-1", "type": "arrow", "color": "#0ec76d", "x": 0.36, "y": 0.47, "direction": "left"}
        ],
    }


def run_demo_processing(job_id: str) -> None:
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
        message="Запущена демо-обработка",
    )

    for index, study in enumerate(studies, start=1):
        metadata = study.get("metadata") or {}
        validation_status = metadata.get("validation_status", "ok")
        update_job(
            job_id,
            current_file=study["display_name"],
            message=f"{STAGES[0]}: {study['display_name']}",
        )
        update_study(study["id"], status="processing", processing_status="Processing")
        started = time.perf_counter()

        if validation_status in ERROR_COPY:
            error = ERROR_COPY[validation_status]
            processing_time = round(time.perf_counter() - started, 2)
            update_study(
                study["id"],
                status="failed",
                quality_class=None,
                quality_prob=None,
                violation_type=metadata.get("validation_message") or error["message"],
                processing_status="Failure",
                time_of_processing=processing_time,
                metadata={
                    **metadata,
                    "error_type": validation_status,
                    "error_title": error["title"],
                    "error_message": metadata.get("validation_message") or error["message"],
                },
            )
            update_job(
                job_id,
                processed_files=index,
                progress=int(index / total * 100),
                message=f"Ошибка обработки: {study['display_name']}",
            )
            continue

        for stage_index, stage in enumerate(STAGES, start=1):
            progress = int(((index - 1) + stage_index / len(STAGES)) / total * 100)
            update_job(job_id, progress=progress, message=stage)
            time.sleep(0.25)

        region = random.choice(ANATOMICAL_REGIONS)
        laterality = ""
        if region == "Проксимальный отдел бедра":
            laterality = random.choice(("Левое бедро", "Правое бедро"))
        quality_class = 1 if random.random() > 0.55 else 0
        quality_prob = round(random.uniform(0.58, 0.94), 3) if quality_class else round(random.uniform(0.02, 0.32), 3)
        violations = ""
        if quality_class:
            selected = random.sample(VIOLATIONS[region], k=random.randint(1, min(2, len(VIOLATIONS[region]))))
            violations = "; ".join(selected)

        contour = _demo_contour(region)
        contour_id = add_contour(study["id"], contour, source="demo-ai")
        processing_time = round(time.perf_counter() - started + random.uniform(1.2, 5.0), 2)
        overlay_path = RESULTS_DIR / f"{study['id']}_overlay.json"
        overlay_path.write_text(str(contour), encoding="utf-8")

        update_study(
            study["id"],
            status="done",
            anatomical_region=region,
            quality_class=quality_class,
            quality_prob=quality_prob,
            violation_type=violations,
            processing_status="Success",
            time_of_processing=processing_time,
            overlay_path=str(overlay_path),
            contour_path=contour_id,
            metadata={
                **metadata,
                "study_uid": f"demo-study-{study['id'][:8]}",
                "image_uid": f"demo-image-{study['id'][9:17]}",
                "laterality": laterality,
            },
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

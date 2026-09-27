from __future__ import annotations

import shutil
from zipfile import BadZipFile, ZipFile
from pathlib import Path, PurePosixPath

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import (
    APP_NAME,
    BASE_DIR,
    DEMO_PROCESSING_ENABLED,
    SUPPORTED_REPORT_FORMATS,
    UPLOADS_DIR,
    ensure_storage,
)
from .demo_processing import run_demo_processing
from .dicom_checks import read_basic_dicom_metadata
from .storage import (
    add_contour,
    add_study,
    create_job,
    delete_job,
    export_results,
    get_job,
    get_study,
    init_db,
    list_contours,
    list_jobs,
    list_studies,
    update_job,
    update_study,
)


ensure_storage()
init_db()

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def safe_upload_path(filename: str | None) -> tuple[Path, str, str]:
    raw = (filename or "study.dcm").replace("\\", "/")
    posix_path = PurePosixPath(raw)
    parts = [part for part in posix_path.parts if part not in {"", ".", ".."}]
    if not parts:
        parts = ["study.dcm"]
    safe_name = parts[-1]
    group_name = "/".join(parts[:-1]) if len(parts) > 1 else "Отдельные файлы"
    return Path(*parts), safe_name, group_name


def safe_zip_member_path(member_name: str) -> Path | None:
    posix_path = PurePosixPath(member_name.replace("\\", "/"))
    parts = [part for part in posix_path.parts if part not in {"", ".", ".."}]
    if not parts:
        return None
    return Path(*parts)


def render(request: Request, template: str, **context):
    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "app_name": APP_NAME,
            "demo_enabled": DEMO_PROCESSING_ENABLED,
            **context,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return render(request, "home.html")


@app.get("/processing/{job_id}", response_class=HTMLResponse)
async def processing_page(request: Request, job_id: str):
    return render(request, "processing.html", job_id=job_id)


@app.get("/results/{job_id}", response_class=HTMLResponse)
async def results_page(request: Request, job_id: str):
    return render(request, "results.html", job_id=job_id)


@app.get("/archive", response_class=HTMLResponse)
async def archive_page(request: Request):
    return render(request, "archive.html")


@app.get("/study/{study_id}", response_class=HTMLResponse)
async def study_page(request: Request, study_id: str):
    return render(request, "study.html", study_id=study_id)


@app.get("/editor/{study_id}", response_class=HTMLResponse)
async def editor_page(request: Request, study_id: str):
    return render(request, "editor.html", study_id=study_id)


@app.post("/api/uploads")
async def upload_studies(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Добавьте хотя бы один файл")

    job_id = create_job(len(files))
    job_dir = UPLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    upload_errors: list[dict[str, str]] = []
    accepted_files = 0

    for file in files:
        relative_path, safe_name, group_name = safe_upload_path(file.filename)
        suffix = relative_path.suffix.lower()
        source_type = "zip" if suffix == ".zip" else "dicom"
        destination = job_dir / "_containers" / safe_name if source_type == "zip" else job_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        metadata = {"original_name": file.filename}
        if source_type == "zip":
            try:
                with ZipFile(destination) as archive:
                    members = [info for info in archive.infolist() if not info.is_dir()]
                    if not members:
                        upload_errors.append(
                            {
                                "file": safe_name,
                                "reason": "ZIP-архив пуст или не содержит файлов исследований",
                            }
                        )
                        continue
                    for member in members:
                        member_path = safe_zip_member_path(member.filename)
                        if member_path is None:
                            continue
                        display_name = member_path.as_posix()
                        extracted_path = job_dir / "_extracted" / safe_name / member_path
                        extracted_path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, extracted_path.open("wb") as target:
                            shutil.copyfileobj(source, target)
                        entry_metadata = {
                            "original_name": f"{safe_name}/{display_name}",
                            "container": safe_name,
                            "container_path": str(destination),
                        }
                        entry_metadata.update(read_basic_dicom_metadata(extracted_path))
                        if entry_metadata.get("validation_status") in {"not_dicom", "unsupported_dicom"}:
                            upload_errors.append(
                                {
                                    "file": f"{safe_name}/{display_name}",
                                    "reason": entry_metadata.get("validation_message") or "Файл не принят",
                                }
                            )
                            continue
                        add_study(
                            job_id=job_id,
                            display_name=display_name,
                            group_name=safe_name,
                            source_type="zip_entry",
                            source_path=extracted_path,
                            metadata=entry_metadata,
                        )
                        accepted_files += 1
            except BadZipFile:
                upload_errors.append(
                    {
                        "file": safe_name,
                        "reason": "ZIP-архив поврежден или не открывается",
                    }
                )
            continue

        if source_type == "dicom":
            metadata.update(read_basic_dicom_metadata(destination))
            if metadata.get("validation_status") in {"not_dicom", "unsupported_dicom"}:
                upload_errors.append(
                    {
                        "file": safe_name,
                        "reason": metadata.get("validation_message") or "Файл не принят",
                    }
                )
                continue
        add_study(
            job_id=job_id,
            display_name=safe_name,
            group_name=group_name,
            source_type=source_type,
            source_path=destination,
            metadata=metadata,
        )
        accepted_files += 1

    if upload_errors:
        shutil.rmtree(job_dir, ignore_errors=True)
        delete_job(job_id)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Файлы не отправлены на исследование",
                "errors": upload_errors,
            },
        )

    if accepted_files == 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        delete_job(job_id)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Файлы не отправлены на исследование",
                "errors": [{"file": "Загрузка", "reason": "Нет файлов, подходящих для обработки"}],
            },
        )

    update_job(job_id, total_files=accepted_files)

    if DEMO_PROCESSING_ENABLED:
        background_tasks.add_task(run_demo_processing, job_id)

    return {"job_id": job_id, "redirect_url": f"/processing/{job_id}"}


@app.get("/api/jobs")
async def api_jobs():
    return {"jobs": list_jobs()}


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Обработка не найдена")
    return {"job": job, "studies": list_studies(job_id)}


@app.get("/api/studies/{study_id}")
async def api_study(study_id: str):
    study = get_study(study_id)
    if not study:
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    return {"study": study, "contours": list_contours(study_id)}


@app.patch("/api/studies/{study_id}")
async def api_update_study(study_id: str, payload: dict):
    if not get_study(study_id):
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    allowed = {
        "anatomical_region",
        "quality_class",
        "quality_prob",
        "violation_type",
        "processing_status",
        "time_of_processing",
        "metadata",
    }
    update = {key: value for key, value in payload.items() if key in allowed}
    update_study(study_id, **update)
    return {"ok": True, "study": get_study(study_id)}


@app.post("/api/studies/{study_id}/contours")
async def api_save_contour(study_id: str, payload: dict):
    if not get_study(study_id):
        raise HTTPException(status_code=404, detail="Исследование не найдено")
    contour_id = add_contour(study_id, payload, source="manual")
    update_study(study_id, contour_path=contour_id)
    return {"ok": True, "contour_id": contour_id, "contours": list_contours(study_id)}


@app.get("/api/archive")
async def api_archive():
    jobs = list_jobs()
    studies = [
        study
        for study in list_studies()
        if study.get("processing_status") in {"Success", "ManualReview"}
    ]
    grouped: dict[str, list[dict]] = {}
    for study in studies:
        grouped.setdefault(study["group_name"], []).append(study)
    return {"jobs": jobs, "groups": grouped}


@app.get("/api/reports/{job_id}.{output_format}")
async def download_report(job_id: str, output_format: str):
    if output_format not in SUPPORTED_REPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат отчета")
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="Обработка не найдена")
    path = export_results(job_id, output_format)
    media_type = (
        "text/csv"
        if output_format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.exception_handler(Exception)
async def fallback_error_handler(_: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Ошибка приложения зафиксирована", "error": str(exc)},
    )

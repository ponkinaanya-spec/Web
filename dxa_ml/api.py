"""Local-only HTTP adapter over the batch inference core."""

from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .batch import MAX_ARCHIVE_BYTES, MAX_DICOM_BYTES, archive_to_csv
from .model_paths import DEFAULT_MODEL_DIR, model_paths
from .pipeline import DxaPipeline
from .report import predict_row


@asynccontextmanager
async def lifespan(app: FastAPI):
    paths = model_paths(os.environ.get("DXA_MODEL_DIR", DEFAULT_MODEL_DIR))
    app.state.pipeline = DxaPipeline(
        paths["anatomy"], paths["artifact"], paths["positioning"],
        device=os.environ.get("DXA_DEVICE", "cpu"),
        hip_positioning_model=paths.get("hip_positioning"),
        hip_binary_model=paths.get("hip_binary"),
    )
    app.state.inference_lock = asyncio.Semaphore(1)
    yield
    del app.state.pipeline


app = FastAPI(title="DXA research prototype", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ready"}


@app.post("/predict/single")
async def predict_single(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".dcm", ".dicom")):
        raise HTTPException(400, "A DICOM file is required")
    async with app.state.inference_lock:
        data = await file.read(MAX_DICOM_BYTES + 1)
        await file.close()
        if len(data) > MAX_DICOM_BYTES:
            raise HTTPException(413, "DICOM exceeds the 256 MB input limit")
        # Return one row with the same schema as the batch CSV.
        return await run_in_threadpool(predict_row, app.state.pipeline, data, file.filename)


@app.post("/predict/batch")
async def predict_batch(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "A ZIP file is required")
    # The lock also covers reading, so waiting requests do not all hold
    # hundreds of megabytes in application memory.
    async with app.state.inference_lock:
        data = await file.read(MAX_ARCHIVE_BYTES + 1)
        await file.close()
        if len(data) > MAX_ARCHIVE_BYTES:
            raise HTTPException(413, "ZIP exceeds the 512 MB input limit")
        try:
            csv_data = await run_in_threadpool(archive_to_csv, app.state.pipeline, data)
        except ValueError as error:
            # Only allow our stable archive error codes into an HTTP response.
            code = str(error)
            if code not in {
                "archive_has_too_many_members", "unsafe_archive_path", "duplicate_archive_path",
                "encrypted_archive_member", "archive_symlink_not_supported",
                "archive_uncompressed_size_limit", "archive_has_no_dicom_files",
                "nested_zip_not_supported", "archive_compressed_size_limit",
                "invalid_zip_archive",
            }:
                code = "invalid_input"
            raise HTTPException(400, code) from error
    return Response(csv_data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="dxa_report.csv"',
                             "Cache-Control": "no-store"})

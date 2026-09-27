# DXA Quality

Prototype web interface for a DXA study quality-control service.

The current version contains only the user-facing FastAPI application layer:

- local upload storage for DICOM files, folders, and ZIP archives;
- SQLite metadata storage;
- archive of uploaded studies and processing runs;
- demo processing that can be removed later;
- result export to CSV and XLSX;
- contour editor placeholder with versioned contour JSON.

For hackathon delivery, XLSX is the safest report format. CSV export is also
available and is written as UTF-8 with BOM, comma-separated fields, and a dot
decimal separator.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Replace Demo Processing

The temporary logic lives in `app/demo_processing.py`. Replace calls to
`run_demo_processing` from `app/main.py` with the real DICOM and AI pipeline
when those modules are ready.

Unreadable files and clearly unsupported DICOM examples are marked as
`processing_status = Failure`, matching the organizer clarification.

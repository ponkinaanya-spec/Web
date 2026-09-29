# DXA Quality Integrated

Integrated prototype web interface for a DXA study quality-control service.

This assembled copy combines:

- local upload storage for DICOM files, folders, and ZIP archives;
- DICOM loading and metadata inventory from `app/dicom_loader.py`;
- the copied ML inference package in `dxa_ml/`;
- copied model checkpoints in `models/`;
- SQLite metadata storage;
- archive of uploaded studies and processing runs;
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
# Install torch and torchvision for your CPU/GPU platform.
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Linux App Launch

The integrated app is ready for Linux/UNIX container delivery. The image
includes the web interface, DICOM loader, ML package, and model checkpoints.
Uploaded DICOM and generated reports are kept in a Docker volume mounted at
`/app/storage`.

For a graphical Linux desktop, open the app with:

```sh
chmod +x launch_app.sh stop_app.sh scripts/run_container.sh
./launch_app.sh
```

You can also double-click `DXA Quality.desktop` in a Linux file manager after
marking it as trusted/executable. The launcher starts the Docker container and
opens the local web interface in an application-style browser window.

To stop the app:

```sh
./stop_app.sh
```

Container-only launch for servers or tests:

```sh
docker compose up --build
```

Open `http://127.0.0.1:8000`. The health endpoint is
`http://127.0.0.1:8000/health`.

The original repositories are copied under `../source_repos` for reference.
This integrated app is the editable assembly layer; those source repositories
are not modified by the integration.

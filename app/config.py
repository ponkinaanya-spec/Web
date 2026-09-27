from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "uploads"
RESULTS_DIR = STORAGE_DIR / "results"
ARCHIVE_DIR = STORAGE_DIR / "archive"
DB_PATH = STORAGE_DIR / "dxa_quality.sqlite3"

APP_NAME = "Оценка качества денситометрии"
DEMO_PROCESSING_ENABLED = True

SUPPORTED_REPORT_FORMATS = ("csv", "xlsx")
MAX_PROCESSING_SECONDS_PER_STUDY = 180

PIXEL_SPACING_MM = {
    "x": 0.6,
    "y": 1.05,
}

ANATOMICAL_REGIONS = (
    "Поясничный отдел позвоночника",
    "Проксимальный отдел бедра",
)

VIOLATIONS = {
    "Поясничный отдел позвоночника": (
        "Некорректная укладка",
        "Не выравнена ось позвоночника",
        "Присутствуют посторонние предметы",
    ),
    "Проксимальный отдел бедра": (
        "Некорректная укладка",
        "Некорректная область интереса",
    ),
}


def ensure_storage() -> None:
    for path in (STORAGE_DIR, UPLOADS_DIR, RESULTS_DIR, ARCHIVE_DIR):
        path.mkdir(parents=True, exist_ok=True)

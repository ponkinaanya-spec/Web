"""
Модуль загрузки и пакетной обработки DICOM.

Поддерживает:
- один DICOM-файл (.dcm);
- ZIP-архив с DICOM-файлами (.zip).

Результат загрузки имеет единый формат независимо от типа входного файла.
"""

import logging
import os
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import pydicom
from pydicom.misc import is_dicom

# Тестовые DICOM могут содержать некорректные UID.
# Они не мешают чтению изображения, поэтому предупреждения
# pydicom не выводятся.
logging.getLogger("pydicom").setLevel(logging.ERROR)

# 1 Ищем дикомы

def find_dicom_files(folder):

    folder = Path(folder)

    dicom_files = []

    for root, _, files in os.walk(folder):

        for filename in files:

            path = Path(root) / filename

            try:

                if is_dicom(str(path)):
                    dicom_files.append(path)

            except Exception:

                # Если файл невозможно проверить,
                # просто переходим к следующему.
                pass

    return sorted(dicom_files)


# 2 Получаем диком-тэги

def get_tag(ds, name, default=None):
    try:

        value = getattr(ds, name, default)

        if value is None:
            return default

        return str(value)

    except Exception:

        return default


# 3 Извлекаем метаданные

def read_metadata(path):

    path = Path(path)

    ds = pydicom.dcmread(
        str(path),
        stop_before_pixels=True,
        force=True
    )

    return {
        "file_path": str(path),
        "file_name": path.name,

        "modality":
            get_tag(ds, "Modality"),

        "study_description":
            get_tag(ds, "StudyDescription"),

        "series_description":
            get_tag(ds, "SeriesDescription"),

        "study_uid":
            get_tag(ds, "StudyInstanceUID"),

        "image_uid":
            get_tag(ds, "SOPInstanceUID"),

        "rows":
            get_tag(ds, "Rows"),

        "columns":
            get_tag(ds, "Columns"),
    }


# 4 Чтение не зипа, а 1 файла

def read_dicom(path):
    path = Path(path)

    ds = pydicom.dcmread(
        str(path),
        force=True
    )

    try:

        image = ds.pixel_array

    except Exception as e:

        raise RuntimeError(
            f"Не удалось получить pixel_array "
            f"из файла {path.name}: {e}"
        )

    metadata = read_metadata(path)

    return {
        "metadata": metadata,
        "image": image
    }

# 5 Обрабатываем дикомы

def process_dicom_files(dicom_files):
    studies = []
    errors = []

    for path in dicom_files:

        try:

            data = read_dicom(path)

            studies.append(data)

        except Exception as e:

            errors.append({
                "file": str(path),
                "error": str(e)
            })

    inventory = pd.DataFrame(
        [
            item["metadata"]
            for item in studies
        ]
    )

    return studies, inventory, errors

# 6 Обрабатываем зип

def process_zip(zip_path):
    
    zip_path = Path(zip_path)

    if not zip_path.exists():

        raise FileNotFoundError(
            f"Файл не найден: {zip_path}"
        )

    try:

        with tempfile.TemporaryDirectory() as temp_dir:

            extract_dir = Path(temp_dir)

            with zipfile.ZipFile(
                zip_path,
                "r"
            ) as archive:

                archive.extractall(extract_dir)

            dicom_files = find_dicom_files(
                extract_dir
            )

            if not dicom_files:

                raise ValueError(
                    "В ZIP-архиве не найдено DICOM-файлов."
                )

            return process_dicom_files(
                dicom_files
            )

    except zipfile.BadZipFile:

        raise ValueError(
            "Файл не является корректным ZIP-архивом."
        )

# 7 Обрабатываем 1 диком

def process_single_dicom(dicom_path):

    dicom_path = Path(dicom_path)

    if not is_dicom(str(dicom_path)):

        raise ValueError(
            "Загруженный файл не распознан как DICOM."
        )

    return process_dicom_files(
        [dicom_path]
    )

# 8 Точка входа

def load_input(file_path):
    """
    Единая точка входа для загрузки исследования.

    Поддерживает:
    - один DICOM-файл;
    - ZIP-архив с DICOM-файлами.

    Parameters
    ----------
    file_path : str | Path
        Путь к входному файлу.

    Returns
    -------
    dict
        {
            "studies": [...],
            "inventory": DataFrame,
            "errors": [...],
            "count": int
        }
    """

    file_path = Path(file_path)

    if not file_path.exists():

        raise FileNotFoundError(
            f"Файл не найден: {file_path}"
        )

    if file_path.suffix.lower() == ".zip":

        studies, inventory, errors = process_zip(
            file_path
        )

    else:

        studies, inventory, errors = process_single_dicom(
            file_path
        )

    return {
        "studies": studies,
        "inventory": inventory,
        "errors": errors,
        "count": len(studies)
    }

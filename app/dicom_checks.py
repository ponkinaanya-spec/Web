from __future__ import annotations

from pathlib import Path
from typing import Any


TECHNICAL_TAGS = {
    (0x0008, 0x0016): "sop_class_uid",
    (0x0008, 0x0060): "modality",
    (0x0008, 0x0070): "manufacturer",
    (0x0008, 0x1030): "study_description",
    (0x0008, 0x103E): "series_description",
    (0x0018, 0x0015): "body_part_examined",
    (0x0028, 0x0010): "rows",
    (0x0028, 0x0011): "columns",
    (0x0028, 0x0030): "pixel_spacing",
}

LONG_VR = {b"OB", b"OD", b"OF", b"OL", b"OW", b"SQ", b"UC", b"UR", b"UT", b"UN"}
SUPPORTED_HINTS = ("DXA", "DEXA", "DENS", "BONE", "LUMBAR", "SPINE", "HIP", "FEMUR", "ПОЯС", "БЕД")
UNSUPPORTED_HINTS = ("CHEST", "THORAX", "LUNG", "XR CHEST", "ГРУД")


def _decode_value(raw: bytes) -> str:
    return raw.decode("latin-1", errors="ignore").strip("\x00 ")


def read_basic_dicom_metadata(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    metadata: dict[str, Any] = {
        "dicom_magic": data[128:132] == b"DICM",
        "validation_status": "ok",
        "validation_message": "",
    }
    if not metadata["dicom_magic"]:
        metadata["validation_status"] = "not_dicom"
        metadata["validation_message"] = "Файл не открывается или не разбирается как DICOM"
        return metadata

    position = 132
    limit = min(len(data), 262_144)
    while position + 8 <= limit and len(TECHNICAL_TAGS) > len([key for key in metadata if key in TECHNICAL_TAGS.values()]):
        group = int.from_bytes(data[position : position + 2], "little")
        element = int.from_bytes(data[position + 2 : position + 4], "little")
        vr = data[position + 4 : position + 6]
        if vr in LONG_VR:
            length = int.from_bytes(data[position + 8 : position + 12], "little")
            value_start = position + 12
        else:
            length = int.from_bytes(data[position + 6 : position + 8], "little")
            value_start = position + 8

        if length == 0xFFFFFFFF or length < 0 or length > len(data) - value_start:
            position += 1
            continue

        tag = (group, element)
        if tag in TECHNICAL_TAGS:
            key = TECHNICAL_TAGS[tag]
            raw = data[value_start : value_start + min(length, 256)]
            if tag in {(0x0028, 0x0010), (0x0028, 0x0011)} and length in (2, 4):
                metadata[key] = int.from_bytes(data[value_start : value_start + length], "little")
            else:
                metadata[key] = _decode_value(raw)
        position = value_start + length

    text = " ".join(
        str(metadata.get(key, ""))
        for key in ("modality", "study_description", "series_description", "body_part_examined")
    ).upper()
    if any(hint in text for hint in UNSUPPORTED_HINTS):
        metadata["validation_status"] = "unsupported_dicom"
        metadata["validation_message"] = "DICOM не похож на денситометрическое исследование"
    elif text and not any(hint in text for hint in SUPPORTED_HINTS):
        metadata["validation_status"] = "unknown_dicom"
        metadata["validation_message"] = "Тип DICOM не удалось уверенно определить на этапе загрузки"

    return metadata

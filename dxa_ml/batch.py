"""Bounded ZIP processing: one report row for every DICOM member."""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

from .report import predict_row, rows_to_csv

MAX_ARCHIVE_BYTES = 512_000_000
MAX_DICOM_BYTES = 256_000_000
MAX_TOTAL_UNCOMPRESSED = 2_000_000_000
MAX_MEMBERS = 5000


def _validated_members(archive: ZipFile):
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ValueError("archive_has_too_many_members")
    total_size = 0
    seen = set()
    dicoms = []
    for member in members:
        name = member.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if name.startswith("/") or ".." in path.parts or not path.parts or ":" in path.parts[0]:
            raise ValueError("unsafe_archive_path")
        if name.casefold() in seen:
            raise ValueError("duplicate_archive_path")
        seen.add(name.casefold())
        if member.flag_bits & 1:
            raise ValueError("encrypted_archive_member")
        # Unix symlink members are not needed and can conceal unexpected data.
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("archive_symlink_not_supported")
        if member.is_dir():
            continue
        total_size += member.file_size
        if total_size > MAX_TOTAL_UNCOMPRESSED or member.file_size > MAX_DICOM_BYTES:
            raise ValueError("archive_uncompressed_size_limit")
        if path.suffix.lower() in (".dcm", ".dicom"):
            dicoms.append((member, name))
        elif path.suffix.lower() == ".zip":
            raise ValueError("nested_zip_not_supported")
    if not dicoms:
        raise ValueError("archive_has_no_dicom_files")
    return dicoms


def predict_archive_bytes(pipeline, archive_bytes: bytes) -> list[dict]:
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("archive_compressed_size_limit")
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            members = _validated_members(archive)
            rows = []
            for member, name in members:
                try:
                    with archive.open(member) as source:
                        data = source.read(MAX_DICOM_BYTES + 1)
                    if len(data) > MAX_DICOM_BYTES:
                        raise ValueError("dicom_size_limit")
                    rows.append(predict_row(pipeline, data, name))
                except Exception as error:
                    # A broken image still gets a row; no raw exception text or PHI.
                    row = predict_row(pipeline, b"", name)
                    row["error_code"] = "archive_member_read_error_" + type(error).__name__
                    rows.append(row)
            return rows
    except BadZipFile as error:
        raise ValueError("invalid_zip_archive") from error


def archive_to_csv(pipeline, archive_bytes: bytes) -> bytes:
    return rows_to_csv(predict_archive_bytes(pipeline, archive_bytes))

"""Bounded ZIP extraction; no traversal, links, archive nesting or overwrite."""
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile


def safe_extract(source, destination, max_bytes=2_000_000_000, max_files=5000):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > max_files or sum(m.file_size for m in members) > max_bytes:
            raise ValueError("archive_limit_exceeded")
        targets = []
        seen = set()
        for member in members:
            rel = PurePosixPath(member.filename.replace("\\", "/"))
            if rel.is_absolute() or ".." in rel.parts or any(":" in p for p in rel.parts):
                raise ValueError("unsafe_archive_path")
            if stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1:
                raise ValueError("links_or_encryption_not_supported")
            target = (root / Path(*rel.parts)).resolve()
            if not target.is_relative_to(root) or target == root:
                raise ValueError("unsafe_archive_path")
            key = str(target).casefold()
            if key in seen or target.exists():
                raise ValueError("duplicate_archive_path")
            seen.add(key)
            if member.file_size > 256_000_000:
                raise ValueError("archive_member_too_large")
            targets.append((member, target))
        # Validate ALL paths before creating any files.
        for member, target in targets:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024*1024)
    return root

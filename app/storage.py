"""Upload handling. Streams to disk -- a 500 MB meeting must never be buffered in RAM."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from . import config

_SAFE = re.compile(r"[^\w؀-ۿ.\- ]+", re.UNICODE)
CHUNK = 1024 * 1024


class UploadError(ValueError):
    pass


def sanitize_name(name: str) -> str:
    name = Path(name or "audio").name
    name = _SAFE.sub("_", name).strip() or "audio"
    return name[:200]


def check_extension(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    if ext not in config.ALLOWED_EXTENSIONS:
        raise UploadError(
            f"پسوند «{ext or '—'}» پشتیبانی نمی‌شود. مجاز: {'، '.join(config.ALLOWED_EXTENSIONS)}"
        )
    return ext


async def save_upload(upload, job_id: str) -> tuple[str, int]:
    """Stream an UploadFile to AUDIO_DIR. Returns (stored_name, size_bytes)."""
    original = sanitize_name(upload.filename)
    ext = check_extension(original)
    stored_name = f"{job_id}.{ext}"
    dest = config.AUDIO_DIR / stored_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await upload.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise UploadError(
                        f"حجم فایل بیش از حد مجاز است ({config.MAX_UPLOAD_MB} مگابایت)."
                    )
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    if size == 0:
        dest.unlink(missing_ok=True)
        raise UploadError("فایل خالی است.")
    return stored_name, size


def audio_path(stored_name: str) -> Path:
    return config.AUDIO_DIR / stored_name


def new_job_id() -> str:
    return str(uuid.uuid4())


def disk_usage() -> dict:
    def total(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    return {
        "audio_bytes": total(config.AUDIO_DIR),
        "results_bytes": total(config.RESULTS_DIR),
    }

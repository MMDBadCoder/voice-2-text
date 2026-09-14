"""دستیار صوتی جلسات — FastAPI front end."""
from __future__ import annotations

import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import case

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, config, db, exporters, queue as qmod, results, storage
from .db import Job, JobStatus, SessionLocal

log = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s [api] %(name)s: %(message)s",
    )
    for warning in config.validate(role="api"):
        log.warning("%s", warning)
    db.init_db()
    requested, cores = config.cpu_budget()
    log.info(
        "api ready: %d worker(s) x %d thread(s) = %d of %d cores; backend=%s",
        config.WORKER_COUNT, config.WORKER_CPU_THREADS, requested, cores, config.ASR_BACKEND,
    )
    yield


app = FastAPI(title=config.APP_TITLE, version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))



def _content_disposition(filename: str, job_id: str, ext: str) -> str:
    """RFC 6266 header. Persian filenames are not latin-1, so the raw name can
    never go in the header: `filename` gets an ASCII fallback and the real name
    is percent-encoded into `filename*`."""
    fallback = f"transcript-{job_id[:8]}.{ext}"
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quoted}"


def _stub_mode() -> bool:
    return config.ASR_BACKEND == "stub"


def _tier_labels() -> list[dict]:
    labels = {
        "fast": ("سریع", "زمان کمتر، دقت کمتر"),
        "accurate": ("دقیق", "کیفیت بهتر برای گفت‌وگوهای فارسی"),
    }
    return [
        {"value": t, "label": labels[t][0], "hint": labels[t][1]}
        for t in config.ENABLED_TIERS if t in labels
    ]


# ----------------------------------------------------------------- pages ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": config.APP_TITLE,
            "stub_mode": _stub_mode(),
            "tiers": _tier_labels(),
            "default_tier": config.DEFAULT_TIER,
            "max_upload_mb": config.MAX_UPLOAD_MB,
            "allowed": config.ALLOWED_EXTENSIONS,
        },
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        data = job.to_dict()
    return templates.TemplateResponse(
        request,
        "job.html",
        {"title": config.APP_TITLE, "job": data, "stub_mode": _stub_mode()},
    )


# ------------------------------------------------------------------- api ---
@app.post("/api/jobs")
async def create_job(file: UploadFile, tier: str = Form(default=""), title: str = Form(default="")):
    title = title.strip()
    if len(title) > 200:
        raise HTTPException(400, "عنوان باید حداکثر ۲۰۰ نویسه باشد")
    tier = (tier or config.DEFAULT_TIER).strip()
    if tier not in config.ENABLED_TIERS:
        raise HTTPException(400, f"سطح پردازش نامعتبر است: {tier}")

    job_id = storage.new_job_id()
    try:
        stored_name, size = await storage.save_upload(file, job_id)
    except storage.UploadError as exc:
        raise HTTPException(400, str(exc)) from exc

    with SessionLocal() as session:
        job = Job(
            id=job_id,
            title=title or None,
            original_name=storage.sanitize_name(file.filename),
            stored_name=stored_name,
            size_bytes=size,
            tier=tier,
            language=config.LANGUAGE,
            status=JobStatus.QUEUED,
        )
        session.add(job)
        session.commit()

    try:
        rq_id = qmod.enqueue(job_id)
    except Exception as exc:
        # Redis down: the row would otherwise sit "queued" forever with no worker.
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"صف در دسترس نیست / queue unavailable: {exc}"
            session.commit()
        raise HTTPException(503, "صف پردازش در دسترس نیست. Redis را بررسی کنید.") from exc

    with SessionLocal() as session:
        job = session.get(Job, job_id)
        job.rq_job_id = rq_id
        session.commit()
        payload = job.to_dict()
    return JSONResponse(payload, status_code=201)


@app.get("/api/jobs")
async def list_jobs(limit: int = 50, offset: int = 0, status: str | None = None, search: str = ""):
    if status and status not in {s.value for s in JobStatus}:
        raise HTTPException(400, "وضعیت نامعتبر است")
    limit = max(1, min(limit, 200))
    with SessionLocal() as session:
        q = session.query(Job)
        if search.strip():
            term = search.strip()
            q = q.filter(db.or_(Job.title.contains(term, autoescape=True), Job.original_name.contains(term, autoescape=True)))
        if status:
            q = q.filter(Job.status == JobStatus(status))
        total = q.count()
        rows = q.order_by(Job.created_at.desc()).offset(offset).limit(limit).all()
        items = [r.to_dict() for r in rows]
    for item in items:
        if item["status"] == "queued":
            item["queue_position"] = None
    return {"total": total, "items": items}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        data = job.to_dict()
        rq_job_id = job.rq_job_id
    if data["status"] == "queued" and rq_job_id:
        data["queue_position"] = qmod.position(rq_job_id)
    return data


@app.get("/api/jobs/{job_id}/segments")
async def get_segments(job_id: str):
    result = results.load(job_id)
    if result is None:
        raise HTTPException(404, "نتیجه‌ای موجود نیست")
    return {
        "language": result.language,
        "duration": result.duration,
        "backend": result.backend,
        "is_stub": result.backend == "stub",
        "segments": [s.to_dict() for s in result.segments],
        "meta": results.load_meta(job_id),
    }


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Stop a job but keep the row, so the user can see it was canceled."""
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELED):
            raise HTTPException(409, "این جلسه در حال پردازش نیست")
        was_running = job.status == JobStatus.RUNNING
        rq_job_id = job.rq_job_id
        qmod.request_cancel(job_id)
        changed = session.query(Job).filter(
            Job.id == job_id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        ).update({
            "stage": case((Job.status == JobStatus.RUNNING, "canceling"), else_="canceled"),
            "finished_at": case((Job.status == JobStatus.RUNNING, None), else_=db.utcnow()),
            "status": JobStatus.CANCELED,
        }, synchronize_session=False)
        session.commit()
        if not changed:
            raise HTTPException(409, "این جلسه در حال پردازش نیست")
        session.refresh(job)
        payload = job.to_dict()

    # Both, always: dropping it from the queue stops a worker picking it up,
    # and the flag stops the worker that already has it.
    if rq_job_id:
        qmod.cancel(rq_job_id)
    qmod.request_cancel(job_id)
    payload["cancel_latency_note"] = (
        "در حال توقف پردازش…"
        if was_running else None
    )
    return payload


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        stored_name = job.stored_name
        rq_job_id = job.rq_job_id
        was_running = job.status == JobStatus.RUNNING
        qmod.request_cancel(job_id)
        session.delete(job)
        session.commit()

    # Order matters: flag the cancel BEFORE the row disappears, or a running
    # worker keeps decoding a job that no longer exists -- burning a core and
    # blocking the queue behind a ghost.
    qmod.request_cancel(job_id)
    if rq_job_id:
        qmod.cancel(rq_job_id)

    # Always unlink, even mid-decode: on POSIX the worker's open fd keeps the
    # inode alive until it closes. Leaving it to the worker leaks the file
    # whenever the worker has already finished or died.
    storage.audio_path(stored_name).unlink(missing_ok=True)
    results.delete(job_id)
    return {"deleted": job_id, "was_running": was_running}


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str, format: str = "txt", timestamps: bool = True):
    if format not in exporters.FORMATS:
        raise HTTPException(400, f"قالب نامعتبر. مجاز: {', '.join(exporters.FORMATS)}")

    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        if job.status != JobStatus.DONE:
            raise HTTPException(409, "پردازش هنوز کامل نشده است")
        base_name = storage.sanitize_name(job.title) if job.title else (Path(job.original_name).stem or job_id)
        meta_for_doc = {
            "فایل": job.original_name,
            "مدت": f"{(job.duration_sec or 0) / 60:.1f} دقیقه",
            "تاریخ": job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "",
        }

    result = results.load(job_id)
    if result is None:
        raise HTTPException(404, "فایل نتیجه یافت نشد")

    ext = "docx" if format == "docx" else ("txt" if format == "plain" else format)
    headers = {"Content-Disposition": _content_disposition(f"{base_name}.{ext}", job_id, ext)}

    if format == "docx":
        tmp = config.RESULTS_DIR / f"{job_id}.docx"
        ok = exporters.to_docx(result, tmp, title=base_name, meta=meta_for_doc)
        if not ok:
            raise HTTPException(501, "python-docx نصب نیست؛ از قالب دیگری استفاده کنید.")
        return FileResponse(tmp, media_type=exporters.MEDIA_TYPES["docx"], headers=headers)

    body = {
        "txt": lambda: exporters.to_txt(result, timestamps=timestamps),
        "plain": lambda: exporters.to_plain(result),
        "srt": lambda: exporters.to_srt(result),
        "vtt": lambda: exporters.to_vtt(result),
        "json": lambda: exporters.to_json(result, results.load_meta(job_id)),
    }[format]()
    return Response(body, media_type=exporters.MEDIA_TYPES[format], headers=headers)


@app.get("/api/jobs/{job_id}/audio")
async def get_audio(job_id: str):
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        path = storage.audio_path(job.stored_name)
    if not path.exists():
        raise HTTPException(404, "فایل صوتی حذف شده است")
    return FileResponse(path)


@app.post("/api/jobs/{job_id}/retry")
async def retry(job_id: str):
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "جلسه یافت نشد")
        if job.stage == "canceling":
            raise HTTPException(409, "لطفاً تا توقف کامل پردازش صبر کنید")
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELED):
            raise HTTPException(409, "فقط کارهای ناموفق یا لغوشده قابل اجرای دوباره‌اند")
        if not storage.audio_path(job.stored_name).exists():
            raise HTTPException(409, "فایل صوتی دیگر موجود نیست")
        job.status = JobStatus.QUEUED
        job.progress = 0.0
        job.stage = "queued"
        job.error = None
        job.started_at = None
        job.finished_at = None
        session.commit()
    qmod.clear_cancel(job_id)  # a stale flag would abort the retry immediately
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        job.rq_job_id = qmod.enqueue(job_id)
        session.commit()
        return job.to_dict()


@app.get("/api/health")
async def health():
    requested, cores = config.cpu_budget()
    return {
        "ok": True,
        "app": config.APP_TITLE,
        "backend": config.ASR_BACKEND,
        "tiers": config.ENABLED_TIERS,
        "compute_type": config.COMPUTE_TYPE,
        "diarization": config.DIARIZATION_ENABLED,
        "pool": {
            "worker_count": config.WORKER_COUNT,
            "cpu_threads_per_worker": config.WORKER_CPU_THREADS,
            "threads_requested": requested,
            "cores_detected": cores,
            "reserved_cores": config.RESERVED_CORES,
        },
        "queue": qmod.health(),
        "jobs": db.counts_by_status(),
        "disk": storage.disk_usage(),
    }


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

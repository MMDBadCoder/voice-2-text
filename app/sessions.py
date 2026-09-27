"""Private multi-clip session editor; only closing a session enqueues work."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, live, media, queue as qmod, results, storage
from .db import AudioClip, Job, JobStatus, LiveBlock, SessionLocal, next_block_seq, utcnow

router = APIRouter()


class NewSession(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    tier: str = Field(default="", max_length=16)


class ClipOrder(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=200)


def editable(s, request, job_id):
    job = auth.owned_job(s, request, job_id)
    if not job.is_session or job.status != JobStatus.OPEN:
        raise HTTPException(409, "این جلسه باز نیست و امکان ویرایش ندارد")
    return job


@router.post("/api/sessions", status_code=201)
def create_session(body: NewSession, request: Request):
    user = auth.require_user(request)
    title = body.title.strip()
    tier = body.tier or config.DEFAULT_TIER
    if not title:
        raise HTTPException(400, "عنوان جلسه را وارد کنید")
    if tier not in config.ENABLED_TIERS:
        raise HTTPException(400, "روش تبدیل نامعتبر است")
    with SessionLocal() as s:
        jid = storage.new_job_id()
        job = Job(id=jid, title=title, original_name=title, stored_name=f"{jid}.mp3",
                  owner_id=user["id"], is_session=1, tier=tier, status=JobStatus.OPEN,
                  stage="open", size_bytes=0, language=config.LANGUAGE)
        s.add(job)
        s.commit()
        return job.to_dict()


@router.get("/api/sessions/{job_id}/clips")
def list_clips(job_id: str, request: Request):
    with SessionLocal() as s:
        job = auth.owned_job(s, request, job_id)
        clips = s.query(AudioClip).filter_by(job_id=job.id).order_by(AudioClip.position).all()
        return {"job": {**job.to_dict(), "clip_count": len(clips)}, "items": [c.to_dict() for c in clips],
                "max_files": config.MAX_SESSION_FILES, "max_total_mb": config.MAX_SESSION_MB}


@router.post("/api/sessions/{job_id}/clips", status_code=201)
async def append_clip(job_id: str, request: Request, file: UploadFile):
    with SessionLocal() as s:
        editable(s, request, job_id)
    cid = storage.new_job_id()
    name = None
    committed = False
    try:
        name, size = await storage.save_upload(file, cid)
        duration = await run_in_threadpool(media.probe_duration, storage.audio_path(name))
        if not duration or duration <= 0:
            raise HTTPException(400, "فایل صوتی قابل خواندن نیست؛ قالب دیگری را امتحان کنید")
        with SessionLocal() as s:
            db.write_lock(s)
            job = editable(s, request, job_id)
            clips = s.query(AudioClip).filter_by(job_id=job_id).all()
            if len(clips) >= config.MAX_SESSION_FILES:
                raise HTTPException(400, "تعداد فایل‌های جلسه به حد مجاز رسیده است")
            if sum(c.size_bytes for c in clips) + size > config.MAX_SESSION_MB*1024*1024:
                raise HTTPException(400, "حجم مجموع فایل‌های جلسه بیش از حد مجاز است")
            clip = AudioClip(id=cid, job_id=job_id, original_name=storage.sanitize_name(file.filename),
                             stored_name=name, size_bytes=size, duration_sec=duration,
                             position=max([c.position for c in clips], default=0)+1)
            s.add(clip)
            s.add(LiveBlock(job_id=job_id, seq=next_block_seq(s, job_id), source="file",
                            state="pending", text="", label=clip.original_name[:200], clip_id=cid))
            job.size_bytes = (job.size_bytes or 0) + size
            job.duration_sec = (job.duration_sec or 0) + duration
            s.commit()
            committed = True
            payload = clip.to_dict()

        try:
            queued = qmod.enqueue_clip(job_id, cid)
        except Exception:
            queued = None
        if not queued:
            live.resolve_file_block(job_id, cid, "صف در دسترس نیست؛ تبدیل را دوباره امتحان کنید", 0, failed=True)
        payload["transcription_queued"] = bool(queued)
        return payload
    except storage.UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    except BaseException:
        if name and not committed:
            storage.audio_path(name).unlink(missing_ok=True)
        raise


@router.get("/api/sessions/{job_id}/clips/{clip_id}/audio")
def clip_audio(job_id: str, clip_id: str, request: Request):
    with SessionLocal() as s:
        auth.owned_job(s, request, job_id)
        clip = s.query(AudioClip).filter_by(id=clip_id, job_id=job_id).first()
        if not clip:
            raise HTTPException(404, "فایل یافت نشد")
        path = storage.audio_path(clip.stored_name)
        if not path.exists():
            raise HTTPException(404, "فایل صوتی حذف شده است")
    return FileResponse(path)


@router.delete("/api/sessions/{job_id}/clips/{clip_id}")
def remove_clip(job_id: str, clip_id: str, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        job = editable(s, request, job_id)
        clip = s.query(AudioClip).filter_by(id=clip_id, job_id=job_id).first()
        if not clip:
            raise HTTPException(404, "فایل یافت نشد")
        path = storage.audio_path(clip.stored_name)
        job.size_bytes = max(0, (job.size_bytes or 0)-clip.size_bytes)
        job.duration_sec = max(0, (job.duration_sec or 0)-(clip.duration_sec or 0))
        s.query(LiveBlock).filter_by(job_id=job_id, clip_id=clip_id).delete()
        s.delete(clip)
        s.commit()
    path.unlink(missing_ok=True)
    return {"deleted": clip_id}


@router.post("/api/sessions/{job_id}/order")
def reorder(job_id: str, body: ClipOrder, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        editable(s, request, job_id)
        clips = s.query(AudioClip).filter_by(job_id=job_id).all()
        if len(set(body.clip_ids)) != len(body.clip_ids) or set(body.clip_ids) != {c.id for c in clips}:
            raise HTTPException(400, "فهرست فایل‌ها تغییر کرده است؛ صفحه را تازه کنید")
        positions = {cid: i+1 for i,cid in enumerate(body.clip_ids)}
        for clip in clips:
            clip.position = positions[clip.id]
        blocks = s.query(LiveBlock).filter_by(job_id=job_id, source="file").order_by(LiveBlock.seq).all()
        slots = sorted(b.seq for b in blocks)
        for slot, block in zip(slots, sorted(blocks, key=lambda b: positions.get(b.clip_id, 0))):
            block.seq = slot
        s.commit()
    return {"ok": True}


@router.post("/api/sessions/{job_id}/close")
def close_session(job_id: str, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        job = editable(s, request, job_id)
        clips = s.query(AudioClip).filter_by(job_id=job_id).all()
        if not clips:
            blocks = s.query(LiveBlock).filter_by(job_id=job_id, state="done").order_by(LiveBlock.seq).all()
            if not blocks:
                raise HTTPException(400, "ابتدا صدا اضافه کنید یا صحبت کنید")
            from .asr import Segment, TranscriptionResult
            elapsed = 0.0
            segments = []
            for block in blocks:
                end = elapsed + block.duration_ms / 1000
                segments.append(Segment(start=elapsed, end=end, text=block.text))
                elapsed = end
            results.save(job_id, TranscriptionResult(segments=segments, duration=elapsed,
                         language=config.LANGUAGE, backend="live"), {"job_id": job_id, "original_name": job.title})
            job.status, job.stage, job.progress = JobStatus.DONE, "done", 1
            job.finished_at, job.audio_deleted = utcnow(), 1
            job.duration_sec, job.num_segments = elapsed, len(segments)
            job.text_chars = sum(len(b.text or "") for b in blocks)
            s.commit()
            return job.to_dict()
        if any(not storage.audio_path(c.stored_name).exists() for c in clips):
            raise HTTPException(409, "یکی از فایل‌های صوتی موجود نیست")
        job.status = JobStatus.QUEUED
        job.stage = "queued"
        job.progress = 0
        job.error = None
        job.audio_deleted = 0
        s.commit()
    try:
        rq_id = qmod.enqueue(job_id)
    except Exception:
        from .tasks import _update
        _update(job_id, status=JobStatus.FAILED, stage="failed", error="صف پردازش در دسترس نیست")
        raise HTTPException(503, "صف در دسترس نیست؛ دوباره تلاش کنید") from None
    with SessionLocal() as s:
        job = auth.owned_job(s, request, job_id)
        job.rq_job_id = rq_id
        s.commit()
        return job.to_dict()


@router.post("/api/sessions/{job_id}/reopen")
def reopen(job_id: str, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        job = auth.owned_job(s, request, job_id)
        if not job.is_session or job.status not in (JobStatus.FAILED, JobStatus.CANCELED) or job.stage == "canceling":
            raise HTTPException(409, "ابتدا پردازش را متوقف کنید و تا توقف کامل صبر کنید")
        job.status = JobStatus.OPEN
        job.stage = "open"
        job.error = None
        job.progress = 0
        job.started_at = None
        job.finished_at = None
        qmod.clear_cancel(job_id)
        results.delete(job_id)
        storage.audio_path(job.stored_name).unlink(missing_ok=True)
        s.commit()
        return job.to_dict()


@router.post("/api/sessions/{job_id}/clips/{clip_id}/retry")
def retry_clip(job_id: str, clip_id: str, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        editable(s, request, job_id)
        clip = s.query(AudioClip).filter_by(id=clip_id, job_id=job_id).first()
        block = s.query(LiveBlock).filter_by(job_id=job_id, clip_id=clip_id).first()
        if not clip or not block:
            raise HTTPException(404, "فایل یافت نشد")
        if block.state != "failed":
            raise HTTPException(409, "فایل در حال تبدیل است یا آماده شده است")
        block.state, block.text = "pending", ""
        s.commit()
    try:
        queued = qmod.enqueue_clip(job_id, clip_id)
    except Exception:
        queued = None
    if not queued:
        live.resolve_file_block(job_id, clip_id, "صف در دسترس نیست", 0, failed=True)
        raise HTTPException(503, "صف در دسترس نیست")
    return {"queued": True}

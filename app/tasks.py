"""The background job. Runs inside an rq worker, never in the web process."""
from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path

from . import asr, config, db, diarize, media, queue as qmod, results
from .db import Job, JobStatus, SessionLocal, utcnow

log = logging.getLogger(__name__)

# Progress is written to SQLite, which is cheap but not free -- throttle it so a
# three-hour meeting does not issue thousands of UPDATEs.
_PROGRESS_MIN_INTERVAL = 1.5

# How often the cancel check is allowed to touch the database.
_CANCEL_DB_CHECK_INTERVAL = 5.0


def _update(job_id: str, **fields) -> None:
    with SessionLocal() as session:
        # Conditional SQL prevents late native-code callbacks reviving canceled jobs.
        session.query(Job).filter(Job.id == job_id, Job.status != JobStatus.CANCELED).update(fields)
        session.commit()


def _purge_audio(job_id: str) -> int:
    """Remove the upload for a job whose row is already gone.

    Stored names are always `{job_id}.{ext}`, so a glob finds it without the
    row. Unlinking a file a decoder still has open is safe on POSIX -- the
    inode survives until the fd closes.
    """
    removed = 0
    for path in config.AUDIO_DIR.glob(f"{job_id}.*"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def _cleanup_canceled(job_id: str, audio_path: str, fully_stopped: bool = True) -> None:
    """Drop partial output and stop charging the queue for a job nobody wants."""
    results.delete(job_id)
    if fully_stopped:
        qmod.clear_cancel(job_id)
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            # Row already deleted by the API -- take the audio with it.
            _purge_audio(job_id)
            return
        job.status = JobStatus.CANCELED
        job.stage = "canceled" if fully_stopped else "canceling"
        job.finished_at = utcnow() if fully_stopped else None
        session.commit()


def run_transcription(job_id: str) -> dict:
    """Entry point enqueued by the API. Must be importable by the worker."""
    started = time.monotonic()
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            # Deleted while it sat in the queue, or deleted mid-run and we were
            # restarted. Not an error -- raising here would just fill rq's
            # failed registry with noise -- but the upload must not be orphaned.
            log.info("job %s no longer exists; skipping", job_id)
            _purge_audio(job_id)
            qmod.clear_cancel(job_id)
            return {"status": "canceled", "reason": "deleted"}
        if job.status == JobStatus.CANCELED or qmod.is_cancel_requested(job_id):
            log.info("job %s was canceled before it started", job_id)
            qmod.clear_cancel(job_id)
            return {"status": "canceled"}
        audio_path = str(config.AUDIO_DIR / job.stored_name)
        tier = job.tier or config.DEFAULT_TIER
        original_name = job.original_name

    _update(job_id, status=JobStatus.RUNNING, started_at=utcnow(), progress=0.0,
            stage="probing", error=None)

    try:
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"audio file missing: {audio_path}")

        duration = media.probe_duration(audio_path)
        if duration:
            _update(job_id, duration_sec=duration)

        last_write = [0.0]

        def on_progress(fraction: float, stage: str) -> None:
            now = time.monotonic()
            if now - last_write[0] < _PROGRESS_MIN_INTERVAL and fraction < 0.999:
                return
            last_write[0] = now
            _update(job_id, progress=fraction, stage=stage)

        # Checked between segments. Redis is the fast path; the DB check catches
        # a row deleted straight out from under us (the API's DELETE), and is
        # throttled so we are not querying SQLite on every segment.
        last_db_check = [time.monotonic()]

        def should_cancel() -> bool:
            if qmod.is_cancel_requested(job_id):
                return True
            now = time.monotonic()
            if now - last_db_check[0] < _CANCEL_DB_CHECK_INTERVAL:
                return False
            last_db_check[0] = now
            with SessionLocal() as session:
                job = session.get(Job, job_id)
                return job is None or job.status == JobStatus.CANCELED

        result = asr.transcribe(audio_path, tier=tier, duration_hint=duration,
                                progress=on_progress, should_cancel=should_cancel)

        if should_cancel():
            raise asr.TranscriptionCanceled()

        num_speakers = 0
        if config.DIARIZATION_ENABLED:
            _update(job_id, stage="diarizing", progress=0.995)
            try:
                turns = diarize.diarize(audio_path)
                num_speakers = diarize.assign_speakers(result.segments, turns)
            except Exception:
                # A diarization failure must not throw away a good transcript.
                log.exception("diarization failed for job %s; keeping transcript", job_id)

        if should_cancel():
            raise asr.TranscriptionCanceled()
        elapsed = time.monotonic() - started
        meta = {
            "job_id": job_id,
            "original_name": original_name,
            "tier": tier,
            "elapsed_sec": round(elapsed, 2),
            "speed_factor": round((duration or 0) / elapsed, 2) if duration and elapsed else None,
            "diarized": bool(num_speakers),
            "num_speakers": num_speakers or None,
        }
        results.save(job_id, result, meta)

        text_chars = sum(len(s.text) for s in result.segments)
        audio_deleted = 0
        if config.DELETE_AUDIO_AFTER_TRANSCRIBE:
            Path(audio_path).unlink(missing_ok=True)
            audio_deleted = 1

        _update(
            job_id,
            status=JobStatus.DONE,
            progress=1.0,
            stage="done",
            finished_at=utcnow(),
            duration_sec=duration or result.duration,
            detected_language=result.language,
            num_segments=len(result.segments),
            text_chars=text_chars,
            diarized=1 if num_speakers else 0,
            num_speakers=num_speakers or None,
            audio_deleted=audio_deleted,
        )
        qmod.clear_cancel(job_id)
        log.info("job %s done: %d segments in %.1fs", job_id, len(result.segments), elapsed)
        return {"status": "done", "segments": len(result.segments), "elapsed_sec": elapsed}

    except asr.TranscriptionCanceled as exc:
        log.info("job %s canceled: %s", job_id, exc)
        _cleanup_canceled(job_id, audio_path, fully_stopped=os.getenv("VOICE_SUPERVISED_JOB") != job_id)
        return {"status": "canceled", "elapsed_sec": time.monotonic() - started}

    except Exception as exc:
        log.exception("job %s failed", job_id)
        _update(
            job_id,
            status=JobStatus.FAILED,
            stage="failed",
            finished_at=utcnow(),
            error=f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc(limit=5)}",
        )
        raise


def sweep_retention() -> dict:
    """Delete audio and results past RETENTION_DAYS. Safe to run repeatedly."""
    from datetime import timedelta

    if config.RETENTION_DAYS <= 0:
        return {"deleted": 0, "reason": "retention disabled"}

    cutoff = utcnow() - timedelta(days=config.RETENTION_DAYS)
    deleted = 0
    with SessionLocal() as session:
        stale = session.query(Job).filter(Job.created_at < cutoff).all()
        for job in stale:
            (config.AUDIO_DIR / job.stored_name).unlink(missing_ok=True)
            results.delete(job.id)
            session.delete(job)
            deleted += 1
        session.commit()
    log.info("retention sweep removed %d job(s) older than %d days",
             deleted, config.RETENTION_DAYS)
    return {"deleted": deleted}


def _stop_process(process) -> None:
    """Kill the owned process group, including native decoding and helper processes."""
    import os
    import signal
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_isolated(job_id: str) -> dict:
    """Supervise the entire pipeline; cancellation never waits for a segment.

    A fresh interpreter avoids forking initialized CTranslate2/OpenMP threads.
    Model load is paid per job in exchange for bounded cancellation and released RAM.
    """
    import subprocess
    import sys

    with SessionLocal() as session:
        # Claim atomically: duplicate queue deliveries cannot launch two decoders.
        claimed = session.query(Job).filter(Job.id == job_id, Job.status == JobStatus.QUEUED).update(
            {"status": JobStatus.RUNNING, "stage": "probing", "started_at": utcnow()})
        session.commit()
        if not claimed:
            return {"status": "skipped"}
    process = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.job_process", job_id],
            cwd=str(config.BASE_DIR), start_new_session=True,
        )
        while True:
            with SessionLocal() as session:
                job = session.get(Job, job_id)
                canceled = job is None or job.status == JobStatus.CANCELED
            if canceled or qmod.is_cancel_requested(job_id):
                _stop_process(process)
                _cleanup_canceled(job_id, "")
                results.result_path(job_id).with_suffix(".json.tmp").unlink(missing_ok=True)
                log.info("job %s process %s stopped and reaped", job_id, process.pid)
                return {"status": "canceled"}
            if process.poll() is not None:
                break
            if time.monotonic() - started > config.WORKER_JOB_TIMEOUT:
                raise TimeoutError("Transcription exceeded job timeout")
            time.sleep(0.2)
        if process.returncode != 0:
            raise RuntimeError(f"Transcription process exited with code {process.returncode}")
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            canceled = job is None or job.status == JobStatus.CANCELED
            status = job.status.value if job else "canceled"
        if canceled:
            _cleanup_canceled(job_id, "")
        return {"status": status}
    except BaseException as exc:
        if process is not None:
            _stop_process(process)
        _update(job_id, status=JobStatus.FAILED, stage="failed",
                finished_at=utcnow(), error=str(exc))
        raise

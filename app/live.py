"""رونویسی زندهٔ میکروفن — live microphone transcription.

The browser does the segmenting: it watches microphone energy, waits for a real
pause, and only then ships one complete utterance. That is what keeps this
cheap enough for CPU-only inference — the server never re-decodes a sliding
window, it decodes each utterance exactly once.

Live work deliberately does NOT go through the RQ queue. A queued live chunk
would sit behind a 40-minute meeting, and "live" would mean nothing. It runs in
the API process against its own warm model, bounded by a semaphore so a busy
room cannot starve the web server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
import threading
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import auth, config, db
from .db import Job, JobStatus, LiveBlock, SessionLocal, next_block_seq
from .normalize import collapse_repetitions, is_hallucinated, normalize

log = logging.getLogger(__name__)
router = APIRouter()

SAMPLE_RATE = 16000
MAX_UTTERANCE_SEC = 30
MIN_UTTERANCE_SEC = 0.35


# --------------------------------------------------------------------- engine
class LiveEngine:
    """One warm model shared by every live session in this process."""

    def __init__(self) -> None:
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()
        self._sem: asyncio.Semaphore | None = None

    def semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(max(1, config.LIVE_MAX_CONCURRENT))
        return self._sem

    def _load(self, tier: str | None = None):
        tier = tier or config.LIVE_TIER
        with self._lock:
            if tier in self._models:
                return self._models[tier]
            config.apply_thread_env(config.LIVE_CPU_THREADS)
            from faster_whisper import WhisperModel

            path = config.resolve_model_path(tier)
            log.info("live: loading %s (threads=%d)", path, config.LIVE_CPU_THREADS)
            started = time.monotonic()
            model = WhisperModel(
                path, device="cpu", compute_type=config.COMPUTE_TYPE,
                cpu_threads=config.LIVE_CPU_THREADS, num_workers=1, local_files_only=True,
            )
            log.info("live: model ready in %.1fs", time.monotonic() - started)
            self._models[tier] = model
            return model

    def warm(self, tier: str | None = None) -> None:
        if config.LIVE_ENABLED and config.ASR_BACKEND == "faster_whisper":
            self._load(tier)

    def transcribe_pcm(self, pcm: bytes, tier: str | None = None) -> str:
        """Decode one utterance of 16 kHz mono int16 PCM."""
        # Length is a property of the input, so this guard must sit ahead of the
        # backend switch: a stray click is noise whatever engine is loaded.
        if len(pcm) // 2 < SAMPLE_RATE * MIN_UTTERANCE_SEC:
            return ""
        if config.ASR_BACKEND == "stub":
            return "متن آزمایشی زنده."

        import numpy as np

        audio = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        model = self._load(tier)
        segments, _info = model.transcribe(
            audio,
            language=config.LANGUAGE or None,
            task="transcribe",
            beam_size=config.LIVE_BEAM_SIZE,   # smaller than batch: latency beats the last 1% WER
            vad_filter=False,                  # the browser already trimmed the silence
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4],
        )
        parts = []
        for seg in segments:
            text = collapse_repetitions((seg.text or "").strip())
            if text and not is_hallucinated(text):
                parts.append(text)
        return normalize(" ".join(parts))


engine = LiveEngine()


# ---------------------------------------------------------------- persistence
def _append_block(job_id: str, text: str, *, source: str, duration_ms: int,
                  state: str = "done", label: str | None = None,
                  clip_id: str | None = None) -> dict:
    with SessionLocal() as s:
        db.write_lock(s)
        job = s.get(Job, job_id)
        if job is None or job.status != JobStatus.OPEN:
            return None
        block = LiveBlock(job_id=job_id, seq=next_block_seq(s, job_id), source=source,
                          state=state, text=text, duration_ms=duration_ms,
                          label=label, clip_id=clip_id)
        s.add(block)
        s.commit()
        return block.to_dict()


def append_pending_file_block(job_id: str, clip_id: str, label: str) -> dict:
    """Reserve a slot the moment a file is added, so ordering matches what the
    user saw when they added it -- not whenever the queue happens to finish."""
    return _append_block(job_id, "", source="file", duration_ms=0,
                         state="pending", label=label, clip_id=clip_id)


def resolve_file_block(job_id: str, clip_id: str, text: str, duration_ms: int,
                       failed: bool = False) -> None:
    with SessionLocal() as s:
        block = (s.query(LiveBlock)
                 .filter(LiveBlock.job_id == job_id, LiveBlock.clip_id == clip_id)
                 .first())
        if block is None:
            return
        if block.edited:
            return
        block.text = text
        block.state = "failed" if failed else "done"
        block.duration_ms = duration_ms
        s.commit()


def session_text(job_id: str) -> str:
    with SessionLocal() as s:
        blocks = (s.query(LiveBlock)
                  .filter(LiveBlock.job_id == job_id, LiveBlock.state == "done")
                  .order_by(LiveBlock.seq).all())
    return "\n\n".join(b.text.strip() for b in blocks if (b.text or "").strip())


def has_blocks(job_id: str) -> bool:
    with SessionLocal() as s:
        return s.query(LiveBlock.id).filter(LiveBlock.job_id == job_id).first() is not None


# ----------------------------------------------------------------------- REST
class BlockEdit(BaseModel):
    text: str = Field(default="", max_length=20000)


@router.get("/api/sessions/{job_id}/transcript")
def get_transcript(job_id: str, request: Request):
    with SessionLocal() as s:
        job = auth.owned_job(s, request, job_id)
        blocks = (s.query(LiveBlock).filter(LiveBlock.job_id == job.id)
                  .order_by(LiveBlock.seq).all())
        return {"items": [b.to_dict() for b in blocks],
                "live_enabled": config.LIVE_ENABLED,
                "editable": job.status == JobStatus.OPEN,
                "chars": sum(len(b.text or "") for b in blocks)}


@router.patch("/api/sessions/{job_id}/transcript/{block_id}")
def edit_block(job_id: str, block_id: str, body: BlockEdit, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        job = auth.owned_job(s, request, job_id)
        if job.status != JobStatus.OPEN:
            raise HTTPException(409, "جلسه بسته شده است")
        block = s.query(LiveBlock).filter(LiveBlock.id == block_id,
                                          LiveBlock.job_id == job_id).first()
        if block is None:
            raise HTTPException(404, "این بخش یافت نشد")
        if block.state != "done":
            raise HTTPException(409, "ابتدا منتظر پایان تبدیل بمانید")
        block.text = body.text.strip()
        block.edited = 1
        s.commit()
        return block.to_dict()


@router.delete("/api/sessions/{job_id}/transcript/{block_id}")
def delete_block(job_id: str, block_id: str, request: Request):
    with SessionLocal() as s:
        db.write_lock(s)
        job = auth.owned_job(s, request, job_id)
        if job.status != JobStatus.OPEN:
            raise HTTPException(409, "جلسه بسته شده است")
        block = s.query(LiveBlock).filter(LiveBlock.id == block_id,
                                          LiveBlock.job_id == job_id).first()
        if block is None:
            raise HTTPException(404, "این بخش یافت نشد")
        s.delete(block)
        s.commit()
    return {"deleted": block_id}


# ------------------------------------------------------------------ websocket
async def _reject(ws: WebSocket, code: int, reason: str) -> None:
    await ws.accept()
    await ws.send_text(json.dumps({"type": "error", "fatal": True, "message": reason}))
    await ws.close(code=code)


@router.websocket("/ws/sessions/{job_id}/live")
async def live_socket(ws: WebSocket, job_id: str):
    if not config.LIVE_ENABLED:
        return await _reject(ws, 1011, "رونویسی زنده در این سرور فعال نیست")

    origin = ws.headers.get("origin")
    expected = config.PUBLIC_BASE_URL or str(ws.url.replace(scheme="https" if ws.url.scheme == "wss" else "http", path="", query=""))
    if origin and origin.rstrip("/") != expected.rstrip("/"):
        return await _reject(ws, 1008, "مبدأ اتصال معتبر نیست")
    user, _ = auth.lookup_session(ws.cookies.get(auth.COOKIE))
    if not user or user.get("status") != "approved":
        return await _reject(ws, 1008, "ابتدا وارد حساب خود شوید")

    with SessionLocal() as s:
        job = s.query(Job).filter(Job.id == job_id, Job.owner_id == user["id"]).first()
        if job is None:
            return await _reject(ws, 1008, "جلسه یافت نشد")
        if not job.is_session or job.status != JobStatus.OPEN:
            return await _reject(ws, 1008, "این جلسه باز نیست")
        # Honour the tier chosen for this session: live text and the final
        # transcript should not come from two different models.
        tier = job.tier if job.tier in config.ENABLED_TIERS else config.LIVE_TIER

    await ws.accept()
    await ws.send_text(json.dumps({"type": "ready", "sample_rate": SAMPLE_RATE, "tier": tier}))

    loop = asyncio.get_running_loop()
    inflight = 0
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if (payload := message.get("bytes")) is not None:
                # frame: uint32 client sequence, then int16 PCM
                if len(payload) < 4:
                    continue
                (client_seq,) = struct.unpack_from("<I", payload, 0)
                current, _ = await asyncio.to_thread(auth.lookup_session, ws.cookies.get(auth.COOKIE))
                with SessionLocal() as s:
                    active = s.get(Job, job_id)
                    allowed = current and current.get("status") == "approved" and active and active.owner_id == current["id"] and active.status == JobStatus.OPEN
                if not allowed:
                    await ws.send_json({"type": "error", "fatal": True, "message": "جلسه یا دسترسی شما بسته شده است"})
                    break
                pcm = payload[4:]
                cap = SAMPLE_RATE * 2 * MAX_UTTERANCE_SEC
                if len(pcm) > cap or len(pcm) % 2:
                    await ws.send_json({"type": "error", "client_seq": client_seq, "message": "قالب یا طول صوت نامعتبر است"})
                    continue
                duration_ms = int(len(pcm) / 2 / SAMPLE_RATE * 1000)

                if inflight >= config.LIVE_MAX_QUEUED:
                    await ws.send_text(json.dumps(
                        {"type": "dropped", "client_seq": client_seq,
                         "message": "سرور شلوغ است؛ این بخش نادیده گرفته شد"}))
                    continue

                inflight += 1
                await ws.send_text(json.dumps(
                    {"type": "working", "client_seq": client_seq, "duration_ms": duration_ms}))
                started = time.monotonic()
                try:
                    async with engine.semaphore():
                        text = await loop.run_in_executor(
                            None, engine.transcribe_pcm, pcm, tier)
                except Exception:
                    log.exception("live: decode failed")
                    await ws.send_text(json.dumps(
                        {"type": "error", "client_seq": client_seq,
                         "message": "تبدیل این بخش انجام نشد"}))
                    continue
                finally:
                    inflight -= 1

                elapsed_ms = int((time.monotonic() - started) * 1000)
                if not text:
                    await ws.send_text(json.dumps(
                        {"type": "empty", "client_seq": client_seq, "latency_ms": elapsed_ms}))
                    continue

                current, _ = await asyncio.to_thread(auth.lookup_session, ws.cookies.get(auth.COOKIE))
                if not current or current.get("status") != "approved":
                    await ws.send_json({"type": "error", "fatal": True, "message": "دسترسی شما پایان یافته است"})
                    break
                block = await loop.run_in_executor(
                    None, lambda: _append_block(job_id, text, source="mic",
                                                duration_ms=duration_ms))
                if block is None:
                    await ws.send_json({"type": "error", "client_seq": client_seq, "message": "جلسه پیش از ذخیره بسته شد"})
                    continue
                await ws.send_text(json.dumps(
                    {"type": "block", "client_seq": client_seq,
                     "latency_ms": elapsed_ms, "block": block}))

            elif (text_msg := message.get("text")) is not None:
                try:
                    data = json.loads(text_msg)
                except ValueError:
                    continue
                if data.get("type") == "finish":
                    await ws.send_json({"type": "finished"})
                    break
                if data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("live: socket failed for job %s", job_id)
    finally:
        try:
            await ws.close()
        except Exception:
            pass

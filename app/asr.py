"""ASR backends.

`faster_whisper` is the real one (CTranslate2, CPU, int8). `stub` produces
plausible fake output so the queue, UI, exports and retention can be verified
on a box where the model has not been copied over yet.

The model is loaded once per *process* and cached. This is why the workers run
as rq SimpleWorker rather than the default forking Worker: a fork per job would
reload ~800 MB from disk every single time.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from . import config

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class TranscriptionCanceled(Exception):
    """Raised when a cancel was requested mid-decode."""


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "no_speech_prob": round(self.no_speech_prob, 4),
            "avg_logprob": round(self.avg_logprob, 4),
        }
        if self.speaker:
            d["speaker"] = self.speaker
        return d


@dataclass
class TranscriptionResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = "fa"
    language_probability: float = 1.0
    duration: float = 0.0
    backend: str = ""
    model: str = ""


_model_cache: dict[str, object] = {}
_cache_lock = threading.Lock()


def _load_faster_whisper(model_path: str):
    """Load and cache a CTranslate2 Whisper model for this process."""
    with _cache_lock:
        if model_path in _model_cache:
            return _model_cache[model_path]

        config.apply_thread_env()  # must precede the ctranslate2 import
        from faster_whisper import WhisperModel

        log.info(
            "loading model %s (compute_type=%s, cpu_threads=%d)",
            model_path, config.COMPUTE_TYPE, config.WORKER_CPU_THREADS,
        )
        started = time.monotonic()
        model = WhisperModel(
            model_path,
            device="cpu",
            compute_type=config.COMPUTE_TYPE,
            cpu_threads=config.WORKER_CPU_THREADS,
            num_workers=1,
            local_files_only=True,  # hard guarantee: never reaches for the network
        )
        log.info("model loaded in %.1fs", time.monotonic() - started)
        _model_cache[model_path] = model
        return model


def warm_up(tier: str | None = None) -> None:
    """Pay the model-load cost at worker startup, not on the first user's job.

    Only the default tier by default: every loaded model costs its full size in
    RSS, per worker process, and a tier nobody requests is pure waste. Other
    tiers load lazily on their first job. WARM_UP_ALL_TIERS=true overrides.
    """
    if config.ASR_BACKEND != "faster_whisper":
        return
    if tier:
        tiers = [tier]
    elif config.WARM_UP_ALL_TIERS:
        tiers = config.ENABLED_TIERS
    else:
        tiers = [config.DEFAULT_TIER]
    for t in tiers:
        _load_faster_whisper(config.resolve_model_path(t))


def _transcribe_faster_whisper(
    audio_path: str, tier: str, duration_hint: float | None, progress: ProgressFn | None,
    should_cancel: CancelFn | None = None,
) -> TranscriptionResult:
    from . import normalize as norm

    model_path = config.resolve_model_path(tier)
    model = _load_faster_whisper(model_path)

    if progress:
        progress(0.0, "transcribing")

    vad_params = None
    if config.VAD_ENABLED:
        vad_params = {
            "min_silence_duration_ms": config.VAD_MIN_SILENCE_MS,
            "speech_pad_ms": config.VAD_SPEECH_PAD_MS,
        }

    segment_iter, info = model.transcribe(
        audio_path,
        language=config.LANGUAGE or None,   # explicit "fa"; autodetect confuses fa/ar/ur
        task="transcribe",
        beam_size=config.BEAM_SIZE,
        vad_filter=config.VAD_ENABLED,
        vad_parameters=vad_params,
        condition_on_previous_text=False,   # without this Whisper loops on silence
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        word_timestamps=False,
    )

    total = duration_hint or getattr(info, "duration", 0.0) or 0.0
    segments: list[Segment] = []

    # faster-whisper returns a generator: decoding happens as we iterate, which
    # is what lets us report real progress -- and gives us the only safe place to
    # abort, since we cannot interrupt a native CTranslate2 call already in flight.
    for raw in segment_iter:
        if should_cancel and should_cancel():
            raise TranscriptionCanceled(f"canceled after {len(segments)} segment(s)")
        # Normalize HERE, not only in the exporters: the stored segments are what
        # the UI renders, and screen text that disagrees with the downloaded file
        # is its own bug. normalize() is idempotent, so exporters re-running it
        # on older results is harmless.
        text = norm.normalize(norm.collapse_repetitions((raw.text or "").strip()))
        if text and not norm.is_hallucinated(text):
            segments.append(
                Segment(
                    start=float(raw.start),
                    end=float(raw.end),
                    text=text,
                    no_speech_prob=float(getattr(raw, "no_speech_prob", 0.0) or 0.0),
                    avg_logprob=float(getattr(raw, "avg_logprob", 0.0) or 0.0),
                )
            )
        if progress and total:
            progress(min(float(raw.end) / total, 0.999), "transcribing")

    return TranscriptionResult(
        segments=segments,
        language=getattr(info, "language", config.LANGUAGE) or config.LANGUAGE,
        language_probability=float(getattr(info, "language_probability", 1.0) or 1.0),
        duration=float(getattr(info, "duration", total) or total),
        backend="faster_whisper",
        model=model_path,
    )


_STUB_SENTENCES = [
    "سلام، جلسه را شروع می‌کنیم.",
    "دستور جلسهٔ امروز بررسی وضعیت پروژه است.",
    "گزارش هفتهٔ گذشته را آماده کرده‌ایم.",
    "لطفاً نتیجه را تا پایان هفته ارسال کنید.",
    "سؤالی در این مورد وجود دارد؟",
    "جمع‌بندی می‌کنم و جلسه را می‌بندیم.",
]


def _transcribe_stub(
    audio_path: str, tier: str, duration_hint: float | None, progress: ProgressFn | None,
    should_cancel: CancelFn | None = None,
) -> TranscriptionResult:
    total = duration_hint or 60.0
    step = 10.0
    segments: list[Segment] = []
    t = 0.0
    i = 0
    while t < total:
        if should_cancel and should_cancel():
            raise TranscriptionCanceled(f"canceled after {len(segments)} segment(s)")
        end = min(t + step, total)
        segments.append(Segment(start=t, end=end, text=_STUB_SENTENCES[i % len(_STUB_SENTENCES)]))
        if progress:
            progress(min(end / total, 0.999), "transcribing")
        time.sleep(0.05)  # make progress observable in the UI
        t = end
        i += 1
    return TranscriptionResult(
        segments=segments, language="fa", duration=total, backend="stub", model="stub"
    )


def transcribe(
    audio_path: str,
    tier: str = "fast",
    duration_hint: float | None = None,
    progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
) -> TranscriptionResult:
    if config.ASR_BACKEND == "stub":
        return _transcribe_stub(audio_path, tier, duration_hint, progress, should_cancel)
    if config.ASR_BACKEND == "faster_whisper":
        return _transcribe_faster_whisper(audio_path, tier, duration_hint, progress, should_cancel)
    raise ValueError(f"Unknown ASR_BACKEND={config.ASR_BACKEND!r} (use: faster_whisper, stub)")

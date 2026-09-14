"""پیکربندی مرکزی — همه‌چیز از طریق متغیرهای محیطی قابل تنظیم است.

Single source of truth for the whole app. Both the API and the workers read
this module, and docker-compose reads the same .env file, so there is exactly
one place to change how many workers run and how much of the machine they get.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _str(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(key: str, default: str) -> list[str]:
    return [p.strip() for p in _str(key, default).split(",") if p.strip()]


# ---------------------------------------------------------------- storage ---
DATA_DIR = Path(_str("DATA_DIR", str(BASE_DIR / "data")))
AUDIO_DIR = DATA_DIR / "audio"
RESULTS_DIR = DATA_DIR / "results"
MODELS_DIR = Path(_str("MODELS_DIR", str(BASE_DIR / "models")))
DB_URL = _str("DB_URL", f"sqlite:///{DATA_DIR / 'jobs.db'}")

# ------------------------------------------------------------------ queue ---
REDIS_URL = _str("REDIS_URL", "redis://127.0.0.1:6379/0")
QUEUE_NAME = _str("QUEUE_NAME", "transcription")

# ------------------------------------------------------------ worker pool ---
# WORKER_COUNT is consumed by docker-compose (--scale) / run-workers.sh.
# WORKER_CPU_THREADS is handed to CTranslate2 *and* exported as OMP_NUM_THREADS
# before the model is imported -- setting only one of the two is not enough,
# OpenMP will otherwise spawn one thread per core underneath CTranslate2.
WORKER_COUNT = _int("WORKER_COUNT", 2)
WORKER_CPU_THREADS = _int("WORKER_CPU_THREADS", 4)
WORKER_JOB_TIMEOUT = _int("WORKER_JOB_TIMEOUT", 14400)  # 4h
RESERVED_CORES = _int("RESERVED_CORES", 2)  # left for API + Redis + ffmpeg

# ------------------------------------------------------------- asr models ---
# "stub" needs no model at all and is how you smoke-test the plumbing before
# the multi-hundred-megabyte model has finished copying onto an offline box.
ASR_BACKEND = _str("ASR_BACKEND", "faster_whisper")  # faster_whisper | stub
MODEL_FAST = _str("MODEL_FAST", str(MODELS_DIR / "whisper-fa-turbo-int8"))
MODEL_ACCURATE = _str("MODEL_ACCURATE", str(MODELS_DIR / "whisper-fa-large-v3-int8"))
DEFAULT_TIER = _str("DEFAULT_TIER", "fast")  # fast | accurate
ENABLED_TIERS = _list("ENABLED_TIERS", "fast")
COMPUTE_TYPE = _str("COMPUTE_TYPE", "int8")
# Warm every enabled tier at startup, or just the default one? Each loaded model
# costs its full size in RSS per worker process, so warming all tiers on a
# memory-tight box is expensive for a tier nobody may ask for.
WARM_UP_ALL_TIERS = _bool("WARM_UP_ALL_TIERS", False)
LANGUAGE = _str("LANGUAGE", "fa")
BEAM_SIZE = _int("BEAM_SIZE", 5)
# Whisper's Persian punctuation is weak without a nudge.
INITIAL_PROMPT = _str(
    "INITIAL_PROMPT",
    "این یک جلسهٔ کاری است. متن با نقطه، ویرگول و علائم نگارشی فارسی نوشته می‌شود.",
)

# --------------------------------------------------------------------- vad ---
VAD_ENABLED = _bool("VAD_ENABLED", True)
VAD_MIN_SILENCE_MS = _int("VAD_MIN_SILENCE_MS", 500)
VAD_SPEECH_PAD_MS = _int("VAD_SPEECH_PAD_MS", 200)

# ------------------------------------------------------------- diarization ---
DIARIZATION_ENABLED = _bool("DIARIZATION_ENABLED", False)
DIARIZATION_SEGMENTATION_MODEL = _str(
    "DIARIZATION_SEGMENTATION_MODEL", str(MODELS_DIR / "diarization" / "segmentation.onnx")
)
DIARIZATION_EMBEDDING_MODEL = _str(
    "DIARIZATION_EMBEDDING_MODEL", str(MODELS_DIR / "diarization" / "embedding.onnx")
)
DIARIZATION_NUM_SPEAKERS = _int("DIARIZATION_NUM_SPEAKERS", 0)  # 0 = auto-detect
DIARIZATION_CLUSTER_THRESHOLD = _float("DIARIZATION_CLUSTER_THRESHOLD", 0.5)

# ---------------------------------------------------------------- uploads ---
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 500)
ALLOWED_EXTENSIONS = _list("ALLOWED_EXTENSIONS", "mp3,wav,m4a,ogg,opus,flac,mp4,mkv,webm,aac,wma")

# --------------------------------------------------------------- retention ---
DELETE_AUDIO_AFTER_TRANSCRIBE = _bool("DELETE_AUDIO_AFTER_TRANSCRIBE", False)
RETENTION_DAYS = _int("RETENTION_DAYS", 0)  # 0 = keep forever

# --------------------------------------------------------------------- web ---
APP_TITLE = _str("APP_TITLE", "دستیار صوتی جلسات")
HOST = _str("HOST", "0.0.0.0")
PORT = _int("PORT", 8000)


class ConfigError(RuntimeError):
    pass


def resolve_model_path(tier: str) -> str:
    return MODEL_ACCURATE if tier == "accurate" else MODEL_FAST


def cpu_budget() -> tuple[int, int]:
    """(threads requested by the pool, cores available)."""
    return WORKER_COUNT * WORKER_CPU_THREADS, os.cpu_count() or 1


def validate(role: str = "api") -> list[str]:
    """Fail fast on a misconfiguration; return non-fatal warnings.

    An oversubscribed pool is the classic way to make this app *slower* by
    adding workers, so it is an error rather than a warning.
    """
    warnings: list[str] = []

    requested, cores = cpu_budget()
    usable = cores - RESERVED_CORES
    if WORKER_COUNT < 1:
        raise ConfigError("WORKER_COUNT must be >= 1")
    if WORKER_CPU_THREADS < 1:
        raise ConfigError("WORKER_CPU_THREADS must be >= 1")
    if requested > usable:
        raise ConfigError(
            f"پیکربندی بیش از ظرفیت CPU است / Oversubscribed CPU: "
            f"WORKER_COUNT({WORKER_COUNT}) x WORKER_CPU_THREADS({WORKER_CPU_THREADS}) "
            f"= {requested} threads, but only {usable} of {cores} cores are usable "
            f"(RESERVED_CORES={RESERVED_CORES}). Lower the pool or RESERVED_CORES."
        )

    if DEFAULT_TIER not in ENABLED_TIERS:
        raise ConfigError(f"DEFAULT_TIER={DEFAULT_TIER!r} is not in ENABLED_TIERS={ENABLED_TIERS}")
    for tier in ENABLED_TIERS:
        if tier not in ("fast", "accurate"):
            raise ConfigError(f"Unknown tier {tier!r} in ENABLED_TIERS (use: fast, accurate)")

    if role == "worker" and ASR_BACKEND == "faster_whisper":
        for tier in ENABLED_TIERS:
            path = Path(resolve_model_path(tier))
            if not path.exists():
                raise ConfigError(
                    f"مدل یافت نشد / Model for tier {tier!r} not found at {path}. "
                    f"Run scripts/fetch-offline-deps.sh on an online machine and copy it over, "
                    f"or set ASR_BACKEND=stub to test without a model."
                )
            if not (path / "model.bin").exists():
                warnings.append(f"{path} has no model.bin -- is it really a CTranslate2 directory?")

    if role == "worker" and DIARIZATION_ENABLED:
        for label, path in (
            ("DIARIZATION_SEGMENTATION_MODEL", DIARIZATION_SEGMENTATION_MODEL),
            ("DIARIZATION_EMBEDDING_MODEL", DIARIZATION_EMBEDDING_MODEL),
        ):
            if not Path(path).exists():
                raise ConfigError(f"{label} not found at {path} (DIARIZATION_ENABLED=true)")

    if requested < usable:
        warnings.append(
            f"{usable - requested} core(s) idle: pool uses {requested} of {usable} usable cores."
        )
    if ASR_BACKEND == "stub":
        warnings.append("ASR_BACKEND=stub -- transcripts are fake placeholder text.")
    return warnings


def apply_thread_env() -> None:
    """Must run BEFORE ctranslate2/onnxruntime are imported."""
    n = str(WORKER_CPU_THREADS)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, n)


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

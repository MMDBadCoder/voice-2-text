"""Job store. SQLite by default; point DB_URL at Postgres for multi-host setups."""
import enum
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Enum, Float, Integer, String, Text, create_engine, func,
    inspect, or_,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

Base = declarative_base()


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(200))
    original_name = Column(String(512), nullable=False)
    stored_name = Column(String(512), nullable=False)
    size_bytes = Column(Integer, default=0)
    duration_sec = Column(Float)

    status = Column(Enum(JobStatus), default=JobStatus.QUEUED, nullable=False, index=True)
    progress = Column(Float, default=0.0)
    stage = Column(String(64), default="queued")
    error = Column(Text)

    tier = Column(String(16), default="fast")
    language = Column(String(8), default="fa")
    detected_language = Column(String(8))
    diarized = Column(Integer, default=0)
    num_speakers = Column(Integer)

    num_segments = Column(Integer, default=0)
    text_chars = Column(Integer, default=0)
    audio_deleted = Column(Integer, default=0)

    rq_job_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))

    def elapsed_sec(self) -> float | None:
        if not self.started_at:
            return None
        end = self.finished_at or utcnow()
        start = self.started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return (end - start).total_seconds()

    def speed_factor(self) -> float | None:
        """How many seconds of audio processed per second of wall clock."""
        elapsed = self.elapsed_sec()
        if not elapsed or not self.duration_sec:
            return None
        return self.duration_sec / elapsed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "title": self.title,
            "display_title": self.title or self.original_name,
            "size_bytes": self.size_bytes,
            "duration_sec": self.duration_sec,
            "status": self.status.value if isinstance(self.status, JobStatus) else self.status,
            "progress": round(self.progress or 0.0, 4),
            "stage": self.stage,
            "error": self.error,
            "tier": self.tier,
            "language": self.language,
            "detected_language": self.detected_language,
            "diarized": bool(self.diarized),
            "num_speakers": self.num_speakers,
            "num_segments": self.num_segments,
            "text_chars": self.text_chars,
            "audio_deleted": bool(self.audio_deleted),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_sec": self.elapsed_sec(),
            "speed_factor": self.speed_factor(),
        }


_connect_args = {}
if config.DB_URL.startswith("sqlite"):
    # Workers and the API hit the same file from different processes.
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(config.DB_URL, future=True, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(attempts: int = 5) -> None:
    """Create the schema, tolerating concurrent callers.

    `docker compose up --scale worker=N` starts every worker at once and they
    all call this. SQLAlchemy's create_all does check-then-create, which is not
    atomic across processes: the losers raise "table already exists" and the
    worker dies on cold start. So treat that specific failure as success,
    provided the table really is there afterwards.
    """
    config.ensure_dirs()

    for attempt in range(attempts):
        try:
            Base.metadata.create_all(engine)
            break
        except OperationalError as exc:
            message = str(exc).lower()
            raced = "already exists" in message or "database is locked" in message
            if not raced or attempt == attempts - 1:
                raise
            if "already exists" in message and inspect(engine).has_table(Job.__tablename__):
                break
            time.sleep(0.2 * (attempt + 1))

    # Additive migration preserves recordings created before optional titles.
    if "title" not in {c["name"] for c in inspect(engine).get_columns("jobs")}:
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN title VARCHAR(200)")
        except OperationalError:
            if "title" not in {c["name"] for c in inspect(engine).get_columns("jobs")}:
                raise

    if config.DB_URL.startswith("sqlite"):
        # WAL lets the API read while a worker writes progress.
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA busy_timeout=30000")


def counts_by_status() -> dict:
    with SessionLocal() as s:
        rows = s.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    return {(k.value if isinstance(k, JobStatus) else k): v for k, v in rows}

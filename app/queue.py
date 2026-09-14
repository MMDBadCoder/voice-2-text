"""صف پردازش — RQ over Redis."""
from __future__ import annotations

import logging

from redis import Redis
from rq import Queue
from rq.job import Job as RQJob

from . import config

log = logging.getLogger(__name__)

_redis: Redis | None = None


def connection() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(config.REDIS_URL)
    return _redis


def get_queue() -> Queue:
    return Queue(config.QUEUE_NAME, connection=connection(), default_timeout=config.WORKER_JOB_TIMEOUT)


def enqueue(job_id: str) -> str:
    rq_job = get_queue().enqueue(
        "app.tasks.run_isolated",
        job_id,
        job_id=f"transcribe-{job_id}",  # rq rejects ":" in job ids
        job_timeout=config.WORKER_JOB_TIMEOUT,
        result_ttl=86400,
        failure_ttl=604800,
    )
    return rq_job.id


def cancel(rq_job_id: str) -> bool:
    """Drop a job that is still waiting in the queue, so no worker picks it up."""
    try:
        rq_job = RQJob.fetch(rq_job_id, connection=connection())
        rq_job.cancel()
        return True
    except Exception as exc:
        log.warning("could not cancel %s: %s", rq_job_id, exc)
        return False


# The supervisor observes cancellation every 200 ms and kills/reaps the whole
# processing group. Segment checks remain a secondary defense for direct calls.

CANCEL_KEY = "majles:cancel:{}"
CANCEL_TTL = 86400


def request_cancel(job_id: str) -> bool:
    """Ask the supervisor to stop the running processing group."""
    try:
        connection().set(CANCEL_KEY.format(job_id), b"1", ex=CANCEL_TTL)
        return True
    except Exception as exc:
        log.warning("could not flag %s for cancellation: %s", job_id, exc)
        return False


def is_cancel_requested(job_id: str) -> bool:
    try:
        return connection().exists(CANCEL_KEY.format(job_id)) > 0
    except Exception:
        # Redis unreachable: never abort work on the strength of a failed check.
        return False


def clear_cancel(job_id: str) -> None:
    try:
        connection().delete(CANCEL_KEY.format(job_id))
    except Exception:
        pass


def health() -> dict:
    try:
        conn = connection()
        conn.ping()
        q = get_queue()
        return {"ok": True, "queued": len(q), "workers": _worker_count(conn)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _worker_count(conn: Redis) -> int:
    try:
        from rq import Worker

        return len(Worker.all(connection=conn))
    except Exception:
        return 0


def position(rq_job_id: str) -> int | None:
    """1-based position in the queue, or None if not waiting."""
    try:
        ids = get_queue().get_job_ids()
        return ids.index(rq_job_id) + 1
    except (ValueError, Exception):
        return None

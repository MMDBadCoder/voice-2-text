import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# config.py reads the environment at import time, so this must run first.
_TMP = tempfile.mkdtemp(prefix="majles-test-")
os.environ.update({
    "DATA_DIR": _TMP,
    "DB_URL": f"sqlite:///{_TMP}/test.db",
    "ASR_BACKEND": "stub",
    "WORKER_COUNT": "1",
    "WORKER_CPU_THREADS": "1",
    "RESERVED_CORES": "0",
    "ENABLED_TIERS": "fast",
    "DEFAULT_TIER": "fast",
    "DIARIZATION_ENABLED": "false",
    "MAX_UPLOAD_MB": "5",
})

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def tmp_data_dir():
    return Path(_TMP)


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main, queue as qmod, tasks

    # No Redis in CI: record the enqueue instead of performing it, and back the
    # cancel flags with an in-memory set so the cancellation path is real.
    enqueued = []
    flags: set[str] = set()
    monkeypatch.setattr(qmod, "enqueue", lambda job_id: enqueued.append(job_id) or f"rq:{job_id}")
    monkeypatch.setattr(main.qmod, "enqueue", lambda job_id: enqueued.append(job_id) or f"rq:{job_id}")
    for mod in (qmod, main.qmod, tasks.qmod):
        monkeypatch.setattr(mod, "request_cancel", lambda jid: bool(flags.add(jid)) or True)
        monkeypatch.setattr(mod, "is_cancel_requested", lambda jid: jid in flags)
        monkeypatch.setattr(mod, "clear_cancel", lambda jid: flags.discard(jid))
        monkeypatch.setattr(mod, "cancel", lambda rq_id: True)
    monkeypatch.setattr(main.qmod, "position", lambda rq_id: 1)
    monkeypatch.setattr(main.qmod, "health", lambda: {"ok": True, "queued": len(enqueued), "workers": 1})

    with TestClient(main.app) as c:
        c.enqueued = enqueued
        c.cancel_flags = flags
        yield c

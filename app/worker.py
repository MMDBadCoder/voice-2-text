"""Queue supervisor. Each recording runs in a fresh, cancellable subprocess.

The queue process stays alive while decoding, probing, and diarization run in
an owned process group. Models are loaded in the child and released on exit.
"""
from __future__ import annotations

import logging
import os
import sys

from . import config

# Thread limits must be set before ctranslate2 / onnxruntime are imported.
config.apply_thread_env()

from rq import Queue, SimpleWorker  # noqa: E402

from . import db, queue as qmod  # noqa: E402

log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s [worker %(process)d] %(name)s: %(message)s",
    )

    try:
        warnings = config.validate(role="worker")
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    for warning in warnings:
        log.warning("%s", warning)

    db.init_db()

    log.info(
        "worker starting: backend=%s tiers=%s compute=%s cpu_threads=%d (pool: %d workers)",
        config.ASR_BACKEND, ",".join(config.ENABLED_TIERS), config.COMPUTE_TYPE,
        config.WORKER_CPU_THREADS, config.WORKER_COUNT,
    )

    conn = qmod.connection()
    worker = SimpleWorker([Queue(config.QUEUE_NAME, connection=conn)], connection=conn)
    worker.work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

import importlib
import os

import pytest


def _reload(**env):
    old = dict(os.environ)
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        from app import config
        importlib.reload(config)
        return config
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_oversubscribed_pool_is_rejected():
    """The guard that stops you making the app slower by adding workers."""
    cores = os.cpu_count() or 4
    config = _reload(WORKER_COUNT=cores, WORKER_CPU_THREADS=cores, RESERVED_CORES=2,
                     ASR_BACKEND="stub", ENABLED_TIERS="fast", DEFAULT_TIER="fast")
    with pytest.raises(config.ConfigError, match="Oversubscribed"):
        config.validate(role="api")
    _reload()


def test_sane_pool_passes():
    config = _reload(WORKER_COUNT=1, WORKER_CPU_THREADS=1, RESERVED_CORES=0,
                     ASR_BACKEND="stub", ENABLED_TIERS="fast", DEFAULT_TIER="fast")
    assert isinstance(config.validate(role="api"), list)
    _reload()


def test_default_tier_must_be_enabled():
    config = _reload(WORKER_COUNT=1, WORKER_CPU_THREADS=1, RESERVED_CORES=0,
                     ASR_BACKEND="stub", ENABLED_TIERS="fast", DEFAULT_TIER="accurate")
    with pytest.raises(config.ConfigError, match="not in ENABLED_TIERS"):
        config.validate(role="api")
    _reload()


def test_worker_role_requires_model_to_exist():
    config = _reload(WORKER_COUNT=1, WORKER_CPU_THREADS=1, RESERVED_CORES=0,
                     ASR_BACKEND="faster_whisper", ENABLED_TIERS="fast", DEFAULT_TIER="fast",
                     MODEL_FAST="/nonexistent/model")
    with pytest.raises(config.ConfigError, match="not found"):
        config.validate(role="worker")
    # The API must still start without models -- only workers load them.
    config.validate(role="api")
    _reload()


def test_apply_thread_env_sets_openmp():
    config = _reload(WORKER_CPU_THREADS=3, WORKER_COUNT=1, RESERVED_CORES=0)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.pop(var, None)
    config.apply_thread_env()
    assert os.environ["OMP_NUM_THREADS"] == "3"
    assert os.environ["MKL_NUM_THREADS"] == "3"
    _reload()


def test_init_db_is_safe_from_concurrent_workers(tmp_path):
    """Regression: `--scale worker=N` starts every worker at once and they all
    call init_db. SQLAlchemy's check-then-create is not atomic across processes,
    so the losers used to die with 'table jobs already exists'."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    db_file = tmp_path / "race.db"
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from app import db; db.init_db(); print('ok')" % str(root)
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "DATA_DIR": str(tmp_path),
        "DB_URL": f"sqlite:///{db_file}",
        "ASR_BACKEND": "stub",
    }
    procs = [
        subprocess.Popen([sys.executable, "-c", code], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(6)
    ]
    results = [(p.wait(), *p.communicate()) for p in procs]
    failures = [err for rc, _out, err in results if rc != 0]
    assert not failures, "concurrent init_db failed:\n" + "\n".join(failures[:2])

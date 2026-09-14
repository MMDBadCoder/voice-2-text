"""Canonical result storage: one JSON per job, every other format derived on demand."""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .asr import Segment, TranscriptionResult
from .exporters import to_json


def result_path(job_id: str) -> Path:
    return config.RESULTS_DIR / f"{job_id}.json"


def save(job_id: str, result: TranscriptionResult, meta: dict | None = None) -> Path:
    path = result_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(to_json(result, meta), encoding="utf-8")
    tmp.replace(path)  # atomic: the API never sees a half-written file
    return path


def load(job_id: str) -> TranscriptionResult | None:
    path = result_path(job_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return TranscriptionResult(
        segments=[
            Segment(
                start=s["start"],
                end=s["end"],
                text=s["text"],
                speaker=s.get("speaker"),
                no_speech_prob=s.get("no_speech_prob", 0.0),
                avg_logprob=s.get("avg_logprob", 0.0),
            )
            for s in data.get("segments", [])
        ],
        language=data.get("language", "fa"),
        language_probability=data.get("language_probability", 1.0),
        duration=data.get("duration", 0.0),
        backend=data.get("backend", ""),
        model=data.get("model", ""),
    )


def load_meta(job_id: str) -> dict:
    path = result_path(job_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("meta", {})


def delete(job_id: str) -> None:
    result_path(job_id).unlink(missing_ok=True)

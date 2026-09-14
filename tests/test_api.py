import io

import pytest


def _mp3(size=2048):
    # Not a decodable mp3 -- the stub backend never opens it. Enough to exercise
    # upload validation, storage and the job row.
    return io.BytesIO(b"ID3\x03\x00\x00\x00" + b"\x00" * size)


def test_index_is_rtl_persian(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'dir="rtl"' in r.text
    assert 'lang="fa"' in r.text
    assert "دستیار صوتی جلسات" in r.text


def test_health_reports_pool(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    pool = r.json()["pool"]
    assert pool["worker_count"] == 1
    assert pool["cpu_threads_per_worker"] == 1
    assert pool["threads_requested"] == 1


def test_upload_creates_queued_job(client):
    r = client.post("/api/jobs", files={"file": ("جلسه.mp3", _mp3(), "audio/mpeg")})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["original_name"] == "جلسه.mp3"
    assert job["size_bytes"] > 0
    assert job["id"] in client.enqueued


def test_rejects_unsupported_extension(client):
    r = client.post("/api/jobs", files={"file": ("notes.pdf", _mp3(), "application/pdf")})
    assert r.status_code == 400
    assert "پشتیبانی نمی‌شود" in r.json()["detail"]


def test_rejects_empty_file(client):
    r = client.post("/api/jobs", files={"file": ("empty.mp3", io.BytesIO(b""), "audio/mpeg")})
    assert r.status_code == 400


def test_rejects_oversized_file(client):
    # MAX_UPLOAD_MB=5 in conftest
    big = io.BytesIO(b"\x00" * (6 * 1024 * 1024))
    r = client.post("/api/jobs", files={"file": ("big.mp3", big, "audio/mpeg")})
    assert r.status_code == 400
    assert "حجم" in r.json()["detail"]


def test_rejects_unknown_tier(client):
    r = client.post(
        "/api/jobs",
        files={"file": ("a.mp3", _mp3(), "audio/mpeg")},
        data={"tier": "turbo-max"},
    )
    assert r.status_code == 400


def test_job_lifecycle_end_to_end(client, monkeypatch):
    """Upload -> run the real task function -> download every format."""
    from app import media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 30.0)

    r = client.post("/api/jobs", files={"file": ("standup.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]

    result = tasks.run_transcription(job_id)
    assert result["status"] == "done"

    r = client.get(f"/api/jobs/{job_id}")
    job = r.json()
    assert job["status"] == "done"
    assert job["progress"] == 1.0
    assert job["num_segments"] == 3      # 30s of audio at the stub's 10s stride
    assert job["duration_sec"] == 30.0
    assert job["speed_factor"] is not None

    segs = client.get(f"/api/jobs/{job_id}/segments").json()
    assert len(segs["segments"]) == 3
    assert segs["segments"][0]["text"]

    for fmt in ("txt", "plain", "srt", "vtt", "json", "docx"):
        d = client.get(f"/api/jobs/{job_id}/download?format={fmt}")
        assert d.status_code == 200, f"{fmt}: {d.text[:200]}"
        assert len(d.content) > 0

    srt = client.get(f"/api/jobs/{job_id}/download?format=srt").text
    assert "00:00:00,000 --> 00:00:10,000" in srt
    assert "1\n" in srt


def test_download_before_completion_is_409(client):
    r = client.post("/api/jobs", files={"file": ("x.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    assert client.get(f"/api/jobs/{job_id}/download?format=txt").status_code == 409


def test_missing_audio_marks_job_failed(client, monkeypatch):
    from app import config, tasks

    r = client.post("/api/jobs", files={"file": ("gone.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    (config.AUDIO_DIR / f"{job_id}.mp3").unlink()

    with pytest.raises(FileNotFoundError):
        tasks.run_transcription(job_id)

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert "FileNotFoundError" in job["error"]


def test_delete_removes_job_and_files(client):
    from app import config

    r = client.post("/api/jobs", files={"file": ("tmp.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    audio = config.AUDIO_DIR / f"{job_id}.mp3"
    assert audio.exists()

    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert not audio.exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_retry_only_allowed_on_failed(client):
    r = client.post("/api/jobs", files={"file": ("r.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409


def test_filename_with_path_traversal_is_sanitized(client):
    r = client.post("/api/jobs", files={"file": ("../../etc/passwd.mp3", _mp3(), "audio/mpeg")})
    assert r.status_code == 201
    assert "/" not in r.json()["original_name"]


def test_404_for_unknown_job(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_download_with_persian_filename(client, monkeypatch):
    """Regression: Persian filenames are not latin-1, so they must be
    percent-encoded into Content-Disposition filename* or the response 500s."""
    from app import media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 20.0)
    r = client.post("/api/jobs", files={"file": ("جلسه هفتگی.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    tasks.run_transcription(job_id)

    for fmt in ("txt", "srt", "json", "docx"):
        d = client.get(f"/api/jobs/{job_id}/download?format={fmt}")
        assert d.status_code == 200, f"{fmt} -> {d.status_code}"
        cd = d.headers["content-disposition"]
        cd.encode("latin-1")                       # must be header-safe
        assert "filename*=UTF-8''" in cd
        assert "%D8%AC" in cd                      # the percent-encoded "ج"


def test_stub_mode_shows_warning_banner(client):
    """Fake transcripts must never be mistakable for real ones. conftest runs
    the whole suite with ASR_BACKEND=stub, so the banner must be present."""
    html = client.get("/").text
    assert "stub-banner" in html
    assert "ساختگی" in html                      # "fabricated"
    assert "ASR_BACKEND=faster_whisper" in html  # tells the reader how to fix it


def test_no_banner_when_backend_is_real(client, monkeypatch):
    from app import config, main

    monkeypatch.setattr(config, "ASR_BACKEND", "faster_whisper")
    monkeypatch.setattr(main.config, "ASR_BACKEND", "faster_whisper")
    assert "stub-banner" not in client.get("/").text


def test_segments_endpoint_flags_stub_output(client, monkeypatch):
    """A transcript carries the backend that produced it, so a job created in
    stub mode stays flagged even after the server switches to a real model."""
    from app import media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 20.0)
    r = client.post("/api/jobs", files={"file": ("s.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    tasks.run_transcription(job_id)

    data = client.get(f"/api/jobs/{job_id}/segments").json()
    assert data["backend"] == "stub"
    assert data["is_stub"] is True


# --------------------------------------------------------------- cancellation
def test_cancel_running_job_stops_the_worker(client, monkeypatch):
    """The bug: DELETE/cancel left the worker decoding a job nobody wanted,
    burning a core and blocking the queue behind a ghost."""
    from app import asr, media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 600.0)
    r = client.post("/api/jobs", files={"file": ("long.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]

    # Cancel arrives while the job is mid-decode.
    client.post(f"/api/jobs/{job_id}/cancel")
    result = tasks.run_transcription(job_id)

    assert result["status"] == "canceled"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "canceled"


def test_delete_while_running_flags_cancel(client, monkeypatch):
    """Deleting a running job must stop it, not just hide it from the UI."""
    from app import config, media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 600.0)
    r = client.post("/api/jobs", files={"file": ("ghost.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]

    from app.db import Job, JobStatus, SessionLocal
    with SessionLocal() as s:
        s.get(Job, job_id).status = JobStatus.RUNNING
        s.commit()

    d = client.delete(f"/api/jobs/{job_id}")
    assert d.json()["was_running"] is True
    assert job_id in client.cancel_flags          # worker will see this

    # The worker, already holding the job, aborts and cleans up after itself.
    assert tasks.run_transcription(job_id)["status"] == "canceled"
    assert not (config.AUDIO_DIR / f"{job_id}.mp3").exists()


def test_deleted_queued_job_does_not_raise(client):
    """A job deleted while queued used to raise and pollute rq's failed registry."""
    from app import tasks

    r = client.post("/api/jobs", files={"file": ("q.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    client.delete(f"/api/jobs/{job_id}")

    out = tasks.run_transcription(job_id)
    assert out == {"status": "canceled", "reason": "deleted"}


def test_cancel_is_rejected_once_finished(client, monkeypatch):
    from app import media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 10.0)
    r = client.post("/api/jobs", files={"file": ("d.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    tasks.run_transcription(job_id)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_retry_clears_stale_cancel_flag(client, monkeypatch):
    """A leftover flag would abort the retry the instant it started."""
    from app import media, tasks

    monkeypatch.setattr(media, "probe_duration", lambda p: 20.0)
    r = client.post("/api/jobs", files={"file": ("retry.mp3", _mp3(), "audio/mpeg")})
    job_id = r.json()["id"]
    client.post(f"/api/jobs/{job_id}/cancel")
    assert job_id in client.cancel_flags

    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 200
    assert job_id not in client.cancel_flags
    assert tasks.run_transcription(job_id)["status"] == "done"


def test_asr_raises_on_cancel_mid_stream():
    from app import asr

    calls = {"n": 0}

    def cancel_after_two():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(asr.TranscriptionCanceled):
        asr.transcribe("ignored.mp3", tier="fast", duration_hint=300.0,
                       should_cancel=cancel_after_two)

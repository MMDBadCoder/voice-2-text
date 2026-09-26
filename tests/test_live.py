"""رونویسی زنده — live microphone transcription."""
import json
import struct

import pytest


def _pcm(seconds=1.5, rate=16000):
    """Silence is fine: the stub backend ignores content, and the length check
    is what we actually care about exercising."""
    return b"\x00\x00" * int(rate * seconds)


def _open_session(client, title="جلسهٔ زنده"):
    r = client.post("/api/sessions", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_transcript_starts_empty(client):
    job_id = _open_session(client)
    body = client.get(f"/api/sessions/{job_id}/transcript").json()
    assert body["items"] == []
    assert body["live_enabled"] is True


def test_live_socket_appends_a_block(client):
    """The round trip that matters: PCM in, a persisted transcript block out."""
    job_id = _open_session(client)
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_bytes(struct.pack("<I", 1) + _pcm())

        kinds = []
        block = None
        for _ in range(4):
            msg = json.loads(ws.receive_text())
            kinds.append(msg["type"])
            if msg["type"] == "block":
                block = msg["block"]
                break
        assert "working" in kinds, kinds
        assert block is not None, kinds
        assert block["source"] == "mic"
        assert block["state"] == "done"
        assert block["text"]

    stored = client.get(f"/api/sessions/{job_id}/transcript").json()["items"]
    assert len(stored) == 1
    assert stored[0]["seq"] == 1


def test_blocks_keep_arrival_order(client):
    job_id = _open_session(client)
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        ws.receive_text()
        for n in (1, 2, 3):
            ws.send_bytes(struct.pack("<I", n) + _pcm())
            while json.loads(ws.receive_text())["type"] != "block":
                pass
    seqs = [b["seq"] for b in client.get(f"/api/sessions/{job_id}/transcript").json()["items"]]
    assert seqs == [1, 2, 3]


def test_too_short_utterance_returns_empty_not_a_block(client):
    """A stray click must not litter the transcript with an empty block."""
    job_id = _open_session(client)
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        ws.receive_text()
        ws.send_bytes(struct.pack("<I", 1) + _pcm(seconds=0.1))
        kinds = [json.loads(ws.receive_text())["type"] for _ in range(2)]
    assert "empty" in kinds, kinds
    assert client.get(f"/api/sessions/{job_id}/transcript").json()["items"] == []


def test_socket_rejects_anonymous(client):
    job_id = _open_session(client)
    client.cookies.clear()
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        msg = json.loads(ws.receive_text())
    assert msg["type"] == "error" and msg["fatal"] is True


def test_socket_rejects_someone_elses_session(client):
    job_id = _open_session(client)
    from app.db import Job, SessionLocal
    with SessionLocal() as s:
        s.get(Job, job_id).owner_id = "someone-else"
        s.commit()
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        assert json.loads(ws.receive_text())["fatal"] is True


def test_socket_rejects_a_closed_session(client):
    """A closed session is being turned into its final transcript; appending
    live text to it would silently diverge from the file that gets produced."""
    job_id = _open_session(client)
    from app.db import Job, JobStatus, SessionLocal
    with SessionLocal() as s:
        s.get(Job, job_id).status = JobStatus.QUEUED
        s.commit()
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        assert json.loads(ws.receive_text())["fatal"] is True


def test_block_can_be_edited_and_deleted(client):
    job_id = _open_session(client)
    with client.websocket_connect(f"/ws/sessions/{job_id}/live") as ws:
        ws.receive_text()
        ws.send_bytes(struct.pack("<I", 1) + _pcm())
        while True:
            msg = json.loads(ws.receive_text())
            if msg["type"] == "block":
                block_id = msg["block"]["id"]
                break

    edited = client.patch(f"/api/sessions/{job_id}/transcript/{block_id}",
                          json={"text": "متن اصلاح‌شده"}).json()
    assert edited["text"] == "متن اصلاح‌شده"
    assert edited["edited"] is True

    assert client.delete(f"/api/sessions/{job_id}/transcript/{block_id}").status_code == 200
    assert client.get(f"/api/sessions/{job_id}/transcript").json()["items"] == []


def test_cannot_touch_blocks_of_another_users_session(client):
    job_id = _open_session(client)
    from app.db import Job, SessionLocal
    with SessionLocal() as s:
        s.get(Job, job_id).owner_id = "intruder"
        s.commit()
    assert client.get(f"/api/sessions/{job_id}/transcript").status_code == 404


def test_uploaded_clip_reserves_a_pending_block(client):
    """A file dropped into an open session must appear in the transcript
    immediately, in the position the user dropped it -- not whenever the
    queue happens to finish it."""
    import io

    job_id = _open_session(client)
    from app import media
    import app.sessions as sessions_mod
    original = sessions_mod.media.probe_duration
    sessions_mod.media.probe_duration = lambda p: 12.0
    try:
        r = client.post(f"/api/sessions/{job_id}/clips",
                        files={"file": ("گزارش.mp3", io.BytesIO(b"ID3" + b"\x00" * 4096), "audio/mpeg")})
        assert r.status_code == 201, r.text
        clip_id = r.json()["id"]
    finally:
        sessions_mod.media.probe_duration = original

    items = client.get(f"/api/sessions/{job_id}/transcript").json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "file"
    assert items[0]["state"] == "pending"
    assert items[0]["clip_id"] == clip_id
    assert (job_id, clip_id) in client.clip_jobs      # queued for transcription


def test_clip_block_resolves_when_the_queue_finishes(client):
    import io
    from app import live

    job_id = _open_session(client)
    import app.sessions as sessions_mod
    original = sessions_mod.media.probe_duration
    sessions_mod.media.probe_duration = lambda p: 12.0
    try:
        clip_id = client.post(
            f"/api/sessions/{job_id}/clips",
            files={"file": ("a.mp3", io.BytesIO(b"ID3" + b"\x00" * 4096), "audio/mpeg")},
        ).json()["id"]
    finally:
        sessions_mod.media.probe_duration = original

    live.resolve_file_block(job_id, clip_id, "متن فایل بارگذاری‌شده", 12000)
    item = client.get(f"/api/sessions/{job_id}/transcript").json()["items"][0]
    assert item["state"] == "done"
    assert item["text"] == "متن فایل بارگذاری‌شده"
    assert live.session_text(job_id) == "متن فایل بارگذاری‌شده"


def test_engine_caches_one_model_per_tier(monkeypatch):
    """Regression: the per-tier refactor left a stale `return self._model`, so
    warm-up crashed the API at startup. The stub backend never loads a model,
    so only a test that exercises _load directly catches this."""
    from app import config, live

    built = []

    class FakeModel:
        def __init__(self, path, **kw):
            built.append(path)

    monkeypatch.setattr(config, "ASR_BACKEND", "faster_whisper")
    monkeypatch.setattr(config, "resolve_model_path", lambda tier: f"/models/{tier}")
    import sys, types
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    engine = live.LiveEngine()
    first = engine._load("fast")
    assert isinstance(first, FakeModel)
    assert engine._load("fast") is first          # cached, not rebuilt
    other = engine._load("accurate")
    assert other is not first                     # separate model per tier
    assert built == ["/models/fast", "/models/accurate"]

    engine.warm("fast")                           # must not raise

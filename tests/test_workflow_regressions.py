"""Failures found by reviewing the complete recording-to-export workflow."""
import json
import struct
from app import live, results, tasks
from app.db import Job, JobStatus, LiveBlock, SessionLocal
from test_sessions import new, add


def test_live_stop_acknowledges_last_sentence_and_exports(client):
    jid = new(client)
    with client.websocket_connect(f'/ws/sessions/{jid}/live') as ws:
        ws.receive_text()
        ws.send_bytes(struct.pack('<I', 1) + b'\0\0' * 16000)
        ws.send_text(json.dumps({'type': 'finish'}))
        types = []
        while True:
            msg = json.loads(ws.receive_text()); types.append(msg['type'])
            if msg['type'] == 'finished': break
        assert types == ['working', 'block', 'finished']
    assert client.post(f'/api/sessions/{jid}/close').json()['status'] == 'done'
    assert 'متن آزمایشی' in client.get(f'/api/jobs/{jid}/download?format=plain').text


def test_socket_origin_and_revoked_access(client):
    jid = new(client)
    with client.websocket_connect(f'/ws/sessions/{jid}/live', headers={'origin': 'https://another.example'}) as ws:
        assert json.loads(ws.receive_text())['fatal']
    with client.websocket_connect(f'/ws/sessions/{jid}/live') as ws:
        ws.receive_text()
        with SessionLocal() as s:
            s.get(Job, jid).status = JobStatus.DONE; s.commit()
        ws.send_bytes(struct.pack('<I', 1) + b'\0\0' * 16000)
        assert json.loads(ws.receive_text())['fatal']
    assert not live.has_blocks(jid)


def test_malformed_pcm_does_not_crash_connection(client):
    jid = new(client)
    with client.websocket_connect(f'/ws/sessions/{jid}/live') as ws:
        ws.receive_text(); ws.send_bytes(struct.pack('<I', 1) + b'x')
        assert json.loads(ws.receive_text())['type'] == 'error'
        ws.send_text('{"type":"finish"}')
        assert json.loads(ws.receive_text())['type'] == 'finished'


def test_queue_failure_preserves_clip_and_can_retry(client, monkeypatch):
    from app import queue, storage
    jid = new(client)
    monkeypatch.setattr(queue, 'enqueue_clip', lambda *args: None)
    clip = add(client, jid).json()
    assert clip['transcription_queued'] is False
    assert client.get(f'/api/sessions/{jid}/clips/{clip["id"]}/audio').status_code == 200
    assert client.get(f'/api/sessions/{jid}/transcript').json()['items'][0]['state'] == 'failed'
    monkeypatch.setattr(queue, 'enqueue_clip', lambda *args: 'queued')
    assert client.post(f'/api/sessions/{jid}/clips/{clip["id"]}/retry').status_code == 200
    assert client.get(f'/api/sessions/{jid}/transcript').json()['items'][0]['state'] == 'pending'


def test_delete_clip_and_session_remove_transcript(client):
    jid = new(client); clip = add(client, jid).json()
    assert client.delete(f'/api/sessions/{jid}/clips/{clip["id"]}').status_code == 200
    assert not live.has_blocks(jid)
    live._append_block(jid, 'keep', source='mic', duration_ms=1000)
    client.delete(f'/api/jobs/{jid}')
    assert not live.has_blocks(jid)
    assert live._append_block(jid, 'late', source='mic', duration_ms=1000) is None


def test_mixed_session_keeps_live_text_in_final_export(client):
    jid = new(client); add(client, jid)
    live._append_block(jid, 'متن میکروفن حفظ شود', source='mic', duration_ms=1000)
    client.post(f'/api/sessions/{jid}/close'); tasks.run_transcription(jid)
    assert 'متن میکروفن حفظ شود' in client.get(f'/api/jobs/{jid}/download?format=plain').text


def test_retention_removes_live_text_as_well_as_job(client, monkeypatch):
    from datetime import timedelta
    from app import config, db
    jid = new(client)
    live._append_block(jid, 'expired transcript', source='mic', duration_ms=1000)
    with SessionLocal() as s:
        s.get(Job, jid).created_at = db.utcnow() - timedelta(days=10); s.commit()
    monkeypatch.setattr(config, 'RETENTION_DAYS', 5)
    tasks.sweep_retention()
    assert not live.has_blocks(jid)

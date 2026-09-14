"""Regression coverage for titles, migration, and actual process cancellation."""
import io
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


def upload(client, title=''):
    return client.post('/api/jobs', files={'file': ('voice.mp3', io.BytesIO(b'ID3' + b'0' * 100), 'audio/mpeg')}, data={'title': title})


def test_title_search_and_export(client, monkeypatch):
    from app import media, tasks
    monkeypatch.setattr(media, 'probe_duration', lambda _: 10)
    j = upload(client, '  جلسهٔ طراحی  ').json()
    assert j['title'] == 'جلسهٔ طراحی'
    assert j['display_title'] == j['title']
    assert client.get('/api/jobs', params={'search': 'طراحی'}).json()['items'][0]['id'] == j['id']
    assert client.get('/api/jobs', params={'search': '%'}).json()['total'] == 0
    assert 'جلسهٔ طراحی' in client.get('/jobs/' + j['id']).text
    tasks.run_transcription(j['id'])
    from urllib.parse import unquote
    assert 'جلسهٔ طراحی.txt' in unquote(client.get(f"/api/jobs/{j['id']}/download?format=plain").headers['content-disposition'])
    assert upload(client).json()['display_title'] == 'voice.mp3'
    assert upload(client, 'x' * 201).status_code == 400


def test_migrate_existing_database(tmp_path, monkeypatch):
    from app import db
    from sqlalchemy import create_engine, inspect
    engine = create_engine('sqlite:///' + str(tmp_path / 'old.db'))
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY, original_name VARCHAR(512))')
        conn.exec_driver_sql("INSERT INTO jobs VALUES ('old', 'meeting.mp3')")
    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    db.init_db()
    assert 'title' in {c['name'] for c in inspect(engine).get_columns('jobs')}
    with engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT original_name, title FROM jobs').one() == ('meeting.mp3', None)
    engine.dispose()


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_supervisor_kills_uncooperative_process(client, monkeypatch, tmp_path, action):
    """Use a real CPU-bound child that never checks cancellation."""
    from app import tasks
    from app.db import Job, JobStatus, SessionLocal
    jid = upload(client).json()['id']
    launched = threading.Event()
    children = []
    real_popen = subprocess.Popen
    def launch(*args, **kwargs):
        process = real_popen([sys.executable, '-c', 'while True: pass'], start_new_session=True)
        children.append(process)
        launched.set()
        return process
    monkeypatch.setattr(subprocess, 'Popen', launch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tasks.run_isolated, jid)
        assert launched.wait(5)
        try:
            if action == 'cancel':
                response = client.post(f'/api/jobs/{jid}/cancel')
                assert response.json()['stage'] == 'canceling'
                assert client.post(f'/api/jobs/{jid}/retry').status_code == 409
            else:
                assert client.delete(f'/api/jobs/{jid}').status_code == 200
            assert future.result(timeout=5)['status'] == 'canceled'
            assert children[0].poll() is not None
            with pytest.raises(ProcessLookupError):
                os.kill(children[0].pid, 0)
            if action == 'cancel':
                assert client.get(f'/api/jobs/{jid}').json()['stage'] == 'canceled'
                assert client.post(f'/api/jobs/{jid}/retry').status_code == 200
        finally:
            if children[0].poll() is None:
                children[0].kill()
                children[0].wait()


def test_late_worker_update_does_not_revive_canceled_job(client):
    from app import tasks
    from app.db import JobStatus
    jid = upload(client).json()['id']
    client.post(f'/api/jobs/{jid}/cancel')
    tasks._update(jid, status=JobStatus.DONE, stage='done', progress=1)
    assert client.get(f'/api/jobs/{jid}').json()['status'] == 'canceled'


def test_supervisor_completes_real_child(client):
    import wave
    from app import tasks
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000)
    r = client.post('/api/jobs', files={'file': ('sample.wav', buffer.getvalue(), 'audio/wav')})
    jid = r.json()['id']
    assert tasks.run_isolated(jid)['status'] == 'done'
    assert client.get(f'/api/jobs/{jid}/segments').status_code == 200
    # A duplicate delivery must not reprocess the same completed job.
    assert tasks.run_isolated(jid)['status'] == 'skipped'


def test_child_cannot_confirm_stop_before_supervisor_reaps(client):
    from app import tasks
    jid = upload(client).json()['id']
    client.post(f'/api/jobs/{jid}/cancel')
    tasks._cleanup_canceled(jid, '', fully_stopped=False)
    assert client.get(f'/api/jobs/{jid}').json()['stage'] == 'canceling'
    assert client.post(f'/api/jobs/{jid}/retry').status_code == 409
    tasks._cleanup_canceled(jid, '')
    assert client.get(f'/api/jobs/{jid}').json()['stage'] == 'canceled'

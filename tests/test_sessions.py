import io
import math
import struct
import wave
from pathlib import Path

import pytest

from app import config, db, media, tasks
from app.db import AudioClip, Job, JobStatus, SessionLocal


def wav(frequency=440, seconds=1):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
        w.writeframes(b''.join(struct.pack('<h',int(12000*math.sin(2*math.pi*frequency*i/16000))) for i in range(int(16000*seconds))))
    return out.getvalue()


def new(client):
    response=client.post('/api/sessions',json={'title':'جلسهٔ چندبخشی','tier':'fast'})
    assert response.status_code==201,response.text
    return response.json()['id']


def add(client,jid,name='sample.wav',content=None):
    return client.post(f'/api/sessions/{jid}/clips',files={'file':(name,content or wav(),'audio/wav')})


def test_session_stays_open_until_explicit_close(client):
    jid=new(client)
    assert client.get(f'/api/jobs/{jid}').json()['status']=='open'
    assert jid not in client.enqueued
    assert client.post(f'/api/sessions/{jid}/close').status_code==400
    r=add(client,jid);assert r.status_code==201,r.text
    assert jid not in client.enqueued
    assert client.get('/api/jobs').json()['items'][0]['clip_count']==1
    assert client.get(f'/api/sessions/{jid}/clips/{r.json()["id"]}/audio').status_code==200
    assert client.post(f'/api/sessions/{jid}/close').status_code==200
    assert client.enqueued.count(jid)==1
    assert client.post(f'/api/sessions/{jid}/close').status_code==409
    assert add(client,jid).status_code==409
    assert client.post(f'/api/sessions/{jid}/order',json={'clip_ids':[r.json()['id']]}).status_code==409
    assert client.delete(f'/api/sessions/{jid}/clips/{r.json()["id"]}').status_code==409


def test_ordered_session_processing_and_exports(client):
    jid=new(client)
    first=add(client,jid,'first.wav',wav(440)).json()
    second=add(client,jid,'second.wav',wav(880)).json()
    assert client.post(f'/api/sessions/{jid}/order',json={'clip_ids':[second['id'],first['id']]}).status_code==200
    assert client.get(f'/api/sessions/{jid}/clips').json()['items'][0]['id']==second['id']
    assert client.post(f'/api/sessions/{jid}/close').status_code==200
    result=tasks.run_transcription(jid)
    assert result['status']=='done'
    j=client.get(f'/api/jobs/{jid}').json()
    assert j['duration_sec'] == pytest.approx(2,abs=.15)
    assert client.get(f'/api/jobs/{jid}/audio').status_code==200
    for fmt in ['plain','txt','docx','srt','vtt','json']:
        assert client.get(f'/api/jobs/{jid}/download?format={fmt}').status_code==200
    # Inspect the assembled audio: reordering must affect the audio, not just the UI.
    import av,numpy as np
    with av.open(str(config.AUDIO_DIR/f'{jid}.mp3')) as container:
        samples=np.concatenate([f.to_ndarray().reshape(-1) for f in container.decode(audio=0)])
    def dominant(start):
        chunk=samples[int(start*16000):int((start+.3)*16000)]
        return np.fft.rfftfreq(len(chunk),1/16000)[np.argmax(abs(np.fft.rfft(chunk)))]
    assert dominant(.2)==pytest.approx(880,abs=5)
    assert dominant(1.2)==pytest.approx(440,abs=5)


def test_mixed_formats_and_deletion(client,tmp_path):
    jid=new(client)
    p=tmp_path/'input.wav';p.write_bytes(wav())
    mp3=tmp_path/'converted.mp3';media.concatenate_audio([str(p)],str(mp3))
    a=add(client,jid,'one.wav').json()
    b=add(client,jid,'two.mp3',mp3.read_bytes()).json()
    assert client.get(f'/api/sessions/{jid}/clips').json()['items'][1]['id']==b['id']
    assert client.delete(f'/api/sessions/{jid}/clips/{a["id"]}').status_code==200
    assert not (config.AUDIO_DIR/f'{a["id"]}.wav').exists()
    assert client.delete(f'/api/jobs/{jid}').status_code==200
    assert not (config.AUDIO_DIR/f'{b["id"]}.mp3').exists()
    with SessionLocal() as s:assert s.query(AudioClip).filter_by(job_id=jid).count()==0


def test_reopen_stopped_session_and_retry(client):
    jid=new(client);add(client,jid)
    client.post(f'/api/sessions/{jid}/close')
    client.post(f'/api/jobs/{jid}/cancel')
    assert client.post(f'/api/sessions/{jid}/reopen').status_code==200
    assert add(client,jid).status_code==201
    client.post(f'/api/sessions/{jid}/close')
    client.post(f'/api/jobs/{jid}/cancel')
    assert client.post(f'/api/jobs/{jid}/retry').status_code==200
    assert tasks.run_transcription(jid)['status']=='done'


def test_clip_limits_and_order_validation(client,monkeypatch):
    jid=new(client);clip=add(client,jid).json()
    monkeypatch.setattr(config,'MAX_SESSION_FILES',1)
    before=set(config.AUDIO_DIR.iterdir())
    assert add(client,jid).status_code==400
    assert set(config.AUDIO_DIR.iterdir())==before
    assert client.post(f'/api/sessions/{jid}/order',json={'clip_ids':[clip['id'],clip['id']]}).status_code==400
    assert client.post(f'/api/sessions/{jid}/order',json={'clip_ids':['foreign-clip']}).status_code==400
    assert add(client,jid,'fake.wav',b'not audio').status_code==400
    assert set(config.AUDIO_DIR.iterdir())==before


def test_late_upload_cannot_attach_to_closed_session(client,monkeypatch):
    jid=new(client);add(client,jid)
    original=media.probe_duration
    def close_during_probe(path):
        client.post(f'/api/sessions/{jid}/close')
        return original(path)
    monkeypatch.setattr(media,'probe_duration',close_during_probe)
    before=set(config.AUDIO_DIR.iterdir())
    assert add(client,jid).status_code==409
    assert set(config.AUDIO_DIR.iterdir())==before
    assert len(client.get(f'/api/sessions/{jid}/clips').json()['items'])==1


def test_delete_audio_setting_removes_all_source_clips(client,monkeypatch):
    jid=new(client);clip=add(client,jid).json()
    monkeypatch.setattr(config,'DELETE_AUDIO_AFTER_TRANSCRIBE',True)
    client.post(f'/api/sessions/{jid}/close');tasks.run_transcription(jid)
    assert client.get(f'/api/jobs/{jid}').json()['audio_deleted']
    assert not (config.AUDIO_DIR/f'{clip["id"]}.wav').exists()
    assert not (config.AUDIO_DIR/f'{jid}.mp3').exists()


def test_queue_failure_can_be_retried_without_becoming_stuck(client,monkeypatch):
    from app import queue as qmod
    jid=new(client);add(client,jid)
    def unavailable(_):raise RuntimeError('unavailable')
    monkeypatch.setattr(qmod,'enqueue',unavailable)
    assert client.post(f'/api/sessions/{jid}/close').status_code==503
    assert client.get(f'/api/jobs/{jid}').json()['status']=='failed'
    assert client.post(f'/api/jobs/{jid}/retry').status_code==503
    assert client.get(f'/api/jobs/{jid}').json()['status']=='failed'

import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, bale, config, db, main
from app.db import BaleIdentity, Job, LoginSession, RateBucket, SessionLocal, User, VerificationChallenge


@pytest.fixture
def account_env(client, monkeypatch):
    with SessionLocal() as s:
        s.query(RateBucket).delete()
        s.commit()
    monkeypatch.setattr(config, 'BALE_BOT_TOKEN', 'test-token-not-real')
    monkeypatch.setattr(config, 'BALE_BOT_USERNAME', 'test_bot')
    messages = []
    monkeypatch.setattr(bale, 'send', lambda chat_id, text, **opts: messages.append((str(chat_id), text, opts)))
    return messages


def phone():
    return '09' + str(uuid.uuid4().int % 1_000_000_000).zfill(9)


def anonymous():
    return TestClient(main.app)


def verify_in_bot(c, number, purpose, messages, sender=9001):
    response=c.post('/api/auth/challenges', json={'phone':number,'purpose':purpose})
    assert response.status_code==200, response.text
    challenge=response.json()
    bale.process_update({'message':{'chat':{'id':sender,'type':'private'},'from':{'id':sender},'text':challenge['bot_command']}})
    bale.process_update({'message':{'chat':{'id':sender,'type':'private'},'from':{'id':sender},'contact':{'user_id':sender,'phone_number':'+98'+number[1:]}}})
    code=next(re.search(r'\b\d{6}\b', item[1]).group() for item in reversed(messages) if re.search(r'\b\d{6}\b', item[1]))
    return {'phone':number,'challenge_id':challenge['challenge_id'],'code':code}


def test_phone_validation():
    assert auth.normalize_phone('۰۹۱۲۳۴۵۶۷۸۹')=='09123456789'
    assert auth.normalize_phone('+989123456789',contact=True)=='09123456789'
    for number in ['0912345678','091234567890','08123456789','+989123456789','09abcdefghi']:
        with pytest.raises(Exception): auth.normalize_phone(number)


def test_signup_pending_admin_approval_and_ownership(client, account_env):
    c=anonymous();number=phone()
    verified=verify_in_bot(c,number,'signup',account_env,sender=uuid.uuid4().int%1_000_000_000)
    response=c.post('/api/auth/signup',json={**verified,'full_name':'کاربر جدید','password':'new-password'})
    assert response.status_code==200,response.text
    payload=response.json();assert payload['user']['status']=='pending'
    assert not payload['user']['is_admin']
    assert 'password' not in payload['user']
    c.headers['X-CSRF-Token']=payload['csrf_token']
    assert c.get('/api/jobs').status_code==403
    assert c.post('/api/sessions',json={'title':'blocked'}).status_code==403
    assert c.get('/api/admin/users').status_code==403
    assert c.get('/',follow_redirects=False).headers['location']=='/pending'
    uid=payload['user']['id']
    assert client.post(f'/api/admin/users/{uid}/status',json={'status':'approved'}).status_code==200
    assert c.get('/api/jobs').status_code==200
    assert c.get('/api/admin/users').status_code==403
    j=c.post('/api/sessions',json={'title':'private'}).json()
    assert j['id'] not in [x['id'] for x in client.get('/api/jobs').json()['items']]
    # Admin privileges do not bypass private workspace ownership.
    for endpoint in [f'/api/jobs/{j["id"]}',f'/api/jobs/{j["id"]}/segments',f'/api/jobs/{j["id"]}/audio',f'/api/jobs/{j["id"]}/download',f'/sessions/{j["id"]}',f'/jobs/{j["id"]}',f'/api/sessions/{j["id"]}/clips']:
        assert client.get(endpoint).status_code==404,endpoint
    assert client.delete('/api/jobs/'+j['id']).status_code==404
    assert client.post('/api/jobs/'+j['id']+'/cancel').status_code==404
    assert client.post(f'/api/admin/users/{uid}/status',json={'status':'suspended'}).status_code==200
    assert c.get('/api/jobs').status_code==401  # approval removal revokes login sessions


def test_csrf_logout_and_unauthorized(client):
    c=anonymous()
    assert c.get('/api/jobs').status_code==401
    assert c.get('/api/health').status_code==401
    assert c.get('/healthz').status_code==200
    token=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/sessions',json={'title':'bad'}).status_code==403
    client.headers['X-CSRF-Token']=token
    assert client.post('/api/sessions',json={'title':'bad'},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/jobs').status_code==401


def test_bale_rejects_other_contact_and_wrong_phone(client, account_env):
    c=anonymous();number=phone();sender=uuid.uuid4().int%1_000_000_000
    challenge=c.post('/api/auth/challenges',json={'phone':number,'purpose':'signup'}).json()
    envelope={'chat':{'id':sender,'type':'private'},'from':{'id':sender}}
    bale.process_update({'message':{**envelope,'text':challenge['bot_command']}})
    for contact in [{'user_id':sender+1,'phone_number':number},{'user_id':sender,'phone_number':phone()},{'phone_number':number}]:
        bale.process_update({'message':{**envelope,'contact':contact}})
        with SessionLocal() as s:
            assert s.get(BaleIdentity,number) is None
            assert not s.get(VerificationChallenge,challenge['challenge_id']).delivered


def test_code_attempts_expiry_and_purpose(client, account_env):
    c=anonymous();number=phone();payload=verify_in_bot(c,number,'signup',account_env,sender=uuid.uuid4().int%1_000_000_000)
    assert c.post('/api/auth/login/bale',json=payload).status_code==400
    wrong={**payload,'code':'999999' if payload['code']!='999999' else '111111','full_name':'آزمون کد','password':'some-password'}
    for _ in range(5): assert c.post('/api/auth/signup',json=wrong).status_code==400
    assert c.post('/api/auth/signup',json={**wrong,'code':payload['code']}).status_code==400
    with SessionLocal() as s: assert s.get(VerificationChallenge,payload['challenge_id']).attempts==5
    number2=phone();p2=verify_in_bot(c,number2,'signup',account_env,sender=uuid.uuid4().int%1_000_000_000)
    with SessionLocal() as s:
        s.get(VerificationChallenge,p2['challenge_id']).expires_at=int(time.time())-1;s.commit()
    assert c.post('/api/auth/signup',json={**p2,'full_name':'expired','password':'some-password'}).status_code==400


def test_bale_login_reset_and_replay(client, account_env):
    c=anonymous();number=phone();sender=uuid.uuid4().int%1_000_000_000
    with SessionLocal() as s:
        u=User(phone=number,full_name='حساب آزمایشی',password_hash=auth.hash_password('old-password'),status='approved')
        s.add(u);s.commit();uid=u.id
    payload=verify_in_bot(c,number,'login',account_env,sender=sender)
    response=c.post('/api/auth/login/bale',json=payload);assert response.status_code==200
    c.headers['X-CSRF-Token']=response.json()['csrf_token']
    assert c.post('/api/auth/login/bale',json=payload).status_code==400
    assert c.get('/api/jobs').status_code==200
    with SessionLocal() as s:
        s.query(VerificationChallenge).filter_by(phone=number).update({'created_at':int(time.time())-61});s.commit()
    reset=verify_in_bot(anonymous(),number,'reset',account_env,sender=sender)
    assert anonymous().post('/api/auth/password/reset',json={**reset,'password':'changed-password'}).status_code==200
    assert c.get('/api/jobs').status_code==401
    assert anonymous().post('/api/auth/login/password',json={'phone':number,'password':'old-password'}).status_code==401
    assert anonymous().post('/api/auth/login/password',json={'phone':number,'password':'changed-password'}).status_code==200
    assert anonymous().post('/api/auth/password/reset',json={**reset,'password':'again-password'}).status_code==400


def test_password_change_revokes_other_sessions(client, account_env):
    other=anonymous()
    r=other.post('/api/auth/login/password',json={'phone':client.phone,'password':'test-password'})
    assert r.status_code==200
    assert client.post('/api/auth/password/change',json={'current_password':'wrong','password':'changed-password'}).status_code==400
    r=client.post('/api/auth/password/change',json={'current_password':'test-password','password':'changed-password'})
    assert r.status_code==200
    client.headers['X-CSRF-Token']=r.json()['csrf_token']
    assert client.get('/api/jobs').status_code==200
    assert other.get('/api/jobs').status_code==401
    assert client.post(f'/api/admin/users/{client.user_id}/status',json={'status':'suspended'}).status_code==409


def test_challenge_cooldown_and_password_rate_limit(client, account_env):
    c=anonymous();number=phone()
    assert c.post('/api/auth/challenges',json={'phone':number,'purpose':'login'}).status_code==200
    assert c.post('/api/auth/challenges',json={'phone':number,'purpose':'login'}).status_code==429
    for _ in range(10):assert c.post('/api/auth/login/password',json={'phone':number,'password':'wrong'}).status_code==401
    assert c.post('/api/auth/login/password',json={'phone':number,'password':'wrong'}).status_code==429


def test_bootstrap_adopts_legacy_only_once(client, monkeypatch):
    number=phone();monkeypatch.setattr(config,'BOOTSTRAP_ADMIN_PHONE',number);monkeypatch.setattr(config,'BOOTSTRAP_ADMIN_PASSWORD','bootstrap-pass')
    with SessionLocal() as s:
        j=Job(original_name='legacy.mp3',stored_name='legacy.mp3',owner_id=None)
        s.add(j);s.commit();jid=j.id
    auth.bootstrap_admin()
    with SessionLocal() as s:
        u=s.query(User).filter_by(phone=number).one();assert u.is_admin and u.status=='approved'
        assert s.get(Job,jid).owner_id==u.id
        u.password_hash=auth.hash_password('changed-after-bootstrap');s.commit()
    auth.bootstrap_admin()
    with SessionLocal() as s:
        u=s.query(User).filter_by(phone=number).one();assert auth.check_password(u.password_hash,'changed-after-bootstrap')

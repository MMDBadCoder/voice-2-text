"""Account security primitives shared by HTTP routes and the Bale service."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from . import config, db
from .db import Job, LoginSession, RateBucket, SessionLocal, User, VerificationChallenge

COOKIE = "vazhe_session"
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_phone(value: str, *, contact: bool = False) -> str:
    phone = value.strip().translate(_DIGITS)
    if contact:
        phone = phone.replace(" ", "").replace("-", "")
        if phone.startswith("+98"):
            phone = "0" + phone[3:]
        elif phone.startswith("0098"):
            phone = "0" + phone[4:]
        elif phone.startswith("98"):
            phone = "0" + phone[2:]
    if not re.fullmatch(r"09[0-9]{9}", phone):
        raise HTTPException(400, "شماره همراه باید ۱۱ رقم و با ۰۹ شروع شود")
    return phone


def validate_password(password: str) -> str:
    if not 8 <= len(password) <= 128:
        raise HTTPException(400, "رمز عبور باید بین ۸ تا ۱۲۸ نویسه باشد")
    return password


def hash_password(password: str) -> str:
    return _hasher.hash(validate_password(password))


def check_password(encoded: str | None, password: str) -> bool:
    try:
        return _hasher.verify(encoded or _DUMMY_HASH, password) and encoded is not None
    except (VerificationError, InvalidHashError):
        return False


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache(maxsize=8)
def _secret_for(directory: str) -> bytes:
    from pathlib import Path
    path = Path(directory) / ".auth-secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another startup may be writing its newly created key.
        for _ in range(50):
            key = path.read_bytes()
            if len(key) == 32:
                return key
            time.sleep(.02)
        raise RuntimeError("Invalid authentication secret file")
    key = secrets.token_bytes(32)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def code_digest(challenge_id: str, code: str) -> str:
    return hmac.new(_secret_for(str(config.DATA_DIR)), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def rate_limit(kind: str, value: str, limit: int, seconds: int):
    now = int(time.time())
    key = kind + ":" + digest(value)
    with SessionLocal() as s:
        db.write_lock(s)
        s.query(RateBucket).filter(RateBucket.expires_at < now - 3600).delete()
        bucket = s.get(RateBucket, key)
        if bucket is None:
            bucket = RateBucket(key=key, count=0, expires_at=now + seconds)
            s.add(bucket)
        elif bucket.expires_at <= now:
            bucket.count = 0
            bucket.expires_at = now + seconds
        if bucket.count >= limit:
            raise HTTPException(429, "تعداد تلاش‌ها زیاد است؛ چند دقیقه دیگر دوباره امتحان کنید",
                                headers={"Retry-After": str(max(1, bucket.expires_at-now))})
        bucket.count += 1
        s.commit()


def client_ip(request: Request) -> str:
    # No trust in arbitrary forwarded IP headers for the rate-limit identity.
    return request.client.host if request.client else "unknown"


def lookup_session(token: str | None):
    if not token or len(token) > 200:
        return None, None
    with SessionLocal() as s:
        login = s.get(LoginSession, digest(token))
        if login is None or login.expires_at <= time.time():
            return None, None
        user = s.get(User, login.user_id)
        if user is None:
            return None, None
        return user.to_dict(), {"token_hash": login.token_hash, "csrf_token": login.csrf_token}


def require_user(request: Request, *, approved: bool = True, admin: bool = False):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "ابتدا وارد حساب خود شوید")
    if approved and user["status"] != "approved":
        raise HTTPException(403, "حساب شما هنوز اجازهٔ استفاده ندارد")
    if admin and not user["is_admin"]:
        raise HTTPException(403, "این بخش فقط برای مدیر سامانه است")
    return user


def owned_job(session, request: Request, job_id: str):
    user = require_user(request)
    job = session.query(Job).filter(Job.id == job_id, Job.owner_id == user["id"]).first()
    if job is None:
        raise HTTPException(404, "جلسه یافت نشد")
    return job


def issue_session(session, user: User):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    session.add(LoginSession(token_hash=digest(token), user_id=user.id, csrf_token=csrf,
                             expires_at=int(time.time()) + config.AUTH_SESSION_DAYS * 86400))
    return token, csrf


def login_response(user, token, csrf):
    response = JSONResponse({"user": user, "csrf_token": csrf})
    response.set_cookie(COOKIE, token, max_age=config.AUTH_SESSION_DAYS*86400,
                        httponly=True, secure=config.AUTH_COOKIE_SECURE, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


def consume_challenge(session, challenge_id, phone, purpose, code):
    challenge = session.get(VerificationChallenge, challenge_id)
    code = code.strip().translate(_DIGITS)
    if (challenge is None or challenge.phone != phone or challenge.purpose != purpose
            or challenge.used or challenge.expires_at <= time.time() or challenge.attempts >= 5
            or not challenge.delivered or not challenge.code_hash):
        raise HTTPException(400, "کد نامعتبر یا منقضی است؛ کد جدید درخواست کنید")
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_hash, code_digest(challenge.id, code)):
        session.commit()  # A failed attempt must survive the HTTP error/rollback.
        raise HTTPException(400, "کد تأیید درست نیست")
    challenge.used = 1
    return challenge


def bootstrap_admin():
    """Create once; never reset an existing admin's chosen password at startup."""
    if not config.BOOTSTRAP_ADMIN_PHONE:
        return
    phone = normalize_phone(config.BOOTSTRAP_ADMIN_PHONE)
    with SessionLocal() as s:
        db.write_lock(s)
        user = s.query(User).filter_by(phone=phone).first()
        if user is None:
            if not config.BOOTSTRAP_ADMIN_PASSWORD:
                raise RuntimeError("Bootstrap administrator password is required")
            user = User(phone=phone, full_name="مدیر سامانه", is_admin=1, status="approved",
                        password_hash=hash_password(config.BOOTSTRAP_ADMIN_PASSWORD))
            s.add(user)
            s.flush()
        if not user.is_admin:
            raise RuntimeError("Configured bootstrap phone belongs to a non-admin account")
        s.query(Job).filter(Job.owner_id.is_(None)).update({"owner_id": user.id})
        s.query(LoginSession).filter(LoginSession.expires_at < int(time.time())).delete()
        s.query(VerificationChallenge).filter(VerificationChallenge.expires_at < int(time.time())-86400).delete()
        s.commit()

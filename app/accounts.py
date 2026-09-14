"""Signup, login, recovery and administrator approval HTTP endpoints."""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from . import auth, bale, config, db, queue as qmod
from .db import AdminAudit, BaleIdentity, Job, JobStatus, LoginSession, SessionLocal, User, VerificationChallenge

router = APIRouter()


class PhoneInput(BaseModel):
    phone: str = Field(max_length=32)


class ChallengeInput(PhoneInput):
    purpose: str = Field(pattern="^(signup|login|reset)$")


class CodeInput(PhoneInput):
    challenge_id: str = Field(max_length=64)
    code: str = Field(min_length=1, max_length=12)


class SignupInput(CodeInput):
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class PasswordInput(PhoneInput):
    password: str = Field(max_length=128)


class ResetInput(CodeInput):
    password: str = Field(min_length=8, max_length=128)


class ChangeInput(BaseModel):
    current_password: str = Field(max_length=128)
    password: str = Field(min_length=8, max_length=128)


class StatusInput(BaseModel):
    status: str = Field(pattern="^(approved|suspended|pending)$")


@router.get("/api/auth/me")
def me(request: Request):
    user = auth.require_user(request, approved=False)
    return {"user": user, "csrf_token": request.state.login_session["csrf_token"]}


@router.post("/api/auth/challenges")
def create_challenge(body: ChallengeInput, request: Request):
    phone = auth.normalize_phone(body.phone)
    auth.rate_limit("otp-ip", auth.client_ip(request), 30, 600)
    auth.rate_limit("otp-phone", phone, 5, 600)
    if not config.BALE_BOT_TOKEN or not config.BALE_BOT_USERNAME:
        raise HTTPException(503, "سرویس تأیید بله هنوز آماده نیست")
    now = int(time.time())
    with SessionLocal() as s:
        db.write_lock(s)
        recent = s.query(VerificationChallenge).filter(VerificationChallenge.phone == phone,
                                                       VerificationChallenge.created_at > now-60).first()
        if recent:
            raise HTTPException(429, "برای درخواست دوبارهٔ کد، یک دقیقه صبر کنید", headers={"Retry-After": "60"})
        s.query(VerificationChallenge).filter_by(phone=phone, used=0).update({"used": 1})
        challenge = VerificationChallenge(id=secrets.token_urlsafe(24), phone=phone, purpose=body.purpose,
                                          created_at=now, expires_at=now+config.OTP_TTL_SECONDS)
        s.add(challenge)
        identity = s.get(BaleIdentity, phone)
        chat_id = identity.chat_id if identity else None
        s.commit()
        challenge_id = challenge.id
    sent = False
    if chat_id:
        try:
            sent = bale.deliver_code(challenge_id, chat_id)
        except bale.BaleUnavailable:
            # Keep a valid bot link; opening the bot lets the user retry delivery.
            pass
    return {"challenge_id": challenge_id, "expires_in": config.OTP_TTL_SECONDS, "sent": sent,
            "bot_url": f"https://ble.ir/{config.BALE_BOT_USERNAME}?start=v_{challenge_id}",
            "bot_command": f"/start v_{challenge_id}"}


def code_limits(request):
    auth.rate_limit("code-ip", auth.client_ip(request), 40, 600)


@router.post("/api/auth/signup")
def signup(body: SignupInput, request: Request):
    code_limits(request)
    phone = auth.normalize_phone(body.phone)
    name = body.full_name.strip()
    if len(name) < 2:
        raise HTTPException(400, "نام و نام خانوادگی را وارد کنید")
    encoded = auth.hash_password(body.password)
    try:
        with SessionLocal() as s:
            db.write_lock(s)
            auth.consume_challenge(s, body.challenge_id, phone, "signup", body.code)
            if s.query(User).filter_by(phone=phone).first():
                raise HTTPException(409, "برای این شماره حسابی وجود دارد؛ وارد شوید")
            user = User(phone=phone, full_name=name, password_hash=encoded, status="pending")
            s.add(user)
            s.flush()
            token, csrf = auth.issue_session(s, user)
            s.commit()
            return auth.login_response(user.to_dict(), token, csrf)
    except IntegrityError:
        raise HTTPException(409, "برای این شماره حسابی وجود دارد") from None


@router.post("/api/auth/login/password")
def password_login(body: PasswordInput, request: Request):
    phone = auth.normalize_phone(body.phone)
    auth.rate_limit("password-ip", auth.client_ip(request), 30, 600)
    auth.rate_limit("password-phone", phone, 10, 600)
    with SessionLocal() as s:
        db.write_lock(s)
        user = s.query(User).filter_by(phone=phone).first()
        if not auth.check_password(user.password_hash if user else None, body.password):
            raise HTTPException(401, "شماره یا رمز عبور درست نیست")
        token, csrf = auth.issue_session(s, user)
        s.commit()
        return auth.login_response(user.to_dict(), token, csrf)


@router.post("/api/auth/login/bale")
def bale_login(body: CodeInput, request: Request):
    code_limits(request)
    phone = auth.normalize_phone(body.phone)
    with SessionLocal() as s:
        db.write_lock(s)
        auth.consume_challenge(s, body.challenge_id, phone, "login", body.code)
        user = s.query(User).filter_by(phone=phone).first()
        if not user:
            raise HTTPException(400, "ابتدا ثبت‌نام کنید")
        token, csrf = auth.issue_session(s, user)
        s.commit()
        return auth.login_response(user.to_dict(), token, csrf)


@router.post("/api/auth/password/reset")
def reset_password(body: ResetInput, request: Request):
    code_limits(request)
    phone = auth.normalize_phone(body.phone)
    encoded = auth.hash_password(body.password)
    with SessionLocal() as s:
        db.write_lock(s)
        auth.consume_challenge(s, body.challenge_id, phone, "reset", body.code)
        user = s.query(User).filter_by(phone=phone).first()
        if not user:
            raise HTTPException(400, "حسابی برای بازیابی موجود نیست")
        user.password_hash = encoded
        s.query(LoginSession).filter_by(user_id=user.id).delete()
        s.commit()
    return {"ok": True}


@router.post("/api/auth/password/change")
def change_password(body: ChangeInput, request: Request):
    current = auth.require_user(request, approved=False)
    auth.rate_limit("change-password", current["id"], 10, 600)
    with SessionLocal() as s:
        db.write_lock(s)
        user = s.get(User, current["id"])
        if not auth.check_password(user.password_hash, body.current_password):
            raise HTTPException(400, "رمز عبور فعلی درست نیست")
        user.password_hash = auth.hash_password(body.password)
        s.query(LoginSession).filter_by(user_id=user.id).delete()
        token, csrf = auth.issue_session(s, user)
        s.commit()
        return auth.login_response(user.to_dict(), token, csrf)


@router.post("/api/auth/logout")
def logout(request: Request):
    auth.require_user(request, approved=False)
    with SessionLocal() as s:
        s.query(LoginSession).filter_by(token_hash=request.state.login_session["token_hash"]).delete()
        s.commit()
    from fastapi.responses import JSONResponse
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/", secure=config.AUTH_COOKIE_SECURE, httponly=True, samesite="lax")
    return response


@router.get("/api/admin/users")
def list_users(request: Request, search: str = "", status: str = "", offset: int = 0):
    auth.require_user(request, admin=True)
    with SessionLocal() as s:
        query = s.query(User)
        if search.strip():
            query = query.filter(db.or_(User.phone.contains(search.strip(), autoescape=True),
                                      User.full_name.contains(search.strip(), autoescape=True)))
        if status:
            query = query.filter(User.status == status)
        return {"total": query.count(), "items": [u.to_dict() for u in query.order_by(User.created_at.desc()).offset(max(0,offset)).limit(100)]}


@router.post("/api/admin/users/{user_id}/status")
def set_status(user_id: str, body: StatusInput, request: Request):
    actor = auth.require_user(request, admin=True)
    with SessionLocal() as s:
        db.write_lock(s)
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(404, "کاربر یافت نشد")
        if user.is_admin or user.id == actor["id"]:
            raise HTTPException(409, "وضعیت مدیر از این بخش تغییر نمی‌کند")
        user.status = body.status
        if body.status != "approved":
            s.query(LoginSession).filter_by(user_id=user.id).delete()
            for job in s.query(Job).filter(Job.owner_id == user.id, Job.status.in_([JobStatus.QUEUED,JobStatus.RUNNING])):
                qmod.request_cancel(job.id)
                if job.rq_job_id:
                    qmod.cancel(job.rq_job_id)
                job.stage = "canceling" if job.status == JobStatus.RUNNING else "canceled"
                job.status = JobStatus.CANCELED
        s.add(AdminAudit(actor_id=actor["id"], user_id=user.id, action=body.status))
        s.commit()
        return user.to_dict()

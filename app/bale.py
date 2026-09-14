"""Bale verification transport and a dedicated long-polling bot process.

Only self-owned contacts in private chats can establish a phone identity.
Secrets and one-time codes are never logged.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.error
import urllib.request

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from . import auth, config, db
from .db import BaleFlow, BaleIdentity, BotState, SessionLocal, VerificationChallenge

log = logging.getLogger(__name__)


class BaleUnavailable(RuntimeError):
    pass


def call(method: str, payload: dict | None = None, timeout: int = 15):
    if not config.BALE_BOT_TOKEN:
        raise BaleUnavailable("Bale is not configured")
    request = urllib.request.Request(
        f"https://tapi.bale.ai/bot{config.BALE_BOT_TOKEN}/{method}",
        data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        if not data.get("ok"):
            raise BaleUnavailable("Bale rejected the request")
        return data.get("result")
    except (OSError, ValueError) as exc:
        # Exception URLs contain the token. Do not propagate/log their text.
        raise BaleUnavailable("Bale connection unavailable") from None


def send(chat_id, text, **options):
    return call("sendMessage", {"chat_id": str(chat_id), "text": text, **options})


def deliver_code(challenge_id: str, chat_id: str):
    code = f"{secrets.randbelow(1_000_000):06d}"
    with SessionLocal() as s:
        db.write_lock(s)
        challenge = s.get(VerificationChallenge, challenge_id)
        if not challenge or challenge.used or challenge.expires_at <= time.time() or challenge.delivered:
            return False
        identity = s.get(BaleIdentity, challenge.phone)
        if not identity or identity.chat_id != str(chat_id):
            return False
        challenge.code_hash = auth.code_digest(challenge.id, code)
        # Persist before the network request, but verification also requires delivered.
        s.commit()
        purpose = {"signup": "ثبت‌نام", "login": "ورود", "reset": "بازیابی رمز"}[challenge.purpose]
    send(chat_id, f"کد {purpose} در واژه: {code}\nاعتبار: ۵ دقیقه. این کد را در اختیار دیگران نگذارید.",
         reply_markup={"remove_keyboard": True})
    with SessionLocal() as s:
        s.query(VerificationChallenge).filter_by(id=challenge_id, code_hash=auth.code_digest(challenge_id, code)).update({"delivered": 1})
        s.commit()
    return True


def process_update(update: dict):
    message = update.get("message") or {}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or sender.get("is_bot") or not sender.get("id"):
        return
    chat_id, sender_id = str(chat.get("id")), str(sender["id"])
    if chat_id != sender_id:
        return
    text = message.get("text", "")
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        challenge_id = parts[1][2:] if len(parts) == 2 and parts[1].startswith("v_") else ""
        with SessionLocal() as s:
            challenge = s.get(VerificationChallenge, challenge_id)
            valid = challenge and not challenge.used and challenge.expires_at > time.time()
            if valid:
                s.merge(BaleFlow(chat_id=chat_id, challenge_id=challenge_id, expires_at=challenge.expires_at))
                s.commit()
        if not valid:
            send(chat_id, "برای دریافت کد، ابتدا در سایت واژه درخواست ورود یا ثبت‌نام بدهید و لینک بله را باز کنید.")
            return
        send(chat_id, "برای تأیید شماره همراه، دکمهٔ زیر را بزنید و شمارهٔ خودتان را به اشتراک بگذارید.",
             reply_markup={"keyboard": [[{"text": "اشتراک شمارهٔ من", "request_contact": True}]],
                           "resize_keyboard": True, "one_time_keyboard": True})
        return
    contact = message.get("contact")
    if not contact:
        return
    # Forwarded or manually supplied contacts are not proof of phone ownership.
    if str(contact.get("user_id", "")) != sender_id or message.get("forward_from") or message.get("forward_origin"):
        send(chat_id, "فقط شمارهٔ خودتان را با دکمهٔ اشتراک شماره ارسال کنید.")
        return
    try:
        phone = auth.normalize_phone(contact.get("phone_number", ""), contact=True)
    except HTTPException:
        send(chat_id, "شمارهٔ همراه معتبر ایرانی لازم است.")
        return
    challenge_id = None
    try:
        with SessionLocal() as s:
            db.write_lock(s)
            flow = s.get(BaleFlow, chat_id)
            challenge = s.get(VerificationChallenge, flow.challenge_id) if flow else None
            if (not flow or flow.expires_at <= time.time() or not challenge or challenge.used
                    or challenge.expires_at <= time.time() or challenge.phone != phone):
                valid = False
            else:
                identity = s.get(BaleIdentity, phone)
                other = s.query(BaleIdentity).filter_by(user_id=sender_id).first()
                valid = not ((identity and identity.user_id != sender_id) or (other and other.phone != phone))
                if valid:
                    s.merge(BaleIdentity(phone=phone, user_id=sender_id, chat_id=chat_id))
                    challenge_id = challenge.id
            s.commit()
    except IntegrityError:
        valid = False
    if not valid:
        send(chat_id, "شماره با درخواست سایت مطابقت ندارد یا درخواست منقضی شده است. از سایت دوباره درخواست بدهید.")
        return
    deliver_code(challenge_id, chat_id)


def main():
    import fcntl
    config.ensure_dirs()
    lock = (config.DATA_DIR / ".bale-poller.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A Bale poller is already running")
    db.init_db()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [bale] %(message)s")
    if not config.BALE_BOT_TOKEN:
        raise SystemExit("BALE_BOT_TOKEN is required")
    webhook = call("getWebhookInfo")
    if webhook and webhook.get("url"):
        raise SystemExit("This bot has an active webhook; remove it before enabling long polling")
    log.info("Verification bot started")
    while True:
        try:
            with SessionLocal() as s:
                state = s.get(BotState, "offset")
                offset = int(state.value) if state else 0
            updates = call("getUpdates", {"offset": offset, "timeout": 20, "limit": 50,
                                          "allowed_updates": ["message"]}, timeout=30) or []
            for update in updates:
                process_update(update)
                with SessionLocal() as s:
                    s.merge(BotState(key="offset", value=str(int(update["update_id"]) + 1)))
                    s.commit()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.warning("Bot poll failed (%s); retrying", type(exc).__name__)
            time.sleep(3)


if __name__ == "__main__":
    main()

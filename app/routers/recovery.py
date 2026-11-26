"""Secure chat-history recovery between an old install and a new one.

Proof of ownership = proof of possession of the old device token: the server
issues a one-time, short-lived random phrase to the NEW install; the user types
that exact phrase as a chat message in the OLD app. Only the holder of the old
device's token can write into that device's history, so the phrase appearing
there proves possession. The claim then copies (never moves) the old device's
conversations to the new device. Single-use, expiring, idempotent.
"""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_device
from ..db import Conversation, Device, Message, RecoveryAttempt, RecoveryGrant, get_db
from ..ratelimit import check_ip_limit, client_ip

router = APIRouter(prefix="/v1/recovery", tags=["recovery"])

GRANT_TTL_MINUTES = 15
MAX_FAILED_CLAIMS = 5            # per device
CLAIM_WINDOW_MINUTES = 10
CLAIM_IP_LIMIT = 30              # claim attempts per IP per window
GLOBAL_FAILED_CLAIMS = 50        # all failed claims per window, any device/IP
MAX_FAILED_CLAIMS_PER_GRANT = 10 # a code locks after this many total failures


def _record_attempt(db: Session, device_id: int, code: str, success: bool) -> None:
    db.add(RecoveryAttempt(device_id=device_id, code=code[:40], success=success))
    db.commit()


def _check_rate_limit(db: Session, device_id: int) -> None:
    since = _now() - timedelta(minutes=CLAIM_WINDOW_MINUTES)
    fails = (
        db.query(RecoveryAttempt)
        .filter(
            RecoveryAttempt.device_id == device_id,
            RecoveryAttempt.success.is_(False),
            RecoveryAttempt.created_at >= since.replace(tzinfo=None),
        )
        .count()
    )
    if fails >= MAX_FAILED_CLAIMS:
        raise HTTPException(status_code=429, detail="too many failed attempts; wait 10 minutes")


def _check_global_backoff(db: Session) -> None:
    since = _now() - timedelta(minutes=CLAIM_WINDOW_MINUTES)
    fails = (
        db.query(RecoveryAttempt)
        .filter(
            RecoveryAttempt.success.is_(False),
            RecoveryAttempt.created_at >= since.replace(tzinfo=None),
        )
        .count()
    )
    if fails >= GLOBAL_FAILED_CLAIMS:
        raise HTTPException(
            status_code=429, detail="recovery is temporarily busy; try again in a few minutes"
        )


def _lock_if_too_many_failures(db: Session, grant: RecoveryGrant) -> None:
    if grant.status != "pending":
        return
    fails = (
        db.query(RecoveryAttempt)
        .filter(RecoveryAttempt.code == grant.code, RecoveryAttempt.success.is_(False))
        .count()
    )
    if fails >= MAX_FAILED_CLAIMS_PER_GRANT:
        grant.status = "locked"
        db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh_code() -> str:
    h = secrets.token_hex(8).upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def _get_grant(db: Session, code: str) -> RecoveryGrant:
    grant = db.query(RecoveryGrant).filter(RecoveryGrant.code == code.strip().upper()).first()
    if grant is None:
        # uniform with the wrong-device response: do not reveal code existence
        raise HTTPException(status_code=404, detail="invalid recovery code")
    if grant.status == "pending" and grant.expires_at.replace(tzinfo=timezone.utc) < _now():
        grant.status = "expired"
        db.commit()
    return grant


@router.post("/request")
def request_grant(
    request: Request,
    device: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    check_ip_limit(db, client_ip(request), "request", CLAIM_IP_LIMIT, CLAIM_WINDOW_MINUTES)
    # one active grant per new device: older pending ones expire
    db.query(RecoveryGrant).filter(
        RecoveryGrant.new_device_id == device.id, RecoveryGrant.status == "pending"
    ).update({"status": "expired"}, synchronize_session=False)
    grant = RecoveryGrant(
        code=_fresh_code(),
        new_device_id=device.id,
        expires_at=_now() + timedelta(minutes=GRANT_TTL_MINUTES),
        max_message_id=db.query(func.max(Message.id)).scalar() or 0,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return {
        "code": grant.code,
        "expires_in_minutes": GRANT_TTL_MINUTES,
        "instructions_ar": (
            "افتح تطبيق MAHDI AI القديم وابعت الكود ده كرسالة في أي محادثة، "
            "بعدين ارجع هنا واضغط استرجاع. الكود بينتهي بعد 15 دقيقة وبيشتغل مرة واحدة بس."
        ),
    }


class ClaimIn(BaseModel):
    code: str


@router.post("/claim")
def claim_grant(
    body: ClaimIn,
    request: Request,
    device: Device = Depends(current_device),
    db: Session = Depends(get_db),
):
    # layers, cheapest first: per-IP, then per-device. Global backoff is NOT
    # applied here: it is checked below only for guesses (unknown/foreign/dead
    # codes), so an attacker flooding bad guesses can never block a legitimate
    # owner who holds a pending grant on their own device.
    check_ip_limit(db, client_ip(request), "claim", CLAIM_IP_LIMIT, CLAIM_WINDOW_MINUTES)
    _check_rate_limit(db, device.id)
    try:
        grant = _get_grant(db, body.code)
    except HTTPException:
        _check_global_backoff(db)
        _record_attempt(db, device.id, body.code, False)
        raise
    if grant.new_device_id != device.id:
        # same status+detail as an unknown code: no existence leak, and the
        # failure counts toward the grant lock so rotating devices cannot
        # multiply guesses against this code
        _check_global_backoff(db)
        _record_attempt(db, device.id, grant.code, False)
        _lock_if_too_many_failures(db, grant)
        raise HTTPException(status_code=404, detail="invalid recovery code")
    if grant.status != "pending":
        # expired/locked/consumed: not an active recovery, pressure applies
        _check_global_backoff(db)
    _lock_if_too_many_failures(db, grant)
    if grant.status == "consumed":
        raise HTTPException(status_code=409, detail="code already used")
    if grant.status != "pending":
        _record_attempt(db, device.id, body.code, False)
        raise HTTPException(status_code=410, detail=f"code {grant.status}")

    # find the device whose history contains the phrase (excluding the new one).
    # Only a user-role message written AFTER the grant was created counts: it had
    # to be written through the chat endpoint with that device's bearer token.
    # (id threshold, not timestamps: sqlite string datetime compare is unreliable)
    matches = (
        db.query(Conversation.device_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(
            Message.content.contains(grant.code),
            Message.role == "user",
            Message.id > grant.max_message_id,
            Conversation.device_id != device.id,
        )
        .distinct()
        .all()
    )
    if not matches:
        _record_attempt(db, device.id, body.code, False)
        raise HTTPException(
            status_code=409,
            detail="phrase not seen in the old app yet; send it as a chat message there first",
        )
    if len(matches) > 1:
        _record_attempt(db, device.id, body.code, False)
        raise HTTPException(status_code=409, detail="phrase seen on multiple devices; request a new code")
    old_device_id = matches[0][0]

    convs = db.query(Conversation).filter(Conversation.device_id == old_device_id).all()
    n_convs = n_msgs = 0
    for conv in convs:
        new_conv = Conversation(device_id=device.id, title=conv.title, style=conv.style)
        db.add(new_conv)
        db.flush()
        n_convs += 1
        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.id)
            .all()
        )
        for m in msgs:
            db.add(
                Message(
                    conversation_id=new_conv.id,
                    role=m.role,
                    content=m.content,
                    provider=m.provider,
                    created_at=m.created_at,
                )
            )
            n_msgs += 1

    grant.status = "consumed"
    grant.old_device_id = old_device_id
    grant.consumed_at = _now()
    db.commit()
    _record_attempt(db, device.id, body.code, True)
    return {
        "status": "recovered",
        "conversations": n_convs,
        "messages": n_msgs,
        "original_untouched": True,
    }


@router.post("/status")
def grant_status(body: ClaimIn, device: Device = Depends(current_device), db: Session = Depends(get_db)):
    grant = _get_grant(db, body.code)
    if grant.new_device_id != device.id:
        # uniform with unknown code: no existence leak
        raise HTTPException(status_code=404, detail="invalid recovery code")
    return {"code": grant.code, "status": grant.status, "expires_at": grant.expires_at}

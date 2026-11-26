"""Shared client-IP extraction and per-IP request throttling.

X-Forwarded-For trust model: clients can put anything in the header they send,
so the FIRST entries are attacker-controlled. Each trusted proxy APPENDS the
IP it observed; the rightmost entry is the one Render's edge proxy added for
the direct client and the only one we trust. With no header (direct connection,
tests), fall back to the peer address.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .db import IpAttempt


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        # rightmost = appended by the trusted proxy; never the client
        return fwd.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def check_ip_limit(db: Session, ip: str, action: str, limit: int, window_minutes: int) -> None:
    """429 if the IP already made `limit` requests of this action in the window.

    Count BEFORE recording so `limit` means exactly `limit` allowed: with
    limit=10 the 11th request sees 10 recorded and is refused.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    n = (
        db.query(IpAttempt)
        .filter(
            IpAttempt.ip == ip,
            IpAttempt.action == action,
            IpAttempt.created_at >= since.replace(tzinfo=None),
        )
        .count()
    )
    if n >= limit:
        raise HTTPException(
            status_code=429,
            detail="too many requests from this network; wait and try again",
        )
    db.add(IpAttempt(ip=ip, action=action))
    db.commit()

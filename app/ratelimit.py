"""Shared client-IP extraction and per-IP request throttling.

Render (and any reverse proxy) sets X-Forwarded-For; we trust the first hop
entry. Falls back to the direct peer (TestClient: "testclient").
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .db import IpAttempt


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def check_ip_limit(db: Session, ip: str, action: str, limit: int, window_minutes: int) -> None:
    """Record this request and 429 if the IP exceeded `limit` in the window.

    The attempt is recorded BEFORE the check so throttled callers keep counting
    against themselves.
    """
    db.add(IpAttempt(ip=ip, action=action))
    db.commit()
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
    if n > limit:
        raise HTTPException(
            status_code=429,
            detail="too many requests from this network; wait and try again",
        )

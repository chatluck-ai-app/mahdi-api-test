"""Shared client-IP extraction and per-IP request throttling.

Trusted proxy chain for this deployment (verified live on mahdi-api-test):

    client -> Cloudflare edge -> Render proxy -> uvicorn

- Cloudflare SETS `CF-Connecting-IP` to the real client IP, overwriting any
  client-supplied value. This is the primary source.
- Cloudflare appends the client IP to X-Forwarded-For; Render's proxy then
  appends the Cloudflare edge IP it received the request from. So the
  RIGHTMOST XFF entry is a shared Cloudflare/Render edge address (observed
  live: alternating edge IPs) and must NOT be used. The SECOND-FROM-RIGHT
  entry is the client IP as Cloudflare reported it: the fallback.
- With neither header (direct connection, local tests) the peer address is
  all we have.

Every earlier XFF entry and any client-supplied CF-Connecting-IP on a chain
without Cloudflare is attacker-controlled and never trusted here.
"""

import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .db import IpAttempt

# Cloudflare must inject this header via a Request Header Transform Rule; the
# app only trusts edge-supplied identity headers when the secret matches.
# A client connecting directly (bypassing Cloudflare) cannot know the secret.
TRUSTED_EDGE_HEADER = "x-mahdi-edge"


def _edge_authenticated(request: Request) -> bool:
    secret = os.getenv("TRUSTED_EDGE_SECRET", "")
    return bool(secret) and request.headers.get(TRUSTED_EDGE_HEADER, "") == secret


def client_ip(request: Request) -> str:
    if _edge_authenticated(request):
        # proven to have passed the trusted edge: Cloudflare overwrote
        # CF-Connecting-IP with the real client IP there
        cf = request.headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf[:64]
        fwd = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if len(parts) >= 2:
            # rightmost is the edge's own address; the entry before it is what
            # the edge observed as the client
            return parts[-2][:64]
    # No trusted-edge proof: NEVER trust client-supplied headers (a direct
    # connection can forge CF-Connecting-IP and every XFF entry). Bucket by
    # the direct peer instead. Fail-safe: proxied traffic then shares the
    # proxy's bucket, which over-throttles rather than over-allows.
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

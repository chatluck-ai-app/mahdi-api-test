"""Shared client-IP derivation and per-IP request throttling.

Verified live chain on mahdi-api-test (2026-11-26):

    client -> Cloudflare edge -> Render proxy -> Render internal -> uvicorn

Observed X-Forwarded-For at the app: "client, cloudflare-edge(172.68.x),
render-internal(10.x)". Facts that make derivation possible WITHOUT any
injected secret:

- Every hop APPENDS what it saw; a client can only add forged entries on the
  LEFT. It can never remove or reorder the entries Cloudflare/Render append.
- So: find the RIGHTMOST Cloudflare-range entry (forged CF-range entries on
  the left are harmless); the real client is the entry immediately LEFT of it.
- Every entry to the RIGHT of that CF entry must be Cloudflare or
  private/internal. A direct-connection forger can craft "client, cf-range"
  on the left, but the entry the edge appends to its right (the forger's own
  public IP) breaks that invariant, so forgery fails closed.
- If CF-Connecting-IP is present it must EQUAL the derived client. Cloudflare
  overwrites a client-sent CF-Connecting-IP (verified live: forged values
  never reach the app through CF); a mismatch means a direct-origin forgery
  attempt.
- Anything that does not match this structure (direct connections, unknown
  chains) falls into ONE shared "unknown" bucket: fail-safe (over-throttles,
  never over-allows), and honest users on the Cloudflare path are unaffected.
- request.client.host is NEVER used: uvicorn trusts forwarded headers, so the
  peer is attacker-controlled (verified live).

Cloudflare ranges embedded from https://www.cloudflare.com/ips-v4 and
https://www.cloudflare.com/ips-v6 on 2026-11-26. If Cloudflare ever adds
ranges, legit traffic falls into the unknown bucket (fail-safe) until the
list is refreshed.
"""

import ipaddress
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .db import IpAttempt

CF_NETWORKS = [
    ipaddress.ip_network(r)
    for r in [
        # ips-v4 (2026-11-26)
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        # ips-v6 (2026-11-26)
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
        "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
    ]
]

UNKNOWN_BUCKET = "unknown"


def _is_cloudflare(addr) -> bool:
    return any(addr in net for net in CF_NETWORKS)


def _is_internal(addr) -> bool:
    return addr.is_private or addr.is_loopback or addr.is_link_local


def derive_client_ip(request: Request) -> tuple[str, str]:
    """Return (bucket, source): source is "cf-chain" or "unknown"."""
    fwd = request.headers.get("x-forwarded-for", "")
    entries = []
    for part in fwd.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            entries.append((part, ipaddress.ip_address(part)))
        except ValueError:
            return UNKNOWN_BUCKET, "unknown"  # malformed chain: trust nothing
    # rightmost Cloudflare entry that still has entries on both sides
    for i in range(len(entries) - 2, 0, -1):
        if not _is_cloudflare(entries[i][1]):
            continue
        if all(
            _is_cloudflare(addr) or _is_internal(addr) for _, addr in entries[i + 1:]
        ):
            derived = entries[i - 1][0]
            cf_header = request.headers.get("cf-connecting-ip", "").strip()
            if cf_header and cf_header != derived:
                # CF would have overwritten a forged header; mismatch = direct
                # origin forgery attempt
                return UNKNOWN_BUCKET, "unknown"
            return derived, "cf-chain"
        return UNKNOWN_BUCKET, "unknown"
    return UNKNOWN_BUCKET, "unknown"


def client_ip(request: Request) -> str:
    return derive_client_ip(request)[0]


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

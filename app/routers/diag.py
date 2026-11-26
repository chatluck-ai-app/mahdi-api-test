"""TEST-ONLY diagnostics for the mahdi-api-test service.

Disabled unless MAHDI_DIAG=1 is set in the environment (set it ONLY on the
test service; render.yaml does not set it, so it is off everywhere by
default). Every route also requires the admin token. This whole module is
meant to be deleted before any production candidate.
"""

import os

from fastapi import APIRouter, Header, HTTPException, Request

from .. import config
from ..ratelimit import client_ip

router = APIRouter(prefix="/v1/diag", tags=["diag"])


@router.get("/client-ip")
def diag_client_ip(request: Request, x_admin_token: str = Header(default="")):
    if os.getenv("MAHDI_DIAG", "") != "1":
        raise HTTPException(status_code=404, detail="not found")
    if not config.ADMIN_TOKEN or x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "chosen_ip": client_ip(request),
        "cf_connecting_ip": request.headers.get("cf-connecting-ip", ""),
        "x_forwarded_for": request.headers.get("x-forwarded-for", ""),
        "peer": request.client.host if request.client else None,
    }

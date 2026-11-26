from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..providers.router import available_providers
from .code import NET_ISOLATED, RUN_TIMEOUT

router = APIRouter(tags=["meta"])


@router.get("/")
def root():
    return {"service": "mahdi-api", "status": "ok"}


@router.get("/v1/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "up"
    except Exception:  # noqa: BLE001
        db_status = "down"
    return {
        "status": "ok",
        "version": "1.0.0",
        "db": db_status,
        "providers": available_providers(),
        "code_run": {"timeout_seconds": RUN_TIMEOUT, "network_isolated": NET_ISOLATED},
    }

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import Device, get_db


def current_device(
    authorization: str = Header(default=""), db: Session = Depends(get_db)
) -> Device:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[len("Bearer "):].strip()
    device = db.query(Device).filter(Device.token == token).first()
    if device is None:
        raise HTTPException(status_code=401, detail="invalid device token")
    return device


def device_by_token(db: Session, token: str) -> Device | None:
    if not token:
        return None
    return db.query(Device).filter(Device.token == token).first()

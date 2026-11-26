from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import Device, get_db
from ..ratelimit import check_ip_limit, client_ip

router = APIRouter(prefix="/v1/devices", tags=["devices"])

REGISTER_IP_LIMIT = 10          # registrations per IP per window
REGISTER_IP_WINDOW_MINUTES = 60


@router.post("/register")
def register(request: Request, db: Session = Depends(get_db)):
    check_ip_limit(
        db, client_ip(request), "register", REGISTER_IP_LIMIT, REGISTER_IP_WINDOW_MINUTES
    )
    device = Device()
    db.add(device)
    db.commit()
    db.refresh(device)
    return {"device_token": device.token, "created_at": device.created_at}

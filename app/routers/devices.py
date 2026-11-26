from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import Device, get_db

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post("/register")
def register(db: Session = Depends(get_db)):
    device = Device()
    db.add(device)
    db.commit()
    db.refresh(device)
    return {"device_token": device.token, "created_at": device.created_at}

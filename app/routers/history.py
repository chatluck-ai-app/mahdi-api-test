from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
import hashlib
import json

from ..auth import current_device
from ..db import Conversation, Device, ImportLog, Message, get_db

router = APIRouter(prefix="/v1/history", tags=["history"])


@router.get("")
def list_conversations(device: Device = Depends(current_device), db: Session = Depends(get_db)):
    rows = (
        db.query(Conversation)
        .filter(Conversation.device_id == device.id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
        .all()
    )
    result = []
    for conv in rows:
        last = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.id.desc())
            .first()
        )
        count = db.query(func.count(Message.id)).filter(Message.conversation_id == conv.id).scalar()
        result.append(
            {
                "id": conv.id,
                "title": conv.title,
                "style": conv.style,
                "updated_at": conv.updated_at,
                "message_count": count,
                "last_message": last.content[:200] if last else "",
            }
        )
    return {"conversations": result}


def _own_conversation(db: Session, device: Device, conversation_id: int) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.device_id != device.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.post("/import")
def import_history(
    payload: dict, device: Device = Depends(current_device), db: Session = Depends(get_db)
):
    """Restore a mahdi-history-v1 export into this device (copies, new ids).

    Idempotent: re-importing the same export into the same device is a no-op
    that reports already_imported instead of duplicating conversations.
    """
    if payload.get("format") != "mahdi-history-v1" or not isinstance(payload.get("conversations"), list):
        raise HTTPException(status_code=422, detail="not a mahdi-history-v1 export")

    canonical = json.dumps(payload["conversations"], ensure_ascii=False, sort_keys=True)
    export_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    prior = (
        db.query(ImportLog)
        .filter(ImportLog.device_id == device.id, ImportLog.export_hash == export_hash)
        .first()
    )
    if prior is not None:
        return {
            "already_imported": True,
            "imported_conversations": 0,
            "imported_messages": 0,
            "original_import": {"conversations": prior.conversations, "messages": prior.messages},
        }

    imported_convs = 0
    imported_msgs = 0
    for conv in payload["conversations"][:200]:
        if not isinstance(conv, dict):
            continue
        new_conv = Conversation(
            device_id=device.id,
            title=str(conv.get("title") or "")[:120],
            style=conv.get("style") if conv.get("style") in ("egyptian", "fusha", "coder") else "egyptian",
        )
        db.add(new_conv)
        db.flush()
        imported_convs += 1
        for m in (conv.get("messages") or [])[:2000]:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                continue
            db.add(
                Message(
                    conversation_id=new_conv.id,
                    role=m["role"],
                    content=str(m.get("content") or ""),
                    provider=str(m.get("provider") or "")[:20],
                )
            )
            imported_msgs += 1
    db.add(
        ImportLog(
            device_id=device.id,
            export_hash=export_hash,
            conversations=imported_convs,
            messages=imported_msgs,
        )
    )
    db.commit()
    return {"already_imported": False, "imported_conversations": imported_convs, "imported_messages": imported_msgs}


@router.get("/export")
def export_history(device: Device = Depends(current_device), db: Session = Depends(get_db)):
    """Full JSON export of this device's chat history (backup / portability)."""
    convs = (
        db.query(Conversation)
        .filter(Conversation.device_id == device.id)
        .order_by(Conversation.id)
        .all()
    )
    out = []
    for conv in convs:
        msgs = (
            db.query(Message)
            .filter(Message.conversation_id == conv.id)
            .order_by(Message.id)
            .all()
        )
        out.append(
            {
                "id": conv.id,
                "title": conv.title,
                "style": conv.style,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "provider": m.provider,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in msgs
                ],
            }
        )
    return {"format": "mahdi-history-v1", "conversations": out}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: int, device: Device = Depends(current_device), db: Session = Depends(get_db)
):
    conv = _own_conversation(db, device, conversation_id)
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.id.asc())
        .all()
    )
    return {
        "id": conv.id,
        "title": conv.title,
        "style": conv.style,
        "messages": [
            {"role": m.role, "content": m.content, "provider": m.provider, "created_at": m.created_at}
            for m in rows
        ],
    }


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int, device: Device = Depends(current_device), db: Session = Depends(get_db)
):
    conv = _own_conversation(db, device, conversation_id)
    db.query(Message).filter(Message.conversation_id == conv.id).delete()
    db.delete(conv)
    db.commit()
    return {"deleted": conversation_id}

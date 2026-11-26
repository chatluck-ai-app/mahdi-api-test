from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import current_device, device_by_token
from ..db import Conversation, Device, Message, SessionLocal, get_db
from ..providers import router as provider_router

router = APIRouter(prefix="/v1", tags=["chat"])

STYLE_PROMPTS = {
    "egyptian": (
        "أنت MAHDI AI، مساعد ذكاء اصطناعي بتتكلم بالعامية المصرية بشكل طبيعي وودود. "
        "جاوب بشكل مفيد ومباشر. لو طُلب منك كود، اكتبه كامل وواضح مع شرح قصير."
    ),
    "fusha": (
        "أنت MAHDI AI، مساعد ذكاء اصطناعي يتحدث باللغة العربية الفصحى الواضحة. "
        "أجب بشكل مفيد ومباشر. إذا طُلب منك كود، فاكتبه كاملًا وواضحًا مع شرح موجز."
    ),
    "coder": (
        "أنت MAHDI AI، مساعد برمجي محترف. ساعد المستخدم في كتابة الكود وتصحيحه وشرحه. "
        "اكتب الكود كاملًا وقابلًا للتشغيل داخل ``` مع ذكر اللغة، واشرح بالعربي باختصار. "
        "لو الكود يحتاج تجربة، اقترح استخدام أداة تشغيل الكود من تبويب الوكلاء."
    ),
}

HISTORY_LIMIT = 20


def normalize_style(style: str | None) -> str:
    return style if style in STYLE_PROMPTS else "egyptian"


def get_or_create_conversation(
    db: Session, device: Device, conversation_id: int | None, style: str, first_message: str
) -> Conversation:
    if conversation_id is not None:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.device_id != device.id:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conv
    conv = Conversation(device_id=device.id, title=first_message[:60], style=style)
    db.add(conv)
    db.flush()
    return conv


def history_messages(db: Session, conversation_id: int) -> list[dict]:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


def store_message(db: Session, conversation: Conversation, role: str, content: str, provider: str = ""):
    db.add(Message(conversation_id=conversation.id, role=role, content=content, provider=provider))
    conversation.updated_at = func.now()
    db.commit()


class ChatIn(BaseModel):
    message: str
    style: Literal["egyptian", "fusha", "coder"] = "egyptian"
    conversation_id: int | None = None


class ChatOut(BaseModel):
    reply: str
    conversation_id: int
    provider: str


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, device: Device = Depends(current_device), db: Session = Depends(get_db)):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="empty message")
    conv = get_or_create_conversation(db, device, body.conversation_id, body.style, message)
    store_message(db, conv, "user", message)
    messages = history_messages(db, conv.id)
    try:
        reply, provider = await provider_router.complete(messages, STYLE_PROMPTS[body.style])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    store_message(db, conv, "assistant", reply, provider)
    return ChatOut(reply=reply, conversation_id=conv.id, provider=provider)


@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket, token: str = ""):
    await websocket.accept()
    db = SessionLocal()
    try:
        device = device_by_token(db, token)
        if device is None:
            await websocket.send_json({"type": "error", "detail": "invalid device token"})
            await websocket.close(code=4401)
            return
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            message = (data.get("message") or "").strip()
            if not message:
                await websocket.send_json({"type": "error", "detail": "empty message"})
                continue
            style = normalize_style(data.get("style"))
            try:
                conv = get_or_create_conversation(
                    db, device, data.get("conversation_id"), style, message
                )
            except HTTPException as e:
                await websocket.send_json({"type": "error", "detail": e.detail})
                continue
            store_message(db, conv, "user", message)
            messages = history_messages(db, conv.id)
            parts: list[str] = []
            provider_name = ""
            try:
                async for item in provider_router.stream(messages, STYLE_PROMPTS[style]):
                    provider_name = item["provider"]
                    if item.get("error"):
                        await websocket.send_json({"type": "error", "detail": item["error"]})
                        break
                    if item["text"]:
                        parts.append(item["text"])
                        await websocket.send_json({"type": "chunk", "text": item["text"]})
            except Exception as e:  # noqa: BLE001
                await websocket.send_json({"type": "error", "detail": str(e)})
                continue
            reply = "".join(parts)
            store_message(db, conv, "assistant", reply, provider_name)
            await websocket.send_json(
                {"type": "done", "conversation_id": conv.id, "provider": provider_name}
            )
    finally:
        db.close()

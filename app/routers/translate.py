import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_device
from ..db import Device
from ..providers import router as provider_router

router = APIRouter(prefix="/v1", tags=["translate"])

STYLE_NAMES = {
    "msa": "اللغة العربية الفصحى",
    "fusha": "اللغة العربية الفصحى",
    "egyptian": "العامية المصرية الطبيعية",
}


def target_desc(target: str, style: str) -> str:
    if target == "en":
        return "natural English"
    return STYLE_NAMES.get(style, STYLE_NAMES["msa"])


def system_prompt(target: str, style: str, batch: bool) -> str:
    desc = target_desc(target, style)
    if target == "en":
        prompt = "Translate the following text into natural English."
        if batch:
            prompt += (
                " If the text has several lines, return the translation of each line on its own"
                " line, in the same order and with the same number of lines."
            )
        return prompt + " Return only the translation, no notes."
    prompt = f"ترجم النص التالي إلى {desc}."
    if batch:
        prompt += " إن كان النص عدة أسطر، أعد ترجمة كل سطر في سطر مستقل بنفس الترتيب ونفس العدد."
    return prompt + " أعد الترجمة فقط دون أي شرح أو ملاحظات."


class TranslateIn(BaseModel):
    text: str | None = None
    texts: list[str] | None = None
    target: str = "ar"
    style: str = "msa"


class TranslateOut(BaseModel):
    translation: str | None = None
    translations: list[str] | None = None
    target: str
    style: str
    provider: str


async def _one(text: str, system: str) -> str:
    translation, _ = await provider_router.complete([{"role": "user", "content": text}], system)
    return translation.strip()


@router.post("/translate", response_model=TranslateOut)
async def translate(body: TranslateIn, device: Device = Depends(current_device)):
    if body.target not in ("ar", "en"):
        raise HTTPException(status_code=422, detail="unsupported target")
    system = system_prompt(body.target, body.style, batch=body.texts is not None)

    if body.texts is not None:
        texts = [t for t in body.texts if t.strip()]
        if not texts:
            raise HTTPException(status_code=422, detail="empty texts")
        provider = ""
        joined = "\n".join(texts)
        translations: list[str] | None = None
        try:
            out, provider = await provider_router.complete(
                [{"role": "user", "content": joined}], system
            )
            lines = [l.strip() for l in out.strip().split("\n") if l.strip()]
            if len(lines) == len(texts):
                translations = lines
        except RuntimeError:
            translations = None
        if translations is None:
            # Alignment failed or provider errored: translate line by line instead.
            results = await asyncio.gather(
                *[_one(t, system) for t in texts], return_exceptions=True
            )
            failures = [r for r in results if isinstance(r, Exception)]
            if failures and len(failures) == len(results):
                raise HTTPException(status_code=503, detail=f"all providers failed: {failures[0]}")
            translations = [
                r.strip() if isinstance(r, str) else texts[i] for i, r in enumerate(results)
            ]
        return TranslateOut(
            translations=translations, target=body.target, style=body.style, provider=provider
        )

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="empty text")
    try:
        translation, provider = await provider_router.complete(
            [{"role": "user", "content": text}], system
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return TranslateOut(
        translation=translation.strip(), target=body.target, style=body.style, provider=provider
    )

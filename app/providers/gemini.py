import json

import httpx

from .. import config

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _models() -> list[str]:
    """GEMINI_MODEL may be one model or a comma-separated fallback list."""
    return [m.strip() for m in (config.GEMINI_MODEL or "").split(",") if m.strip()] or [
        "gemini-flash-lite-latest"
    ]


def _headers() -> dict:
    # Key goes in a header, never in the URL, so no exception text can carry it.
    return {"x-goog-api-key": config.GEMINI_API_KEY}


class GeminiProvider:
    name = "gemini"

    def available(self) -> bool:
        return bool(config.GEMINI_API_KEY)

    def _payload(self, messages: list[dict], system: str) -> dict:
        contents = [
            {
                "role": "user" if m["role"] == "user" else "model",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
        ]
        return {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7},
        }

    async def complete(self, messages: list[dict], system: str) -> str:
        last = "no model"
        for model in _models():
            url = f"{BASE}/{model}:generateContent"
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    r = await client.post(
                        url, json=self._payload(messages, system), headers=_headers()
                    )
                if r.status_code != 200:
                    last = f"gemini {model} http {r.status_code}"
                    continue
                data = r.json()
                text = "".join(
                    part.get("text", "")
                    for part in data["candidates"][0]["content"].get("parts", [])
                )
                if text:
                    return text
                last = f"gemini {model} empty reply"
            except Exception as e:  # noqa: BLE001
                last = f"gemini {model} {type(e).__name__}"
        raise RuntimeError(last)

    async def stream(self, messages: list[dict], system: str):
        last = "no model"
        for model in _models():
            url = f"{BASE}/{model}:streamGenerateContent?alt=sse"
            started = False
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300)) as client:
                    async with client.stream(
                        "POST", url, json=self._payload(messages, system), headers=_headers()
                    ) as r:
                        if r.status_code != 200:
                            last = f"gemini {model} http {r.status_code}"
                            continue
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                data = json.loads(line[len("data:"):].strip())
                            except json.JSONDecodeError:
                                continue
                            for cand in data.get("candidates", []):
                                for part in cand.get("content", {}).get("parts", []):
                                    text = part.get("text")
                                    if text:
                                        started = True
                                        yield text
                if started:
                    return
                last = f"gemini {model} empty reply"
            except Exception as e:  # noqa: BLE001
                if started:
                    raise RuntimeError(f"gemini {model} {type(e).__name__}")
                last = f"gemini {model} {type(e).__name__}"
        raise RuntimeError(last)

import json

import httpx

from .. import config

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


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
        url = f"{BASE}/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=self._payload(messages, system))
            r.raise_for_status()
            data = r.json()
        return "".join(
            part.get("text", "")
            for part in data["candidates"][0]["content"].get("parts", [])
        )

    async def stream(self, messages: list[dict], system: str):
        url = (
            f"{BASE}/{config.GEMINI_MODEL}:streamGenerateContent"
            f"?alt=sse&key={config.GEMINI_API_KEY}"
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300)) as client:
            async with client.stream("POST", url, json=self._payload(messages, system)) as r:
                r.raise_for_status()
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
                                yield text

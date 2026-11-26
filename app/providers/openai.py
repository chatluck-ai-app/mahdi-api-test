import json

import httpx

from .. import config

URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    name = "openai"

    def available(self) -> bool:
        return bool(config.OPENAI_API_KEY)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}

    def _payload(self, messages: list[dict], system: str) -> dict:
        return {
            "model": config.OPENAI_MODEL,
            "messages": [{"role": "system", "content": system}] + messages,
            "max_tokens": 2048,
            "temperature": 0.7,
        }

    async def complete(self, messages: list[dict], system: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(URL, headers=self._headers(), json=self._payload(messages, system))
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict], system: str):
        payload = self._payload(messages, system) | {"stream": True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300)) as client:
            async with client.stream("POST", URL, headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in event.get("choices", []):
                        text = choice.get("delta", {}).get("content")
                        if text:
                            yield text

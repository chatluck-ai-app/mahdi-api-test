import json

import httpx

from .. import config

URL = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def available(self) -> bool:
        return bool(config.ANTHROPIC_API_KEY)

    def _headers(self) -> dict:
        return {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": VERSION}

    def _payload(self, messages: list[dict], system: str) -> dict:
        return {
            "model": config.ANTHROPIC_MODEL,
            "max_tokens": 2048,
            "system": system,
            "messages": [
                {"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
                for m in messages
            ],
        }

    async def complete(self, messages: list[dict], system: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(URL, headers=self._headers(), json=self._payload(messages, system))
            r.raise_for_status()
            data = r.json()
        return "".join(block.get("text", "") for block in data.get("content", []))

    async def stream(self, messages: list[dict], system: str):
        payload = self._payload(messages, system) | {"stream": True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300)) as client:
            async with client.stream("POST", URL, headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "content_block_delta":
                        text = event.get("delta", {}).get("text")
                        if text:
                            yield text

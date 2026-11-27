from typing import AsyncIterator

import asyncio
import re

from .. import config
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .mock import FlakyProvider, MockProvider
from .openai import OpenAIProvider

_PROVIDERS = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "mock": MockProvider,
    "flaky": FlakyProvider,
}

MAX_ATTEMPTS_PER_PROVIDER = 2
RETRY_DELAY_SECONDS = 0.7

_SECRET = re.compile(r"(key=)[^&\s'\"]+|AIza[0-9A-Za-z_-]{20,}|AQ\.[0-9A-Za-z_-]{20,}")


def safe_error(err) -> str:
    """Error text that is safe to show a client: secrets removed, length capped."""
    return _SECRET.sub(lambda m: (m.group(1) or "") + "REDACTED", str(err))[:200]


def get_chain():
    chain = []
    for name in config.PROVIDER_PRIORITY:
        cls = _PROVIDERS.get(name)
        if cls is None:
            continue
        provider = cls()
        if provider.available():
            chain.append(provider)
    return chain


def available_providers() -> list[str]:
    return [p.name for p in get_chain()]


async def complete(messages: list[dict], system: str) -> tuple[str, str]:
    """Return (text, provider_name); one retry per provider, then fall back."""
    last_err: Exception | None = None
    for provider in get_chain():
        for attempt in range(MAX_ATTEMPTS_PER_PROVIDER):
            try:
                text = await provider.complete(messages, system)
                return text, provider.name
            except Exception as e:  # noqa: BLE001 - retry once, then next provider
                last_err = e
                if attempt + 1 < MAX_ATTEMPTS_PER_PROVIDER:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"all providers failed: {safe_error(last_err)}")


async def stream(messages: list[dict], system: str) -> AsyncIterator[dict]:
    """Yield {"text", "provider"} dicts, falling back before the first chunk."""
    last_err: Exception | None = None
    for provider in get_chain():
        try:
            started = False
            async for chunk in provider.stream(messages, system):
                started = True
                yield {"text": chunk, "provider": provider.name}
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            if started:
                yield {"text": "", "provider": provider.name, "error": safe_error(e)}
                return
    raise RuntimeError(f"all providers failed: {safe_error(last_err)}")

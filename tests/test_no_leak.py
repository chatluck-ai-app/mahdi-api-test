"""Provider failures must never expose the API key or provider URLs to clients."""
import asyncio
import httpx
import pytest

from app import config
from app.providers import router

FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY1234"


def _patch(monkeypatch, status):
    seen = []

    class T(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req):
            seen.append(str(req.url))
            return httpx.Response(status, json={})

    orig = httpx.AsyncClient

    class C(orig):
        def __init__(self, *a, **k):
            k.pop("transport", None)
            super().__init__(*a, transport=T(), **k)

    monkeypatch.setattr(httpx, "AsyncClient", C)
    monkeypatch.setattr(config, "GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "PROVIDER_PRIORITY", ["gemini"])
    return seen


@pytest.mark.parametrize("status", [429, 500, 403])
def test_error_text_has_no_key_or_url(monkeypatch, status):
    seen = _patch(monkeypatch, status)
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-flash-lite-latest")
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(router.complete([{"role": "user", "content": "x"}], "s"))
    msg = str(ei.value)
    assert FAKE_KEY not in msg and "http" not in msg.lower().replace(f"http {status}", "")
    assert "googleapis" not in msg and "key=" not in msg
    assert all("key=" not in u for u in seen)


def test_model_fallback_list(monkeypatch):
    _patch(monkeypatch, 429)
    monkeypatch.setattr(config, "GEMINI_MODEL", "a,b")
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(router.complete([{"role": "user", "content": "x"}], "s"))
    assert "gemini b http 429" in str(ei.value)


def test_safe_error_redacts():
    s = router.safe_error("https://x/y?key=AIzaSyABCDEFGHIJKLMNOPQRSTUV&z=1 AQ.Ab8RN6Kzz3zNEeLQZBjKk22l")
    assert "AIza" not in s and "AQ.Ab8" not in s

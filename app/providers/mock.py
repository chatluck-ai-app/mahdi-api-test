class MockProvider:
    """Deterministic provider for tests and local dev without keys."""

    name = "mock"

    def available(self) -> bool:
        return True

    async def complete(self, messages: list[dict], system: str) -> str:
        return f"رد تجريبي على: {messages[-1]['content']}"

    async def stream(self, messages: list[dict], system: str):
        text = await self.complete(messages, system)
        for word in text.split(" "):
            yield word + " "


class FlakyProvider:
    """Fails once with a transient error, then works. Test-only."""

    name = "flaky"
    _failures = 0

    def available(self) -> bool:
        return True

    async def complete(self, messages: list[dict], system: str) -> str:
        if FlakyProvider._failures < 1:
            FlakyProvider._failures += 1
            raise Exception("429 rate limit")
        return "رد بعد إعادة المحاولة"

    async def stream(self, messages: list[dict], system: str):
        if FlakyProvider._failures < 1:
            FlakyProvider._failures += 1
            raise Exception("boom before first chunk")
        yield "متدفق بعد المحاولة"

from typing import AsyncIterator, Protocol


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def complete(self, messages: list[dict], system: str) -> str: ...

    def stream(self, messages: list[dict], system: str) -> AsyncIterator[str]: ...

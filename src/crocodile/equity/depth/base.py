from __future__ import annotations

from typing import Protocol, runtime_checkable

from crocodile.equity.schema.records import DepthProfile


@runtime_checkable
class DepthSource(Protocol):
    async def snapshot(self, symbol: str) -> DepthProfile: ...

from abc import ABC, abstractmethod

from crocodile.schema.records import Record


class Sink(ABC):
    @abstractmethod
    async def put(self, record: Record) -> None: ...
    @abstractmethod
    async def flush(self) -> None: ...
    async def close(self) -> None:
        await self.flush()

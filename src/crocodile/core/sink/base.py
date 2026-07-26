from abc import ABC, abstractmethod

from crocodile.core.schema.records import Record


class Sink(ABC):
    """Where a connector's records go.

    ``put`` speaks the canonical 30-member union — the same one
    :class:`~crocodile.core.connector.Connector` emits. A sink that can only
    persist part of that union still has to accept all of it; narrowing the
    parameter per sink is how the crypto and equity forks drifted apart.
    """

    @abstractmethod
    async def put(self, record: Record) -> None: ...
    @abstractmethod
    async def flush(self) -> None: ...
    async def close(self) -> None:
        await self.flush()

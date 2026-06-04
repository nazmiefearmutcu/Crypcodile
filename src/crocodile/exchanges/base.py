from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable

from crocodile.instruments.registry import Instrument, InstrumentRegistry
from crocodile.schema.records import Record
from crocodile.sink.base import Sink


def backoff_delays(
    attempt: int,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.25,
    rand: float = 0.0,
) -> float:
    raw = min(cap, base * float(2**attempt))
    return raw * (1.0 + jitter * rand)


class Connector(ABC):
    name: str
    ws_url: str
    rest_url: str

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
    ) -> None:
        self.symbols = symbols
        self.channels = channels
        self.out = out
        self.registry = registry

    @abstractmethod
    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]: ...

    @abstractmethod
    async def list_instruments(self) -> list[Instrument]: ...

    async def backfill(
        self,
        channel: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> AsyncIterator[Record]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this an async generator)

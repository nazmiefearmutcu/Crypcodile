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

    def subscribe_channels(self) -> list[str]:
        """Return the list of WS channel strings this connector will subscribe to.

        Override in concrete connectors. Not abstract so that future connectors
        (Binance T1.9, Bybit T4.4, OKX T4.5, Coinbase T4.6) are not forced to
        implement it before they are ready; the ABC contract (appendix §2) does
        not list subscribe_channels() — only subscribe() is specified there.
        """
        raise NotImplementedError

    async def backfill(
        self,
        channel: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> AsyncIterator[Record]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this an async generator)

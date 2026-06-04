from __future__ import annotations

import asyncio
import json
import logging
import random
import traceback
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable

from crocodile.ingest.deadletter import DeadLetterQueue
from crocodile.ingest.transport import Transport
from crocodile.instruments.registry import Instrument, InstrumentRegistry
from crocodile.schema.records import Record
from crocodile.sink.base import Sink
from crocodile.util.time import now_ns

log = logging.getLogger(__name__)


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
        self.transport: Transport | None = None
        self._dlq: DeadLetterQueue = DeadLetterQueue()

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

    async def _subscribe(self, transport: Transport) -> None:
        """Send subscribe frames for cached channel list."""
        try:
            channels = self.subscribe_channels()
        except NotImplementedError:
            return
        if channels:
            frame = json.dumps(
                {"jsonrpc": "2.0", "method": "public/subscribe", "params": {"channels": channels}}
            ).encode()
            await transport.send(frame)

    async def run(self, max_reconnects: int = -1) -> None:
        """Supervised run loop.

        Connects the transport, subscribes, then drains frames into the sink.
        On exception: exponential backoff then reconnect (up to max_reconnects).
        max_reconnects=-1 means unlimited; max_reconnects=0 means no reconnect.
        Unparseable frames go to the DLQ; the loop continues.
        """
        attempt = 0
        while True:
            transport = self.transport
            if transport is None:
                raise RuntimeError("No transport configured; set conn.transport before run()")
            try:
                await transport.connect()
                await self._subscribe(transport)

                async for raw in transport:
                    local_ts = now_ns()
                    try:
                        msg = json.loads(raw)
                    except Exception as exc:
                        tb = traceback.format_exc()
                        await self._dlq.put(local_ts, raw, type(exc).__name__, tb)
                        log.debug("DLQ: unparseable frame: %s", exc)
                        continue

                    try:
                        for rec in self.normalize(msg, local_ts):
                            await self.out.put(rec)
                    except Exception as exc:
                        tb = traceback.format_exc()
                        await self._dlq.put(local_ts, raw, type(exc).__name__, tb)
                        log.debug("DLQ: normalize error: %s", exc)

                # Transport exhausted normally (StopAsyncIteration) → done
                break

            except Exception as exc:
                log.warning("Connector %s error (attempt %d): %s", self.name, attempt, exc)
                if max_reconnects == 0 or (max_reconnects > 0 and attempt >= max_reconnects):
                    raise
                delay = backoff_delays(
                    attempt, jitter=0.25, rand=random.random()
                )
                log.info("Reconnecting in %.2fs...", delay)
                await asyncio.sleep(delay)
                attempt += 1

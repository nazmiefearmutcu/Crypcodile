from __future__ import annotations

import asyncio
import json
import logging
import random
import traceback
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable

from crypcodile.ingest.deadletter import DeadLetterQueue
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry
from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink
from crypcodile.util.time import now_ns

log = logging.getLogger(__name__)


def backoff_delays(
    attempt: int,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.25,
    rand: float = 0.0,
) -> float:
    raw = min(cap, base * float(2**attempt))
    # Apply the cap AFTER jitter too: jitter can otherwise push a capped delay
    # above `cap` (e.g. raw=30, jitter=0.25, rand→1.0 → 37.5s), defeating the
    # documented ceiling during sustained reconnect failures.
    return min(cap, raw * (1.0 + jitter * rand))


async def http_get_helper(
    url: str,
    params: dict[str, Any] | None = None,
    session: aiohttp.ClientSession | None = None,
    max_retries: int = 3,
    timeout_sec: float = 10.0,
) -> Any:
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    
    async def run_req(sess: aiohttp.ClientSession) -> Any:
        for attempt in range(max_retries):
            try:
                async with sess.get(url, params=params, timeout=timeout) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 1.0))
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retries - 1:
                    raise e
                await asyncio.sleep(0.5 * (2 ** attempt))

    if session is not None:
        return await run_req(session)
    else:
        async with aiohttp.ClientSession() as sess:
            return await run_req(sess)


import aiohttp
from typing import Any

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
        self._session: aiohttp.ClientSession | None = None

    @property
    def http_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def http_get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        max_retries: int = 3,
        timeout_sec: float = 10.0,
    ) -> Any:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        for attempt in range(max_retries):
            try:
                async with self.http_session.get(url, params=params, timeout=timeout) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 1.0))
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retries - 1:
                    raise e
                await asyncio.sleep(0.5 * (2 ** attempt))

    @abstractmethod
    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]: ...

    @abstractmethod
    async def list_instruments(self) -> list[Instrument]: ...

    def subscribe_channels(self) -> list[str] | list[dict[str, str]]:
        """Return the WS channel descriptors this connector will subscribe to.

        Override in concrete connectors.  Not abstract so that future connectors
        are not forced to implement it before they are ready.

        String-arg connectors (Deribit, Binance, Bybit) return ``list[str]``.
        Dict-arg connectors (OKX) return ``list[dict[str, str]]`` because the
        OKX subscribe frame uses ``{"channel": "...", "instId": "..."}`` objects
        rather than plain topic strings.  The ABC is widened to accommodate both
        forms so the override in OKXConnector does not need a ``# type: ignore``
        suppression.
        """
        raise NotImplementedError

    @abstractmethod
    async def _subscribe(self, transport: Transport) -> None:
        """Send exchange-specific subscribe frames over *transport*.

        Each exchange uses a completely different wire format for subscription
        (Deribit: JSON-RPC 2.0 ``public/subscribe``; Binance: ``{"method":
        "SUBSCRIBE", "params": [...]}``; Bybit/OKX/Coinbase differ again —
        appendix §4 table, §3.2).  This method is therefore abstract: every
        concrete connector is responsible for composing and sending its own
        subscribe frame(s).  A connector that needs no subscription (e.g. a
        pure pull source) should implement an explicit no-op.
        """

    async def backfill(
        self,
        channel: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> AsyncIterator[Record]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this an async generator)

    async def run(self, max_reconnects: int = -1) -> None:
        """Supervised run loop.

        Connects the transport, subscribes, then drains frames into the sink.
        On exception: exponential backoff then reconnect (up to max_reconnects).
        max_reconnects=-1 means unlimited; max_reconnects=0 means no reconnect.
        Unparseable frames go to the DLQ; the loop continues.
        ``transport.close()`` is always called — on clean exit and on exception
        — via a ``try/finally`` block so that socket handles are never leaked.
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

                    if isinstance(msg, dict) and msg.get("error") is not None:
                        log.warning(
                            "%s: exchange rejected request: %s", self.name, msg["error"]
                        )
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
                delay = backoff_delays(attempt, jitter=0.25, rand=random.random())
                log.info("Reconnecting in %.2fs...", delay)
                await asyncio.sleep(delay)
                attempt += 1
            finally:
                await transport.close()
                if self._session is not None and not self._session.closed:
                    await self._session.close()

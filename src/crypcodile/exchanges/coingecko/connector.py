"""CoinGecko poll connector — the whole coin universe into the lake.

Poll-based, no WebSocket (CoinGecko has no public stream): each cycle fetches
the top ``pages * 250`` coins by market cap and emits a 24 h OHLCV candle per
coin.  Mirrors the :class:`~crypcodile.exchanges.derive.connector.DerivePollConnector`
shape — owns its ``run`` loop, ``transport = None``.

Registered in the factory as ``coingecko``; ``symbols``/``channels`` are
accepted for interface parity but ignored (the universe is the whole board).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Iterable
from typing import Any

import aiohttp

from crypcodile.exchanges.base import Connector, backoff_delays
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry
from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink
from crypcodile.util.time import now_ns

from . import normalize as norm
from .client import fetch_markets

log = logging.getLogger(__name__)

EXCHANGE = "coingecko"
_DEFAULT_POLL_INTERVAL = 60.0  # CoinGecko free tier is rate-limited; poll gently.


class CoinGeckoConnector(Connector):
    """Poll the CoinGecko coin universe into 24 h OHLCV records.

    Parameters
    ----------
    pages:
        Pages of 250 coins to pull each cycle (default ``1`` = top 250).
        ``pages=4`` ≈ the top 1000; the whole ~17k universe is ~70 pages, which
        the free tier rate-limits heavily — raise ``poll_interval`` accordingly.
    poll_interval:
        Seconds between cycles (default ``60``).
    """

    name = EXCHANGE
    ws_url = ""
    rest_url = "https://api.coingecko.com/api/v3"

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        *,
        pages: int = 1,
        vs_currency: str = "usd",
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        **kwargs: Any,
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.pages = pages
        self.vs_currency = vs_currency
        self.poll_interval = poll_interval
        self.transport = None

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        return ()

    async def _subscribe(self, transport: Transport) -> None:
        """No-op — CoinGecko is pull-only."""

    def subscribe_channels(self) -> list[str]:
        return ["ohlcv"]

    async def list_instruments(self) -> list[Instrument]:
        async with aiohttp.ClientSession() as session:
            coins = await fetch_markets(
                session, vs_currency=self.vs_currency, pages=self.pages
            )
        out: list[Instrument] = []
        for coin in coins:
            inst = norm.coin_to_instrument(coin)
            if inst is not None:
                out.append(inst)
        return out

    async def _poll_once(self) -> int:
        written = 0
        async with aiohttp.ClientSession() as session:
            coins = await fetch_markets(
                session, vs_currency=self.vs_currency, pages=self.pages
            )
        local_ts = now_ns()
        for coin in coins:
            inst = norm.coin_to_instrument(coin)
            if inst is not None:
                self.registry.add(inst)
            rec = norm.coin_to_ohlcv(coin, local_ts=local_ts, registry=self.registry)
            if rec is not None:
                await self.out.put(rec)
                written += 1
        return written

    async def run(self, max_reconnects: int = -1) -> None:
        attempt = 0
        try:
            while True:
                try:
                    n = await self._poll_once()
                    log.info("coingecko: wrote %d coin OHLCV record(s)", n)
                    attempt = 0
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "coingecko poll error (attempt %d): %s", attempt, exc
                    )
                    if max_reconnects == 0 or (
                        max_reconnects > 0 and attempt >= max_reconnects
                    ):
                        raise
                    await asyncio.sleep(
                        backoff_delays(attempt, jitter=0.25, rand=random.random())
                    )
                    attempt += 1
        finally:
            if self._session is not None and not self._session.closed:
                await self._session.close()

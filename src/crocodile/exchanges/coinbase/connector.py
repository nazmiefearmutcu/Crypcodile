"""Coinbase Advanced Trade connector — wiring (REST products + WS subscribe build).

Appendix §7:
- WS public: ``wss://ws-feed.exchange.coinbase.com``
- Subscribe format: ``{"type":"subscribe","product_ids":[...],"channels":["matches"]}``
- ``product_id`` is canonical (e.g. ``BTC-USD``); always cache ``/products``.
- Spot only: no funding/OI/liquidation channels.
- Rate limits: ~5 req/s public REST; ~100 subscriptions per connection.

Channels:
- ``trade`` / ``book_ticker`` → ``ticker`` (Coinbase ticker stream)
- ``book_delta`` / ``book_snapshot`` → ``level2`` (snapshot + incremental updates)
- ``trade`` → ``matches``

Subscribe message triggers immediate snapshot for ``level2`` channel.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import aiohttp

from crocodile.exchanges.base import Connector
from crocodile.ingest.transport import Transport
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.schema.records import Record
from crocodile.sink.base import Sink

from .normalize import normalize_message

log = logging.getLogger(__name__)

EXCHANGE = "coinbase"
WS_URL = "wss://ws-feed.exchange.coinbase.com"
REST_BASE = "https://api.coinbase.com/api/v1/brokerage"

# Mapping from canonical channel names to Coinbase WS channel strings.
# Multiple canonical channels can map to the same Coinbase channel; deduplication
# is applied in build_channels.
_CHANNEL_MAP: dict[str, str] = {
    "trade": "matches",
    "book_delta": "level2",
    "book_snapshot": "level2",
    "book_ticker": "ticker",
    "derivative_ticker": "ticker",  # spot-only fallback (no derivative data emitted)
}


def build_channels(symbols: list[str], channels: list[str]) -> list[str]:
    """Return the set of Coinbase WS channel strings for the given canonical channels.

    Deduplicates: ``book_delta`` and ``book_snapshot`` both map to ``level2``, so only
    one ``level2`` subscription is created regardless of how many canonical channels
    reference it.  The ``symbols`` argument is accepted for interface parity but
    Coinbase uses ``product_ids`` in the subscribe frame, not channel-per-symbol names.

    The ``ticker`` channel is always included whenever any channel is requested;
    it provides top-of-book BookTicker data and also fires on last trade so it
    naturally accompanies ``matches`` and ``level2`` subscriptions.

    Returns a sorted deduplicated list of Coinbase channel name strings.
    """
    result: set[str] = set()
    for ch in channels:
        mapped = _CHANNEL_MAP.get(ch)
        if mapped is not None:
            result.add(mapped)
    # Always include ticker: it provides BookTicker and last-trade data alongside
    # matches and level2.
    if result:
        result.add("ticker")
    return sorted(result)


def parse_products(raw: dict[str, Any]) -> list[Instrument]:
    """Parse the JSON response from Coinbase ``GET /products``.

    Response shape: ``{"products": [{...}]}``.
    All products are spot instruments (Coinbase Advanced Trade is spot only).

    Fields used:
    - ``product_id``       → symbol_raw (canonical Coinbase ID, e.g. ``BTC-USD``)
    - ``base_currency``    → base
    - ``quote_currency``   → quote
    - ``quote_increment``  → tick_size
    """
    out: list[Instrument] = []
    products: list[dict[str, Any]] = raw.get("products") or []
    for item in products:
        product_id: str = item["product_id"]
        base: str = item.get("base_currency", "")
        quote: str = item.get("quote_currency", "")

        tick_size: float | None = None
        qi_raw = item.get("quote_increment")
        if qi_raw:
            try:
                tick_size = float(qi_raw)
            except (ValueError, TypeError):
                pass

        out.append(
            Instrument(
                canonical=f"{EXCHANGE}:{product_id}",
                exchange=EXCHANGE,
                symbol_raw=product_id,
                kind=Kind.SPOT,  # Coinbase Advanced Trade is spot only
                base=base,
                quote=quote,
                tick_size=tick_size,
            )
        )
    return out


class CoinbaseConnector(Connector):
    """Coinbase Advanced Trade WebSocket connector (spot only).

    Subscribes to ``matches`` (trades), ``level2`` (order book), and ``ticker``
    (best bid/ask).  No funding, OI, or liquidation channels exist on Coinbase.

    The ``/products`` endpoint is used to populate the InstrumentRegistry;
    ``product_id`` is the canonical symbol on this exchange.
    """

    name = EXCHANGE
    ws_url = WS_URL
    rest_url = REST_BASE

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self._sub_channels = build_channels(symbols, channels)

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict):
            yield from normalize_message(msg, local_ts=local_ts, registry=self.registry)

    async def list_instruments(self) -> list[Instrument]:
        """Fetch products from Coinbase REST API and parse them.

        Uses ``GET /products`` (public, no auth required).
        Populates the registry with spot instruments; ``product_id`` is canonical.
        """
        async with aiohttp.ClientSession() as session:
            url = f"{REST_BASE}/products"
            async with session.get(url) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
        return parse_products(data)

    def subscribe_channels(self) -> list[str]:
        """Return the list of Coinbase channel strings this connector subscribes to."""
        return self._sub_channels

    async def _subscribe(self, transport: Transport) -> None:
        """Send a Coinbase subscribe frame.

        Coinbase subscribe format::

            {
                "type": "subscribe",
                "product_ids": ["BTC-USD", "ETH-USD"],
                "channels": ["matches", "level2", "ticker"]
            }
        """
        channels = self.subscribe_channels()
        if channels and self.symbols:
            frame = json.dumps(
                {
                    "type": "subscribe",
                    "product_ids": self.symbols,
                    "channels": channels,
                }
            ).encode()
            await transport.send(frame)

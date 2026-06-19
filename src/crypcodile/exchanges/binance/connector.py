"""Binance WebSocket connector — spot and USD-M futures.

Appendix §3.2 + §7:
- Spot WS: ``wss://stream.binance.com:9443/stream``
- USD-M futures WS: ``wss://fstream.binance.com/stream``
- Combined-stream URL format: ``<base>?streams=<topic1>/<topic2>/...``
- Subscribe frame: ``{"method": "SUBSCRIBE", "params": [...topics], "id": <int>}``
- Topics use lowercase symbol + ``@<streamName>``, e.g. ``btcusdt@aggTrade``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import aiohttp

from crypcodile.exchanges.base import Connector
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink

from .normalize import normalize_message

log = logging.getLogger(__name__)

EXCHANGE = "binance"

# WS base URLs per market
_MARKET_WS_URL: dict[str, str] = {
    "spot": "wss://stream.binance.com:9443/stream",
    "usdm": "wss://fstream.binance.com/stream",
    "coinm": "wss://dstream.binance.com/stream",
}

# REST base URLs per market
_MARKET_REST_URL: dict[str, str] = {
    "spot": "https://api.binance.com",
    "usdm": "https://fapi.binance.com",
    "coinm": "https://dapi.binance.com",
}

# Mapping from canonical channel names to Binance stream topic patterns.
# ``{sym}`` is substituted with the lowercase symbol.
# Deduplication is applied in build_channels: topics that share the same wire
# stream (e.g. derivative_ticker + funding both use @markPrice) are collapsed.
_CHANNEL_MAP: dict[str, str] = {
    "trade": "{sym}@aggTrade",
    "book_ticker": "{sym}@bookTicker",
    "book_delta": "{sym}@depth",
    "book_snapshot": "{sym}@depth",
    "derivative_ticker": "{sym}@markPrice",
    "funding": "{sym}@markPrice",       # markPrice carries funding rate
    "liquidation": "{sym}@forceOrder",
    "options_chain": "{sym}@optionMarkPrice",
}


def build_channels(
    symbols: list[str],
    channels: list[str],
    market: str = "spot",
) -> list[str]:
    """Return Binance combined-stream topic strings for the given symbols and channel kinds.

    Topics use lowercase symbol names (Binance convention), e.g.
    ``btcusdt@aggTrade``, ``btcusdt@bookTicker``.

    Deduplicates: multiple canonical channels that map to the same Binance topic
    (e.g. ``derivative_ticker`` + ``funding`` → ``@markPrice``) are collapsed to
    a single entry per symbol.

    The ``market`` parameter selects the stream-name convention; for spot and
    USD-M futures the topic patterns are identical (the URL determines the
    market), so this parameter is accepted but not used to alter topic names.
    It is kept for symmetry with Bybit's ``category`` kwarg.
    """
    seen: set[str] = set()
    result: list[str] = []
    for sym in symbols:
        sym_lower = sym.lower()
        for ch in channels:
            pattern = _CHANNEL_MAP.get(ch)
            if pattern is None:
                continue
            topic = pattern.format(sym=sym_lower)
            if topic not in seen:
                seen.add(topic)
                result.append(topic)
    return result


class BinanceConnector(Connector):
    """Binance combined-stream WebSocket connector.

    Supports ``spot`` (api.binance.com), ``usdm`` (fapi.binance.com, USD-M
    futures), and ``coinm`` (dapi.binance.com, COIN-M futures).  Defaults to
    ``spot``.

    Subscribe frame format::

        {"method": "SUBSCRIBE", "params": ["btcusdt@aggTrade", ...], "id": 1}

    The connector reuses :func:`~.normalize.normalize_message` from the existing
    binance normalizer, passing the ``venue`` as ``"binance-<market>"`` so that
    downstream code can distinguish spot from futures records.
    """

    name = EXCHANGE
    rest_url = _MARKET_REST_URL["spot"]  # overridden per-instance in __init__

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        market: str = "spot",
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.market = market
        self.ws_url = _MARKET_WS_URL.get(market, _MARKET_WS_URL["spot"])
        self.rest_url = _MARKET_REST_URL.get(market, _MARKET_REST_URL["spot"])
        self._sub_topics = build_channels(symbols, channels, market=market)
        # venue tag forwarded to normalize_message so records carry the right exchange label
        self._venue = f"binance-{market}"

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict):
            yield from normalize_message(
                msg,
                local_ts=local_ts,
                venue=self._venue,
                registry=self.registry,
            )

    def subscribe_channels(self) -> list[str]:
        """Return the list of Binance stream topic strings."""
        return self._sub_topics

    async def _subscribe(self, transport: Transport) -> None:  # pragma: no cover
        """Send a Binance SUBSCRIBE frame over the transport."""
        topics = self.subscribe_channels()
        if topics:
            frame = json.dumps(
                {"method": "SUBSCRIBE", "params": topics, "id": 1}
            ).encode()
            await transport.send(frame)

    async def list_instruments(self) -> list[Instrument]:  # pragma: no cover
        """Fetch exchange info from Binance REST and return instruments.

        Uses ``GET /api/v3/exchangeInfo`` for spot or
        ``GET /fapi/v1/exchangeInfo`` for USD-M futures.
        """
        if self.market == "spot":
            url = f"{self.rest_url}/api/v3/exchangeInfo"
        elif self.market == "usdm":
            url = f"{self.rest_url}/fapi/v1/exchangeInfo"
        else:
            url = f"{self.rest_url}/dapi/v1/exchangeInfo"

        data = await self.http_get(url)

        instruments: list[Instrument] = []
        for item in data.get("symbols", []):
            sym: str = item.get("symbol", "")
            base: str = item.get("baseAsset", "")
            quote: str = item.get("quoteAsset", "")
            status: str = item.get("status", "")
            if status not in ("TRADING", "PRE_DELIVERING", ""):
                continue

            contract_type: str = item.get("contractType", "")
            if self.market == "spot":
                kind = Kind.SPOT
            elif contract_type == "PERPETUAL":
                kind = Kind.PERPETUAL
            elif contract_type in (
                "CURRENT_QUARTER", "NEXT_QUARTER", "CURRENT_MONTH", "NEXT_MONTH"
            ):
                kind = Kind.FUTURE
            else:
                kind = Kind.SPOT

            # tick_size from filters
            tick_size: float | None = None
            for f in item.get("filters", []):
                if f.get("filterType") == "PRICE_FILTER":
                    ts_raw = f.get("tickSize")
                    if ts_raw:
                        try:
                            tick_size = float(ts_raw)
                        except (ValueError, TypeError):
                            pass
                    break

            instruments.append(
                Instrument(
                    canonical=f"{EXCHANGE}:{sym}",
                    exchange=EXCHANGE,
                    symbol_raw=sym,
                    kind=kind,
                    base=base,
                    quote=quote,
                    tick_size=tick_size,
                )
            )
        return instruments

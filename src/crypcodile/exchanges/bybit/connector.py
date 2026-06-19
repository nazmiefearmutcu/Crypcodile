"""Bybit V5 connector — wiring (REST instruments + WS subscribe build).

Appendix §7:
- WS public endpoints differ by category:
  ``wss://stream.bybit.com/v5/public/{spot|linear|inverse|option}``
- Subscribe format: ``{"op": "subscribe", "args": ["publicTrade.BTCUSDT"]}``
  (dot-delimited topic.symbol)
- REST: ``https://api.bybit.com/v5``
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
from crypcodile.util.time import ms_to_ns

from .normalize import normalize_message

log = logging.getLogger(__name__)

EXCHANGE = "bybit"
REST_BASE = "https://api.bybit.com/v5"

# Mapping from canonical channel names to Bybit topic patterns.
# ``{sym}`` is substituted with the symbol; ``{depth}`` with the orderbook depth.
_CHANNEL_MAP: dict[str, str] = {
    "trade": "publicTrade.{sym}",
    "book_delta": "orderbook.50.{sym}",
    "book_snapshot": "orderbook.50.{sym}",
    "derivative_ticker": "tickers.{sym}",
    "options_chain": "tickers.{sym}",
    "funding": "tickers.{sym}",    # live funding derived from ticker stream
    "book_ticker": "tickers.{sym}",
    "liquidation": "liquidation.{sym}",
}

# Categories that require separate WS endpoints
_CATEGORY_WS_URL: dict[str, str] = {
    "spot": "wss://stream.bybit.com/v5/public/spot",
    "linear": "wss://stream.bybit.com/v5/public/linear",
    "inverse": "wss://stream.bybit.com/v5/public/inverse",
    "option": "wss://stream.bybit.com/v5/public/option",
}


def build_channels(symbols: list[str], channels: list[str], category: str = "linear") -> list[str]:
    """Return Bybit topic strings for the given symbols and channel kinds.

    Deduplicates: multiple canonical channels that map to the same Bybit topic
    (e.g. ``derivative_ticker``/``funding``/``book_ticker`` all map to
    ``tickers.{sym}``) are collapsed.
    """
    result: set[str] = set()
    for sym in symbols:
        for ch in channels:
            pattern = _CHANNEL_MAP.get(ch)
            if pattern is not None:
                result.add(pattern.format(sym=sym))
    return sorted(result)


def parse_instruments(raw: dict[str, Any], category: str = "linear") -> list[Instrument]:
    """Parse the JSON response from Bybit ``/v5/market/instruments-info``.

    Supports ``linear`` (perp + dated futures), ``inverse``, ``option``,
    and ``spot`` categories. The response shape is:
    ``{"result": {"list": [...instrument dicts...]}}``.
    """
    out: list[Instrument] = []
    result: dict[str, Any] = raw.get("result") or {}
    items: list[dict[str, Any]] = result.get("list") or []

    for item in items:
        sym: str = item["symbol"]
        base: str = item.get("baseCoin", "")
        quote: str = item.get("quoteCoin", "USD")
        settle: str | None = item.get("settleCoin")
        contract_type: str = item.get("contractType", "")
        options_type: str | None = item.get("optionsType")

        # Determine kind
        if category == "option" or options_type:
            kind = Kind.OPTION
        elif "Perpetual" in contract_type:
            kind = Kind.PERPETUAL
        elif "Future" in contract_type or category in ("linear", "inverse"):
            kind = Kind.FUTURE
        elif category == "spot":
            kind = Kind.SPOT
        else:
            kind = Kind.PERPETUAL

        # Option-specific fields
        strike: float | None = None
        expiry: int | None = None
        opt_type: str | None = None

        if kind == Kind.OPTION:
            strike_raw = item.get("strikePrice")
            if strike_raw:
                try:
                    strike = float(strike_raw)
                except ValueError:
                    pass

            delivery_raw = item.get("deliveryTime")
            if delivery_raw:
                try:
                    expiry = ms_to_ns(int(delivery_raw))
                except (ValueError, TypeError):
                    pass

            if options_type:
                lowered = options_type.lower()
                opt_type = "C" if lowered == "call" else "P" if lowered == "put" else None

        # tick_size from priceFilter or top-level tickSize
        tick_size: float | None = None
        price_filter: dict[str, Any] | None = item.get("priceFilter")
        if price_filter:
            ts_raw = price_filter.get("tickSize")
        else:
            ts_raw = item.get("tickSize")
        if ts_raw:
            try:
                tick_size = float(ts_raw)
            except (ValueError, TypeError):
                pass

        out.append(
            Instrument(
                canonical=f"{EXCHANGE}:{sym}",
                exchange=EXCHANGE,
                symbol_raw=sym,
                kind=kind,
                base=base,
                quote=quote,
                strike=strike,
                expiry=expiry,
                opt_type=opt_type,
                tick_size=tick_size,
                settlement_currency=settle,
            )
        )
    return out


class BybitConnector(Connector):
    """Bybit V5 WebSocket connector.

    ``category`` selects the public WS endpoint:
    ``spot`` | ``linear`` | ``inverse`` | ``option``.
    Defaults to ``linear`` (USDT-margined perpetuals).
    """

    name = EXCHANGE
    rest_url = REST_BASE

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        category: str = "linear",
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.category = category
        self.ws_url = _CATEGORY_WS_URL.get(category, _CATEGORY_WS_URL["linear"])
        self._sub_topics = build_channels(symbols, channels, category=category)

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict):
            yield from normalize_message(msg, local_ts=local_ts, venue=self.name,
                                         registry=self.registry)

    async def list_instruments(self) -> list[Instrument]:  # pragma: no cover
        """Fetch instruments from Bybit REST API for this connector's category."""
        instruments: list[Instrument] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            url = f"{REST_BASE}/market/instruments-info"
            data = await self.http_get(url, params=params)
            instruments.extend(parse_instruments(data, category=self.category))
            next_cursor: str = (data.get("result") or {}).get("nextPageCursor", "")
            if not next_cursor:
                break
            cursor = next_cursor
        return instruments

    def subscribe_channels(self) -> list[str]:
        return self._sub_topics

    async def _subscribe(self, transport: Transport) -> None:  # pragma: no cover
        """Send a Bybit V5 subscribe frame."""
        topics = self.subscribe_channels()
        if topics:
            frame = json.dumps({"op": "subscribe", "args": topics}).encode()
            await transport.send(frame)

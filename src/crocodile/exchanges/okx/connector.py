"""OKX V5 connector — wiring (REST instruments + WS subscribe build).

Appendix §7:
- WS public endpoint: ``wss://ws.okx.com:8443/ws/v5/public``
  Region overrides: ``wss://ws.us.okx.com:8443/ws/v5/public`` (US/AU),
  ``wss://ws.eea.okx.com:8443/ws/v5/public`` (EU).
- Subscribe format: ``{"op":"subscribe","args":[{"channel":"trades","instId":"BTC-USDT"}]}``
  (each arg is a dict, not a plain string — differs from Bybit/Binance).
- REST: ``https://openapi.okx.com/api/v5`` (region: ``us.okx.com``,
  ``eea.okx.com``).
- Instrument endpoint: ``/public/instruments?instType={SPOT|MARGIN|SWAP|FUTURES|OPTION}``
  Response: ``{"code":"0","data":[...]}``; fields: ``instType``, ``instId``,
  ``baseCcy``, ``quoteCcy``, ``settleCcy``, ``stk`` (strike, option only),
  ``expTime`` (expiry ms, option only), ``optType`` (``C``/``P``), ``tickSz``.
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
from crocodile.util.time import ms_to_ns

from .normalize import normalize_message

log = logging.getLogger(__name__)

EXCHANGE = "okx"
REST_BASE = "https://openapi.okx.com/api/v5"

# Region-specific base URLs (appendix §7 critical quirk)
_REGION_WS_URL: dict[str, str] = {
    "global": "wss://ws.okx.com:8443/ws/v5/public",
    "us": "wss://ws.us.okx.com:8443/ws/v5/public",
    "eu": "wss://ws.eea.okx.com:8443/ws/v5/public",
}
_REGION_REST_URL: dict[str, str] = {
    "global": "https://openapi.okx.com/api/v5",
    "us": "https://us.okx.com/api/v5",
    "eu": "https://eea.okx.com/api/v5",
}

# Mapping from canonical channel names to OKX channel strings.
# OKX uses a dict-per-arg subscribe format: {"channel": "...", "instId": "..."}.
_CHANNEL_MAP: dict[str, str] = {
    "trade": "trades",
    "book_delta": "books",
    "book_snapshot": "books",
    "derivative_ticker": "tickers",
    "funding": "funding-rate",
    "open_interest": "open-interest",
    "liquidation": "liq-orders",
    "options_chain": "option-summary",
    "book_ticker": "bbo-tbt",
}


def build_channels(symbols: list[str], channels: list[str]) -> list[dict[str, str]]:
    """Return OKX subscribe arg dicts for the given symbols and channel kinds.

    Each element is ``{"channel": "<okx_channel>", "instId": "<sym>"}`` as
    required by the OKX V5 subscribe frame.  Deduplicates entries where
    multiple canonical channels map to the same OKX channel for a given symbol.

    Returns a list of arg dicts (not topic strings like Bybit/Binance) because
    OKX subscribe format differs: ``{"op":"subscribe","args":[{"channel":...,"instId":...}]}``.
    """
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for sym in symbols:
        for ch in channels:
            okx_ch = _CHANNEL_MAP.get(ch)
            if okx_ch is None:
                continue
            key = (okx_ch, sym)
            if key not in seen:
                seen.add(key)
                result.append({"channel": okx_ch, "instId": sym})
    return result


def parse_instruments(raw: dict[str, Any]) -> list[Instrument]:
    """Parse the JSON response from OKX ``/public/instruments``.

    Response shape: ``{"code":"0","data":[...instrument dicts...]}``

    Supports SWAP (perpetuals), FUTURES, SPOT, and OPTION instrument types.
    """
    out: list[Instrument] = []
    items: list[dict[str, Any]] = raw.get("data") or []

    for item in items:
        inst_type: str = item.get("instType", "")
        sym: str = item["instId"]
        base: str = item.get("baseCcy", "")
        quote: str = item.get("quoteCcy", "")
        settle: str | None = item.get("settleCcy") or None

        # Determine kind from instType
        inst_type_upper = inst_type.upper()
        if inst_type_upper == "SWAP":
            kind = Kind.PERPETUAL
        elif inst_type_upper == "FUTURES":
            kind = Kind.FUTURE
        elif inst_type_upper == "OPTION":
            kind = Kind.OPTION
        else:
            kind = Kind.SPOT

        # Option-specific fields
        strike: float | None = None
        expiry: int | None = None
        opt_type: str | None = None

        if kind == Kind.OPTION:
            stk_raw = item.get("stk")
            if stk_raw:
                try:
                    strike = float(stk_raw)
                except (ValueError, TypeError):
                    pass

            exp_raw = item.get("expTime")
            if exp_raw:
                try:
                    expiry = ms_to_ns(int(exp_raw))
                except (ValueError, TypeError):
                    pass

            raw_opt_type = item.get("optType", "")
            if raw_opt_type.upper() in ("C", "CALL"):
                opt_type = "C"
            elif raw_opt_type.upper() in ("P", "PUT"):
                opt_type = "P"

        # tick_size from tickSz
        tick_size: float | None = None
        ts_raw = item.get("tickSz")
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


class OKXConnector(Connector):
    """OKX V5 WebSocket connector.

    ``region`` selects the public WS and REST endpoints:
    ``global`` (default) | ``us`` | ``eu``.

    OKX subscribe format uses dict args:
    ``{"op":"subscribe","args":[{"channel":"trades","instId":"BTC-USDT"}]}``.
    """

    name = EXCHANGE
    rest_url = REST_BASE

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        region: str = "global",
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.region = region
        self.ws_url = _REGION_WS_URL.get(region, _REGION_WS_URL["global"])
        self._rest_base = _REGION_REST_URL.get(region, _REGION_REST_URL["global"])
        self._sub_args = build_channels(symbols, channels)

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict):
            yield from normalize_message(msg, local_ts=local_ts, venue=self.name,
                                         registry=self.registry)

    async def list_instruments(self) -> list[Instrument]:
        """Fetch instruments from OKX REST API.

        Fetches SWAP, FUTURES, SPOT, and OPTION instrument types and merges
        them into a single list.  Each type requires a separate REST call.
        """
        instruments: list[Instrument] = []
        inst_types = ["SWAP", "FUTURES", "SPOT", "OPTION"]
        async with aiohttp.ClientSession() as session:
            for inst_type in inst_types:
                url = f"{self._rest_base}/public/instruments"
                params: dict[str, str] = {"instType": inst_type}
                try:
                    async with session.get(url, params=params) as resp:
                        resp.raise_for_status()
                        data: dict[str, Any] = await resp.json()
                    instruments.extend(parse_instruments(data))
                except Exception as exc:
                    log.warning("OKX: failed to fetch %s instruments: %s", inst_type, exc)
        return instruments

    def subscribe_channels(self) -> list[dict[str, str]]:
        """Return the list of OKX subscribe arg dicts."""
        return self._sub_args

    async def _subscribe(self, transport: Transport) -> None:
        """Send an OKX V5 subscribe frame."""
        args = self.subscribe_channels()
        if args:
            frame = json.dumps({"op": "subscribe", "args": args}).encode()
            await transport.send(frame)

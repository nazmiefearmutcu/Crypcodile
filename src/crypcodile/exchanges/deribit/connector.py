"""Deribit connector — wiring (REST instruments + WS subscribe build)."""

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

EXCHANGE = "deribit"
REST_BASE = "https://www.deribit.com/api/v2"

# Unauthorized-safe throttled interval; .raw channels require authentication and
# ticker.{sym} without an interval is silently accepted but streams nothing.
_WS_INTERVAL = "100ms"

# Mapping from canonical channel names used by callers to Deribit WS channel patterns.
_CHANNEL_MAP: dict[str, str] = {
    "trade": "trades.{sym}." + _WS_INTERVAL,
    "book_delta": "book.{sym}." + _WS_INTERVAL,
    "book_snapshot": "book.{sym}." + _WS_INTERVAL,
    "derivative_ticker": "ticker.{sym}." + _WS_INTERVAL,
    "options_chain": "ticker.{sym}." + _WS_INTERVAL,
    "funding": "ticker.{sym}." + _WS_INTERVAL,
}


def build_channels(symbols: list[str], channels: list[str]) -> list[str]:
    """Return the list of Deribit WS channel strings for the given symbols and channel kinds.

    Deduplicates: if both 'book_delta' and 'book_snapshot' appear they map to the same
    wire channel and are collapsed.
    """
    result: set[str] = set()
    for sym in symbols:
        for ch in channels:
            pattern = _CHANNEL_MAP.get(ch)
            if pattern is not None:
                result.add(pattern.format(sym=sym))
    return sorted(result)


def parse_instruments(raw: dict[str, Any]) -> list[Instrument]:
    """Parse the JSON response from Deribit public/get_instruments."""
    out: list[Instrument] = []
    for item in raw.get("result", []):
        name: str = item["instrument_name"]
        kind_str: str = item.get("kind", "future")
        base: str = item.get("base_currency", "")
        quote: str = item.get("quote_currency", "USD")

        if kind_str == "spot":
            kind = Kind.SPOT
        elif kind_str == "future" and "PERPETUAL" in name.upper():
            kind = Kind.PERPETUAL
        elif kind_str == "future":
            kind = Kind.FUTURE
        elif kind_str == "option":
            kind = Kind.OPTION
        else:
            kind = Kind.FUTURE

        # Option-specific fields
        strike: float | None = item.get("strike")
        expiration_timestamp: int | None = item.get("expiration_timestamp")
        expiry_ns: int | None = (
            ms_to_ns(expiration_timestamp) if expiration_timestamp is not None else None
        )

        option_type_raw: str | None = item.get("option_type")
        opt_type: str | None = None
        if option_type_raw is not None:
            lowered = option_type_raw.lower()
            if lowered == "call":
                opt_type = "C"
            elif lowered == "put":
                opt_type = "P"
            else:
                log.warning(
                    "parse_instruments: unexpected option_type %r for %s; skipping instrument",
                    option_type_raw,
                    name,
                )
                continue

        tick_size: float | None = item.get("tick_size")
        contract_size: float | None = item.get("contract_size")
        settlement_currency: str | None = item.get("settlement_currency")

        out.append(
            Instrument(
                canonical=f"{EXCHANGE}:{name}",
                exchange=EXCHANGE,
                symbol_raw=name,
                kind=kind,
                base=base,
                quote=quote,
                strike=strike,
                expiry=expiry_ns,
                opt_type=opt_type,
                tick_size=tick_size,
                contract_size=contract_size,
                settlement_currency=settlement_currency,
            )
        )
    return out


class DeribitConnector(Connector):
    """Deribit WS connector."""

    name = EXCHANGE
    ws_url = "wss://www.deribit.com/ws/api/v2"
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

    async def list_instruments(self) -> list[Instrument]:  # pragma: no cover
        """Fetch instruments from Deribit REST API and parse them."""
        async with aiohttp.ClientSession() as session:
            # Fetch all currencies x kinds (§3.1: BTC|ETH|SOL|USDC)
            instruments: list[Instrument] = []
            for currency in ("BTC", "ETH", "SOL", "USDC"):
                for kind in ("future", "option", "spot", "future_combo", "option_combo"):
                    url = f"{REST_BASE}/public/get_instruments"
                    params = {"currency": currency, "kind": kind, "expired": "false"}
                    async with session.get(url, params=params) as resp:
                        resp.raise_for_status()
                        data: dict[str, Any] = await resp.json()
                        instruments.extend(parse_instruments(data))
            return instruments

    def subscribe_channels(self) -> list[str]:
        """Return the list of Deribit channel strings this connector will subscribe to."""
        return self._sub_channels

    async def _subscribe(self, transport: Transport) -> None:  # pragma: no cover
        """Send a Deribit JSON-RPC 2.0 ``public/subscribe`` frame."""
        channels = self.subscribe_channels()
        if channels:
            frame = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "public/subscribe",
                    "params": {"channels": channels},
                }
            ).encode()
            await transport.send(frame)

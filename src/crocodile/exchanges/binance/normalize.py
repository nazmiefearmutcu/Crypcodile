from collections.abc import Iterable
from typing import Any

from crocodile.exchanges.binance.book import normalize_depth
from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.enums import Side
from crocodile.schema.records import (
    BookTicker,
    DerivativeTicker,
    Funding,
    Liquidation,
    Record,
    Trade,
)
from crocodile.util.time import ms_to_ns


def normalize_message(
    msg: dict[str, Any],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None = None,
) -> Iterable[Record]:
    """Normalize a Binance combined-stream message to canonical records.

    Handles:
    - @aggTrade -> Trade
    - @bookTicker -> BookTicker
    - @markPrice -> DerivativeTicker + Funding
    - @forceOrder -> Liquidation
    - @depth -> BookDelta (via normalize_depth)
    """
    stream: str = msg.get("stream", "")
    data: dict[str, Any] = msg.get("data", {})

    if "@aggTrade" in stream:
        raw_symbol: str = data["s"]
        # m=true means buyer is maker, so the taker sold -> SELL
        side = Side.SELL if data["m"] else Side.BUY
        # Use T (trade time) not E (event time) for exchange_ts
        exchange_ts = ms_to_ns(data.get("T") or data["E"])
        # Canonical symbol via registry or fallback
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        yield Trade(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            id=str(data["a"]),
            price=float(data["p"]),
            amount=float(data["q"]),
            side=side,
        )

    elif "@bookTicker" in stream:
        raw_symbol = data["s"]
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        yield BookTicker(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=None,
            local_ts=local_ts,
            bid_px=float(data["b"]),
            bid_sz=float(data["B"]),
            ask_px=float(data["a"]),
            ask_sz=float(data["A"]),
            update_id=data.get("u"),
        )

    elif "@markPrice" in stream:
        raw_symbol = data["s"]
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        exchange_ts = ms_to_ns(data["E"])
        funding_ts_raw = data.get("T")
        yield DerivativeTicker(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            mark_price=float(data["p"]),
            index_price=float(data["i"]),
            funding_rate=float(data["r"]),
            funding_timestamp=ms_to_ns(funding_ts_raw) if funding_ts_raw is not None else None,
        )
        yield Funding(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            funding_rate=float(data["r"]),
            funding_timestamp=ms_to_ns(funding_ts_raw) if funding_ts_raw is not None else None,
        )

    elif "@forceOrder" in stream:
        order: dict[str, Any] = data["o"]
        raw_symbol = order["s"]
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        raw_side = order["S"]
        side = Side.BUY if raw_side == "BUY" else Side.SELL if raw_side == "SELL" else Side.UNKNOWN
        yield Liquidation(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=ms_to_ns(order["T"]),
            local_ts=local_ts,
            price=float(order["ap"]),  # average execution price
            amount=float(order["q"]),
            side=side,
        )

    elif "@depth" in stream:
        yield from normalize_depth(msg, local_ts=local_ts, venue=venue, registry=registry)

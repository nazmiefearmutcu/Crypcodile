from collections.abc import Iterable
from typing import Any

from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.enums import Side
from crocodile.schema.records import BookTicker, Record, Trade
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

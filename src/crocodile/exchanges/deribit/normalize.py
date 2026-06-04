from collections.abc import Iterable
from typing import Any

from crocodile.schema.enums import Side
from crocodile.schema.records import BookDelta, BookSnapshot, Liquidation, Record, Trade
from crocodile.util.time import ms_to_ns

EXCHANGE = "deribit"


def _levels(rows: list[list[Any]]) -> list[tuple[float, float]]:
    out = []
    for action, price, amount in rows:
        out.append((float(price), 0.0 if action == "delete" else float(amount)))
    return out


def _side(direction: str) -> Side:
    return Side.BUY if direction == "buy" else Side.SELL if direction == "sell" else Side.UNKNOWN


def normalize_message(msg: dict[str, Any], local_ts: int) -> Iterable[Record]:
    params: dict[str, Any] = msg.get("params") or {}
    channel: str = params.get("channel", "")
    data: Any = params.get("data")
    if channel.startswith("trades."):
        for t in (data or []):
            sym = t["instrument_name"]
            side = _side(t["direction"])
            yield Trade(
                exchange=EXCHANGE,
                symbol=f"{EXCHANGE}:{sym}",
                symbol_raw=sym,
                exchange_ts=ms_to_ns(t["timestamp"]),
                local_ts=local_ts,
                id=str(t["trade_id"]),
                price=float(t["price"]),
                amount=float(t["amount"]),
                side=side,
                liquidation=t.get("liquidation"),
            )
            if t.get("liquidation"):
                yield Liquidation(
                    exchange=EXCHANGE,
                    symbol=f"{EXCHANGE}:{sym}",
                    symbol_raw=sym,
                    exchange_ts=ms_to_ns(t["timestamp"]),
                    local_ts=local_ts,
                    price=float(t["price"]),
                    amount=float(t["amount"]),
                    side=side,
                    id=str(t["trade_id"]),
                )
    if channel.startswith("book."):
        d: dict[str, Any] = data or {}
        sym = d["instrument_name"]
        common: dict[str, Any] = dict(
            exchange=EXCHANGE,
            symbol=f"{EXCHANGE}:{sym}",
            symbol_raw=sym,
            exchange_ts=ms_to_ns(d["timestamp"]),
            local_ts=local_ts,
            bids=_levels(d.get("bids", [])),
            asks=_levels(d.get("asks", [])),
        )
        if d.get("type") == "snapshot":
            yield BookSnapshot(
                **common,
                depth=len(d.get("bids", [])) + len(d.get("asks", [])),
                sequence_id=d.get("change_id"),
                is_snapshot=True,
            )
        else:
            yield BookDelta(
                **common,
                seq_id=d.get("change_id"),
                prev_seq_id=d.get("prev_change_id"),
                is_snapshot=False,
            )

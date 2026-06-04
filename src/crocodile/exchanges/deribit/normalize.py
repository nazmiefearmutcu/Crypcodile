from collections.abc import Iterable
from typing import Any

from crocodile.schema.enums import Side
from crocodile.schema.records import Liquidation, Record, Trade
from crocodile.util.time import ms_to_ns

EXCHANGE = "deribit"


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

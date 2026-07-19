"""Pure CoinGecko ``/coins/markets`` row -> record transforms."""

from __future__ import annotations

from typing import Any

from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.records import OHLCV

EXCHANGE = "coingecko"
INTERVAL = "24h"


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical(registry: InstrumentRegistry | None, symbol_raw: str) -> str:
    if registry is not None:
        inst = registry.get_raw(EXCHANGE, symbol_raw)
        if inst is not None:
            return inst.canonical
    return f"{EXCHANGE}:{symbol_raw}"


def coin_to_instrument(coin: dict[str, Any]) -> Instrument | None:
    """A coin's unique ``id`` becomes the raw symbol; its ticker is the base."""
    coin_id = coin.get("id")
    if not coin_id:
        return None
    return Instrument(
        canonical=f"{EXCHANGE}:{coin_id}",
        exchange=EXCHANGE,
        symbol_raw=str(coin_id),
        kind=Kind.SPOT,
        base=str(coin.get("symbol") or "").upper(),
        quote="USD",
    )


def coin_to_ohlcv(
    coin: dict[str, Any],
    *,
    local_ts: int,
    registry: InstrumentRegistry | None = None,
) -> OHLCV | None:
    """Build a 24 h OHLCV candle from a CoinGecko coin market row.

    ``close`` is the live price; ``high``/``low`` are the API's 24 h extremes
    (falling back to the price when absent); ``open`` is reconstructed from the
    24 h percentage change so the candle is a genuine day bar, not a flat line;
    ``volume`` is 24 h volume.  Returns ``None`` when there is no usable price.
    """
    coin_id = coin.get("id")
    close = _f(coin.get("current_price"))
    if not coin_id or close is None:
        return None
    high = _f(coin.get("high_24h"))
    low = _f(coin.get("low_24h"))
    pct = _f(coin.get("price_change_percentage_24h"))
    if pct is not None and pct != -100.0:
        open_ = close / (1.0 + pct / 100.0)
    else:
        open_ = close
    volume = _f(coin.get("total_volume")) or 0.0
    symbol_raw = str(coin_id)
    return OHLCV(
        exchange=EXCHANGE,
        symbol=_canonical(registry, symbol_raw),
        symbol_raw=symbol_raw,
        exchange_ts=None,
        local_ts=local_ts,
        interval=INTERVAL,
        open=open_,
        high=high if high is not None else max(open_, close),
        low=low if low is not None else min(open_, close),
        close=close,
        volume=volume,
    )

"""Unit tests for the pure CoinGecko coin -> record transforms."""

from __future__ import annotations

from crypcodile.exchanges.coingecko import normalize as norm
from crypcodile.instruments.registry import Kind
from crypcodile.schema.records import OHLCV

LOCAL_TS = 1_700_000_000_000_000_000

# A trimmed real /coins/markets row.
BTC = {
    "id": "bitcoin",
    "symbol": "btc",
    "name": "Bitcoin",
    "current_price": 64547.0,
    "high_24h": 64866.0,
    "low_24h": 64019.0,
    "total_volume": 16_391_495_192,
    "market_cap": 1_294_420_594_834,
    "market_cap_rank": 1,
    "price_change_percentage_24h": 0.73,
}


def test_coin_to_instrument_uses_id_as_raw():
    inst = norm.coin_to_instrument(BTC)
    assert inst is not None
    assert inst.canonical == "coingecko:bitcoin"
    assert inst.symbol_raw == "bitcoin"  # unique id, not the ambiguous ticker
    assert inst.base == "BTC"
    assert inst.quote == "USD"
    assert inst.kind is Kind.SPOT


def test_coin_without_id_returns_none():
    assert norm.coin_to_instrument({"symbol": "btc"}) is None


def test_coin_to_ohlcv_builds_real_day_bar():
    rec = norm.coin_to_ohlcv(BTC, local_ts=LOCAL_TS)
    assert isinstance(rec, OHLCV)
    assert rec.close == 64547.0
    assert rec.high == 64866.0
    assert rec.low == 64019.0
    assert rec.interval == "24h"
    assert rec.symbol == "coingecko:bitcoin"
    # open reconstructed from the 24h % change (not a flat line)
    assert abs(rec.open - 64547.0 / 1.0073) < 1e-6
    assert rec.open != rec.close


def test_coin_to_ohlcv_missing_price_returns_none():
    assert norm.coin_to_ohlcv({"id": "x", "current_price": None}, local_ts=LOCAL_TS) is None


def test_coin_to_ohlcv_no_change_flat_open():
    coin = {"id": "usd-coin", "symbol": "usdc", "current_price": 1.0,
            "high_24h": 1.001, "low_24h": 0.999, "total_volume": 5e9,
            "price_change_percentage_24h": None}
    rec = norm.coin_to_ohlcv(coin, local_ts=LOCAL_TS)
    assert rec is not None
    assert rec.open == 1.0  # no pct → open falls back to close


def test_coin_to_ohlcv_missing_extremes_fall_back_to_price():
    coin = {"id": "obscure", "symbol": "obs", "current_price": 2.0,
            "total_volume": 100.0, "price_change_percentage_24h": 0.0}
    rec = norm.coin_to_ohlcv(coin, local_ts=LOCAL_TS)
    assert rec is not None
    assert rec.high == 2.0 and rec.low == 2.0

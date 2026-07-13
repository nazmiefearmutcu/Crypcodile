"""Tests for crypcodile technical analysis indicators."""

from __future__ import annotations

import pathlib

import numpy as np
import polars as pl
import pytest

from crypcodile.analytics.indicators import (
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
)
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000


def test_sma() -> None:
    prices = [10.0, 20.0, 30.0, 40.0, 50.0]

    # 1. Test List Input
    res_list = calculate_sma(prices, period=3)
    assert len(res_list) == 5
    assert res_list[0] is None
    assert res_list[1] is None
    assert pytest.approx(res_list[2]) == 20.0  # (10+20+30)/3
    assert pytest.approx(res_list[3]) == 30.0  # (20+30+40)/3
    assert pytest.approx(res_list[4]) == 40.0  # (30+40+50)/3

    # 2. Test NumPy Input
    res_np = calculate_sma(np.array(prices), period=3)
    assert isinstance(res_np, np.ndarray)
    assert np.isnan(res_np[0])
    assert np.isnan(res_np[1])
    assert pytest.approx(res_np[2]) == 20.0

    # 3. Test Polars Input
    res_pl = calculate_sma(pl.Series(prices), period=3)
    assert isinstance(res_pl, pl.Series)
    assert res_pl[0] is None
    assert res_pl[1] is None
    assert pytest.approx(res_pl[2]) == 20.0

    # 4. Error case
    with pytest.raises(ValueError):
        calculate_sma(prices, period=0)


def test_ema() -> None:
    prices = [1.0, 2.0, 3.0]

    # 1. Test List Input
    res_list = calculate_ema(prices, period=2)
    assert len(res_list) == 3
    assert pytest.approx(res_list[0]) == 1.0
    assert pytest.approx(res_list[1]) == 1.666667
    assert pytest.approx(res_list[2]) == 2.555556

    # 2. Test NumPy Input
    res_np = calculate_ema(np.array(prices), period=2)
    assert isinstance(res_np, np.ndarray)
    assert pytest.approx(res_np[1]) == 1.666667

    # 3. Test Polars Input
    res_pl = calculate_ema(pl.Series(prices), period=2)
    assert isinstance(res_pl, pl.Series)
    assert pytest.approx(res_pl[2]) == 2.555556

    # 4. Error case
    with pytest.raises(ValueError):
        calculate_ema(prices, period=-1)


def test_rsi() -> None:
    prices = [10.0, 11.0, 12.0, 13.0, 14.0]

    # All gains, zero losses. RSI should tend to 100.
    res_list = calculate_rsi(prices, period=3)
    assert len(res_list) == 5
    assert res_list[0] is None  # first diff is null
    assert pytest.approx(res_list[1]) == 100.0
    assert pytest.approx(res_list[4]) == 100.0

    # Steadily decreasing prices. RSI should tend to 0.
    dec_prices = [50.0, 40.0, 30.0, 20.0, 10.0]
    res_dec = calculate_rsi(dec_prices, period=3)
    assert len(res_dec) == 5
    assert res_dec[0] is None
    assert pytest.approx(res_dec[1]) == 0.0
    assert pytest.approx(res_dec[4]) == 0.0

    # No price movement. RSI should be 50.0
    flat_prices = [10.0, 10.0, 10.0, 10.0]
    res_flat = calculate_rsi(flat_prices, period=3)
    assert len(res_flat) == 4
    assert res_flat[0] is None
    assert pytest.approx(res_flat[1]) == 50.0

    # Error case
    with pytest.raises(ValueError):
        calculate_rsi(prices, period=0)


def test_macd() -> None:
    prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]

    macd_line, signal_line, hist = calculate_macd(
        prices, fast_period=3, slow_period=6, signal_period=3
    )

    assert len(macd_line) == 10
    assert len(signal_line) == 10
    assert len(hist) == 10

    # Assert type preservation
    assert isinstance(macd_line, list)

    macd_np, _, _ = calculate_macd(np.array(prices), fast_period=3, slow_period=6, signal_period=3)
    assert isinstance(macd_np, np.ndarray)

    # Error case
    with pytest.raises(ValueError):
        calculate_macd(prices, fast_period=0)


def test_bollinger_bands() -> None:
    prices = [10.0, 12.0, 11.0, 13.0, 12.0, 14.0]

    upper, mid, lower = calculate_bollinger_bands(prices, period=3, k=2.0)

    assert len(mid) == 6
    assert len(upper) == 6
    assert len(lower) == 6

    # Assert types
    assert isinstance(mid, list)

    # Polars Series validation
    upper_pl, mid_pl, lower_pl = calculate_bollinger_bands(pl.Series(prices), period=3, k=2.0)
    assert isinstance(mid_pl, pl.Series)

    # Check that upper > mid > lower
    for i in range(2, 6):
        assert upper_pl[i] > mid_pl[i]
        assert mid_pl[i] > lower_pl[i]

    # Error case
    with pytest.raises(ValueError):
        calculate_bollinger_bands(prices, period=-5)


# ---------------------------------------------------------------------------
# CrypcodileClient.get_indicators (matches CLI indicators)
# ---------------------------------------------------------------------------


async def _write_trade_bars(data_dir: pathlib.Path) -> None:
    """Write spaced trades so 1s OHLCV produces multiple bars."""
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)
    for i, price in enumerate([100.0, 110.0, 120.0, 115.0, 130.0]):
        ts = _BASE_TS + i * 1_000_000_000
        await sink.put(
            Trade(
                exchange="deribit",
                symbol="deribit:BTC-PERPETUAL",
                symbol_raw="BTC-PERPETUAL",
                exchange_ts=ts,
                local_ts=ts,
                id=str(i),
                price=price,
                amount=1.0,
                side=Side.BUY,
            )
        )
    await sink.flush()


async def test_client_get_indicators_sma(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_trade_bars(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    df = client.get_indicators(
        "deribit:BTC-PERPETUAL",
        _BASE_TS - 1,
        _BASE_TS + 10_000_000_000,
        interval="1s",
        indicator="sma",
        period=2,
    )
    assert isinstance(df, pl.DataFrame)
    assert len(df) > 0
    assert "sma" in df.columns
    assert "close" in df.columns


async def test_client_get_indicators_all_and_empty(
    tmp_path: pathlib.Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_trade_bars(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    df = client.get_indicators(
        "deribit:BTC-PERPETUAL",
        _BASE_TS - 1,
        _BASE_TS + 10_000_000_000,
        interval="1s",
        indicator="all",
        period=2,
    )
    for col in ("sma", "ema", "rsi", "macd", "signal", "hist", "bb_upper", "bb_middle", "bb_lower"):
        assert col in df.columns

    empty_dir = tmp_path_factory.mktemp("empty_lake")
    empty_client = CrypcodileClient(data_dir=empty_dir)
    empty = empty_client.get_indicators(
        "deribit:BTC-PERPETUAL",
        _BASE_TS - 1,
        _BASE_TS + 10_000_000_000,
        interval="1s",
        indicator="sma",
        period=2,
    )
    assert len(empty) == 0

    with pytest.raises(ValueError, match="Unknown indicator"):
        client.get_indicators(
            "deribit:BTC-PERPETUAL",
            _BASE_TS - 1,
            _BASE_TS + 10_000_000_000,
            interval="1s",
            indicator="not_a_thing",
            period=2,
        )

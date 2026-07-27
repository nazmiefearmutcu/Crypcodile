"""Tests for crypcodile technical analysis indicators."""

from __future__ import annotations

import pathlib

import numpy as np
import polars as pl
import pytest

from crocodile.core.analytics.indicators import (
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
)
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import Trade
from crocodile.core.store.parquet_sink import ParquetSink

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


def test_rsi_warms_up_over_a_full_period_before_reporting_a_value() -> None:
    """Migrated: this used to pin RSI starting at index 1, which the crypto surfaces served.

    The equity fork seeded Wilder's averages from the mean of the first ``period`` changes
    and this one started an ``ewm_mean`` at index 0, so the two returned different RSI for
    the same prices under the same name. Core took the seeded form, so the values this test
    pins are the ones the crypto CLI, REST and MCP surfaces moved to: the first non-null
    value slid from index 1 to index ``period``, and every later value changed too, because
    Wilder's recursion never forgets its seed.
    """
    prices = [10.0, 11.0, 12.0, 13.0, 14.0]

    # All gains, zero losses. RSI should tend to 100 — but only once seeded.
    res_list = calculate_rsi(prices, period=3)
    assert len(res_list) == 5
    assert res_list[0] is None  # first diff is null
    assert res_list[1] is None  # inside the warm-up: fewer than `period` changes seen
    assert res_list[2] is None
    assert pytest.approx(res_list[3]) == 100.0
    assert pytest.approx(res_list[4]) == 100.0

    # Steadily decreasing prices. RSI should tend to 0.
    dec_prices = [50.0, 40.0, 30.0, 20.0, 10.0]
    res_dec = calculate_rsi(dec_prices, period=3)
    assert len(res_dec) == 5
    assert res_dec[0] is None
    assert res_dec[1] is None
    assert res_dec[2] is None
    assert pytest.approx(res_dec[3]) == 0.0
    assert pytest.approx(res_dec[4]) == 0.0

    # No price movement. RSI should be 50.0 once there is a seed to say so with.
    flat_prices = [10.0, 10.0, 10.0, 10.0]
    res_flat = calculate_rsi(flat_prices, period=3)
    assert len(res_flat) == 4
    assert res_flat[0] is None
    assert res_flat[1] is None
    assert res_flat[2] is None
    assert pytest.approx(res_flat[3]) == 50.0

    # Error case
    with pytest.raises(ValueError):
        calculate_rsi(prices, period=0)


def test_rsi_needs_more_prices_than_its_period_to_report_anything() -> None:
    """A series too short to seed reports nothing rather than a value built from too little.

    The pre-merge crypto arithmetic answered every one of these, because starting the
    average at index 0 means one observation is enough to average. That is the shape of the
    warm-up bug: not a wrong number in the first few slots, a number where there is no
    basis for one.
    """
    assert calculate_rsi([10.0, 12.0, 11.0], period=3) == [None, None, None]
    assert calculate_rsi([10.0, 12.0], period=3) == [None, None]


def test_rsi_reports_nothing_across_a_gap_rather_than_calling_it_no_movement() -> None:
    """A missing price is not a flat price.

    The equity fork coerced a null change to 0.0 while seeding, which turned a hole in the
    data into an average gain and an average loss of zero — and zero over zero is reported
    as a perfectly neutral RSI of 50. Core kept its own null propagation when it took the
    equity seed, so the gap stays visible.
    """
    with_gap = [10.0, None, 12.0, 13.0, 14.0, 15.0]
    res = calculate_rsi(with_gap, period=2)
    assert res[2] is None, "a fabricated 50.0 here would be the gap reported as calm"
    assert pytest.approx(res[3]) == 100.0, "and the series recovers once the gap is behind it"


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


def test_bollinger_bands_measure_the_window_as_a_population_not_a_sample() -> None:
    """Pins the band half-width, which nothing did while the two forks disagreed about it.

    One fork passed ``ddof=0`` and the other took Polars' ``ddof=1`` default, so the same
    prices produced bands ``sqrt(period/(period-1))`` apart under one name — and the only
    Bollinger assertions either suite had were ``upper > mid > lower``, which both satisfy.
    An unpinned number is how a silent change gets in, so this states the width.

    Over ``[10, 12, 11]`` the mean is 11 and the population deviation is ``sqrt(2/3)``,
    giving a half-width of ``2 * 0.8165 = 1.633``. The sample deviation is exactly 1.0,
    which would put the upper band on 13.0 — the answer the crypto surfaces used to give.
    """
    upper, mid, lower = calculate_bollinger_bands([10.0, 12.0, 11.0], period=3, k=2.0)

    assert pytest.approx(mid[2]) == 11.0
    assert pytest.approx(upper[2], abs=1e-4) == 12.6330
    assert pytest.approx(lower[2], abs=1e-4) == 9.3670
    assert upper[2] != pytest.approx(13.0), "13.0 is the sample-deviation band, ddof=1"


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
                source="deribit",
                symbol="deribit:BTC-PERPETUAL",
                symbol_raw="BTC-PERPETUAL",
                source_ts=ts,
                local_ts=ts,
                asset_class=AssetClass.CRYPTO,
                id=str(i),
                price=price,
                amount=1.0,
                side=Side.BUY,
            )
        )
    await sink.flush()


async def test_client_get_indicators_sma(tmp_path: pathlib.Path) -> None:
    from crocodile.crypto.client.client import CrypcodileClient

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
    from crocodile.crypto.client.client import CrypcodileClient

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

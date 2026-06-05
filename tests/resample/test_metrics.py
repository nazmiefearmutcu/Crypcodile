"""Tests for Task 5.3 — VWAP + derived metrics per interval.

Acceptance criteria (from the plan):
  - ``resample_metrics(catalog, symbol, start_ns, end_ns, interval)`` queries
    the ``trade`` channel via DuckDB ``time_bucket`` and returns a Polars
    DataFrame with VWAP, trade_count, and dollar_volume per interval bucket.
  - VWAP = sum(price * amount) / sum(amount) per bucket.
  - dollar_volume = sum(price * amount) per bucket.
  - trade_count = count of trades per bucket.
  - sum(dollar_volume) across all bars equals sum(price * amount) for all trades.
  - sum(trade_count) across all bars equals total trade count.
  - Empty range returns an empty DataFrame.
  - Result schema includes: bar, symbol, interval, vwap, trade_count, dollar_volume.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
import pytest

from crocodile.resample.metrics import resample_metrics
from crocodile.schema.enums import Side
from crocodile.schema.records import Trade
from crocodile.store.catalog import Catalog
from crocodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 1s = 1_000_000_000 ns
_1S_NS = 1_000_000_000

# A fixed base timestamp: 2023-11-14 22:13:00 UTC in nanoseconds
_BASE_NS = 1_700_000_000_000_000_000  # exact second boundary


def _ts(offset_ns: int) -> int:
    """Return _BASE_NS + offset_ns."""
    return _BASE_NS + offset_ns


def _make_trade(
    ts: int,
    price: float,
    amount: float,
    side: Side,
    tid: str,
) -> Trade:
    return Trade(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=ts,
        local_ts=ts,
        id=tid,
        price=price,
        amount=amount,
        side=side,
    )


async def _write_trades(data_dir: Path, trades: list[Trade]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for t in trades:
        await sink.put(t)
    await sink.flush()


# ---------------------------------------------------------------------------
# Fixture trades (3 trades across 3 distinct seconds)
#
#   t1: +0 ns     price=100.0, amount=1.0, BUY   → dollar_volume = 100.0
#   t2: +1.1s     price=200.0, amount=2.0, SELL  → dollar_volume = 400.0
#   t3: +2.2s     price=50.0,  amount=0.5, BUY   → dollar_volume =  25.0
#
# Total dollar_volume = 525.0
# Total trade_count   = 3
# VWAP (over entire window in 1h bar) = 525.0 / 3.5 = 150.0
# ---------------------------------------------------------------------------

_TRADES = [
    _make_trade(_ts(0), 100.0, 1.0, Side.BUY, "t1"),
    _make_trade(_ts(int(1.1 * _1S_NS)), 200.0, 2.0, Side.SELL, "t2"),
    _make_trade(_ts(int(2.2 * _1S_NS)), 50.0, 0.5, Side.BUY, "t3"),
]

_TOTAL_DOLLAR_VOLUME = 100.0 * 1.0 + 200.0 * 2.0 + 50.0 * 0.5  # 525.0
_TOTAL_AMOUNT = 1.0 + 2.0 + 0.5  # 3.5
_TOTAL_TRADE_COUNT = 3
_EXPECTED_VWAP = _TOTAL_DOLLAR_VOLUME / _TOTAL_AMOUNT  # 150.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    asyncio.run(_write_trades(tmp_path, _TRADES))
    return tmp_path


@pytest.fixture()
def catalog(data_dir: Path) -> Catalog:
    return Catalog(data_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_metrics_returns_polars_dataframe(catalog: Catalog) -> None:
    """resample_metrics must return a Polars DataFrame."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1h",
    )
    assert isinstance(df, pl.DataFrame)


def test_metrics_required_columns(catalog: Catalog) -> None:
    """Result schema must include bar, symbol, interval, vwap, trade_count, dollar_volume."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1m",
    )
    expected = {"bar", "symbol", "interval", "vwap", "trade_count", "dollar_volume"}
    assert expected.issubset(set(df.columns)), (
        f"missing columns: {expected - set(df.columns)}"
    )


def test_metrics_dollar_volume_sum_conserved(catalog: Catalog) -> None:
    """sum(dollar_volume) over all bars must equal sum(price * amount) for all trades."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1s",
    )
    assert abs(df["dollar_volume"].sum() - _TOTAL_DOLLAR_VOLUME) < 1e-6, (
        f"dollar_volume sum mismatch: {df['dollar_volume'].sum()} != {_TOTAL_DOLLAR_VOLUME}"
    )


def test_metrics_trade_count_sum_conserved(catalog: Catalog) -> None:
    """sum(trade_count) over all bars must equal total number of trades."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1s",
    )
    assert df["trade_count"].sum() == _TOTAL_TRADE_COUNT, (
        f"trade_count sum mismatch: {df['trade_count'].sum()} != {_TOTAL_TRADE_COUNT}"
    )


def test_metrics_vwap_single_bar(catalog: Catalog) -> None:
    """VWAP over a single bar (1h interval) = total_dollar_volume / total_amount."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1h",
    )
    assert len(df) == 1
    row = df.row(0, named=True)
    assert abs(row["vwap"] - _EXPECTED_VWAP) < 1e-6, (
        f"VWAP mismatch: {row['vwap']} != {_EXPECTED_VWAP}"
    )
    assert abs(row["dollar_volume"] - _TOTAL_DOLLAR_VOLUME) < 1e-6
    assert row["trade_count"] == _TOTAL_TRADE_COUNT


def test_metrics_per_second_bars(catalog: Catalog) -> None:
    """With 1s interval and 3 trades in distinct seconds, 3 bars are returned."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1s",
    )
    assert len(df) == 3, f"expected 3 bars, got {len(df)}"
    # Each bar has exactly 1 trade
    assert df["trade_count"].to_list() == [1, 1, 1]


def test_metrics_vwap_per_bar(catalog: Catalog) -> None:
    """VWAP for a single-trade bar equals that trade's price (VWAP = price * amount / amount)."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1s",
    )
    assert len(df) == 3
    # Bar with t1: price=100.0, amount=1.0 → vwap=100.0, dollar_volume=100.0
    bar0 = df.row(0, named=True)
    assert abs(bar0["vwap"] - 100.0) < 1e-6, f"bar0 vwap={bar0['vwap']}"
    assert abs(bar0["dollar_volume"] - 100.0) < 1e-6, f"bar0 dv={bar0['dollar_volume']}"
    # Bar with t2: price=200.0, amount=2.0 → vwap=200.0, dollar_volume=400.0
    bar1 = df.row(1, named=True)
    assert abs(bar1["vwap"] - 200.0) < 1e-6, f"bar1 vwap={bar1['vwap']}"
    assert abs(bar1["dollar_volume"] - 400.0) < 1e-6, f"bar1 dv={bar1['dollar_volume']}"
    # Bar with t3: price=50.0, amount=0.5 → vwap=50.0, dollar_volume=25.0
    bar2 = df.row(2, named=True)
    assert abs(bar2["vwap"] - 50.0) < 1e-6, f"bar2 vwap={bar2['vwap']}"
    assert abs(bar2["dollar_volume"] - 25.0) < 1e-6, f"bar2 dv={bar2['dollar_volume']}"


def test_metrics_empty_range_returns_empty_dataframe(catalog: Catalog) -> None:
    """Querying a range with no trades returns an empty DataFrame."""
    start_ns = _ts(int(1000 * _1S_NS))
    end_ns = _ts(int(2000 * _1S_NS))
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        start_ns,
        end_ns,
        "1m",
    )
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_metrics_interval_column_value(catalog: Catalog) -> None:
    """The ``interval`` column stores the requested interval string."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1m",
    )
    assert df["interval"].unique().to_list() == ["1m"]


def test_metrics_bar_column_is_nanoseconds(catalog: Catalog) -> None:
    """The ``bar`` column must contain nanosecond epoch integers (Int64)."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1s",
    )
    # bar values must be in the vicinity of our base timestamp
    bar_vals = df["bar"].to_list()
    for b in bar_vals:
        # Should be close to _BASE_NS (same order of magnitude)
        assert abs(b - _BASE_NS) < 10 * _1S_NS, f"bar={b} looks wrong (not ~ns epoch)"


def test_metrics_ordered_by_bar(catalog: Catalog) -> None:
    """Result must be ordered by bar ascending."""
    df = resample_metrics(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1s",
    )
    bars = df["bar"].to_list()
    assert bars == sorted(bars), f"bars not sorted: {bars}"


def test_metrics_invalid_interval_raises(catalog: Catalog) -> None:
    """An invalid interval string must raise ValueError."""
    with pytest.raises(ValueError):
        resample_metrics(
            catalog,
            "deribit:BTC-PERPETUAL",
            _ts(0),
            _ts(int(3 * _1S_NS)),
            "bad",
        )

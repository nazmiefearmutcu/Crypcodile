"""Tests for Task 5.1 — OHLCV resample from trade records (any interval).

Acceptance criteria (from the plan):
  - ``resample_ohlcv(catalog, symbol, start_ns, end_ns, interval)`` queries
    the ``trade`` channel via DuckDB ``time_bucket`` and returns a Polars
    DataFrame with OHLCV bars.
  - Bars at 1s / 1m / 1h are all tested.
  - ``sum(volume)`` across all bars equals the sum of all trade amounts in
    the source data.
  - ``buy_volume`` + ``sell_volume`` == ``volume`` for every bar.
  - ``num_trades`` equals the count of trades in each bar.
  - Optional forward-fill: when ``fill_empty=True``, the result contains a
    bar for every interval in [start_ns, end_ns] even if no trades fell in
    that bucket.

Fixture approach:
  Trades are written to a temp ``ParquetSink``, a ``Catalog`` is built over
  the same dir, then ``resample_ohlcv`` is called.  No live exchange needed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
import pytest

from crocodile.resample.ohlcv import resample_ohlcv
from crocodile.schema.enums import Side
from crocodile.schema.records import Trade
from crocodile.store.catalog import Catalog
from crocodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Helpers
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
# Fixture trades: 3 trades spread across 3 seconds (one trade/second)
#
#   t0: +0 ns  → bar 0   price=100, amount=1.0, BUY
#   t1: +1.1s  → bar 1   price=101, amount=2.0, SELL
#   t2: +2.2s  → bar 2   price=99,  amount=0.5, BUY
#
# 1s bars: 3 non-empty bars, total volume = 3.5
# 1m bars: 1 bar, total volume = 3.5  (all within the same minute)
# 1h bars: 1 bar, total volume = 3.5
# ---------------------------------------------------------------------------

_TRADES = [
    _make_trade(_ts(0), 100.0, 1.0, Side.BUY, "t1"),
    _make_trade(_ts(int(1.1 * _1S_NS)), 101.0, 2.0, Side.SELL, "t2"),
    _make_trade(_ts(int(2.2 * _1S_NS)), 99.0, 0.5, Side.BUY, "t3"),
]
_TOTAL_VOLUME = 3.5


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    asyncio.run(_write_trades(tmp_path, _TRADES))
    return tmp_path


@pytest.fixture()
def catalog(data_dir: Path) -> Catalog:
    return Catalog(data_dir)


def test_resample_1s_bar_count_and_volume(catalog: Catalog) -> None:
    """Three trades in 3 distinct 1-second buckets → 3 bars; total volume conserved."""
    start_ns = _ts(0)
    end_ns = _ts(int(3 * _1S_NS))

    df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, "1s")

    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3, f"expected 3 bars, got {len(df)}"
    assert abs(df["volume"].sum() - _TOTAL_VOLUME) < 1e-9, (
        f"volume sum mismatch: {df['volume'].sum()} != {_TOTAL_VOLUME}"
    )
    # buy_volume + sell_volume == volume for every bar
    volume_check = (df["buy_volume"] + df["sell_volume"] - df["volume"]).abs().max()
    assert volume_check < 1e-9, f"buy+sell != volume, delta={volume_check}"
    # num_trades: each bar has exactly 1 trade
    assert df["num_trades"].to_list() == [1, 1, 1]


def test_resample_1m_aggregates_all_into_one_bar(catalog: Catalog) -> None:
    """All 3 trades fall within the same minute → single bar."""
    start_ns = _ts(0)
    end_ns = _ts(int(3 * _1S_NS))

    df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, "1m")

    assert len(df) == 1, f"expected 1 bar, got {len(df)}"
    row = df.row(0, named=True)
    # open = price of first trade (lowest local_ts within bar)
    assert row["open"] == 100.0
    # close = price of last trade
    assert row["close"] == 99.0
    # high/low
    assert row["high"] == 101.0
    assert row["low"] == 99.0
    assert abs(row["volume"] - _TOTAL_VOLUME) < 1e-9
    assert row["num_trades"] == 3


def test_resample_1h_aggregates_all_into_one_bar(catalog: Catalog) -> None:
    """All 3 trades fall within the same hour → single bar."""
    start_ns = _ts(0)
    end_ns = _ts(int(3 * _1S_NS))

    df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, "1h")

    assert len(df) == 1
    assert abs(df["volume"].sum() - _TOTAL_VOLUME) < 1e-9


def test_resample_volume_conservation(catalog: Catalog) -> None:
    """sum(volume) == sum(all trade amounts), regardless of interval."""
    start_ns = _ts(0)
    end_ns = _ts(int(3 * _1S_NS))

    for interval in ("1s", "1m", "1h"):
        df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, interval)
        total = df["volume"].sum()
        assert abs(total - _TOTAL_VOLUME) < 1e-9, (
            f"interval={interval!r}: volume {total} != {_TOTAL_VOLUME}"
        )


def test_resample_buy_sell_volume_split(catalog: Catalog) -> None:
    """buy_volume and sell_volume correctly split by trade side."""
    start_ns = _ts(0)
    end_ns = _ts(int(3 * _1S_NS))

    # Expected: buy = 1.0 + 0.5 = 1.5; sell = 2.0
    df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, "1h")
    row = df.row(0, named=True)
    assert abs(row["buy_volume"] - 1.5) < 1e-9, f"buy_volume={row['buy_volume']}"
    assert abs(row["sell_volume"] - 2.0) < 1e-9, f"sell_volume={row['sell_volume']}"


def test_resample_empty_result_no_trades_in_range(catalog: Catalog) -> None:
    """Query a time range with no trades returns an empty DataFrame."""
    # Way in the future — no trades
    start_ns = _ts(int(1000 * _1S_NS))
    end_ns = _ts(int(2000 * _1S_NS))

    df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, "1m")
    assert len(df) == 0


def test_resample_fill_empty_inserts_null_bars(catalog: Catalog, data_dir: Path) -> None:
    """With fill_empty=True, every interval bucket in [start, end] has a row.

    We have trades in seconds 0, 1.1s, 2.2s.  Requesting fill_empty=True
    for a 4-second window at 1s resolution should yield 4 bars (0..3).
    The bar covering second 3 has no trades → volume = 0, OHLCV = None.
    """
    start_ns = _ts(0)
    end_ns = _ts(int(4 * _1S_NS))

    df = resample_ohlcv(
        catalog,
        "deribit:BTC-PERPETUAL",
        start_ns,
        end_ns,
        "1s",
        fill_empty=True,
    )

    # At least 4 bars (start bar through 3s bar)
    assert len(df) >= 4, f"expected >=4 bars with fill_empty, got {len(df)}"
    # Total volume still equals the sum of real trades only
    assert abs(df["volume"].sum() - _TOTAL_VOLUME) < 1e-9


def test_resample_returns_correct_schema(catalog: Catalog) -> None:
    """Result DataFrame contains the required OHLCV columns."""
    start_ns = _ts(0)
    end_ns = _ts(int(3 * _1S_NS))

    df = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", start_ns, end_ns, "1m")
    expected_cols = {
        "bar",
        "symbol",
        "interval",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "buy_volume",
        "sell_volume",
        "num_trades",
    }
    assert expected_cols.issubset(set(df.columns)), (
        f"missing columns: {expected_cols - set(df.columns)}"
    )


def test_resample_interval_column_value(catalog: Catalog) -> None:
    """The ``interval`` column stores the requested interval string."""
    df = resample_ohlcv(
        catalog,
        "deribit:BTC-PERPETUAL",
        _ts(0),
        _ts(int(3 * _1S_NS)),
        "1m",
    )
    assert df["interval"].unique().to_list() == ["1m"]

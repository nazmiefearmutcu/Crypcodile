"""Tests for Task 6.2 — Funding APR analytics.

Acceptance criteria (from the plan, verbatim golden numbers):
  - Write 3 Funding records (rates 0.0001, -0.0002, 0.0003, interval_hours=8)
    through ParquetSink to a tmp lake.
  - funding_apr → 3 rows, apr of row0 ≈ 0.0001*1095 = 0.10950 (tol 1e-6),
    cumulative_funding last ≈ 0.0002.
  - funding_summary.n_events == 3, mean_rate ≈ 0.0000667.
  - Empty range → empty DataFrame.
  - ruff + mypy clean.

Sign convention: positive rate ⇒ longs pay shorts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
import pytest

from crypcodile.analytics.funding import (
    apr_from_rate,
    funding_apr,
    funding_summary,
    periods_per_year,
)
from crypcodile.schema.records import Funding
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 2024-01-01 00:00:00 UTC in nanoseconds — a fixed base to avoid date edges
_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC
_8H_NS = 8 * 3600 * 1_000_000_000  # 8 hours in nanoseconds

_SYMBOL = "deribit:BTC-PERPETUAL"
_EXCHANGE = "deribit"

# The three funding records per the acceptance test
_RATES = [0.0001, -0.0002, 0.0003]
_INTERVAL_HOURS = 8


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_funding(ts: int, rate: float, interval_hours: int) -> Funding:
    """Build a minimal Funding record."""
    return Funding(
        exchange=_EXCHANGE,
        symbol=_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=ts,
        local_ts=ts,
        funding_rate=rate,
        funding_timestamp=ts,
        interval_hours=interval_hours,
    )


async def _write_funding(data_dir: Path, records: list[Funding]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)
    await sink.flush()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def populated_lake(tmp_path: Path) -> Path:
    """Write 3 Funding records to a temp lake and return the dir."""
    records = [
        _make_funding(_BASE_NS + i * _8H_NS, rate, _INTERVAL_HOURS)
        for i, rate in enumerate(_RATES)
    ]
    asyncio.run(_write_funding(tmp_path, records))
    return tmp_path


@pytest.fixture()
def catalog(populated_lake: Path) -> Catalog:
    return Catalog(populated_lake)


# ---------------------------------------------------------------------------
# Unit tests: pure-math helpers
# ---------------------------------------------------------------------------


def test_periods_per_year_8h() -> None:
    """8-hour interval → 1095 periods/year."""
    assert abs(periods_per_year(8) - 1095.0) < 1e-9


def test_periods_per_year_1h() -> None:
    """1-hour interval → 8760 periods/year."""
    assert abs(periods_per_year(1) - 8760.0) < 1e-9


def test_apr_from_rate_golden() -> None:
    """rate=0.0001, interval=8h → apr = 0.0001 * 1095 = 0.10950."""
    result = apr_from_rate(0.0001, 8)
    assert abs(result - 0.10950) < 1e-6, f"apr={result}, expected≈0.10950"


def test_apr_from_rate_negative() -> None:
    """Negative rate maps to a negative APR."""
    result = apr_from_rate(-0.0002, 8)
    assert result < 0.0


def test_apr_from_rate_4h() -> None:
    """4-hour interval → 2190 periods/year."""
    ppy = periods_per_year(4)
    assert abs(ppy - 2190.0) < 1e-9
    result = apr_from_rate(0.0001, 4)
    assert abs(result - 0.0001 * 2190.0) < 1e-9


# ---------------------------------------------------------------------------
# Integration tests: funding_apr (catalog-backed)
# ---------------------------------------------------------------------------


def test_funding_apr_row_count(catalog: Catalog) -> None:
    """3 Funding records → 3 rows in the output DataFrame."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3, f"expected 3 rows, got {len(df)}"


def test_funding_apr_columns(catalog: Catalog) -> None:
    """Output must contain the required columns."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    required = {"funding_ts", "funding_rate", "interval_hours", "apr", "cumulative_funding"}
    assert required.issubset(set(df.columns)), (
        f"missing columns: {required - set(df.columns)}"
    )


def test_funding_apr_row0_apr_golden(catalog: Catalog) -> None:
    """Row 0 APR ≈ 0.0001 * 1095 = 0.10950 (tol 1e-6)."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    row0_apr = df["apr"][0]
    expected = 0.0001 * 1095.0  # 0.10950
    assert abs(row0_apr - expected) < 1e-6, f"apr[0]={row0_apr}, expected≈{expected}"


def test_funding_apr_cumulative_last(catalog: Catalog) -> None:
    """Last cumulative_funding ≈ 0.0001 + (-0.0002) + 0.0003 = 0.0002 (tol 1e-9)."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    last_cum = df["cumulative_funding"][-1]
    expected = 0.0001 + (-0.0002) + 0.0003  # = 0.0002
    assert abs(last_cum - expected) < 1e-9, f"last cumulative={last_cum}, expected≈{expected}"


def test_funding_apr_ordered_ascending(catalog: Catalog) -> None:
    """Output must be ordered by funding_ts ascending."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    ts_list = df["funding_ts"].to_list()
    assert ts_list == sorted(ts_list), "rows are not sorted by funding_ts"


def test_funding_apr_interval_hours_column(catalog: Catalog) -> None:
    """interval_hours column should reflect the stored value (8)."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert all(v == 8 for v in df["interval_hours"].to_list()), (
        f"interval_hours not all 8: {df['interval_hours'].to_list()}"
    )


def test_funding_apr_funding_rate_values(catalog: Catalog) -> None:
    """funding_rate column should match input rates in order."""
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    rates = df["funding_rate"].to_list()
    for actual, expected in zip(rates, _RATES, strict=True):
        assert abs(actual - expected) < 1e-12, f"rate {actual} != expected {expected}"


def test_funding_apr_empty_range_returns_empty(tmp_path: Path) -> None:
    """No data in range → empty DataFrame (consistent with resample_ohlcv contract)."""
    catalog = Catalog(tmp_path)  # empty lake
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_funding_apr_symbol_isolation(populated_lake: Path) -> None:
    """Query for a different symbol returns empty."""
    catalog = Catalog(populated_lake)
    df = funding_apr(catalog, "bybit:ETHUSDT", _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert len(df) == 0


def test_funding_apr_dedupes_duplicate_funding_timestamp(tmp_path: Path) -> None:
    """Live lakes re-emit the same settlement; cumulative must not explode.

    Three distinct funding events are each written multiple times with the same
    ``funding_timestamp``.  Output must still be 3 rows and cumulative funding
    must match the sum of the unique rates — not N× that sum.
    """
    records: list[Funding] = []
    for i, rate in enumerate(_RATES):
        ts = _BASE_NS + i * _8H_NS
        # Simulate 5 re-emits of the same settlement event.
        for _ in range(5):
            records.append(_make_funding(ts, rate, _INTERVAL_HOURS))
    asyncio.run(_write_funding(tmp_path, records))
    catalog = Catalog(tmp_path)

    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert len(df) == 3, f"expected 3 deduped rows, got {len(df)}"

    last_cum = df["cumulative_funding"][-1]
    expected_cum = 0.0001 + (-0.0002) + 0.0003  # = 0.0002, not 5×
    assert abs(last_cum - expected_cum) < 1e-9, (
        f"last cumulative={last_cum}, expected≈{expected_cum} "
        f"(duplicates inflated the sum)"
    )

    rates = df["funding_rate"].to_list()
    for actual, expected in zip(rates, _RATES, strict=True):
        assert abs(actual - expected) < 1e-12

    summary = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert summary["n_events"][0] == 3
    assert abs(summary["total_funding"][0] - expected_cum) < 1e-9


# ---------------------------------------------------------------------------
# Integration tests: funding_summary (catalog-backed)
# ---------------------------------------------------------------------------


def test_funding_summary_n_events(catalog: Catalog) -> None:
    """n_events == 3."""
    df = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 1, "funding_summary should return a single-row DataFrame"
    assert df["n_events"][0] == 3, f"n_events={df['n_events'][0]}, expected 3"


def test_funding_summary_mean_rate_golden(catalog: Catalog) -> None:
    """mean_rate ≈ (0.0001 - 0.0002 + 0.0003) / 3 ≈ 0.0000667 (tol 1e-7)."""
    df = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    mean_rate = df["mean_rate"][0]
    expected = (0.0001 + (-0.0002) + 0.0003) / 3.0  # ≈ 6.6667e-5
    assert abs(mean_rate - expected) < 1e-7, f"mean_rate={mean_rate}, expected≈{expected}"


def test_funding_summary_total_funding(catalog: Catalog) -> None:
    """total_funding ≈ 0.0002 (sum of rates)."""
    df = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    total = df["total_funding"][0]
    expected = 0.0001 + (-0.0002) + 0.0003  # = 0.0002
    assert abs(total - expected) < 1e-9


def test_funding_summary_mean_apr_golden(catalog: Catalog) -> None:
    """mean_apr ≈ mean_rate * 1095 (for 8h intervals)."""
    df = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    mean_rate = (0.0001 + (-0.0002) + 0.0003) / 3.0
    expected_mean_apr = mean_rate * 1095.0
    actual_mean_apr = df["mean_apr"][0]
    assert abs(actual_mean_apr - expected_mean_apr) < 1e-6


def test_funding_summary_columns(catalog: Catalog) -> None:
    """Output must have the required columns."""
    df = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    required = {"n_events", "mean_rate", "mean_apr", "total_funding"}
    assert required.issubset(set(df.columns)), (
        f"missing columns: {required - set(df.columns)}"
    )


def test_funding_summary_empty_range_returns_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame (not a single-row zero-summary)."""
    catalog = Catalog(tmp_path)
    df = funding_summary(catalog, _SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# Guard: periods_per_year rejects non-positive interval_hours
# ---------------------------------------------------------------------------


def test_periods_per_year_zero_raises() -> None:
    """interval_hours=0 must raise ValueError with a legible message."""
    with pytest.raises(ValueError, match="interval_hours must be a positive integer"):
        periods_per_year(0)


def test_periods_per_year_negative_raises() -> None:
    """Negative interval_hours must raise ValueError."""
    with pytest.raises(ValueError, match="interval_hours must be a positive integer"):
        periods_per_year(-1)


def test_apr_from_rate_zero_interval_raises() -> None:
    """apr_from_rate propagates the ValueError from periods_per_year."""
    with pytest.raises(ValueError, match="interval_hours must be a positive integer"):
        apr_from_rate(0.0001, 0)


# ---------------------------------------------------------------------------
# Missing interval_hours defaults to 8
# ---------------------------------------------------------------------------


def test_funding_apr_default_interval_hours(tmp_path: Path) -> None:
    """Records with interval_hours=None default to 8 in the output."""
    # Write a Funding record with interval_hours=None
    rec = Funding(
        exchange=_EXCHANGE,
        symbol=_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=_BASE_NS,
        local_ts=_BASE_NS,
        funding_rate=0.0001,
        funding_timestamp=_BASE_NS,
        interval_hours=None,  # missing → should default to 8
    )
    asyncio.run(_write_funding(tmp_path, [rec]))
    catalog = Catalog(tmp_path)
    df = funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert len(df) == 1
    assert df["interval_hours"][0] == 8, (
        f"expected default interval_hours=8, got {df['interval_hours'][0]}"
    )
    # APR should be computed using the default 8h interval
    assert abs(df["apr"][0] - 0.0001 * 1095.0) < 1e-6


def test_funding_apr_rejects_nonpositive_interval(tmp_path: Path) -> None:
    """Regression: a stored interval_hours <= 0 must raise the validated ValueError.

    ``fill_null`` only replaces NULLs, so a corrupt 0 reaches the APR computation.
    The per-row APR routes through ``apr_from_rate`` → ``periods_per_year``, which
    rejects non-positive intervals — turning what was a ZeroDivisionError (for 0) or
    a silently negated APR (for negatives) into a legible ValueError.
    """
    rec = Funding(
        exchange=_EXCHANGE,
        symbol=_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=_BASE_NS,
        local_ts=_BASE_NS,
        funding_rate=0.0001,
        funding_timestamp=_BASE_NS,
        interval_hours=0,  # corrupt / non-positive — must be rejected, not divided by
    )
    asyncio.run(_write_funding(tmp_path, [rec]))
    catalog = Catalog(tmp_path)
    with pytest.raises(ValueError, match="interval_hours must be a positive integer"):
        funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)

"""Tests for Task 6.3 — Basis analytics (spot-future + perp).

Acceptance criteria (from the plan, verbatim golden numbers):
  - Write spot Trades at t=1000,3000 (px 100,102) and future Trades at
    t=2000,4000 (px 101,104) for two symbols.
  - spot_future_basis → 2 rows:
      at t=2000: spot=100 (asof prior), basis=1, basis_pct=0.01
      at t=4000: spot=102, basis=2, basis_pct≈0.0196
  - With expiry_ns one year out, annualized_pct ≈ basis_pct * 365/365
  - perp_basis: 2 DerivativeTicker rows (mark 100.5/index 100.0) →
    basis 0.5, basis_pct 0.005
  - Empty/one-sided input → empty DataFrame.
  - ruff + mypy clean.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
import pytest

from crocodile.analytics.basis import perp_basis, spot_future_basis
from crocodile.schema.enums import Side
from crocodile.schema.records import DerivativeTicker, Trade
from crocodile.store.catalog import Catalog
from crocodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Nanosecond base timestamps (simple small ints; same-date so catalog finds them)
# Using 2024-01-01 as base to ensure consistent date partitioning
_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC

# Offsets for spot and future timestamps (in nanoseconds)
_T1 = _BASE_NS + 1_000
_T2 = _BASE_NS + 2_000
_T3 = _BASE_NS + 3_000
_T4 = _BASE_NS + 4_000

_SPOT_SYMBOL = "deribit:BTC-SPOT"
_FUTURE_SYMBOL = "deribit:BTC-FUTURE"
_PERP_SYMBOL = "deribit:BTC-PERPETUAL"
_EXCHANGE = "deribit"

# One year in nanoseconds
_ONE_YEAR_NS = 365 * 24 * 3600 * 1_000_000_000


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_trade(ts: int, symbol: str, price: float) -> Trade:
    """Build a minimal Trade record."""
    return Trade(
        exchange=_EXCHANGE,
        symbol=symbol,
        symbol_raw=symbol.split(":", 1)[-1],
        exchange_ts=ts,
        local_ts=ts,
        id=f"t_{ts}",
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def _make_derivative_ticker(ts: int, mark: float, index: float) -> DerivativeTicker:
    """Build a minimal DerivativeTicker record."""
    return DerivativeTicker(
        exchange=_EXCHANGE,
        symbol=_PERP_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=ts,
        local_ts=ts,
        mark_price=mark,
        index_price=index,
    )


async def _write_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)  # type: ignore[arg-type]
    await sink.flush()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def basis_lake(tmp_path: Path) -> Path:
    """Write spot+future Trades to a temp lake."""
    records: list[object] = [
        # Spot: t=1000 px=100, t=3000 px=102
        _make_trade(_T1, _SPOT_SYMBOL, 100.0),
        _make_trade(_T3, _SPOT_SYMBOL, 102.0),
        # Future: t=2000 px=101, t=4000 px=104
        _make_trade(_T2, _FUTURE_SYMBOL, 101.0),
        _make_trade(_T4, _FUTURE_SYMBOL, 104.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    return tmp_path


@pytest.fixture()
def basis_catalog(basis_lake: Path) -> Catalog:
    return Catalog(basis_lake)


@pytest.fixture()
def perp_lake(tmp_path: Path) -> Path:
    """Write 2 DerivativeTicker rows (mark 100.5/100.5, index 100.0/100.0)."""
    records: list[object] = [
        _make_derivative_ticker(_T1, 100.5, 100.0),
        _make_derivative_ticker(_T2, 100.5, 100.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    return tmp_path


@pytest.fixture()
def perp_catalog(perp_lake: Path) -> Catalog:
    return Catalog(perp_lake)


# ---------------------------------------------------------------------------
# spot_future_basis — core golden numbers
# ---------------------------------------------------------------------------


def test_spot_future_basis_row_count(basis_catalog: Catalog) -> None:
    """2 future trades → 2 rows."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2, f"expected 2 rows, got {len(df)}"


def test_spot_future_basis_columns(basis_catalog: Catalog) -> None:
    """Required columns must be present."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    required = {"local_ts", "future_price", "spot_price", "basis", "basis_pct"}
    assert required.issubset(set(df.columns)), f"missing: {required - set(df.columns)}"


def test_spot_future_basis_row0_spot_asof(basis_catalog: Catalog) -> None:
    """At t=2000, nearest prior spot (t=1000) has price 100."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    row0 = df.row(0, named=True)
    assert abs(row0["spot_price"] - 100.0) < 1e-9, f"spot_price[0]={row0['spot_price']}"
    assert abs(row0["future_price"] - 101.0) < 1e-9, f"future_price[0]={row0['future_price']}"


def test_spot_future_basis_row0_basis_golden(basis_catalog: Catalog) -> None:
    """Row 0: basis=F-S=1, basis_pct=(F-S)/S=0.01."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    row0 = df.row(0, named=True)
    assert abs(row0["basis"] - 1.0) < 1e-9, f"basis[0]={row0['basis']}"
    assert abs(row0["basis_pct"] - 0.01) < 1e-9, f"basis_pct[0]={row0['basis_pct']}"


def test_spot_future_basis_row1_spot_asof(basis_catalog: Catalog) -> None:
    """At t=4000, nearest prior spot (t=3000) has price 102."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    row1 = df.row(1, named=True)
    assert abs(row1["spot_price"] - 102.0) < 1e-9, f"spot_price[1]={row1['spot_price']}"
    assert abs(row1["future_price"] - 104.0) < 1e-9, f"future_price[1]={row1['future_price']}"


def test_spot_future_basis_row1_basis_golden(basis_catalog: Catalog) -> None:
    """Row 1: basis=2, basis_pct=2/102≈0.01961."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    row1 = df.row(1, named=True)
    assert abs(row1["basis"] - 2.0) < 1e-9, f"basis[1]={row1['basis']}"
    expected_pct = 2.0 / 102.0
    assert abs(row1["basis_pct"] - expected_pct) < 1e-9, (
        f"basis_pct[1]={row1['basis_pct']}, expected≈{expected_pct}"
    )


def test_spot_future_basis_ordered_ascending(basis_catalog: Catalog) -> None:
    """Rows must be ordered by local_ts ascending."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    ts_list = df["local_ts"].to_list()
    assert ts_list == sorted(ts_list), "rows not ordered by local_ts"


# ---------------------------------------------------------------------------
# spot_future_basis — annualized_pct with expiry_ns
# ---------------------------------------------------------------------------


def test_spot_future_basis_annualized_pct_column_present(basis_catalog: Catalog) -> None:
    """When expiry_ns is given, annualized_pct column must appear."""
    expiry_ns = _T4 + _ONE_YEAR_NS
    df = spot_future_basis(
        basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4, expiry_ns=expiry_ns
    )
    assert "annualized_pct" in df.columns, "annualized_pct column missing"


def test_spot_future_basis_annualized_pct_row0_golden(basis_catalog: Catalog) -> None:
    """Row 0: expiry is one year from T2; days_to_expiry≈365 so annualized≈basis_pct."""
    # Place expiry exactly 365 days after T2 (the first future trade)
    expiry_ns = _T2 + _ONE_YEAR_NS
    df = spot_future_basis(
        basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4, expiry_ns=expiry_ns
    )
    row0 = df.row(0, named=True)
    basis_pct = row0["basis_pct"]
    ann_pct = row0["annualized_pct"]
    # annualized_pct = basis_pct * 365 / days_to_expiry ≈ basis_pct * 365/365 = basis_pct
    # (tiny offset from exact 365-day because days_to_expiry is not exactly 365 due to T2 offset)
    days_to_expiry = (_T2 + _ONE_YEAR_NS - _T2) / (86_400e9)
    expected = basis_pct * 365.0 / days_to_expiry
    assert abs(ann_pct - expected) < 1e-9, f"annualized_pct={ann_pct}, expected≈{expected}"


def test_spot_future_basis_no_annualized_pct_without_expiry(basis_catalog: Catalog) -> None:
    """Without expiry_ns, annualized_pct column must NOT appear."""
    df = spot_future_basis(basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    assert "annualized_pct" not in df.columns


# ---------------------------------------------------------------------------
# spot_future_basis — empty / one-sided edge cases
# ---------------------------------------------------------------------------


def test_spot_future_basis_empty_lake(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    catalog = Catalog(tmp_path)
    df = spot_future_basis(catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_spot_future_basis_only_spot(tmp_path: Path) -> None:
    """Only spot trades, no future trades → empty DataFrame."""
    records: list[object] = [
        _make_trade(_T1, _SPOT_SYMBOL, 100.0),
        _make_trade(_T3, _SPOT_SYMBOL, 102.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = spot_future_basis(catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    assert len(df) == 0


def test_spot_future_basis_only_future(tmp_path: Path) -> None:
    """Only future trades, no spot trades → empty DataFrame."""
    records: list[object] = [
        _make_trade(_T2, _FUTURE_SYMBOL, 101.0),
        _make_trade(_T4, _FUTURE_SYMBOL, 104.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = spot_future_basis(catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# perp_basis — core golden numbers
# ---------------------------------------------------------------------------


def test_perp_basis_row_count(perp_catalog: Catalog) -> None:
    """2 DerivativeTicker rows → 2 rows."""
    df = perp_basis(perp_catalog, _PERP_SYMBOL, _T1, _T4)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2, f"expected 2 rows, got {len(df)}"


def test_perp_basis_columns(perp_catalog: Catalog) -> None:
    """Required columns must be present."""
    df = perp_basis(perp_catalog, _PERP_SYMBOL, _T1, _T4)
    required = {"local_ts", "mark_price", "index_price", "basis", "basis_pct"}
    assert required.issubset(set(df.columns)), f"missing: {required - set(df.columns)}"


def test_perp_basis_basis_golden(perp_catalog: Catalog) -> None:
    """mark=100.5, index=100.0 → basis=0.5, basis_pct=0.005."""
    df = perp_basis(perp_catalog, _PERP_SYMBOL, _T1, _T4)
    row0 = df.row(0, named=True)
    assert abs(row0["basis"] - 0.5) < 1e-9, f"basis={row0['basis']}"
    assert abs(row0["basis_pct"] - 0.005) < 1e-9, f"basis_pct={row0['basis_pct']}"


def test_perp_basis_ordered_ascending(perp_catalog: Catalog) -> None:
    """Rows must be ordered by local_ts ascending."""
    df = perp_basis(perp_catalog, _PERP_SYMBOL, _T1, _T4)
    ts_list = df["local_ts"].to_list()
    assert ts_list == sorted(ts_list), "rows not ordered by local_ts"


def test_perp_basis_empty_lake(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    catalog = Catalog(tmp_path)
    df = perp_basis(catalog, _PERP_SYMBOL, _T1, _T4)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_perp_basis_skips_null_prices(tmp_path: Path) -> None:
    """Rows where mark_price or index_price is null must be skipped."""
    records: list[object] = [
        # Good row
        _make_derivative_ticker(_T1, 100.5, 100.0),
        # Row with null mark_price
        DerivativeTicker(
            exchange=_EXCHANGE,
            symbol=_PERP_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_T2,
            local_ts=_T2,
            mark_price=None,
            index_price=100.0,
        ),
        # Row with null index_price
        DerivativeTicker(
            exchange=_EXCHANGE,
            symbol=_PERP_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_T3,
            local_ts=_T3,
            mark_price=100.5,
            index_price=None,
        ),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = perp_basis(catalog, _PERP_SYMBOL, _T1, _T4)
    # Only the first row passes the null filter
    assert len(df) == 1, f"expected 1 row (null rows skipped), got {len(df)}"


def test_perp_basis_symbol_isolation(perp_lake: Path) -> None:
    """Query for a different symbol returns empty."""
    catalog = Catalog(perp_lake)
    df = perp_basis(catalog, "deribit:ETH-PERPETUAL", _T1, _T4)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# T6 regression: perp_basis must not emit inf when index_price==0
# ---------------------------------------------------------------------------


def test_perp_basis_zero_index_price_does_not_emit_inf(tmp_path: Path) -> None:
    """Rows with index_price==0 must be filtered out (not produce inf basis_pct).

    The fix: filter to mark_price > 0 AND index_price > 0 before computing
    basis_pct, so division-by-zero never reaches the output.
    """
    records: list[object] = [
        # Good row
        _make_derivative_ticker(_T1, 100.5, 100.0),
        # Pathological row: index_price = 0 would create inf if not filtered
        DerivativeTicker(
            exchange=_EXCHANGE,
            symbol=_PERP_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_T2,
            local_ts=_T2,
            mark_price=100.5,
            index_price=0.0,
        ),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = perp_basis(catalog, _PERP_SYMBOL, _T1, _T4)

    # Only the row with index_price=100.0 should appear
    assert len(df) == 1, f"expected 1 row (zero index_price filtered), got {len(df)}"
    assert all(
        v is not None and v != float("inf") and v != float("-inf")
        for v in df["basis_pct"].to_list()
    ), f"basis_pct contains inf: {df['basis_pct'].to_list()}"


def test_perp_basis_zero_mark_price_does_not_appear(tmp_path: Path) -> None:
    """Rows with mark_price==0 must be filtered out."""
    records: list[object] = [
        _make_derivative_ticker(_T1, 100.5, 100.0),
        DerivativeTicker(
            exchange=_EXCHANGE,
            symbol=_PERP_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_T2,
            local_ts=_T2,
            mark_price=0.0,
            index_price=100.0,
        ),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = perp_basis(catalog, _PERP_SYMBOL, _T1, _T4)
    assert len(df) == 1, f"expected 1 row (zero mark_price filtered), got {len(df)}"


# ---------------------------------------------------------------------------
# T6 regression: spot_future_basis — no division by zero when spot_price==0
# ---------------------------------------------------------------------------


def test_spot_future_basis_zero_spot_price_does_not_crash(tmp_path: Path) -> None:
    """A spot trade with price=0.0 must be excluded from the ASOF join result
    (or at minimum not produce inf/nan in basis_pct).

    The fix: add WHERE spot_price > 0 in the SQL so zero-price spot rows
    do not participate in the basis calculation.
    """
    records: list[object] = [
        # Zero-price spot trade (bad data)
        _make_trade(_T1, _SPOT_SYMBOL, 0.0),
        # Good spot trade
        _make_trade(_T3, _SPOT_SYMBOL, 100.0),
        # Future trades
        _make_trade(_T2, _FUTURE_SYMBOL, 101.0),
        _make_trade(_T4, _FUTURE_SYMBOL, 104.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = spot_future_basis(catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4)

    # Check that no inf or nan appear in the result
    if len(df) > 0:
        for col in ("basis_pct",):
            vals = df[col].to_list()
            for v in vals:
                assert v is None or (
                    not (v == float("inf") or v == float("-inf") or v != v)
                ), f"column {col!r} contains bad value: {v} in {vals}"


# ---------------------------------------------------------------------------
# T6 regression: spot_future_basis annualized_pct with expired/same-timestamp
# ---------------------------------------------------------------------------


def test_spot_future_basis_expired_annualized_returns_none_or_zero(
    basis_catalog: Catalog,
) -> None:
    """When days_to_expiry <= 0 (expired or same-timestamp), annualized_pct
    must be None or 0.0 rather than a raw/garbage value.
    """
    # expiry_ns set to _T2 itself — same timestamp as the first future trade.
    # days_to_expiry = 0 → cannot annualise.
    expiry_ns = _T2
    df = spot_future_basis(
        basis_catalog, _FUTURE_SYMBOL, _SPOT_SYMBOL, _T1, _T4, expiry_ns=expiry_ns
    )
    assert "annualized_pct" in df.columns, "annualized_pct column missing"
    for row in df.iter_rows(named=True):
        v = row["annualized_pct"]
        # Must be None or 0.0, not garbage (inf/nan or raw basis_pct stored blindly)
        assert v is None or v == 0.0, (
            f"annualized_pct={v!r} for expired/same-ts row — expected None or 0.0"
        )

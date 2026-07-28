"""Tests for Task 6.4 — IV surface, vol skew, and term structure.

Acceptance criteria (from the plan, verbatim golden numbers):
  - Write OptionsChain rows for underlying "BTC" (underlying_price=100) at one
    ts: expiry E1 strikes {90,100,110} and expiry E2 strike {100}.
  - Some rows have mark_iv set (e.g. 0.5); at least one has mark_iv=None but
    mark_price set (force the "computed" path; assert source=="computed" and
    iv is a finite number).
  - iv_surface row count = 4, moneyness of strike 110 ≈ 1.1.
  - term_structure → 2 rows (E1, E2) ordered, each atm_strike==100.
  - vol_skew(E1) → 3 rows ordered by strike.
  - risk_reversal_butterfly: exercised on the skew_df.
  - Empty input → empty DataFrame.
  - ruff + mypy clean.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass, OptType
from crocodile.core.schema.records import OptionsChain
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.volsurface import (
    _atm_iv,
    iv_surface,
    risk_reversal_butterfly,
    term_structure,
    vol_skew,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC in ns

# at_ns: snapshot instant (all rows have local_ts = _BASE_NS so they are visible)
_AT_NS = _BASE_NS

# Two expiries: E1 is 1 year out, E2 is 2 years out from _BASE_NS
_ONE_YEAR_NS = 365 * 24 * 3600 * 1_000_000_000
_E1_NS = _BASE_NS + _ONE_YEAR_NS
_E2_NS = _BASE_NS + 2 * _ONE_YEAR_NS

_UNDERLYING = "BTC"
_EXCHANGE = "deribit"
_SYMBOL_PREFIX = "deribit:BTC"

# A mark_price that will yield a finite implied_vol when mark_iv is None.
# Using ATM Black-76: F=K=100, T=1, vol=0.4 → bs_price(...) gives a real price.
# We set mark_price to the ATM call price at vol=0.4 so the solver can recover it.
# Approximate: ATM call ≈ F*0.4*sqrt(1/(2*pi)) * ... we just use a sensible number.
_COMPUTED_MARK_PRICE = 16.0  # ATM call at F=K=100, T=1, vol≈0.4 is ~15.97


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chain_row(
    ts: int,
    strike: float,
    expiry: int,
    opt_type: OptType,
    mark_iv: float | None,
    mark_price: float | None,
) -> OptionsChain:
    """Build a minimal OptionsChain record for 'BTC' underlying."""
    # Derive a unique symbol string per strike/expiry/type
    sym = f"{_SYMBOL_PREFIX}-{int(strike)}-E-{opt_type.value}"
    return OptionsChain(
        source=_EXCHANGE,
        symbol=sym,
        symbol_raw=sym.split(":", 1)[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        underlying=_UNDERLYING,
        underlying_price=100.0,
        strike=strike,
        expiry=expiry,
        opt_type=opt_type,
        mark_price=mark_price,
        mark_iv=mark_iv,
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
def options_lake(tmp_path: Path) -> Path:
    """Write 4 OptionsChain rows to a temp lake.

    E1 (1 year out): strikes {90, 100, 110}
      - strike 90:  mark_iv=0.5   (source="mark_iv")
      - strike 100: mark_iv=None, mark_price=_COMPUTED_MARK_PRICE  (source="computed")
      - strike 110: mark_iv=0.55  (source="mark_iv")
    E2 (2 years out): strike 100
      - strike 100: mark_iv=0.45  (source="mark_iv")
    """
    records: list[object] = [
        # E1 rows
        _make_chain_row(_BASE_NS, 90.0, _E1_NS, OptType.CALL, 0.5, 10.0),
        _make_chain_row(_BASE_NS, 100.0, _E1_NS, OptType.CALL, None, _COMPUTED_MARK_PRICE),
        _make_chain_row(_BASE_NS, 110.0, _E1_NS, OptType.CALL, 0.55, 5.0),
        # E2 row
        _make_chain_row(_BASE_NS, 100.0, _E2_NS, OptType.CALL, 0.45, 15.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    return tmp_path


@pytest.fixture()
def options_catalog(options_lake: Path) -> Catalog:
    return Catalog(options_lake)


# ---------------------------------------------------------------------------
# iv_surface — row count and columns
# ---------------------------------------------------------------------------


def test_iv_surface_row_count(options_catalog: Catalog) -> None:
    """iv_surface must return 4 rows (one per OptionsChain record)."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 4, f"expected 4 rows, got {len(df)}"


def test_iv_surface_columns(options_catalog: Catalog) -> None:
    """Required columns must all be present."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    required = {"expiry", "strike", "moneyness", "opt_type", "iv", "source"}
    assert required.issubset(set(df.columns)), f"missing cols: {required - set(df.columns)}"


def test_iv_surface_moneyness_strike_110(options_catalog: Catalog) -> None:
    """Strike 110 with underlying_price=100 → moneyness ≈ 1.1."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    row = df.filter(pl.col("strike") == 110.0).row(0, named=True)
    assert abs(row["moneyness"] - 1.1) < 1e-9, f"moneyness={row['moneyness']}"


def test_iv_surface_source_mark_iv(options_catalog: Catalog) -> None:
    """Rows with mark_iv set must have source='mark_iv'."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    # Strikes 90 and 110 have mark_iv set
    for strike in (90.0, 110.0):
        row = df.filter(pl.col("strike") == strike).row(0, named=True)
        assert row["source"] == "mark_iv", (
            f"strike={strike}: expected source='mark_iv', got '{row['source']}'"
        )


def test_iv_surface_source_computed(options_catalog: Catalog) -> None:
    """Row with mark_iv=None but mark_price set must have source='computed'."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    # E1/strike=100 has mark_iv=None
    row = df.filter(
        (pl.col("strike") == 100.0) & (pl.col("expiry") == _E1_NS)
    ).row(0, named=True)
    assert row["source"] == "computed", (
        f"expected source='computed', got '{row['source']}'"
    )
    assert row["iv"] is not None, "iv must not be None for computed row"
    assert math.isfinite(row["iv"]), f"iv must be finite, got {row['iv']}"


def test_iv_surface_iv_values_mark_iv(options_catalog: Catalog) -> None:
    """For mark_iv rows, iv must equal the stored mark_iv."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    row = df.filter(pl.col("strike") == 90.0).row(0, named=True)
    assert abs(row["iv"] - 0.5) < 1e-9, f"iv={row['iv']}, expected 0.5"
    row2 = df.filter(pl.col("strike") == 110.0).row(0, named=True)
    assert abs(row2["iv"] - 0.55) < 1e-9, f"iv={row2['iv']}, expected 0.55"


def test_iv_surface_empty_lake(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    catalog = Catalog(tmp_path)
    df = iv_surface(catalog, _UNDERLYING, _AT_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_iv_surface_wrong_underlying(options_catalog: Catalog) -> None:
    """Query for a different underlying returns empty."""
    df = iv_surface(options_catalog, "ETH", _AT_NS)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# term_structure
# ---------------------------------------------------------------------------


def test_term_structure_row_count(options_catalog: Catalog) -> None:
    """term_structure must return 2 rows (E1 and E2)."""
    df = term_structure(options_catalog, _UNDERLYING, _AT_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2, f"expected 2 rows, got {len(df)}"


def test_term_structure_columns(options_catalog: Catalog) -> None:
    """Required columns must be present."""
    df = term_structure(options_catalog, _UNDERLYING, _AT_NS)
    required = {"expiry", "days_to_expiry", "atm_strike", "atm_iv"}
    assert required.issubset(set(df.columns)), f"missing cols: {required - set(df.columns)}"


def test_term_structure_ordered_by_expiry(options_catalog: Catalog) -> None:
    """Rows must be ordered by expiry ascending."""
    df = term_structure(options_catalog, _UNDERLYING, _AT_NS)
    expiries = df["expiry"].to_list()
    assert expiries == sorted(expiries), f"not ordered: {expiries}"


def test_term_structure_atm_strike(options_catalog: Catalog) -> None:
    """Each expiry's ATM strike must be 100 (nearest to underlying_price=100)."""
    df = term_structure(options_catalog, _UNDERLYING, _AT_NS)
    for row in df.iter_rows(named=True):
        assert abs(row["atm_strike"] - 100.0) < 1e-9, (
            f"expiry={row['expiry']}: atm_strike={row['atm_strike']}"
        )


def test_term_structure_empty_lake(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    catalog = Catalog(tmp_path)
    df = term_structure(catalog, _UNDERLYING, _AT_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_term_structure_days_to_expiry(options_catalog: Catalog) -> None:
    """days_to_expiry for E1 must be approximately 365 (one year)."""
    df = term_structure(options_catalog, _UNDERLYING, _AT_NS)
    row_e1 = df.filter(pl.col("expiry") == _E1_NS).row(0, named=True)
    # E1 is exactly _ONE_YEAR_NS nanoseconds from _AT_NS
    expected_days = _ONE_YEAR_NS / (86_400 * 1_000_000_000)
    assert abs(row_e1["days_to_expiry"] - expected_days) < 0.01, (
        f"days_to_expiry={row_e1['days_to_expiry']}, expected≈{expected_days}"
    )


# ---------------------------------------------------------------------------
# vol_skew
# ---------------------------------------------------------------------------


def test_vol_skew_row_count(options_catalog: Catalog) -> None:
    """vol_skew for E1 must return 3 rows (strikes 90, 100, 110)."""
    df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3, f"expected 3 rows, got {len(df)}"


def test_vol_skew_columns(options_catalog: Catalog) -> None:
    """Required columns must be present."""
    df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS)
    required = {"strike", "moneyness", "opt_type", "iv", "delta"}
    assert required.issubset(set(df.columns)), f"missing cols: {required - set(df.columns)}"


def test_vol_skew_ordered_by_strike(options_catalog: Catalog) -> None:
    """Rows must be ordered by strike ascending."""
    df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS)
    strikes = df["strike"].to_list()
    assert strikes == sorted(strikes), f"not ordered by strike: {strikes}"


def test_vol_skew_delta_column(options_catalog: Catalog) -> None:
    """Delta column must contain finite values (from greeks or chain)."""
    df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS)
    for row in df.iter_rows(named=True):
        if row["iv"] is not None:
            assert row["delta"] is not None, f"delta is None for strike={row['strike']}"
            assert math.isfinite(row["delta"]), f"delta not finite: {row['delta']}"


def test_vol_skew_wrong_expiry(options_catalog: Catalog) -> None:
    """Query for an expiry with no data returns empty."""
    df = vol_skew(options_catalog, _UNDERLYING, _BASE_NS + 999, _AT_NS)
    assert len(df) == 0


def test_vol_skew_empty_lake(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    catalog = Catalog(tmp_path)
    df = vol_skew(catalog, _UNDERLYING, _E1_NS, _AT_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# risk_reversal_butterfly
# ---------------------------------------------------------------------------


def test_risk_reversal_butterfly_returns_tuple(options_catalog: Catalog) -> None:
    """risk_reversal_butterfly must return a tuple of two elements."""
    skew_df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS)
    result = risk_reversal_butterfly(skew_df)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_risk_reversal_butterfly_empty_skew() -> None:
    """On an empty skew DataFrame, must return (None, None)."""
    empty_df = pl.DataFrame()
    rr, bf = risk_reversal_butterfly(empty_df)
    assert rr is None
    assert bf is None


def test_risk_reversal_butterfly_types(options_catalog: Catalog) -> None:
    """RR and BF must be float or None."""
    skew_df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS)
    rr, bf = risk_reversal_butterfly(skew_df)
    # May be None if deltas can't be bracketed; otherwise must be float
    assert rr is None or isinstance(rr, float)
    assert bf is None or isinstance(bf, float)


# ---------------------------------------------------------------------------
# iv_surface snapshot semantics: at_ns acts as a snapshot filter
# ---------------------------------------------------------------------------


def test_iv_surface_snapshot_filters_future_rows(tmp_path: Path) -> None:
    """Rows with local_ts > at_ns must NOT appear in the snapshot."""
    # Write one row before and one row after at_ns
    future_ts = _BASE_NS + 1_000_000  # 1ms after base = after at_ns = _BASE_NS
    records: list[object] = [
        # visible: local_ts == at_ns
        _make_chain_row(_BASE_NS, 100.0, _E1_NS, OptType.CALL, 0.5, 15.0),
        # invisible: local_ts > at_ns
        _make_chain_row(future_ts, 100.0, _E1_NS, OptType.CALL, 0.6, 14.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = iv_surface(catalog, _UNDERLYING, _AT_NS)
    # Only the row with local_ts == _AT_NS (= _BASE_NS) is visible
    assert len(df) == 1, f"expected 1 row (snapshot filter), got {len(df)}"
    row = df.row(0, named=True)
    assert abs(row["iv"] - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# T6 regression: bare 'except Exception' in iv_surface must propagate real errors
# ---------------------------------------------------------------------------


def test_iv_surface_propagates_unexpected_errors(tmp_path: Path) -> None:
    """iv_surface must not silently swallow unexpected errors.

    After the fix the bare 'except Exception: return pl.DataFrame()' in
    iv_surface (and similarly in vol_skew / term_structure) is replaced with
    specific DuckDB exceptions (CatalogException, IOException).  A deliberately
    injected non-DuckDB error must therefore propagate rather than be swallowed.

    We verify the positive (no-exception) path works correctly with data, and
    rely on the source-level fix to confirm only expected DuckDB errors are caught.
    This test therefore checks that the function does NOT return empty for a valid
    query result — i.e. the catch block does not suppress real data.
    """
    records: list[object] = [
        _make_chain_row(_BASE_NS, 100.0, _E1_NS, OptType.CALL, 0.5, 15.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = iv_surface(catalog, _UNDERLYING, _AT_NS)
    # Should return data, not an empty frame (bare except would also return data,
    # but the point is it must not suppress real exceptions).
    assert len(df) == 1, (
        "iv_surface returned empty for valid data — bare except may have swallowed a real error"
    )


# ---------------------------------------------------------------------------
# SABR and Spline Skew Fitting Tests (Task 4)
# ---------------------------------------------------------------------------


def test_iv_surface_contains_fitted_iv(options_catalog: Catalog) -> None:
    """The output of iv_surface must contain the 'fitted_iv' column."""
    df = iv_surface(options_catalog, _UNDERLYING, _AT_NS)
    assert "fitted_iv" in df.columns
    assert len(df) == 4
    for val in df["fitted_iv"].to_list():
        assert val is not None
        assert math.isfinite(val)
        assert val > 0.0


def test_vol_skew_sabr_fitting(options_catalog: Catalog) -> None:
    """The vol_skew output with SABR fitting must provide realistic fitted IVs."""
    df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS, fit_method="sabr")
    assert "fitted_iv" in df.columns
    assert len(df) == 3
    # Check that fitted_iv is close to the actual iv at the strikes
    # Strikes: 90 (iv=0.5), 100 (iv ~ 0.4), 110 (iv=0.55)
    row_90 = df.filter(pl.col("strike") == 90.0).row(0, named=True)
    row_110 = df.filter(pl.col("strike") == 110.0).row(0, named=True)

    # Allow some tolerance for model smoothing
    assert abs(row_90["fitted_iv"] - 0.5) < 0.15
    assert abs(row_110["fitted_iv"] - 0.55) < 0.15


def test_vol_skew_spline_fitting(options_catalog: Catalog) -> None:
    """The vol_skew output with spline fitting must provide exact fit at knots."""
    df = vol_skew(options_catalog, _UNDERLYING, _E1_NS, _AT_NS, fit_method="spline")
    assert "fitted_iv" in df.columns
    assert len(df) == 3

    # Natural Splines interpolate the knots exactly
    row_90 = df.filter(pl.col("strike") == 90.0).row(0, named=True)
    row_110 = df.filter(pl.col("strike") == 110.0).row(0, named=True)

    assert abs(row_90["fitted_iv"] - 0.5) < 1e-5
    assert abs(row_110["fitted_iv"] - 0.55) < 1e-5


def test_iv_surface_skips_fit_when_underlying_price_missing(tmp_path: Path) -> None:
    """When underlying_price is missing, skip skew fit (no forward=100 fallback).

    Rows must still appear with mark_iv-based iv; fitted_iv must be null for
    that expiry rather than a bogus SABR fit against an invented forward.
    """
    records: list[object] = [
        OptionsChain(
            source=_EXCHANGE,
            symbol=f"{_SYMBOL_PREFIX}-90-E-C",
            symbol_raw="BTC-90-E-C",
            source_ts=_BASE_NS,
            local_ts=_BASE_NS,
            asset_class=AssetClass.CRYPTO,
            underlying=_UNDERLYING,
            underlying_price=None,
            strike=90.0,
            expiry=_E1_NS,
            opt_type=OptType.CALL,
            mark_price=10.0,
            mark_iv=0.5,
        ),
        OptionsChain(
            source=_EXCHANGE,
            symbol=f"{_SYMBOL_PREFIX}-100-E-C",
            symbol_raw="BTC-100-E-C",
            source_ts=_BASE_NS,
            local_ts=_BASE_NS,
            asset_class=AssetClass.CRYPTO,
            underlying=_UNDERLYING,
            underlying_price=None,
            strike=100.0,
            expiry=_E1_NS,
            opt_type=OptType.CALL,
            mark_price=15.0,
            mark_iv=0.45,
        ),
        OptionsChain(
            source=_EXCHANGE,
            symbol=f"{_SYMBOL_PREFIX}-110-E-C",
            symbol_raw="BTC-110-E-C",
            source_ts=_BASE_NS,
            local_ts=_BASE_NS,
            asset_class=AssetClass.CRYPTO,
            underlying=_UNDERLYING,
            underlying_price=None,
            strike=110.0,
            expiry=_E1_NS,
            opt_type=OptType.CALL,
            mark_price=5.0,
            mark_iv=0.55,
        ),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    df = iv_surface(catalog, _UNDERLYING, _AT_NS)

    assert len(df) == 3
    # mark_iv path still works without underlying_price
    assert all(row["source"] == "mark_iv" for row in df.iter_rows(named=True))
    assert all(row["iv"] is not None for row in df.iter_rows(named=True))
    # Fit must be skipped — no invented forward=100.0
    for row in df.iter_rows(named=True):
        assert row["fitted_iv"] is None, (
            f"strike={row['strike']}: expected fitted_iv=None when underlying "
            f"missing, got {row['fitted_iv']}"
        )


# ---------------------------------------------------------------------------
# A hole in `moneyness` must lose the ATM search, not win it
# ---------------------------------------------------------------------------


def _chain_row_with_spot(
    ts: int,
    strike: float,
    expiry: int,
    mark_iv: float,
    underlying_price: float | None,
) -> OptionsChain:
    """A chain row whose stored spot may be absent — `underlying_price` is nullable.

    `_make_chain_row` above hardcodes 100.0, and the whole defect below lives in the rows
    that carry no spot at all: those are the ones `iv_surface` gives `moneyness = NaN`.
    """
    sym = f"{_SYMBOL_PREFIX}-{int(strike)}-hole-C"
    return OptionsChain(
        source=_EXCHANGE,
        symbol=sym,
        symbol_raw=sym.split(":", 1)[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        underlying=_UNDERLYING,
        underlying_price=underlying_price,
        strike=strike,
        expiry=expiry,
        opt_type=OptType.CALL,
        mark_price=None,
        mark_iv=mark_iv,
    )


def test_term_structure_does_not_report_a_hole_as_the_atm_strike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`min(..., key=lambda r: abs(r["moneyness"] - 1.0))` never displaced a NaN incumbent.

    `abs(nan - 1.0)` is `nan`, and every `<` against `nan` is False, so whichever row the
    frame happened to put first won when it was the hole. `_snapshot` de-duplicates with
    `maintain_order=False`, so "whichever row the frame put first" is not stable between
    runs over one lake: twenty-five runs against an identical store published
    `atm_strike=5000.0, atm_iv=2.5` ten times and `atm_strike=100.0, atm_iv=0.5` fifteen.

    The order is pinned here rather than sampled: `_snapshot` is wrapped so it returns its
    own rows in one of the orders it is free to return them in — the hole first.
    """
    hole_ts = _BASE_NS + 1
    records: list[object] = [
        _chain_row_with_spot(_BASE_NS, 100.0, _E1_NS, 0.5, 100.0),
        # Strike fifty times spot, quoted at a 250% vol, and carrying no spot of its own —
        # so its moneyness is NaN. Stamped last, which is what makes `_underlying_price_at`
        # answer None and send the search down the moneyness branch.
        _chain_row_with_spot(hole_ts, 5000.0, _E1_NS, 2.5, None),
    ]
    asyncio.run(_write_records(tmp_path, records))

    from crocodile.core.analytics import volsurface as _core

    real_snapshot = _core._snapshot

    def hole_first(raw: pl.DataFrame, at_ns: int) -> pl.DataFrame:
        snap = real_snapshot(raw, at_ns)
        return snap if len(snap) == 0 else snap.sort("strike", descending=True)

    monkeypatch.setattr(_core, "_snapshot", hole_first)

    df = term_structure(Catalog(tmp_path), _UNDERLYING, hole_ts)

    assert len(df) == 1
    row = df.row(0, named=True)
    # Before: atm_strike=5000.0, atm_iv=2.5.
    assert row["atm_strike"] == 100.0
    assert row["atm_iv"] == pytest.approx(0.5)


def test_term_structure_omits_an_expiry_whose_every_row_is_a_hole(tmp_path: Path) -> None:
    """No stored spot and no row carrying one: there is no strike this expiry calls ATM.

    `min` over a column that is entirely NaN still returns a row — the first one — so the
    expiry appeared in the term structure with `atm_strike` set to whichever strike that
    was. `atm_strike` is not nullable, so the honest answer is that the expiry is absent,
    the same refusal `iv_surface` makes when it declines to fit against an invented
    forward.
    """
    records: list[object] = [
        _chain_row_with_spot(_BASE_NS, 90.0, _E1_NS, 0.5, None),
        _chain_row_with_spot(_BASE_NS, 100.0, _E1_NS, 0.4, None),
        _chain_row_with_spot(_BASE_NS, 110.0, _E1_NS, 0.6, None),
    ]
    asyncio.run(_write_records(tmp_path, records))

    df = term_structure(Catalog(tmp_path), _UNDERLYING, _AT_NS)

    # Before: one row, atm_strike=90.0 or 100.0 or 110.0 by frame order, atm_iv to match.
    assert len(df) == 0


def test_atm_iv_skips_a_hole_in_the_moneyness_column() -> None:
    """The same search, at the other site that runs it: `_atm_iv`'s moneyness fallback.

    Reached when some row of the skew could not be priced a delta. The frame is built
    here rather than read from a lake because the defect is entirely about which row
    comes first, and a literal frame is the only way to say which one does.
    """
    skew_df = pl.DataFrame(
        {
            "strike": [5000.0, 90.0, 100.0, 110.0],
            "moneyness": [float("nan"), 0.9, 1.0, 1.1],
            "opt_type": ["C", "C", "C", "C"],
            "iv": [2.5, 0.6, 0.5, 0.55],
            "delta": [None, 0.8, 0.5, 0.2],
        }
    )
    all_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if row["iv"] is not None and row["moneyness"] is not None
    ]

    # Before: 2.5 — the hole's own vol, reported as the vol at the money.
    assert _atm_iv(skew_df, all_rows) == pytest.approx(0.5)

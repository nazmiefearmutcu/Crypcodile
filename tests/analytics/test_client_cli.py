"""Tests for Task 6.5 — CrypcodileClient analytics methods.

Acceptance criteria (from the plan, verbatim):
  - populate a tmp lake; CrypcodileClient(tmp).funding_apr(...) equals
    analytics.funding.funding_apr(catalog, ...).
  - ruff + mypy clean.

The other half of the plan — "invoke the CLI via Typer's CliRunner" for ``funding-apr``,
``basis``, ``iv-surface``, ``term-structure``, ``vol-skew`` and ``risk-reversal`` — went with
the hand-written crypto Typer app. ``tests/surfaces/test_end_to_end.py`` owns reaching a
capability from the CLI, ``tests/conformance/test_surfaces.py`` owns it being there at all,
and ``tests/capabilities/test_analytics.py`` owns the numbers. The ``basis`` argument-mode
multiplexer those tests pinned no longer exists: ``basis``, ``perp-basis`` and
``spot-future-basis`` are three separately declared capabilities.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass, OptType
from crocodile.core.schema.records import Funding, OptionsChain
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.funding import funding_apr as analytics_funding_apr
from crocodile.crypto.analytics.volsurface import iv_surface as analytics_iv_surface
from crocodile.crypto.client.client import CrypcodileClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC
_8H_NS = 8 * 3600 * 1_000_000_000
_ONE_YEAR_NS = 365 * 24 * 3600 * 1_000_000_000

_SYMBOL = "deribit:BTC-PERPETUAL"
_EXCHANGE = "deribit"
_UNDERLYING = "BTC"

_RATES = [0.0001, -0.0002, 0.0003]
_INTERVAL_HOURS = 8

_FUTURE_SYMBOL = "deribit:BTC-PERPETUAL"
_SPOT_SYMBOL = "binance-spot:BTCUSDT"
_PERP_SYMBOL = "deribit:BTC-PERPETUAL"


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _write_funding_records(data_dir: Path, records: list[Funding]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)
    await sink.flush()


async def _write_options_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)  # type: ignore[arg-type]
    await sink.flush()


# ---------------------------------------------------------------------------
# Fixtures: funding lake
# ---------------------------------------------------------------------------


@pytest.fixture()
def funding_lake(tmp_path: Path) -> Path:
    """Write 3 Funding records to a temp lake and return the dir."""
    records = [
        Funding(
            source=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            source_ts=_BASE_NS + i * _8H_NS,
            local_ts=_BASE_NS + i * _8H_NS,
            asset_class=AssetClass.CRYPTO,
            funding_rate=rate,
            funding_timestamp=_BASE_NS + i * _8H_NS,
            interval_hours=_INTERVAL_HOURS,
        )
        for i, rate in enumerate(_RATES)
    ]
    asyncio.run(_write_funding_records(tmp_path, records))
    return tmp_path


# ---------------------------------------------------------------------------
# Fixtures: options lake
# ---------------------------------------------------------------------------


@pytest.fixture()
def options_lake(tmp_path: Path) -> Path:
    """Write a small OptionsChain fixture to a temp lake."""
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    records: list[object] = [
        OptionsChain(
            source=_EXCHANGE,
            symbol="deribit:BTC-90-C",
            symbol_raw="BTC-90-C",
            source_ts=_BASE_NS,
            local_ts=_BASE_NS,
            asset_class=AssetClass.CRYPTO,
            underlying=_UNDERLYING,
            underlying_price=100.0,
            strike=90.0,
            expiry=e1_ns,
            opt_type=OptType.CALL,
            mark_price=15.0,
            mark_iv=0.5,
        ),
        OptionsChain(
            source=_EXCHANGE,
            symbol="deribit:BTC-100-C",
            symbol_raw="BTC-100-C",
            source_ts=_BASE_NS,
            local_ts=_BASE_NS,
            asset_class=AssetClass.CRYPTO,
            underlying=_UNDERLYING,
            underlying_price=100.0,
            strike=100.0,
            expiry=e1_ns,
            opt_type=OptType.CALL,
            mark_price=8.0,
            mark_iv=0.4,
        ),
    ]
    asyncio.run(_write_options_records(tmp_path, records))
    return tmp_path


# ---------------------------------------------------------------------------
# CrypcodileClient — funding_apr
# ---------------------------------------------------------------------------


def test_client_funding_apr_returns_dataframe(funding_lake: Path) -> None:
    """CrypcodileClient.funding_apr must return a pl.DataFrame."""
    client = CrypcodileClient(funding_lake)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_funding_apr_matches_analytics(funding_lake: Path) -> None:
    """Client method output must equal the direct analytics function output."""
    client = CrypcodileClient(funding_lake)
    catalog = Catalog(funding_lake)

    client_df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    analytics_df = analytics_funding_apr(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)

    assert len(client_df) == len(analytics_df), (
        f"row count mismatch: client={len(client_df)} analytics={len(analytics_df)}"
    )
    # Core column values must agree.
    for col in ("funding_ts", "funding_rate", "apr", "cumulative_funding"):
        assert col in client_df.columns, f"missing column: {col}"
        for v_c, v_a in zip(
            client_df[col].to_list(), analytics_df[col].to_list(), strict=True
        ):
            assert abs(float(v_c) - float(v_a)) < 1e-12, (
                f"col {col}: client={v_c}, analytics={v_a}"
            )


def test_client_funding_apr_row_count(funding_lake: Path) -> None:
    """3 records → 3 rows."""
    client = CrypcodileClient(funding_lake)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert len(df) == 3, f"expected 3 rows, got {len(df)}"


def test_client_funding_apr_apr_golden(funding_lake: Path) -> None:
    """Row-0 APR ≈ 0.0001 * 1095 = 0.10950 (tol 1e-6)."""
    client = CrypcodileClient(funding_lake)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert abs(df["apr"][0] - 0.0001 * 1095.0) < 1e-6


def test_client_funding_apr_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    client = CrypcodileClient(tmp_path)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrypcodileClient — perp_basis
# ---------------------------------------------------------------------------


def test_client_perp_basis_returns_dataframe(tmp_path: Path) -> None:
    """perp_basis on an empty lake must return an empty pl.DataFrame (not error)."""
    client = CrypcodileClient(tmp_path)
    df = client.perp_basis(_PERP_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrypcodileClient — spot_future_basis
# ---------------------------------------------------------------------------


def test_client_spot_future_basis_returns_dataframe(tmp_path: Path) -> None:
    """spot_future_basis on an empty lake must return empty pl.DataFrame."""
    client = CrypcodileClient(tmp_path)
    df = client.spot_future_basis(_FUTURE_SYMBOL, _SPOT_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrypcodileClient — iv_surface
# ---------------------------------------------------------------------------


def test_client_iv_surface_returns_dataframe(options_lake: Path) -> None:
    """CrypcodileClient.iv_surface must return a pl.DataFrame."""
    client = CrypcodileClient(options_lake)
    df = client.iv_surface(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_iv_surface_matches_analytics(options_lake: Path) -> None:
    """Client iv_surface output must match the direct analytics function."""
    client = CrypcodileClient(options_lake)
    catalog = Catalog(options_lake)

    client_df = client.iv_surface(_UNDERLYING, _BASE_NS)
    analytics_df = analytics_iv_surface(catalog, _UNDERLYING, _BASE_NS)

    assert len(client_df) == len(analytics_df), (
        f"row count mismatch: client={len(client_df)} analytics={len(analytics_df)}"
    )


def test_client_iv_surface_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    client = CrypcodileClient(tmp_path)
    df = client.iv_surface(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrypcodileClient — term_structure
# ---------------------------------------------------------------------------


def test_client_term_structure_returns_dataframe(options_lake: Path) -> None:
    """CrypcodileClient.term_structure must return a pl.DataFrame."""
    client = CrypcodileClient(options_lake)
    df = client.term_structure(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_term_structure_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    client = CrypcodileClient(tmp_path)
    df = client.term_structure(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrypcodileClient — spot_perp_basis
# ---------------------------------------------------------------------------


def test_client_spot_perp_basis_returns_dataframe(tmp_path: Path) -> None:
    """spot_perp_basis on an empty lake must return empty pl.DataFrame."""
    client = CrypcodileClient(tmp_path)
    df = client.spot_perp_basis(_SPOT_SYMBOL, _PERP_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrypcodileClient — vol_skew (T8-docs regression)
# ---------------------------------------------------------------------------


def test_client_vol_skew_returns_dataframe(options_lake: Path) -> None:
    """CrypcodileClient.vol_skew must return a pl.DataFrame (T8-docs regression).

    Regression: the method was documented in README but not present on the client.
    """
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    client = CrypcodileClient(options_lake)
    df = client.vol_skew(_UNDERLYING, e1_ns, _BASE_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_vol_skew_columns(options_lake: Path) -> None:
    """vol_skew output must contain the expected columns."""
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    client = CrypcodileClient(options_lake)
    df = client.vol_skew(_UNDERLYING, e1_ns, _BASE_NS)
    required = {"strike", "moneyness", "opt_type", "iv", "delta"}
    assert required.issubset(set(df.columns)), f"missing: {required - set(df.columns)}"


def test_client_vol_skew_empty_lake(tmp_path: Path) -> None:
    """vol_skew on an empty lake must return an empty DataFrame."""
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    client = CrypcodileClient(tmp_path)
    df = client.vol_skew(_UNDERLYING, e1_ns, _BASE_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


def test_client_vol_skew_matches_analytics(options_lake: Path) -> None:
    """Client vol_skew output must match the direct analytics function."""
    from crocodile.crypto.analytics.volsurface import vol_skew as analytics_vol_skew

    e1_ns = _BASE_NS + _ONE_YEAR_NS
    client = CrypcodileClient(options_lake)
    catalog = Catalog(options_lake)

    client_df = client.vol_skew(_UNDERLYING, e1_ns, _BASE_NS)
    analytics_df = analytics_vol_skew(catalog, _UNDERLYING, e1_ns, _BASE_NS)

    assert len(client_df) == len(analytics_df), (
        f"row count mismatch: client={len(client_df)} analytics={len(analytics_df)}"
    )


# ---------------------------------------------------------------------------
# CrypcodileClient — risk_reversal_butterfly (T8-docs regression)
# ---------------------------------------------------------------------------


def test_client_risk_reversal_butterfly_returns_tuple(options_lake: Path) -> None:
    """CrypcodileClient.risk_reversal_butterfly must return a tuple (T8-docs regression).

    Regression: the method was documented in README (via volsurface) but not
    present on the client as a convenience wrapper.
    """
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    client = CrypcodileClient(options_lake)
    skew_df = client.vol_skew(_UNDERLYING, e1_ns, _BASE_NS)
    result = client.risk_reversal_butterfly(skew_df)
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_client_risk_reversal_butterfly_empty_skew(tmp_path: Path) -> None:
    """risk_reversal_butterfly with an empty skew_df must return (None, None)."""
    client = CrypcodileClient(tmp_path)
    rr, bf = client.risk_reversal_butterfly(pl.DataFrame())
    assert rr is None
    assert bf is None


def test_client_risk_reversal_butterfly_types(options_lake: Path) -> None:
    """RR and BF must be float or None."""
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    client = CrypcodileClient(options_lake)
    skew_df = client.vol_skew(_UNDERLYING, e1_ns, _BASE_NS)
    rr, bf = client.risk_reversal_butterfly(skew_df)
    assert rr is None or isinstance(rr, float)
    assert bf is None or isinstance(bf, float)

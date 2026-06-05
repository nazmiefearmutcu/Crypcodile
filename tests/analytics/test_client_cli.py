"""Tests for Task 6.5 — CrocodileClient analytics methods + CLI subcommands.

Acceptance criteria (from the plan, verbatim):
  - populate a tmp lake; CrocodileClient(tmp).funding_apr(...) equals
    analytics.funding.funding_apr(catalog, ...).
  - Invoke the CLI via Typer's CliRunner: `funding-apr --symbol ... --data-dir tmp`
    exits 0 and prints a table containing the APR.
  - `iv-surface` exits 0 on a populated options lake.
  - ruff + mypy clean.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from crocodile.analytics.funding import funding_apr as analytics_funding_apr
from crocodile.analytics.volsurface import iv_surface as analytics_iv_surface
from crocodile.cli import app
from crocodile.client.client import CrocodileClient
from crocodile.schema.enums import OptType
from crocodile.schema.records import Funding, OptionsChain
from crocodile.store.catalog import Catalog
from crocodile.store.parquet_sink import ParquetSink

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
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS + i * _8H_NS,
            local_ts=_BASE_NS + i * _8H_NS,
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
            exchange=_EXCHANGE,
            symbol="deribit:BTC-90-C",
            symbol_raw="BTC-90-C",
            exchange_ts=_BASE_NS,
            local_ts=_BASE_NS,
            underlying=_UNDERLYING,
            underlying_price=100.0,
            strike=90.0,
            expiry=e1_ns,
            opt_type=OptType.CALL,
            mark_price=15.0,
            mark_iv=0.5,
        ),
        OptionsChain(
            exchange=_EXCHANGE,
            symbol="deribit:BTC-100-C",
            symbol_raw="BTC-100-C",
            exchange_ts=_BASE_NS,
            local_ts=_BASE_NS,
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
# CrocodileClient — funding_apr
# ---------------------------------------------------------------------------


def test_client_funding_apr_returns_dataframe(funding_lake: Path) -> None:
    """CrocodileClient.funding_apr must return a pl.DataFrame."""
    client = CrocodileClient(funding_lake)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_funding_apr_matches_analytics(funding_lake: Path) -> None:
    """Client method output must equal the direct analytics function output."""
    client = CrocodileClient(funding_lake)
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
    client = CrocodileClient(funding_lake)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert len(df) == 3, f"expected 3 rows, got {len(df)}"


def test_client_funding_apr_apr_golden(funding_lake: Path) -> None:
    """Row-0 APR ≈ 0.0001 * 1095 = 0.10950 (tol 1e-6)."""
    client = CrocodileClient(funding_lake)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + 3 * _8H_NS)
    assert abs(df["apr"][0] - 0.0001 * 1095.0) < 1e-6


def test_client_funding_apr_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    client = CrocodileClient(tmp_path)
    df = client.funding_apr(_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrocodileClient — perp_basis
# ---------------------------------------------------------------------------


def test_client_perp_basis_returns_dataframe(tmp_path: Path) -> None:
    """perp_basis on an empty lake must return an empty pl.DataFrame (not error)."""
    client = CrocodileClient(tmp_path)
    df = client.perp_basis(_PERP_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrocodileClient — spot_future_basis
# ---------------------------------------------------------------------------


def test_client_spot_future_basis_returns_dataframe(tmp_path: Path) -> None:
    """spot_future_basis on an empty lake must return empty pl.DataFrame."""
    client = CrocodileClient(tmp_path)
    df = client.spot_future_basis(_FUTURE_SYMBOL, _SPOT_SYMBOL, _BASE_NS, _BASE_NS + _8H_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrocodileClient — iv_surface
# ---------------------------------------------------------------------------


def test_client_iv_surface_returns_dataframe(options_lake: Path) -> None:
    """CrocodileClient.iv_surface must return a pl.DataFrame."""
    client = CrocodileClient(options_lake)
    df = client.iv_surface(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_iv_surface_matches_analytics(options_lake: Path) -> None:
    """Client iv_surface output must match the direct analytics function."""
    client = CrocodileClient(options_lake)
    catalog = Catalog(options_lake)

    client_df = client.iv_surface(_UNDERLYING, _BASE_NS)
    analytics_df = analytics_iv_surface(catalog, _UNDERLYING, _BASE_NS)

    assert len(client_df) == len(analytics_df), (
        f"row count mismatch: client={len(client_df)} analytics={len(analytics_df)}"
    )


def test_client_iv_surface_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    client = CrocodileClient(tmp_path)
    df = client.iv_surface(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CrocodileClient — term_structure
# ---------------------------------------------------------------------------


def test_client_term_structure_returns_dataframe(options_lake: Path) -> None:
    """CrocodileClient.term_structure must return a pl.DataFrame."""
    client = CrocodileClient(options_lake)
    df = client.term_structure(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)


def test_client_term_structure_empty(tmp_path: Path) -> None:
    """Empty lake → empty DataFrame."""
    client = CrocodileClient(tmp_path)
    df = client.term_structure(_UNDERLYING, _BASE_NS)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


# ---------------------------------------------------------------------------
# CLI: funding-apr
# ---------------------------------------------------------------------------

_RUNNER = CliRunner()


def test_cli_funding_apr_exits_0(funding_lake: Path) -> None:
    """CLI funding-apr must exit 0 on a populated lake."""
    result = _RUNNER.invoke(
        app,
        [
            "funding-apr",
            "--symbol", _SYMBOL,
            "--start", str(_BASE_NS),
            "--end", str(_BASE_NS + 3 * _8H_NS),
            "--data-dir", str(funding_lake),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


def test_cli_funding_apr_contains_apr(funding_lake: Path) -> None:
    """CLI funding-apr output must contain 'apr' (column header)."""
    result = _RUNNER.invoke(
        app,
        [
            "funding-apr",
            "--symbol", _SYMBOL,
            "--start", str(_BASE_NS),
            "--end", str(_BASE_NS + 3 * _8H_NS),
            "--data-dir", str(funding_lake),
        ],
    )
    assert "apr" in result.output.lower(), (
        f"'apr' not found in CLI output:\n{result.output}"
    )


def test_cli_funding_apr_empty_lake_exits_0(tmp_path: Path) -> None:
    """CLI funding-apr on an empty lake must exit 0 (graceful empty result)."""
    result = _RUNNER.invoke(
        app,
        [
            "funding-apr",
            "--symbol", _SYMBOL,
            "--start", str(_BASE_NS),
            "--end", str(_BASE_NS + _8H_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


# ---------------------------------------------------------------------------
# CLI: basis --perp
# ---------------------------------------------------------------------------


def test_cli_basis_perp_exits_0(tmp_path: Path) -> None:
    """CLI basis --perp on empty lake must exit 0 (graceful empty)."""
    result = _RUNNER.invoke(
        app,
        [
            "basis",
            "--perp", _PERP_SYMBOL,
            "--start", str(_BASE_NS),
            "--end", str(_BASE_NS + _8H_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


# ---------------------------------------------------------------------------
# CLI: basis --future/--spot
# ---------------------------------------------------------------------------


def test_cli_basis_future_exits_0(tmp_path: Path) -> None:
    """CLI basis --future/--spot on empty lake must exit 0."""
    result = _RUNNER.invoke(
        app,
        [
            "basis",
            "--future", _FUTURE_SYMBOL,
            "--spot", _SPOT_SYMBOL,
            "--start", str(_BASE_NS),
            "--end", str(_BASE_NS + _8H_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


# ---------------------------------------------------------------------------
# CLI: iv-surface
# ---------------------------------------------------------------------------


def test_cli_iv_surface_exits_0(options_lake: Path) -> None:
    """CLI iv-surface must exit 0 on a populated options lake."""
    result = _RUNNER.invoke(
        app,
        [
            "iv-surface",
            "--underlying", _UNDERLYING,
            "--at", str(_BASE_NS),
            "--data-dir", str(options_lake),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


def test_cli_iv_surface_empty_exits_0(tmp_path: Path) -> None:
    """CLI iv-surface on an empty lake must exit 0 gracefully."""
    result = _RUNNER.invoke(
        app,
        [
            "iv-surface",
            "--underlying", _UNDERLYING,
            "--at", str(_BASE_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


# ---------------------------------------------------------------------------
# CLI: term-structure
# ---------------------------------------------------------------------------


def test_cli_term_structure_exits_0(options_lake: Path) -> None:
    """CLI term-structure must exit 0 on a populated options lake."""
    result = _RUNNER.invoke(
        app,
        [
            "term-structure",
            "--underlying", _UNDERLYING,
            "--at", str(_BASE_NS),
            "--data-dir", str(options_lake),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"


def test_cli_term_structure_empty_exits_0(tmp_path: Path) -> None:
    """CLI term-structure on empty lake must exit 0 gracefully."""
    result = _RUNNER.invoke(
        app,
        [
            "term-structure",
            "--underlying", _UNDERLYING,
            "--at", str(_BASE_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"

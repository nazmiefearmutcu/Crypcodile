import pathlib

import pytest
from typer.testing import CliRunner

from crypcodile.analytics.liquidity_depth import calculate_block_liquidity_depth
from crypcodile.cli import app
from crypcodile.client.client import CrypcodileClient
from crypcodile.schema.records import BookSnapshot
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000
_SYMBOL = "base_onchain:DEGEN-WETH"
_RUNNER = CliRunner()


async def _write_depth_lake(data_dir: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)

    # Write a BookSnapshot for block 100
    # mid-price is 100.0 (bids[0] is 100.0, asks[0] is 100.0)
    await sink.put(
        BookSnapshot(
            exchange="base_onchain",
            symbol=_SYMBOL,
            symbol_raw="DEGEN-WETH",
            exchange_ts=_BASE_TS,
            local_ts=_BASE_TS,
            bids=[
                (100.0, 10.0),
                (99.0, 5.0),
                (98.0, 3.0),
                (97.0, 2.0),
                (94.0, 1.0),
            ],  # levels: 0%, -1%, -2%, -3%, -6%
            asks=[
                (100.0, 8.0),
                (101.0, 4.0),
                (102.0, 3.0),
                (103.0, 2.0),
                (106.0, 1.0),
            ],  # levels: 0%, +1%, +2%, +3%, +6%
            depth=5,
            sequence_id=100,
            is_snapshot=True,
        )
    )

    # Write a BookSnapshot for block 101
    await sink.put(
        BookSnapshot(
            exchange="base_onchain",
            symbol=_SYMBOL,
            symbol_raw="DEGEN-WETH",
            exchange_ts=_BASE_TS + 1_000_000_000,
            local_ts=_BASE_TS + 1_000_000_000,
            bids=[(100.0, 20.0), (99.0, 10.0), (98.0, 5.0), (97.0, 4.0), (94.0, 2.0)],
            asks=[(100.0, 16.0), (101.0, 8.0), (102.0, 6.0), (103.0, 4.0), (106.0, 2.0)],
            depth=5,
            sequence_id=101,
            is_snapshot=True,
        )
    )

    await sink.flush()


@pytest.fixture
def depth_lake(tmp_path: pathlib.Path) -> pathlib.Path:
    import asyncio

    asyncio.run(_write_depth_lake(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_block_liquidity_depth_calculations(tmp_path: pathlib.Path):
    await _write_depth_lake(tmp_path)

    catalog = Catalog(tmp_path)

    df = calculate_block_liquidity_depth(catalog, _SYMBOL)

    assert len(df) == 2
    # Verify block 100 depth
    assert df["block"][0] == 100
    # bid_depth_1pct (bids >= 99.0): 10.0 + 5.0 = 15.0
    assert df["bid_depth_1pct"][0] == 15.0
    # ask_depth_1pct (asks <= 101.0): 8.0 + 4.0 = 12.0
    assert df["ask_depth_1pct"][0] == 12.0
    # bid_depth_2pct (bids >= 98.0): 10.0 + 5.0 + 3.0 = 18.0
    assert df["bid_depth_2pct"][0] == 18.0
    # bid_depth_5pct (bids >= 95.0): 10.0 + 5.0 + 3.0 + 2.0 = 20.0 (excludes 94.0 because 94.0 < 95.0)
    assert df["bid_depth_5pct"][0] == 20.0

    # Verify block 101 depth (double the sizes)
    assert df["block"][1] == 101
    assert df["bid_depth_1pct"][1] == 30.0
    assert df["ask_depth_1pct"][1] == 24.0


def test_client_calculate_block_liquidity_depth(depth_lake: pathlib.Path) -> None:
    client = CrypcodileClient(depth_lake)
    df = client.calculate_block_liquidity_depth(_SYMBOL)
    assert len(df) == 2
    assert df["block"][0] == 100
    assert df["bid_depth_1pct"][0] == 15.0
    assert "ask_depth_5pct" in df.columns


def test_cli_liquidity_depth_exits_0(depth_lake: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "liquidity-depth",
            "--symbol",
            _SYMBOL,
            "--data-dir",
            str(depth_lake),
        ],
    )
    assert result.exit_code == 0, result.output
    # Polars may truncate wide column names in the table display
    assert "bid_depth" in result.output
    assert "100" in result.output
    assert "15.0" in result.output or "15" in result.output


def test_cli_liquidity_depth_empty_exits_0(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "liquidity-depth",
            "--symbol",
            "base_onchain:MISSING",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No book snapshots found" in result.output


def test_cli_liquidity_depth_requires_symbol(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        ["liquidity-depth", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "symbol is required" in result.output.lower()


def test_cli_liquidity_depth_help() -> None:
    result = _RUNNER.invoke(app, ["liquidity-depth", "--help"])
    assert result.exit_code == 0
    assert "--symbol" in result.output
    assert "--data-dir" in result.output

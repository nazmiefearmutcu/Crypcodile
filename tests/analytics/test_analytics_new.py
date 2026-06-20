"""Tests for Crypcodile analytics commands (Slippage Estimator, OFI Indexer, Whale Alerts Tracker) (Task R4).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
import polars as pl
from typer.testing import CliRunner

from crypcodile.analytics.slippage import estimate_slippage
from crypcodile.analytics.ofi import calculate_ofi, parse_interval_to_ns
from crypcodile.analytics.whale import track_whale_alerts
from crypcodile.cli import app
from crypcodile.client.client import CrypcodileClient
from crypcodile.schema.records import BookSnapshot, Trade, Liquidation
from crypcodile.schema.enums import Side
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC
_SYMBOL = "deribit:BTC-PERPETUAL"
_EXCHANGE = "deribit"


async def _write_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)  # type: ignore[arg-type]
    await sink.flush()


# ---------------------------------------------------------------------------
# Unit Tests: parse_interval_to_ns
# ---------------------------------------------------------------------------


def test_parse_interval_to_ns() -> None:
    assert parse_interval_to_ns("1s") == 1_000_000_000
    assert parse_interval_to_ns("5m") == 300_000_000_000
    assert parse_interval_to_ns("2h") == 7_200_000_000_000
    assert parse_interval_to_ns("1d") == 86_400_000_000_000
    
    with pytest.raises(ValueError, match="Interval string cannot be empty"):
        parse_interval_to_ns("")
    with pytest.raises(ValueError, match="Invalid interval duration value"):
        parse_interval_to_ns("as")
    with pytest.raises(ValueError, match="Unknown interval unit"):
        parse_interval_to_ns("10w")


# ---------------------------------------------------------------------------
# Unit Tests: estimate_slippage
# ---------------------------------------------------------------------------


@pytest.fixture()
def slippage_lake(tmp_path: Path) -> Path:
    path = tmp_path / "slippage"
    path.mkdir(exist_ok=True)
    snapshot = BookSnapshot(
        exchange=_EXCHANGE,
        symbol=_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=_BASE_NS,
        local_ts=_BASE_NS,
        bids=[(100.0, 2.0), (99.0, 3.0)],
        asks=[(101.0, 1.0), (102.0, 4.0)],
        depth=2,
    )
    asyncio.run(_write_records(path, [snapshot]))
    return path


def test_estimate_slippage_buy(slippage_lake: Path) -> None:
    catalog = Catalog(slippage_lake)
    df = estimate_slippage(catalog, _SYMBOL, "buy", 3.0)
    assert len(df) == 1
    assert df["symbol"][0] == _SYMBOL
    assert df["side"][0] == "buy"
    assert df["size"][0] == 3.0
    assert df["best_price"][0] == 101.0
    # Expected: 1.0 @ 101.0 + 2.0 @ 102.0 = 305.0 -> VWAP = 101.666667
    assert pytest.approx(df["expected_price"][0]) == 101.666667
    assert pytest.approx(df["slippage_usd"][0]) == 0.666667
    assert pytest.approx(df["slippage_pct"][0]) == (0.666667 / 101.0) * 100.0


def test_estimate_slippage_sell(slippage_lake: Path) -> None:
    catalog = Catalog(slippage_lake)
    df = estimate_slippage(catalog, _SYMBOL, "sell", 4.0)
    assert len(df) == 1
    assert df["symbol"][0] == _SYMBOL
    assert df["side"][0] == "sell"
    assert df["size"][0] == 4.0
    assert df["best_price"][0] == 100.0
    # Expected: 2.0 @ 100.0 + 2.0 @ 99.0 = 398.0 -> VWAP = 99.5
    assert pytest.approx(df["expected_price"][0]) == 99.5
    assert pytest.approx(df["slippage_usd"][0]) == 0.5
    assert pytest.approx(df["slippage_pct"][0]) == 0.5


def test_estimate_slippage_exceeds_depth(slippage_lake: Path) -> None:
    catalog = Catalog(slippage_lake)
    with pytest.raises(ValueError, match="exceeds total order book depth"):
        estimate_slippage(catalog, _SYMBOL, "buy", 6.0)


def test_estimate_slippage_invalid_inputs(slippage_lake: Path) -> None:
    catalog = Catalog(slippage_lake)
    with pytest.raises(ValueError, match="Size must be greater than zero"):
        estimate_slippage(catalog, _SYMBOL, "buy", -1.0)
    with pytest.raises(ValueError, match="Invalid side"):
        estimate_slippage(catalog, _SYMBOL, "invalid", 1.0)


def test_estimate_slippage_empty_lake(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    with pytest.raises(ValueError, match="No book snapshots found for symbol"):
        estimate_slippage(catalog, _SYMBOL, "buy", 1.0)


# ---------------------------------------------------------------------------
# Unit Tests: calculate_ofi
# ---------------------------------------------------------------------------


@pytest.fixture()
def ofi_lake(tmp_path: Path) -> Path:
    path = tmp_path / "ofi"
    path.mkdir(exist_ok=True)
    # Set up 4 snapshots at 10s intervals
    snapshots = [
        # Snap 1: Start
        BookSnapshot(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS,
            local_ts=_BASE_NS,
            bids=[(100.0, 2.0)],
            asks=[(101.0, 1.0)],
            depth=1,
        ),
        # Snap 2: Size changed only (10s later)
        BookSnapshot(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS + 10_000_000_000,
            local_ts=_BASE_NS + 10_000_000_000,
            bids=[(100.0, 3.0)],
            asks=[(101.0, 2.0)],
            depth=1,
        ),
        # Snap 3: Prices improved (20s later)
        BookSnapshot(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS + 20_000_000_000,
            local_ts=_BASE_NS + 20_000_000_000,
            bids=[(101.0, 4.0)],
            asks=[(102.0, 1.0)],
            depth=1,
        ),
        # Snap 4: Prices worsened (30s later)
        BookSnapshot(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS + 30_000_000_000,
            local_ts=_BASE_NS + 30_000_000_000,
            bids=[(100.0, 2.0)],
            asks=[(101.0, 3.0)],
            depth=1,
        ),
    ]
    asyncio.run(_write_records(path, snapshots))
    return path


def test_calculate_ofi_binning(ofi_lake: Path) -> None:
    catalog = Catalog(ofi_lake)
    # Using 15s bins. Start = _BASE_NS
    # Bin 1: _BASE_NS <= ts < _BASE_NS + 15s. Contains Snap 2 (ts = +10s).
    #   Step OFI (Snap 1 -> Snap 2):
    #     bids: 100.0 == 100.0 -> delta_wb = 3.0 - 2.0 = 1.0
    #     asks: 101.0 == 101.0 -> delta_wa = 2.0 - 1.0 = 1.0
    #     step OFI = 1.0 - 1.0 = 0.0
    #   Best Bid/Ask at end of Bin 1: (100.0, 101.0)
    # Bin 2: _BASE_NS + 15s <= ts < _BASE_NS + 30s. Contains Snap 3 (ts = +20s).
    #   Step OFI (Snap 2 -> Snap 3):
    #     bids: 101.0 > 100.0 -> delta_wb = 4.0
    #     asks: 102.0 > 101.0 -> delta_wa = -2.0
    #     step OFI = 4.0 - (-2.0) = 6.0
    #   Best Bid/Ask at end of Bin 2: (101.0, 102.0)
    # Bin 3: _BASE_NS + 30s <= ts < _BASE_NS + 45s. Contains Snap 4 (ts = +30s).
    #   Step OFI (Snap 3 -> Snap 4):
    #     bids: 100.0 < 101.0 -> delta_wb = -4.0
    #     asks: 101.0 < 102.0 -> delta_wa = 3.0
    #     step OFI = -4.0 - 3.0 = -7.0
    #   Best Bid/Ask at end of Bin 3: (100.0, 101.0)
    df = calculate_ofi(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 40_000_000_000, "15s")
    assert len(df) == 3
    assert df["timestamp"][0] == _BASE_NS
    assert df["ofi"][0] == 0.0
    assert df["best_bid"][0] == 100.0
    assert df["best_ask"][0] == 101.0

    assert df["timestamp"][1] == _BASE_NS + 15_000_000_000
    assert df["ofi"][1] == 6.0
    assert df["best_bid"][1] == 101.0
    assert df["best_ask"][1] == 102.0

    assert df["timestamp"][2] == _BASE_NS + 30_000_000_000
    assert df["ofi"][2] == -7.0
    assert df["best_bid"][2] == 100.0
    assert df["best_ask"][2] == 101.0


def test_calculate_ofi_empty_lake(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    df = calculate_ofi(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 40_000_000_000, "15s")
    assert len(df) == 0
    assert isinstance(df, pl.DataFrame)


# ---------------------------------------------------------------------------
# Unit Tests: track_whale_alerts
# ---------------------------------------------------------------------------


@pytest.fixture()
def whale_lake(tmp_path: Path) -> Path:
    path = tmp_path / "whale"
    path.mkdir(exist_ok=True)
    records = [
        Trade(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS,
            local_ts=_BASE_NS,
            id="t1",
            price=100.0,
            amount=2.0,  # val = 200
            side=Side.BUY,
        ),
        Liquidation(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS + 5_000_000_000,
            local_ts=_BASE_NS + 5_000_000_000,
            price=100.0,
            amount=15.0,  # val = 1500
            side=Side.SELL,
        ),
        Trade(
            exchange=_EXCHANGE,
            symbol=_SYMBOL,
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_NS + 10_000_000_000,
            local_ts=_BASE_NS + 10_000_000_000,
            id="t2",
            price=100.0,
            amount=20.0,  # val = 2000
            side=Side.BUY,
        ),
    ]
    asyncio.run(_write_records(path, records))
    return path


def test_track_whale_alerts(whale_lake: Path) -> None:
    catalog = Catalog(whale_lake)
    df = track_whale_alerts(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 20_000_000_000, 1000.0)
    assert len(df) == 2
    # Sorted by timestamp
    assert df["event_type"][0] == "Liquidation"
    assert df["usd_value"][0] == 1500.0
    assert df["side"][0] == "sell"
    assert df["timestamp"][0] == _BASE_NS + 5_000_000_000

    assert df["event_type"][1] == "Trade"
    assert df["usd_value"][1] == 2000.0
    assert df["side"][1] == "buy"
    assert df["timestamp"][1] == _BASE_NS + 10_000_000_000


def test_track_whale_alerts_empty_lake(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    df = track_whale_alerts(catalog, _SYMBOL, _BASE_NS, _BASE_NS + 20_000_000_000, 1000.0)
    assert len(df) == 0
    assert isinstance(df, pl.DataFrame)


# ---------------------------------------------------------------------------
# CLI & Client Wrapper Verification
# ---------------------------------------------------------------------------


def test_client_methods(slippage_lake: Path, ofi_lake: Path, whale_lake: Path) -> None:
    # Test slippage client wrapper
    client = CrypcodileClient(slippage_lake)
    df = client.estimate_slippage(_SYMBOL, "buy", 3.0)
    assert len(df) == 1
    assert df["expected_price"][0] > 0

    # Test ofi client wrapper
    client = CrypcodileClient(ofi_lake)
    df = client.calculate_ofi(_SYMBOL, _BASE_NS, _BASE_NS + 40_000_000_000, "15s")
    assert len(df) == 3

    # Test whale alerts client wrapper
    client = CrypcodileClient(whale_lake)
    df = client.track_whale_alerts(_SYMBOL, _BASE_NS, _BASE_NS + 20_000_000_000, 1000.0)
    assert len(df) == 2


def test_cli_commands_non_interactive(slippage_lake: Path, ofi_lake: Path, whale_lake: Path) -> None:
    import os
    from unittest.mock import patch
    from collections import namedtuple

    os.environ["POLARS_FMT_MAX_COLS"] = "20"
    os.environ["POLARS_TABLE_WIDTH"] = "1000"

    runner = CliRunner()

    TerminalSize = namedtuple("TerminalSize", ["columns", "lines"])
    with patch("shutil.get_terminal_size", return_value=TerminalSize(1000, 100)):
        # CLI slippage command
        result = runner.invoke(
            app,
            [
                "slippage",
                "--symbol", _SYMBOL,
                "--side", "buy",
                "--size", "3.0",
                "--data-dir", str(slippage_lake),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "buy" in result.output
        assert "101.66" in result.output

        # CLI ofi command
        result = runner.invoke(
            app,
            [
                "ofi",
                "--symbol", _SYMBOL,
                "--start", str(_BASE_NS),
                "--end", str(_BASE_NS + 40_000_000_000),
                "--interval", "15s",
                "--data-dir", str(ofi_lake),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ofi" in result.output

        # CLI whale-alerts command
        result = runner.invoke(
            app,
            [
                "whale-alerts",
                "--symbol", _SYMBOL,
                "--start", str(_BASE_NS),
                "--end", str(_BASE_NS + 20_000_000_000),
                "--min-usd", "1000",
                "--data-dir", str(whale_lake),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Liquidation" in result.output
        assert "Trade" in result.output


def test_cli_commands_interactive_fallback(slippage_lake: Path) -> None:
    from unittest.mock import patch
    runner = CliRunner()
    
    # Simulate interactive input for slippage
    # inputs:
    # 1. Symbol (defaults to deribit:BTC-PERPETUAL)
    # 2. Side (defaults to buy)
    # 3. Size (e.g. 3.0)
    with patch("crypcodile.cli.is_interactive_stdin", return_value=True):
        result = runner.invoke(
            app,
            ["slippage", "--data-dir", str(slippage_lake)],
            input="deribit:BTC-PERPETUAL\nbuy\n3.0\n",
        )
    # Note: under tests/pipes where tty is mock, click/typer falls back to prompt
    # and the prompt returns what we sent.
    assert result.exit_code == 0, result.output
    assert "buy" in result.output


def test_shell_integration_registration() -> None:
    runner = CliRunner()
    
    # Run the interactive shell and verify commands exist in the help menu
    result = runner.invoke(app, ["shell"], input="help\nexit\n")
    assert result.exit_code == 0, result.output
    assert "slippage" in result.output
    assert "ofi" in result.output
    assert "whale-alerts" in result.output

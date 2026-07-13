"""Tests for sequencer latency analytics, client wrapper, and CLI."""

from __future__ import annotations

import asyncio
import pathlib

import pytest
from typer.testing import CliRunner

from crypcodile.analytics.sequencer_latency import calculate_sequencer_latency
from crypcodile.cli import app
from crypcodile.client.client import CrypcodileClient
from crypcodile.schema.records import BookTicker
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000
_EXCHANGE = "base_onchain"
_RUNNER = CliRunner()


async def _write_latency_lake(data_dir: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)

    # BookTickers with 2-second production intervals and rising ingestion delay:
    # 1. exchange_ts = BASE, local_ts = BASE + 0.5s
    # 2. exchange_ts = BASE + 2.0s, local_ts = BASE + 2.7s (interval=2.0, delay=0.7)
    # 3. exchange_ts = BASE + 4.0s, local_ts = BASE + 4.9s (interval=2.0, delay=0.9)
    await sink.put(
        BookTicker(
            exchange=_EXCHANGE,
            symbol="base_onchain:AERO-USDC",
            symbol_raw="AERO-USDC",
            exchange_ts=_BASE_TS,
            local_ts=_BASE_TS + int(0.5 * 1e9),
            bid_px=1.0,
            bid_sz=1.0,
            ask_px=1.01,
            ask_sz=1.0,
        )
    )
    await sink.put(
        BookTicker(
            exchange=_EXCHANGE,
            symbol="base_onchain:AERO-USDC",
            symbol_raw="AERO-USDC",
            exchange_ts=_BASE_TS + int(2.0 * 1e9),
            local_ts=_BASE_TS + int(2.7 * 1e9),
            bid_px=1.0,
            bid_sz=1.0,
            ask_px=1.01,
            ask_sz=1.0,
        )
    )
    await sink.put(
        BookTicker(
            exchange=_EXCHANGE,
            symbol="base_onchain:AERO-USDC",
            symbol_raw="AERO-USDC",
            exchange_ts=_BASE_TS + int(4.0 * 1e9),
            local_ts=_BASE_TS + int(4.9 * 1e9),
            bid_px=1.0,
            bid_sz=1.0,
            ask_px=1.01,
            ask_sz=1.0,
        )
    )
    await sink.flush()


@pytest.fixture
def latency_lake(tmp_path: pathlib.Path) -> pathlib.Path:
    asyncio.run(_write_latency_lake(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_sequencer_latency_calculations(tmp_path: pathlib.Path):
    await _write_latency_lake(tmp_path)

    catalog = Catalog(tmp_path)
    df = calculate_sequencer_latency(catalog, _EXCHANGE)

    assert len(df) == 2
    # Row 0: production_interval
    assert df["metric"][0] == "production_interval"
    # avg production interval is 2.0s
    assert df["avg_seconds"][0] == pytest.approx(2.0)
    assert df["max_seconds"][0] == pytest.approx(2.0)

    # Row 1: ingestion_delay
    assert df["metric"][1] == "ingestion_delay"
    # first row filtered (prod_int null); delays 0.7 + 0.9 -> avg 0.8
    assert df["avg_seconds"][1] == pytest.approx(0.8)
    assert df["max_seconds"][1] == pytest.approx(0.9)


def test_client_calculate_sequencer_latency(latency_lake: pathlib.Path) -> None:
    client = CrypcodileClient(latency_lake)
    df = client.calculate_sequencer_latency(_EXCHANGE)
    assert len(df) == 2
    assert df["metric"][0] == "production_interval"
    assert df["avg_seconds"][0] == pytest.approx(2.0)
    assert "ingestion_delay" in df["metric"].to_list()


def test_cli_sequencer_latency_exits_0(latency_lake: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "sequencer-latency",
            "--exchange",
            _EXCHANGE,
            "--data-dir",
            str(latency_lake),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "production_interval" in result.output
    assert "ingestion_delay" in result.output
    assert "2.0" in result.output or "2" in result.output


def test_cli_sequencer_latency_default_exchange(latency_lake: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        ["sequencer-latency", "--data-dir", str(latency_lake)],
    )
    assert result.exit_code == 0, result.output
    assert "production_interval" in result.output


def test_cli_sequencer_latency_empty_exits_0(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "sequencer-latency",
            "--exchange",
            "missing_exchange",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No sequencer latency data found" in result.output


def test_cli_sequencer_latency_help() -> None:
    result = _RUNNER.invoke(app, ["sequencer-latency", "--help"])
    assert result.exit_code == 0
    assert "--exchange" in result.output
    assert "--data-dir" in result.output

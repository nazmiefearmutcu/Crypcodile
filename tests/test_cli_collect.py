"""Regression / acceptance tests for the wired ``collect`` CLI command (T7b-collect).

Strategy
--------
- We cannot touch the network in unit tests.
- We monkeypatch ``crypcodile.cli.make_connector`` to return a pre-configured
  ``DeribitConnector`` with a ``FakeTransport`` carrying one scripted trade frame.
- We also monkeypatch ``crypcodile.cli.AiohttpWsTransport`` so the CLI doesn't
  try to open a real WebSocket (the connector's transport is pre-set by the
  make_connector stub before cli.collect wires it).
- The CLI's ``collect`` command calls ``asyncio.run()`` internally; Typer's
  CliRunner invokes it synchronously, so the full async path is exercised.

Tests
-----
1. ``test_collect_cli_constructs_connector_and_sink``
       Verify that after a successful run the ParquetSink has received data
       (at least one Parquet file appears in the tmp_path data dir).

2. ``test_collect_cli_bad_exchange_exits_nonzero``
       Verify that an unknown exchange name makes the command exit with code 1
       (the factory raises ValueError).

3. ``test_collect_cli_keyboard_interrupt_exits_zero``
       Verify that simulated KeyboardInterrupt (CancelledError) exits 0 cleanly.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from crypcodile.cli import app
from crypcodile.exchanges.deribit.connector import DeribitConnector
from crypcodile.ingest.transport import FakeTransport
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.store.parquet_sink import ParquetSink

# ─── shared fixtures ─────────────────────────────────────────────────────────

_TRADE_FRAME: bytes = json.dumps(
    {
        "params": {
            "channel": "trades.BTC-PERPETUAL.raw",
            "data": [
                {
                    "trade_id": "cli-t1",
                    "price": 50000.0,
                    "amount": 2.0,
                    "direction": "buy",
                    "timestamp": 1700000000000,
                    "instrument_name": "BTC-PERPETUAL",
                }
            ],
        }
    }
).encode()


def _make_fake_connector(sink: ParquetSink) -> DeribitConnector:
    """Build a DeribitConnector with a FakeTransport that emits one trade."""
    conn = DeribitConnector(
        symbols=["BTC-PERPETUAL"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    conn.transport = FakeTransport(frames=[_TRADE_FRAME])
    return conn


# ─── tests ───────────────────────────────────────────────────────────────────

_RUNNER = CliRunner()


def test_collect_cli_constructs_connector_and_sink(tmp_path: pathlib.Path) -> None:
    """After a CLI collect run with a fake connector, Parquet files appear on disk."""

    # Track the sink that the CLI creates so we can inspect it after.
    created_sinks: list[ParquetSink] = []

    class _CaptureSink(ParquetSink):
        def __init__(self, *args, **kwargs) -> None:  # type: ignore[override]
            super().__init__(*args, **kwargs)
            created_sinks.append(self)

    def _fake_make_connector(exchange, symbols, channels, out, registry, **kw):
        # Wire a FakeTransport so no real WebSocket is opened.
        conn = DeribitConnector(
            symbols=symbols,
            channels=channels,
            out=out,
            registry=registry,
        )
        conn.transport = FakeTransport(frames=[_TRADE_FRAME])
        return conn

    with (
        patch("crypcodile.cli.make_connector", side_effect=_fake_make_connector),
        patch("crypcodile.cli.ParquetSink", _CaptureSink),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),  # never used (transport pre-set)
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "deribit",
                "--symbols", "BTC-PERPETUAL",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code}:\n{result.output}"
    )
    # At least one Parquet file must have been written
    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert len(parquet_files) > 0, (
        f"No Parquet files found under {tmp_path}. CLI output:\n{result.output}"
    )


def test_collect_cli_bad_exchange_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """An unknown exchange name makes the CLI exit with a non-zero code."""
    # No patch needed — the real factory raises ValueError for unknown exchanges.
    result = _RUNNER.invoke(
        app,
        [
            "collect",
            "--exchange", "nonexistent_exchange",
            "--symbols", "BTCUSD",
            "--channels", "trade",
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code != 0, (
        f"Expected non-zero exit for bad exchange, got 0.\n{result.output}"
    )


def test_collect_cli_keyboard_interrupt_exits_zero(tmp_path: pathlib.Path) -> None:
    """Simulated KeyboardInterrupt (CancelledError) must exit 0 cleanly.

    We simulate this by replacing the ``collect`` coroutine with one that
    immediately raises ``asyncio.CancelledError``, analogous to the user
    pressing Ctrl-C.
    """
    async def _cancelled_collect(*_args, **_kwargs):
        raise asyncio.CancelledError()

    def _fake_make_connector(exchange, symbols, channels, out, registry, **kw):
        conn = DeribitConnector(
            symbols=symbols,
            channels=channels,
            out=out,
            registry=registry,
        )
        conn.transport = FakeTransport(frames=[])
        return conn

    with (
        patch("crypcodile.cli.make_connector", side_effect=_fake_make_connector),
        patch("crypcodile.cli.collect_live", _cancelled_collect),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "deribit",
                "--symbols", "BTC-PERPETUAL",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, (
        f"Expected exit 0 on KeyboardInterrupt, got {result.exit_code}.\n{result.output}"
    )

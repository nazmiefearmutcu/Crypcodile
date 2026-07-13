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


def test_collect_cli_wizard(tmp_path: pathlib.Path) -> None:
    """Test that select_collect_params_interactively runs when parameters are missing and interactive."""
    def _fake_make_connector(exchange, symbols, channels, out, registry, **kw):
        conn = DeribitConnector(
            symbols=symbols,
            channels=channels,
            out=out,
            registry=registry,
        )
        conn.transport = FakeTransport(frames=[_TRADE_FRAME])
        return conn

    with (
        patch("crypcodile.cli.is_interactive_stdin", return_value=True),
        patch("crypcodile.cli.make_connector", side_effect=_fake_make_connector),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--data-dir", str(tmp_path),
            ],
            # Inputs (list_exchanges is sorted: 5=deribit):
            # 1. Select exchange: '5' for deribit.
            # 2. Select channels: '1' for trade.
            # 3. Select symbol: '1' for BTC-PERPETUAL.
            input="5\n1\n1\n",
        )

    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # Verify the selected exchange and symbols appear in output
    assert "exchange='deribit'" in result.output
    assert "['BTC-PERPETUAL']" in result.output
    assert "['trade']" in result.output


def test_collect_cli_accepts_max_reconnects_and_duration_flags(
    tmp_path: pathlib.Path,
) -> None:
    """--max-reconnects and --duration-seconds are accepted; max_reconnects is forwarded."""
    seen: dict = {}

    async def _fake_collect(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

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
        patch("crypcodile.cli.collect_live", _fake_collect),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
        patch("crypcodile.cli.is_interactive_stdin", return_value=False),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "deribit",
                "--symbols", "BTC-PERPETUAL",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
                "--max-reconnects", "5",
                "--duration-seconds", "1.5",
            ],
        )

    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code}:\n{result.output}"
    )
    assert seen.get("kwargs", {}).get("max_reconnects") == 5
    assert "max_reconnects=5" in result.output
    assert "duration_seconds=1.5" in result.output


def test_collect_cli_duration_seconds_auto_stops(tmp_path: pathlib.Path) -> None:
    """--duration-seconds cancels a long-running collect and exits cleanly."""

    async def _hanging_collect(*_args, **_kwargs):
        await asyncio.sleep(3600)

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
        patch("crypcodile.cli.collect_live", _hanging_collect),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
        patch("crypcodile.cli.is_interactive_stdin", return_value=False),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "deribit",
                "--symbols", "BTC-PERPETUAL",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
                "--duration-seconds", "0.05",
            ],
        )

    assert result.exit_code == 0, (
        f"Expected exit 0 on duration auto-stop, got {result.exit_code}.\n"
        f"{result.output}"
    )
    assert "Collection stopped" in result.output


def test_collect_cli_multi_exchange_repeated_flags(tmp_path: pathlib.Path) -> None:
    """Repeated --exchange builds one connector per exchange and passes the list."""
    make_calls: list[dict] = []
    seen: dict = {}

    async def _fake_collect(connectors, sink, **kwargs):
        seen["connectors"] = connectors
        seen["n"] = len(connectors)

    def _fake_make_connector(exchange, symbols, channels, out, registry, **kw):
        make_calls.append(
            {"exchange": exchange, "symbols": list(symbols), "channels": list(channels)}
        )
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
        patch("crypcodile.cli.collect_live", _fake_collect),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
        patch("crypcodile.cli.is_interactive_stdin", return_value=False),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "binance",
                "--exchange", "deribit",
                "--symbols", "BTC",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code}:\n{result.output}"
    )
    assert [c["exchange"] for c in make_calls] == ["binance", "deribit"]
    # Same channels for every exchange; symbols normalized per exchange.
    assert make_calls[0]["channels"] == ["trade"]
    assert make_calls[1]["channels"] == ["trade"]
    assert make_calls[0]["symbols"] == ["BTCUSDT"]
    assert make_calls[1]["symbols"] == ["BTC-PERPETUAL"]
    assert seen.get("n") == 2
    assert len(seen.get("connectors", [])) == 2
    assert "exchanges=['binance', 'deribit']" in result.output


def test_collect_cli_multi_exchange_comma_separated(tmp_path: pathlib.Path) -> None:
    """Comma-separated --exchange builds one connector per name."""
    make_calls: list[str] = []
    seen: dict = {}

    async def _fake_collect(connectors, sink, **kwargs):
        seen["n"] = len(connectors)

    def _fake_make_connector(exchange, symbols, channels, out, registry, **kw):
        make_calls.append(exchange)
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
        patch("crypcodile.cli.collect_live", _fake_collect),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
        patch("crypcodile.cli.is_interactive_stdin", return_value=False),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "binance,bybit",
                "--symbols", "ETHUSDT",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code}:\n{result.output}"
    )
    assert make_calls == ["binance", "bybit"]
    assert seen.get("n") == 2
    assert "exchanges=['binance', 'bybit']" in result.output


def test_collect_cli_multi_exchange_mixed_repeat_and_csv(tmp_path: pathlib.Path) -> None:
    """Mix of repeated flags and commas expands and de-duplicates in order."""
    make_calls: list[str] = []

    async def _fake_collect(connectors, sink, **kwargs):
        pass

    def _fake_make_connector(exchange, symbols, channels, out, registry, **kw):
        make_calls.append(exchange)
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
        patch("crypcodile.cli.collect_live", _fake_collect),
        patch("crypcodile.cli.AiohttpWsTransport", MagicMock()),
        patch("crypcodile.cli.is_interactive_stdin", return_value=False),
    ):
        result = _RUNNER.invoke(
            app,
            [
                "collect",
                "--exchange", "binance,deribit",
                "--exchange", "binance",
                "--exchange", "okx",
                "--symbols", "BTC",
                "--channels", "trade",
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, (
        f"CLI exited {result.exit_code}:\n{result.output}"
    )
    assert make_calls == ["binance", "deribit", "okx"]


def test_expand_csv_options_helpers() -> None:
    """Unit-test expand_csv_options / unique_preserve without invoking CLI."""
    from crypcodile.cli import expand_csv_options, unique_preserve

    assert expand_csv_options(None) == []
    assert expand_csv_options([]) == []
    assert expand_csv_options(["binance", "deribit"]) == ["binance", "deribit"]
    assert expand_csv_options(["binance, deribit", "okx"]) == [
        "binance",
        "deribit",
        "okx",
    ]
    assert expand_csv_options([" a , , b "]) == ["a", "b"]
    assert unique_preserve(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

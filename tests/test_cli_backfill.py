"""CLI tests for ``crypcodile backfill`` (historical REST data).

Strategy
--------
- No live network: inject a ``BinanceBackfill`` with fixture-backed fetch
  callbacks via ``backfill_factory`` on ``run_historical_backfill``, or patch
  the CLI entry so the orchestrator uses mocked HTTP pages.
- Unsupported exchange / channel must exit non-zero with a clear message.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from crypcodile.cli import app
from crypcodile.exchanges.binance.backfill import BinanceBackfill

_RUNNER = CliRunner()

_FIXTURES = pathlib.Path(__file__).parent / "exchanges" / "binance" / "fixtures"

# Time bounds covering the fixture trade timestamps (T = 1700000000100 / 200 ms).
_START_NS = 1_700_000_000_000 * 1_000_000  # 1700000000000 ms → ns
_END_NS = 1_700_000_001_000 * 1_000_000


def _load_aggtrades() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "rest_aggtrades.json").read_text())


def _make_fixture_binance_backfill() -> BinanceBackfill:
    """BinanceBackfill whose fetch callbacks return saved fixture pages (no HTTP)."""
    page = _load_aggtrades()

    async def fetch_aggtrades(
        symbol: str,
        from_id: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # First call (time-bounded) returns fixture; subsequent fromId pages empty.
        if from_id is not None:
            return []
        return page

    return BinanceBackfill(
        fetch_aggtrades=fetch_aggtrades,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )


# ─── unsupported / validation ────────────────────────────────────────────────


def test_backfill_unsupported_exchange_exits_nonzero(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "backfill",
            "--exchange", "coinbase",
            "--channel", "trade",
            "--symbols", "BTC-USD",
            "--from", str(_START_NS),
            "--to", str(_END_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "Unsupported exchange" in result.output
    assert "coinbase" in result.output


def test_backfill_unknown_exchange_exits_nonzero(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "backfill",
            "--exchange", "not-a-real-exchange",
            "--channel", "trade",
            "--symbols", "BTCUSDT",
            "--from", str(_START_NS),
            "--to", str(_END_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "Unsupported exchange" in result.output
    assert "binance" in result.output  # lists supported names


def test_backfill_missing_args_exits_nonzero(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        ["backfill", "--exchange", "binance", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "required" in result.output.lower()


def test_backfill_unsupported_channel_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """Binance has no funding REST backfill — must error clearly."""
    result = _RUNNER.invoke(
        app,
        [
            "backfill",
            "--exchange", "binance",
            "--channel", "funding",
            "--symbols", "BTCUSDT",
            "--from", str(_START_NS),
            "--to", str(_END_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "funding" in result.output.lower() or "not supported" in result.output.lower()


def test_backfill_from_after_to_exits_nonzero(tmp_path: pathlib.Path) -> None:
    result = _RUNNER.invoke(
        app,
        [
            "backfill",
            "--exchange", "binance",
            "--channel", "trade",
            "--symbols", "BTCUSDT",
            "--from", str(_END_NS),
            "--to", str(_START_NS),
            "--data-dir", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "from" in result.output.lower()


# ─── binance happy path (mocked REST) ────────────────────────────────────────


def test_backfill_binance_trade_writes_parquet(tmp_path: pathlib.Path) -> None:
    """Binance trade backfill with fixture fetch writes Parquet under data_dir."""
    from crypcodile.client.backfill import run_historical_backfill as real_run

    async def _run_with_factory(*args, **kwargs):
        kwargs["backfill_factory"] = _make_fixture_binance_backfill
        return await real_run(*args, **kwargs)

    with patch("crypcodile.cli.run_historical_backfill", side_effect=_run_with_factory):
        result = _RUNNER.invoke(
            app,
            [
                "backfill",
                "--exchange", "binance",
                "--channel", "trade",
                "--symbols", "BTCUSDT",
                "--from", str(_START_NS),
                "--to", str(_END_NS),
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, f"CLI exited {result.exit_code}:\n{result.output}"
    assert "2 records" in result.output or "Backfill complete" in result.output
    parquet_files = list(tmp_path.rglob("*.parquet"))
    assert len(parquet_files) > 0, (
        f"No Parquet files under {tmp_path}. Output:\n{result.output}"
    )


def test_backfill_binance_trade_start_end_aliases(tmp_path: pathlib.Path) -> None:
    """``--start`` / ``--end`` are accepted as aliases for ``--from`` / ``--to``."""

    def _factory() -> BinanceBackfill:
        return _make_fixture_binance_backfill()

    async def _run_with_factory(*args, **kwargs):
        from crypcodile.client.backfill import run_historical_backfill as real

        kwargs["backfill_factory"] = _factory
        return await real(*args, **kwargs)

    with patch("crypcodile.cli.run_historical_backfill", side_effect=_run_with_factory):
        result = _RUNNER.invoke(
            app,
            [
                "backfill",
                "--exchange", "binance",
                "--channel", "trade",
                "--symbols", "BTCUSDT",
                "--start", str(_START_NS),
                "--end", str(_END_NS),
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, f"CLI exited {result.exit_code}:\n{result.output}"
    assert list(tmp_path.rglob("*.parquet"))


def test_backfill_binance_symbol_normalize_btc(tmp_path: pathlib.Path) -> None:
    """Bare ``BTC`` normalizes to ``BTCUSDT`` for binance before backfill."""
    seen_symbols: list[str] = []

    def _factory() -> BinanceBackfill:
        page = _load_aggtrades()

        async def fetch_aggtrades(
            symbol: str,
            from_id: int | None,
            start_time_ms: int | None,
            end_time_ms: int | None,
            limit: int,
        ) -> list[dict[str, Any]]:
            seen_symbols.append(symbol)
            if from_id is not None:
                return []
            return page

        return BinanceBackfill(
            fetch_aggtrades=fetch_aggtrades,
            fetch_klines=None,
            fetch_open_interest=None,
            fetch_open_interest_hist=None,
        )

    async def _run_with_factory(*args, **kwargs):
        from crypcodile.client.backfill import run_historical_backfill as real

        kwargs["backfill_factory"] = _factory
        return await real(*args, **kwargs)

    with patch("crypcodile.cli.run_historical_backfill", side_effect=_run_with_factory):
        result = _RUNNER.invoke(
            app,
            [
                "backfill",
                "--exchange", "binance",
                "--channel", "trade",
                "--symbols", "BTC",
                "--from", str(_START_NS),
                "--to", str(_END_NS),
                "--data-dir", str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "BTCUSDT" in seen_symbols


# ─── orchestrator unit (direct, no CLI) ──────────────────────────────────────


async def test_run_historical_backfill_binance_direct(tmp_path: pathlib.Path) -> None:
    """``run_historical_backfill`` with injected BinanceBackfill writes rows."""
    from crypcodile.client.backfill import run_historical_backfill
    from crypcodile.store.parquet_sink import ParquetSink

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
    count = await run_historical_backfill(
        exchange="binance",
        channel="trade",
        symbols=["BTCUSDT"],
        start_ns=_START_NS,
        end_ns=_END_NS,
        sink=sink,
        backfill_factory=_make_fixture_binance_backfill,
    )
    assert count == 2
    assert list(tmp_path.rglob("*.parquet"))


async def test_run_historical_backfill_unsupported_exchange() -> None:
    from crypcodile.client.backfill import run_historical_backfill
    from crypcodile.sink.memory import MemorySink

    sink = MemorySink()
    try:
        await run_historical_backfill(
            exchange="coinbase",
            channel="trade",
            symbols=["BTC-USD"],
            start_ns=0,
            end_ns=1,
            sink=sink,
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unsupported exchange" in str(exc)
        assert "coinbase" in str(exc)

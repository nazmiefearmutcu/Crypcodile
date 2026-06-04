"""Acceptance tests for the Typer CLI (Task 3.5).

Tests use ``typer.testing.CliRunner`` to invoke ``query`` and ``catalog``
against a fixture data directory and assert exit code 0 with expected output.
"""

from __future__ import annotations

import pathlib

from crocodile.schema.enums import Side
from crocodile.schema.records import BookSnapshot, Trade
from crocodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


async def _write_fixtures(data_dir: pathlib.Path) -> None:
    """Write 3 trades + 1 book_snapshot into the data lake."""
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)
    for price in [100.0, 200.0, 300.0]:
        await sink.put(
            Trade(
                exchange="deribit",
                symbol="deribit:BTC-PERPETUAL",
                symbol_raw="BTC-PERPETUAL",
                exchange_ts=_BASE_TS,
                local_ts=_BASE_TS,
                id=str(price),
                price=price,
                amount=1.0,
                side=Side.BUY,
            )
        )
    await sink.put(
        BookSnapshot(
            exchange="deribit",
            symbol="deribit:BTC-PERPETUAL",
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_TS,
            local_ts=_BASE_TS,
            bids=[(100.0, 5.0)],
            asks=[(101.0, 4.0)],
            depth=1,
            sequence_id=1,
            is_snapshot=True,
        )
    )
    await sink.flush()


# ---------------------------------------------------------------------------
# query command
# ---------------------------------------------------------------------------


async def test_cli_query_exits_zero_with_output(tmp_path: pathlib.Path) -> None:
    """``query`` against a fixture data dir returns exit code 0 and count=3."""
    from typer.testing import CliRunner

    from crocodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["query", "SELECT count(*) AS n FROM trade", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # The count=3 should appear somewhere in the output
    assert "3" in result.output


# ---------------------------------------------------------------------------
# catalog command
# ---------------------------------------------------------------------------


async def test_cli_catalog_exits_zero_lists_channels(tmp_path: pathlib.Path) -> None:
    """``catalog`` lists available channels and their row counts, exit code 0."""
    from typer.testing import CliRunner

    from crocodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["catalog", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # Both channels written to the fixture should appear
    assert "trade" in result.output
    assert "book_snapshot" in result.output


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------


async def test_cli_export_csv_creates_file(tmp_path: pathlib.Path) -> None:
    """``export`` with fmt=csv writes a non-empty file, exit code 0."""
    from typer.testing import CliRunner

    from crocodile.cli import app

    await _write_fixtures(tmp_path)
    dest = tmp_path / "out" / "trades.csv"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "--channel",
            "trade",
            "--symbols",
            "deribit:BTC-PERPETUAL",
            "--from",
            str(_BASE_TS - 1),
            "--to",
            str(_BASE_TS + 1),
            "--fmt",
            "csv",
            "--dest",
            str(dest),
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert dest.exists()
    assert dest.stat().st_size > 0


# ---------------------------------------------------------------------------
# replay command (smoke -- just checks exit code)
# ---------------------------------------------------------------------------


async def test_cli_replay_exits_zero(tmp_path: pathlib.Path) -> None:
    """``replay`` lists records from the fixture data lake, exit code 0."""
    from typer.testing import CliRunner

    from crocodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "replay",
            "--channels",
            "trade",
            "--symbols",
            "deribit:BTC-PERPETUAL",
            "--from",
            str(_BASE_TS - 1),
            "--to",
            str(_BASE_TS + 1),
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # Should print at least one record representation
    assert "trade" in result.output or "deribit" in result.output

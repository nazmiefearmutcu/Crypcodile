"""Acceptance tests for the Typer CLI (Task 3.5).

Tests use ``typer.testing.CliRunner`` to invoke ``query`` and ``catalog``
against a fixture data directory and assert exit code 0 with expected output.
"""

from __future__ import annotations

import pathlib

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, Trade, Funding
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


async def _write_fixtures(data_dir: pathlib.Path) -> None:
    """Write 3 trades + 1 book_snapshot + 1 funding record into the data lake."""
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
    await sink.put(
        Funding(
            exchange="deribit",
            symbol="deribit:BTC-PERPETUAL",
            symbol_raw="BTC-PERPETUAL",
            exchange_ts=_BASE_TS,
            local_ts=_BASE_TS,
            funding_rate=0.0001,
            interval_hours=8,
        )
    )
    await sink.flush()


# ---------------------------------------------------------------------------
# query command
# ---------------------------------------------------------------------------


async def test_cli_query_exits_zero_with_output(tmp_path: pathlib.Path) -> None:
    """``query`` against a fixture data dir returns exit code 0 and count=3."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

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

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["catalog", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # Both channels written to the fixture should appear
    assert "trade" in result.output
    assert "book_snapshot" in result.output


async def test_cli_catalog_symbols_inventory(tmp_path: pathlib.Path) -> None:
    """``catalog --symbols`` prints inventory summary rows."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog", "--symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "symbol" in result.output
    assert "row_count" in result.output
    assert "trade" in result.output


def test_cli_catalog_symbols_empty_lake(tmp_path: pathlib.Path) -> None:
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog", "--symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No data found" in result.output


def test_cli_catalog_lists_empty_partition_dirs(tmp_path: pathlib.Path) -> None:
    """``catalog`` uses filesystem list_channels; empty partitions show 0 rows."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    (tmp_path / "exchange=deribit" / "channel=trade").mkdir(parents=True)
    (tmp_path / "exchange=deribit" / "channel=funding").mkdir(parents=True)
    (tmp_path / "exchange=binance" / "channel=trade").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(app, ["catalog", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "trade" in result.output
    assert "funding" in result.output
    # No parquet → zero rows (not "No data found", not -1).
    assert "No data found" not in result.output
    lines = [ln for ln in result.output.splitlines() if "trade" in ln or "funding" in ln]
    assert any(ln.strip().endswith("0") for ln in lines)


async def test_cli_catalog_symbols_with_empty_partition_dirs(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog --symbols`` still inventories parquet data when empty dirs exist.

    Filesystem ``list_channels`` discovers empty partitions; inventory / --symbols
    must keep working and only report symbols backed by queryable views.
    """
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    # Extra empty partition (no parquet) alongside real data.
    (tmp_path / "exchange=binance" / "channel=liquidations").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog", "--symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "row_count" in result.output
    assert "trade" in result.output
    # Empty liquidations partition has no symbols → must not invent rows.
    assert "liquidations" not in result.output
    assert "No data found" not in result.output


def test_cli_catalog_symbols_empty_partitions_only(tmp_path: pathlib.Path) -> None:
    """``catalog --symbols`` with only empty partitions → No data found."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    (tmp_path / "exchange=deribit" / "channel=trade").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog", "--symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No data found" in result.output


def test_cli_catalog_uses_list_channels(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Default ``catalog`` delegates channel discovery to client.list_channels."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.list_channels.return_value = ["trade", "funding"]
    mock_client._catalog = MagicMock()
    mock_client._catalog._registered_channels = set()  # no views → 0 rows

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            self._catalog = mock_client._catalog

        def list_channels(self):
            return mock_client.list_channels()

        def inventory(self):
            return mock_client.inventory()

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(app, ["catalog", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "trade" in result.output
    assert "funding" in result.output
    mock_client.list_channels.assert_called_once_with()


# ---------------------------------------------------------------------------
# catalog-summary command
# ---------------------------------------------------------------------------


def test_cli_catalog_summary_empty_lake(tmp_path: pathlib.Path) -> None:
    """``catalog-summary`` on empty lake prints zero counts; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-summary", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "channel_count:  0" in result.output
    assert "exchange_count: 0" in result.output
    assert "channels: (none)" in result.output
    assert "exchanges_on_disk: (none)" in result.output


async def test_cli_catalog_summary_with_data(tmp_path: pathlib.Path) -> None:
    """``catalog-summary`` lists channels + exchanges_on_disk with counts."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-summary", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "channel_count:  3" in result.output  # book_snapshot, funding, trade
    assert "exchange_count: 1" in result.output
    assert "trade" in result.output
    assert "book_snapshot" in result.output
    assert "funding" in result.output
    assert "deribit" in result.output


def test_cli_catalog_summary_uses_client_discovery(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI delegates to client list_channels + list_exchanges_on_disk."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.list_channels.return_value = ["trade"]
    mock_client.list_exchanges_on_disk.return_value = ["deribit", "binance"]

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def list_channels(self):
            return mock_client.list_channels()

        def list_exchanges_on_disk(self):
            return mock_client.list_exchanges_on_disk()

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    # Also patch resolve so empty tmp does not trigger interactive prompts.
    monkeypatch.setattr(
        "crypcodile.cli.resolve_data_dir", lambda d: d
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-summary", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "channel_count:  1" in result.output
    assert "exchange_count: 2" in result.output
    assert "channels: trade" in result.output
    assert "exchanges_on_disk: deribit, binance" in result.output
    mock_client.list_channels.assert_called_once_with()
    mock_client.list_exchanges_on_disk.assert_called_once_with()


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


async def test_cli_search_finds_symbol(tmp_path: pathlib.Path) -> None:
    """``search BTC`` returns matching rows with score columns."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "BTC", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "score" in result.output
    assert "exchange" in result.output
    assert "channels" in result.output


async def test_cli_search_no_matches(tmp_path: pathlib.Path) -> None:
    """Empty / no-match search prints 'No matches.' and exits 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "ZZZZ-NO-MATCH", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No matches." in result.output


def test_cli_search_empty_lake(tmp_path: pathlib.Path) -> None:
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "BTC", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No matches." in result.output


async def test_cli_search_channel_filter(tmp_path: pathlib.Path) -> None:
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "BTC",
            "--channel",
            "trade",
            "--limit",
            "5",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "trade" in result.output


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------


async def test_cli_export_csv_creates_file(tmp_path: pathlib.Path) -> None:
    """``export`` with fmt=csv writes a non-empty file, exit code 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

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

    from crypcodile.cli import app

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


# ---------------------------------------------------------------------------
# shell command
# ---------------------------------------------------------------------------


async def test_cli_shell_exits_zero() -> None:
    """``shell`` runs interactively and exits on 'exit' input."""
    from typer.testing import CliRunner
    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["shell"], input="help\nexit\n")
    assert result.exit_code == 0
    assert "Welcome to Crypcodile Interactive Shell!" in result.output
    assert "query" in result.output


async def test_cli_export_wizard_selects_symbol(tmp_path: pathlib.Path) -> None:
    """Test that select_symbols_interactively runs in export and successfully filters and selects symbols."""
    from typer.testing import CliRunner
    from unittest.mock import patch
    import sys
    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    dest = tmp_path / "out" / "trades_wizard.csv"

    with patch("crypcodile.cli.is_interactive_stdin", return_value=True):
        runner = CliRunner()
        # Input sequence:
        # 1. Wizard prompts for channel (since channel is not provided).
        #    Options are book_snapshot, funding, trade. We choose '3' (trade).
        # 2. Wizard prompts 'Search/Select' for symbol. The user types 'BTC' to search.
        # 3. Next search/select prompt: user selects option '1' (which is 'deribit:BTC-PERPETUAL').
        # 4. Prompt for start range (0).
        # 5. Prompt for end range (9999999999999999999).
        result = runner.invoke(
            app,
            [
                "export",
                "--fmt",
                "csv",
                "--dest",
                str(dest),
                "--data-dir",
                str(tmp_path),
            ],
            input="3\nBTC\n1\n0\n9999999999999999999\n",
        )
        print(f"DEBUG WIZARD OUTPUT:\n{result.output}")
        assert result.exit_code == 0, f"stdout:\n{result.output}"
        assert dest.exists()
        assert dest.stat().st_size > 0, f"Output is empty. stdout:\n{result.output}"


async def test_cli_replay_wizard(tmp_path: pathlib.Path) -> None:
    """Test that select_symbols_interactively runs in replay command."""
    from typer.testing import CliRunner
    from unittest.mock import patch
    import sys
    from crypcodile.cli import app

    await _write_fixtures(tmp_path)

    with patch("crypcodile.cli.is_interactive_stdin", return_value=True):
        runner = CliRunner()
        # Input sequence:
        # 1. Wizard prompts for channel. Choose '3' (trade).
        # 2. Wizard prompts for symbol search. Search 'BTC'.
        # 3. Choose '1' (deribit:BTC-PERPETUAL).
        # 4. Prompt for start range (0).
        # 5. Prompt for end range (9999999999999999999).
        result = runner.invoke(
            app,
            [
                "replay",
                "--data-dir",
                str(tmp_path),
            ],
            input="3\nBTC\n1\n0\n9999999999999999999\n",
        )
        assert result.exit_code == 0, f"stdout:\n{result.output}"
        assert "deribit" in result.output or "trade" in result.output


async def test_cli_funding_apr_wizard(tmp_path: pathlib.Path) -> None:
    """Test that select_symbols_interactively runs in funding-apr command."""
    from typer.testing import CliRunner
    from unittest.mock import patch
    import sys
    from crypcodile.cli import app

    await _write_fixtures(tmp_path)

    with patch("crypcodile.cli.is_interactive_stdin", return_value=True):
        runner = CliRunner()
        # Input sequence:
        # 1. Wizard prompts for symbol search. Search 'BTC'.
        # 2. Choose '1'.
        # 3. Prompt for start range (0).
        # 4. Prompt for end range (9999999999999999999).
        result = runner.invoke(
            app,
            [
                "funding-apr",
                "--data-dir",
                str(tmp_path),
            ],
            input="BTC\n1\n0\n9999999999999999999\n",
        )
        # funding-apr might exit 0 with "No funding data found." since trade is not funding channel.
        assert result.exit_code == 0, f"stdout:\n{result.output}"
        assert "No funding data found." in result.output or "deribit" in result.output or "0.0001" in result.output


async def test_cli_indicators_exits_zero(tmp_path: pathlib.Path) -> None:
    """``indicators`` calculates technical analysis indicators, exit code 0."""
    from typer.testing import CliRunner
    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "indicators",
            "--symbol",
            "deribit:BTC-PERPETUAL",
            "--indicator",
            "sma",
            "--period",
            "2",
            "--interval",
            "1s",
            "--from",
            str(_BASE_TS - 1),
            "--to",
            str(_BASE_TS + 1_000_000_000),
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "sma" in result.output

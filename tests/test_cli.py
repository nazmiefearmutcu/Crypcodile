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
    """CLI delegates to client.catalog_summary (shared REST/MCP/CLI contract)."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.catalog_summary.return_value = {
        "channels": ["trade"],
        "exchanges_on_disk": ["binance", "deribit"],
        "exchange_count": 2,
        "channel_count": 1,
    }

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def catalog_summary(self):
            return mock_client.catalog_summary()

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
    assert "exchanges_on_disk: binance, deribit" in result.output
    mock_client.catalog_summary.assert_called_once_with()


# ---------------------------------------------------------------------------
# catalog-stats command
# ---------------------------------------------------------------------------


def test_cli_catalog_stats_empty_lake(tmp_path: pathlib.Path) -> None:
    """``catalog-stats`` on empty lake prints zero counts; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-stats", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "channel_count:  0" in result.output
    assert "row_counts: (none)" in result.output


async def test_cli_catalog_stats_with_data(tmp_path: pathlib.Path) -> None:
    """``catalog-stats`` lists per-channel COUNT(*) row counts."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-stats", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # fixtures: book_snapshot, funding, trade (see _write_fixtures in test_cli)
    assert "channel_count:  3" in result.output
    assert "row_counts:" in result.output
    assert "trade:" in result.output
    assert "book_snapshot:" in result.output
    assert "funding:" in result.output


def test_cli_catalog_stats_uses_client(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI delegates to client.catalog_stats (shared REST/MCP/CLI contract)."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.catalog_stats.return_value = {
        "row_counts": {"book_snapshot": 42, "trade": 7},
        "channel_count": 2,
    }

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def catalog_stats(self):
            return mock_client.catalog_stats()

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr(
        "crypcodile.cli.resolve_data_dir", lambda d: d
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-stats", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "channel_count:  2" in result.output
    assert "book_snapshot: 42" in result.output
    assert "trade: 7" in result.output
    mock_client.catalog_stats.assert_called_once_with()


def test_cli_catalog_stats_query_failure_reports_minus_one(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI prints -1 from client.catalog_stats (REST/MCP parity)."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.catalog_stats.return_value = {
        "row_counts": {"trade": 10, "funding": -1},
        "channel_count": 2,
    }

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def catalog_stats(self):
            return mock_client.catalog_stats()

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr(
        "crypcodile.cli.resolve_data_dir", lambda d: d
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-stats", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "channel_count:  2" in result.output
    assert "trade: 10" in result.output
    assert "funding: -1" in result.output
    mock_client.catalog_stats.assert_called_once_with()


def test_cli_catalog_stats_escapes_double_quotes(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI prints odd channel keys from client.catalog_stats as-is."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.catalog_stats.return_value = {
        "row_counts": {'odd"chan': 1},
        "channel_count": 1,
    }

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def catalog_stats(self):
            return mock_client.catalog_stats()

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr(
        "crypcodile.cli.resolve_data_dir", lambda d: d
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-stats", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert 'odd"chan: 1' in result.output
    mock_client.catalog_stats.assert_called_once_with()


def test_cli_catalog_stats_in_main_help() -> None:
    """``catalog-stats`` appears in top-level ``--help`` Commands listing."""
    from typer.testing import CliRunner

    import crypcodile.cli as cli_mod
    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "catalog-stats" in result.output
    assert "catalog-stats" in (cli_mod.__doc__ or "")


# ---------------------------------------------------------------------------
# catalog-dates command
# ---------------------------------------------------------------------------


def test_cli_catalog_dates_empty_lake(tmp_path: pathlib.Path) -> None:
    """``catalog-dates`` on empty lake prints No dates.; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["catalog-dates", "--channel", "trade", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No dates." in result.output


async def test_cli_catalog_dates_with_data(tmp_path: pathlib.Path) -> None:
    """``catalog-dates`` lists hive date= partitions for a channel."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["catalog-dates", "--channel", "trade", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # Fixture trades use _BASE_TS → date partition 2023-11-14 (UTC).
    assert "2023-11-14" in result.output
    assert "No dates." not in result.output


def test_cli_catalog_dates_unknown_channel(tmp_path: pathlib.Path) -> None:
    """Unknown channel yields No dates. (exit 0)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    (tmp_path / "exchange=deribit" / "channel=trade" / "date=2024-01-01").mkdir(
        parents=True
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-dates",
            "--channel",
            "liquidations",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No dates." in result.output


def test_cli_catalog_dates_strips_channel(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Whitespace around --channel is stripped before list_dates."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.list_dates.return_value = ["2024-06-01"]

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def list_dates(self, channel: str):
            return mock_client.list_dates(channel)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-dates",
            "--channel",
            "  trade  ",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "2024-06-01" in result.output
    mock_client.list_dates.assert_called_once_with("trade")


def test_cli_catalog_dates_missing_channel(tmp_path: pathlib.Path) -> None:
    """``catalog-dates`` without --channel exits non-zero."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-dates", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code != 0


def test_cli_catalog_dates_uses_client(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI delegates to client.list_dates."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.list_dates.return_value = ["2023-11-14", "2023-11-15"]

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def list_dates(self, channel: str):
            return mock_client.list_dates(channel)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["catalog-dates", "--channel", "trade", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["2023-11-14", "2023-11-15"]
    mock_client.list_dates.assert_called_once_with("trade")


# ---------------------------------------------------------------------------
# catalog-symbols command
# ---------------------------------------------------------------------------


def test_cli_catalog_symbols_cmd_empty_lake(tmp_path: pathlib.Path) -> None:
    """``catalog-symbols`` on empty lake prints No symbols.; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No symbols." in result.output


async def test_cli_catalog_symbols_cmd_with_data(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-symbols`` lists distinct inventory symbols (one per line)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "No symbols." not in result.output
    # Lighter than catalog --symbols: no coverage table headers.
    assert "row_count" not in result.output
    assert "min_ts" not in result.output


async def test_cli_catalog_symbols_cmd_channel_filter(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-symbols --channel trade`` still finds trade symbols."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-symbols",
            "--channel",
            "trade",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output


async def test_cli_catalog_symbols_cmd_exchange_filter(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-symbols --exchange deribit`` filters inventory."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    ok = runner.invoke(
        app,
        [
            "catalog-symbols",
            "--exchange",
            "deribit",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert ok.exit_code == 0, f"stdout:\n{ok.output}"
    assert "deribit:BTC-PERPETUAL" in ok.output

    miss = runner.invoke(
        app,
        [
            "catalog-symbols",
            "--exchange",
            "binance",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert miss.exit_code == 0, f"stdout:\n{miss.output}"
    assert "No symbols." in miss.output


def test_cli_catalog_symbols_cmd_strips_filters(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Empty/whitespace --channel/--exchange treated as no filter."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    inv = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [0],
            "max_ts": [1],
            "row_count": [1],
        }
    )
    mock_client = MagicMock()
    mock_client.inventory.return_value = inv

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def inventory(self, channel=None, exchange=None):
            return mock_client.inventory(channel=channel, exchange=exchange)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-symbols",
            "--channel",
            "   ",
            "--exchange",
            "",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    mock_client.inventory.assert_called_once_with(channel=None, exchange=None)


def test_cli_catalog_symbols_cmd_uses_inventory(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI builds distinct sorted symbols from client.inventory."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    inv = pl.DataFrame(
        {
            "exchange": ["binance", "deribit", "deribit"],
            "channel": ["trade", "trade", "funding"],
            "symbol": [
                "binance:BTCUSDT",
                "deribit:BTC-PERPETUAL",
                "deribit:BTC-PERPETUAL",
            ],
            "min_ts": [0, 0, 0],
            "max_ts": [1, 1, 1],
            "row_count": [1, 2, 3],
        }
    )
    mock_client = MagicMock()
    mock_client.inventory.return_value = inv

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def inventory(self, channel=None, exchange=None):
            return mock_client.inventory(channel=channel, exchange=exchange)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-symbols",
            "--channel",
            "trade",
            "--exchange",
            "deribit",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    # Distinct + sorted.
    assert lines == ["binance:BTCUSDT", "deribit:BTC-PERPETUAL"]
    mock_client.inventory.assert_called_once_with(
        channel="trade", exchange="deribit"
    )


# ---------------------------------------------------------------------------
# catalog-inventory command
# ---------------------------------------------------------------------------


def test_cli_catalog_inventory_empty_lake(tmp_path: pathlib.Path) -> None:
    """``catalog-inventory`` on empty lake prints No inventory.; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-inventory", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No inventory." in result.output


async def test_cli_catalog_inventory_with_data(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-inventory`` prints inventory coverage table rows."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-inventory", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "No inventory." not in result.output
    # Full inventory table (heavier than catalog-symbols).
    assert "row_count" in result.output
    assert "min_ts" in result.output
    assert "exchange" in result.output


async def test_cli_catalog_inventory_channel_filter(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-inventory --channel trade`` still finds trade rows."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-inventory",
            "--channel",
            "trade",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "trade" in result.output


async def test_cli_catalog_inventory_exchange_filter(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-inventory --exchange deribit`` filters; miss → No inventory."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    ok = runner.invoke(
        app,
        [
            "catalog-inventory",
            "--exchange",
            "deribit",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert ok.exit_code == 0, f"stdout:\n{ok.output}"
    assert "deribit:BTC-PERPETUAL" in ok.output

    miss = runner.invoke(
        app,
        [
            "catalog-inventory",
            "--exchange",
            "binance",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert miss.exit_code == 0, f"stdout:\n{miss.output}"
    assert "No inventory." in miss.output


def test_cli_catalog_inventory_strips_filters(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Empty/whitespace --channel/--exchange treated as no filter."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    inv = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [0],
            "max_ts": [1],
            "row_count": [1],
        }
    )
    mock_client = MagicMock()
    mock_client.inventory.return_value = inv

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def inventory(self, channel=None, exchange=None):
            return mock_client.inventory(channel=channel, exchange=exchange)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-inventory",
            "--channel",
            "   ",
            "--exchange",
            "",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    mock_client.inventory.assert_called_once_with(channel=None, exchange=None)


def test_cli_catalog_inventory_uses_inventory(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI forwards channel/exchange filters to client.inventory."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    inv = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [100],
            "max_ts": [200],
            "row_count": [42],
        }
    )
    mock_client = MagicMock()
    mock_client.inventory.return_value = inv

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def inventory(self, channel=None, exchange=None):
            return mock_client.inventory(channel=channel, exchange=exchange)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "catalog-inventory",
            "--channel",
            "trade",
            "--exchange",
            "deribit",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "42" in result.output
    mock_client.inventory.assert_called_once_with(
        channel="trade", exchange="deribit"
    )


def test_cli_catalog_inventory_in_main_help() -> None:
    """``catalog-inventory`` appears in top-level ``--help`` Commands listing."""
    from typer.testing import CliRunner

    import crypcodile.cli as cli_mod
    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "catalog-inventory" in result.output
    assert "catalog-inventory" in (cli_mod.__doc__ or "")


# ---------------------------------------------------------------------------
# catalog-exchanges command
# ---------------------------------------------------------------------------


def test_cli_catalog_exchanges_empty_lake(tmp_path: pathlib.Path) -> None:
    """``catalog-exchanges`` on empty lake prints No exchanges.; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-exchanges", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No exchanges." in result.output


async def test_cli_catalog_exchanges_with_data(
    tmp_path: pathlib.Path,
) -> None:
    """``catalog-exchanges`` lists on-disk hive exchange= partitions."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-exchanges", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit" in result.output
    assert "No exchanges." not in result.output


def test_cli_catalog_exchanges_empty_partition_dirs(
    tmp_path: pathlib.Path,
) -> None:
    """Empty hive exchange= dirs still appear (filesystem discovery)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    (tmp_path / "exchange=binance" / "channel=trade").mkdir(parents=True)
    (tmp_path / "exchange=deribit" / "channel=funding").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-exchanges", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["binance", "deribit"]


def test_cli_catalog_exchanges_uses_client(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI delegates to client.list_exchanges_on_disk."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.list_exchanges_on_disk.return_value = ["binance", "okx"]

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def list_exchanges_on_disk(self):
            return mock_client.list_exchanges_on_disk()

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app, ["catalog-exchanges", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["binance", "okx"]
    mock_client.list_exchanges_on_disk.assert_called_once_with()


# ---------------------------------------------------------------------------
# list-exchanges command (factory registry; no lake)
# ---------------------------------------------------------------------------


def test_cli_list_exchanges_in_main_help() -> None:
    """``list-exchanges`` appears in top-level ``--help`` Commands listing.

    Typer surfaces the registered command short help (function docstring);
    the module docstring also documents the command for source readers.
    """
    from typer.testing import CliRunner

    import crypcodile.cli as cli_mod
    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "list-exchanges" in result.output
    # Short help from command docstring (factory registry, not lake hive).
    assert "factory" in result.output.lower() or "registered" in result.output.lower()
    # Distinct from on-disk catalog listing.
    assert "catalog-exchanges" in result.output
    # Module docstring Commands list (wave 58 / 59 discovery surface).
    assert "list-exchanges" in (cli_mod.__doc__ or "")


def test_cli_list_exchanges_matches_factory() -> None:
    """``list-exchanges`` prints sorted factory registry names (no lake)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app
    from crypcodile.exchanges.factory import list_exchanges

    runner = CliRunner()
    result = runner.invoke(app, ["list-exchanges"])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == list_exchanges()
    assert lines == sorted(lines)
    assert "binance" in lines
    assert "base_onchain" in lines
    assert "superchain" in lines
    assert "deribit" in lines


def test_cli_list_exchanges_uses_factory(monkeypatch) -> None:
    """CLI delegates to factory.list_exchanges (not lake client)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    calls: list[int] = []

    def _fake_list() -> list[str]:
        calls.append(1)
        return ["alpha", "zeta"]

    monkeypatch.setattr("crypcodile.cli.list_exchanges", _fake_list)

    runner = CliRunner()
    result = runner.invoke(app, ["list-exchanges"])
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["alpha", "zeta"]
    # Typer may touch the bound name during help/callback setup; require ≥1.
    assert len(calls) >= 1


def test_cli_list_exchanges_distinct_from_catalog_exchanges(
    tmp_path: pathlib.Path,
) -> None:
    """Factory list is independent of on-disk hive partitions."""
    from typer.testing import CliRunner

    from crypcodile.cli import app
    from crypcodile.exchanges.factory import list_exchanges

    # Empty lake: catalog-exchanges → No exchanges.; list-exchanges still full.
    runner = CliRunner()
    catalog = runner.invoke(
        app, ["catalog-exchanges", "--data-dir", str(tmp_path)]
    )
    assert catalog.exit_code == 0
    assert "No exchanges." in catalog.output

    registered = runner.invoke(app, ["list-exchanges"])
    assert registered.exit_code == 0
    lines = [ln.strip() for ln in registered.output.splitlines() if ln.strip()]
    assert lines == list_exchanges()
    assert len(lines) >= 1
    assert "No exchanges." not in registered.output


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


async def test_cli_search_exchange_filter(tmp_path: pathlib.Path) -> None:
    """``search BTC --exchange deribit`` filters inventory by exchange."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    # Second venue so a wrong --exchange can miss while BTC still matches.
    sink = ParquetSink(
        data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999
    )
    await sink.put(
        Trade(
            exchange="binance",
            symbol="binance:BTCUSDT",
            symbol_raw="BTCUSDT",
            exchange_ts=_BASE_TS,
            local_ts=_BASE_TS,
            id="binance-btc",
            price=50_000.0,
            amount=0.1,
            side=Side.BUY,
        )
    )
    await sink.flush()

    runner = CliRunner()
    ok = runner.invoke(
        app,
        [
            "search",
            "BTC",
            "--exchange",
            "deribit",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert ok.exit_code == 0, f"stdout:\n{ok.output}"
    assert "deribit:BTC-PERPETUAL" in ok.output
    assert "binance:BTCUSDT" not in ok.output

    miss = runner.invoke(
        app,
        [
            "search",
            "BTC",
            "--exchange",
            "coinbase",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert miss.exit_code == 0, f"stdout:\n{miss.output}"
    assert "No matches." in miss.output


def test_cli_search_strips_exchange_filter(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Empty/whitespace --exchange treated as no filter (delegated strip)."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    search_df = pl.DataFrame(
        {
            "symbol": ["deribit:BTC-PERPETUAL"],
            "exchange": ["deribit"],
            "channels": ["trade"],
            "score": [100],
            "min_ts": [0],
            "max_ts": [1],
            "row_count": [1],
        }
    )
    mock_client = MagicMock()
    mock_client.search_symbols.return_value = search_df

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def search_symbols(self, q, *, channel=None, exchange=None, limit=20):
            return mock_client.search_symbols(
                q, channel=channel, exchange=exchange, limit=limit
            )

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "search",
            "BTC",
            "--channel",
            "   ",
            "--exchange",
            "",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    # Empty/whitespace filters stripped to None before client call.
    mock_client.search_symbols.assert_called_once_with(
        "BTC", channel=None, exchange=None, limit=20
    )


# ---------------------------------------------------------------------------
# resolve-symbols command
# ---------------------------------------------------------------------------


def test_cli_resolve_symbols_empty_lake(tmp_path: pathlib.Path) -> None:
    """``resolve-symbols`` on empty lake yields no-match error (exit 1)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "resolve-symbols",
            "BTC-PERPETUAL",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1, f"stdout:\n{result.output}"
    assert "Error:" in result.output


async def test_cli_resolve_symbols_with_data(tmp_path: pathlib.Path) -> None:
    """``resolve-symbols BTC-PERPETUAL`` prints canonical symbol."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "resolve-symbols",
            "BTC-PERPETUAL",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert "deribit:BTC-PERPETUAL" in lines


async def test_cli_resolve_symbols_comma_separated(
    tmp_path: pathlib.Path,
) -> None:
    """Comma-separated free-form inputs resolve to canonical symbols."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "resolve-symbols",
            "BTC-PERPETUAL,deribit:BTC-PERPETUAL",
            "--ambiguous",
            "first",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    # Deduped: exact pass-through + search both map to same canonical.
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["deribit:BTC-PERPETUAL"]


async def test_cli_resolve_symbols_channel_filter(
    tmp_path: pathlib.Path,
) -> None:
    """``--channel trade`` still resolves trade-backed symbols."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "resolve-symbols",
            "BTC-PERPETUAL",
            "--channel",
            "trade",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output


def test_cli_resolve_symbols_missing_arg(tmp_path: pathlib.Path) -> None:
    """Missing symbols argument exits non-zero."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["resolve-symbols", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code != 0


def test_cli_resolve_symbols_strips_channel_and_default_ambiguous(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Whitespace channel → None; blank ambiguous → error."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.resolve_symbols.return_value = ["deribit:BTC-PERPETUAL"]

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def resolve_symbols(self, symbols, *, channel=None, ambiguous="error"):
            return mock_client.resolve_symbols(
                symbols, channel=channel, ambiguous=ambiguous
            )

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "resolve-symbols",
            "  BTC-PERPETUAL  ,  ETH  ",
            "--channel",
            "  trade  ",
            "--ambiguous",
            "   ",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    mock_client.resolve_symbols.assert_called_once_with(
        ["BTC-PERPETUAL", "ETH"], channel="trade", ambiguous="error"
    )


def test_cli_resolve_symbols_value_error_exits_1(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Client ValueError (no match / ambiguous / invalid mode) → exit 1."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.resolve_symbols.side_effect = ValueError(
        "no matches for 'ZZZ'"
    )

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def resolve_symbols(self, symbols, *, channel=None, ambiguous="error"):
            return mock_client.resolve_symbols(
                symbols, channel=channel, ambiguous=ambiguous
            )

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["resolve-symbols", "ZZZ", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 1, f"stdout:\n{result.output}"
    assert "Error: no matches for 'ZZZ'" in result.output


def test_cli_resolve_symbols_uses_client(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI is a thin wrapper over client.resolve_symbols."""
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    mock_client = MagicMock()
    mock_client.resolve_symbols.return_value = [
        "deribit:BTC-PERPETUAL",
        "deribit:ETH-PERPETUAL",
    ]

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def resolve_symbols(self, symbols, *, channel=None, ambiguous="error"):
            return mock_client.resolve_symbols(
                symbols, channel=channel, ambiguous=ambiguous
            )

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "resolve-symbols",
            "BTC,ETH",
            "--channel",
            "trade",
            "--ambiguous",
            "all",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    assert lines == ["deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"]
    mock_client.resolve_symbols.assert_called_once_with(
        ["BTC", "ETH"], channel="trade", ambiguous="all"
    )


# ---------------------------------------------------------------------------
# data-coverage command
# ---------------------------------------------------------------------------


def test_cli_data_coverage_empty_lake(tmp_path: pathlib.Path) -> None:
    """``data-coverage`` on empty lake prints No coverage.; exit 0."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data-coverage",
            "--symbol",
            "deribit:BTC-PERPETUAL",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No coverage." in result.output


async def test_cli_data_coverage_with_data(tmp_path: pathlib.Path) -> None:
    """``data-coverage --symbol`` prints inventory rows for that symbol."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data-coverage",
            "--symbol",
            "deribit:BTC-PERPETUAL",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "trade" in result.output
    assert "funding" in result.output
    assert "row_count" in result.output
    assert "No coverage." not in result.output


async def test_cli_data_coverage_channel_filter(
    tmp_path: pathlib.Path,
) -> None:
    """``--channel trade`` restricts coverage to trade inventory rows."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data-coverage",
            "--symbol",
            "deribit:BTC-PERPETUAL",
            "--channel",
            "trade",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "trade" in result.output
    assert "deribit:BTC-PERPETUAL" in result.output
    # funding channel should not appear when filtered to trade.
    lines = [
        ln
        for ln in result.output.splitlines()
        if "deribit:BTC-PERPETUAL" in ln
    ]
    assert lines
    assert all("funding" not in ln for ln in lines)


async def test_cli_data_coverage_no_symbol_match(
    tmp_path: pathlib.Path,
) -> None:
    """Unknown exact symbol yields No coverage. (exit 0)."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    await _write_fixtures(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data-coverage",
            "--symbol",
            "deribit:NO-SUCH-SYMBOL",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "No coverage." in result.output


def test_cli_data_coverage_missing_symbol(tmp_path: pathlib.Path) -> None:
    """Missing --symbol exits non-zero."""
    from typer.testing import CliRunner

    from crypcodile.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["data-coverage", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code != 0


def test_cli_data_coverage_strips_empty_channel(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Whitespace --channel treated as no filter."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    inv = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [0],
            "max_ts": [1],
            "row_count": [3],
        }
    )
    mock_client = MagicMock()
    mock_client.inventory.return_value = inv

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def inventory(self, **kwargs):  # noqa: ANN003
            return mock_client.inventory(**kwargs)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data-coverage",
            "--symbol",
            "deribit:BTC-PERPETUAL",
            "--channel",
            "   ",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    mock_client.inventory.assert_called_once_with(channel=None)


def test_cli_data_coverage_uses_inventory(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """CLI filters client.inventory by exact symbol match."""
    import polars as pl
    from unittest.mock import MagicMock
    from typer.testing import CliRunner

    from crypcodile.cli import app

    inv = pl.DataFrame(
        {
            "exchange": ["deribit", "deribit", "binance"],
            "channel": ["trade", "funding", "trade"],
            "symbol": [
                "deribit:BTC-PERPETUAL",
                "deribit:BTC-PERPETUAL",
                "binance:BTCUSDT",
            ],
            "min_ts": [0, 0, 0],
            "max_ts": [1, 2, 3],
            "row_count": [3, 1, 9],
        }
    )
    mock_client = MagicMock()
    mock_client.inventory.return_value = inv

    class _FakeClient:
        def __init__(self, data_dir=None) -> None:  # noqa: ANN001
            pass

        def inventory(self, **kwargs):  # noqa: ANN003
            return mock_client.inventory(**kwargs)

    monkeypatch.setattr(
        "crypcodile.client.client.CrypcodileClient", _FakeClient
    )
    monkeypatch.setattr("crypcodile.cli.resolve_data_dir", lambda d: d)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data-coverage",
            "--symbol",
            "deribit:BTC-PERPETUAL",
            "--channel",
            "trade",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"stdout:\n{result.output}"
    assert "deribit:BTC-PERPETUAL" in result.output
    assert "binance:BTCUSDT" not in result.output
    mock_client.inventory.assert_called_once_with(channel="trade")


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

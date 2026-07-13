"""Unit tests for MCP discovery tool handlers (Wave 2 Task 5).

Exercises pure handlers without hanging on stdio.
"""

from __future__ import annotations

import pathlib

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, Trade
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000


def _trade(
    price: float = 1.0,
    local_ts: int = _BASE_TS,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
) -> Trade:
    return Trade(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol.rsplit(":", 1)[-1],
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(price),
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def _snap(local_ts: int = _BASE_TS) -> BookSnapshot:
    return BookSnapshot(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        bids=[(100.0, 5.0)],
        asks=[(101.0, 4.0)],
        depth=1,
        sequence_id=1,
        is_snapshot=True,
    )


async def _write_fixtures(data_dir: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade(100.0))
    await sink.put(_trade(200.0, local_ts=_BASE_TS + 1_000_000_000))
    await sink.put(_snap())
    await sink.flush()


def test_list_data_channels_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_data_channels, TOOLS

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_data_channels(client) == []
    names = {t["name"] for t in TOOLS}
    assert "list_data_channels" in names
    assert "list_dates" in names
    assert "list_exchanges_on_disk" in names
    assert "catalog_summary" in names
    assert "search_symbols" in names
    assert "list_symbols" in names
    assert "data_coverage" in names
    assert "inventory_snapshot" in names


async def test_list_data_channels_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_data_channels

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_data_channels(client) == ["book_snapshot", "trade"]


def test_list_exchanges_on_disk_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_exchanges_on_disk, TOOLS

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_exchanges_on_disk(client) == []
    names = {t["name"] for t in TOOLS}
    assert "list_exchanges_on_disk" in names
    tool = next(t for t in TOOLS if t["name"] == "list_exchanges_on_disk")
    assert tool["inputSchema"]["required"] == []


async def test_list_exchanges_on_disk_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_exchanges_on_disk

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_exchanges_on_disk(client) == ["deribit"]


def test_list_exchanges_on_disk_delegates_to_client() -> None:
    """Handler is a thin wrapper over client.list_exchanges_on_disk."""
    from unittest.mock import MagicMock

    from crypcodile.mcp_server import handle_list_exchanges_on_disk

    client = MagicMock()
    client.list_exchanges_on_disk.return_value = ["binance", "deribit"]
    assert handle_list_exchanges_on_disk(client) == ["binance", "deribit"]
    client.list_exchanges_on_disk.assert_called_once_with()


def test_catalog_summary_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_catalog_summary, TOOLS

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_catalog_summary(client) == {
        "channels": [],
        "exchanges_on_disk": [],
        "exchange_count": 0,
        "channel_count": 0,
    }
    names = {t["name"] for t in TOOLS}
    assert "catalog_summary" in names
    tool = next(t for t in TOOLS if t["name"] == "catalog_summary")
    assert tool["inputSchema"]["required"] == []


async def test_catalog_summary_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_catalog_summary

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    result = handle_catalog_summary(client)
    assert result == {
        "channels": ["book_snapshot", "trade"],
        "exchanges_on_disk": ["deribit"],
        "exchange_count": 1,
        "channel_count": 2,
    }


def test_catalog_summary_delegates_to_client() -> None:
    """Handler wraps list_channels + list_exchanges_on_disk with counts."""
    from unittest.mock import MagicMock

    from crypcodile.mcp_server import handle_catalog_summary

    client = MagicMock()
    client.list_channels.return_value = ["book_snapshot", "trade"]
    client.list_exchanges_on_disk.return_value = ["binance", "deribit"]
    assert handle_catalog_summary(client) == {
        "channels": ["book_snapshot", "trade"],
        "exchanges_on_disk": ["binance", "deribit"],
        "exchange_count": 2,
        "channel_count": 2,
    }
    client.list_channels.assert_called_once_with()
    client.list_exchanges_on_disk.assert_called_once_with()


def test_list_dates_empty_lake(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_dates, TOOLS

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_dates(client, "trade") == []
    assert handle_list_dates(client, "") == []
    assert handle_list_dates(client, "   ") == []
    names = {t["name"] for t in TOOLS}
    assert "list_dates" in names
    tool = next(t for t in TOOLS if t["name"] == "list_dates")
    assert "channel" in tool["inputSchema"]["properties"]
    assert "channel" in tool["inputSchema"]["required"]


async def test_list_dates_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_dates

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    dates = handle_list_dates(client, "trade")
    assert isinstance(dates, list)
    assert len(dates) >= 1
    assert all(isinstance(d, str) for d in dates)
    # Same channel with surrounding whitespace is stripped
    assert handle_list_dates(client, "  trade  ") == dates
    assert handle_list_dates(client, "no_such_channel") == []


def test_list_dates_strips_channel_before_client() -> None:
    """Handler strips channel; empty after strip never calls the client."""
    from unittest.mock import MagicMock

    from crypcodile.mcp_server import handle_list_dates

    client = MagicMock()
    client.list_dates.return_value = ["2024-01-01"]
    assert handle_list_dates(client, "  book_snapshot  ") == ["2024-01-01"]
    client.list_dates.assert_called_once_with("book_snapshot")

    client.reset_mock()
    assert handle_list_dates(client, "") == []
    assert handle_list_dates(client, "   ") == []
    client.list_dates.assert_not_called()


def test_search_symbols_empty_lake(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_search_symbols

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_search_symbols(client, "BTC") == []


async def test_search_symbols_finds_match(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_search_symbols

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_search_symbols(client, "BTC-PERPETUAL", limit=10)
    assert len(rows) >= 1
    assert rows[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert "score" in rows[0]
    assert "channels" in rows[0]


async def test_search_symbols_channel_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_search_symbols

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_search_symbols(client, "BTC", channel="trade")
    assert len(rows) >= 1
    assert all("trade" in r["channels"] for r in rows)


async def test_search_symbols_exchange_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_search_symbols, TOOLS

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_search_symbols(client, "BTC", exchange="deribit")
    assert len(rows) >= 1
    assert all(r["exchange"] == "deribit" for r in rows)
    assert handle_search_symbols(client, "BTC", exchange="binance") == []

    tool = next(t for t in TOOLS if t["name"] == "search_symbols")
    assert "exchange" in tool["inputSchema"]["properties"]
    assert "channel" in tool["inputSchema"]["properties"]


def test_data_coverage_empty_lake(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_data_coverage

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_data_coverage(client, "deribit:BTC-PERPETUAL") == []


async def test_data_coverage_returns_rows(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_data_coverage

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_data_coverage(client, "deribit:BTC-PERPETUAL")
    assert len(rows) >= 2  # trade + book_snapshot
    channels = {r["channel"] for r in rows}
    assert "trade" in channels
    assert "book_snapshot" in channels
    for r in rows:
        assert r["symbol"] == "deribit:BTC-PERPETUAL"
        assert "min_ts" in r
        assert "max_ts" in r
        assert "row_count" in r


async def test_data_coverage_channel_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_data_coverage

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_data_coverage(
        client, "deribit:BTC-PERPETUAL", channel="trade"
    )
    assert len(rows) == 1
    assert rows[0]["channel"] == "trade"
    assert rows[0]["row_count"] == 2


def test_inventory_snapshot_empty_lake(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_inventory_snapshot

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_inventory_snapshot(client) == []


async def test_inventory_snapshot_returns_rows(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_inventory_snapshot

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_inventory_snapshot(client)
    assert len(rows) >= 2  # trade + book_snapshot
    channels = {r["channel"] for r in rows}
    assert "trade" in channels
    assert "book_snapshot" in channels
    for r in rows:
        assert r["exchange"] == "deribit"
        assert r["symbol"] == "deribit:BTC-PERPETUAL"
        assert "min_ts" in r
        assert "max_ts" in r
        assert "row_count" in r


async def test_inventory_snapshot_channel_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_inventory_snapshot

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_inventory_snapshot(client, channel="trade")
    assert len(rows) == 1
    assert rows[0]["channel"] == "trade"
    assert rows[0]["row_count"] == 2


async def test_inventory_snapshot_exchange_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_inventory_snapshot

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    rows = handle_inventory_snapshot(client, exchange="deribit")
    assert len(rows) >= 2
    assert all(r["exchange"] == "deribit" for r in rows)
    assert handle_inventory_snapshot(client, exchange="binance") == []


def test_list_symbols_empty_lake(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_symbols, TOOLS

    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_symbols(client) == []
    names = {t["name"] for t in TOOLS}
    assert "list_symbols" in names
    tool = next(t for t in TOOLS if t["name"] == "list_symbols")
    assert "channel" in tool["inputSchema"]["properties"]
    assert "exchange" in tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["required"] == []


async def test_list_symbols_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_symbols

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    symbols = handle_list_symbols(client)
    assert symbols == ["deribit:BTC-PERPETUAL"]


async def test_list_symbols_channel_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_symbols

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_symbols(client, channel="trade") == [
        "deribit:BTC-PERPETUAL"
    ]
    assert handle_list_symbols(client, channel="no_such_channel") == []


async def test_list_symbols_exchange_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.mcp_server import handle_list_symbols

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert handle_list_symbols(client, exchange="deribit") == [
        "deribit:BTC-PERPETUAL"
    ]
    assert handle_list_symbols(client, exchange="binance") == []


def test_list_symbols_strips_empty_filters() -> None:
    """Empty/whitespace channel/exchange treated as no filter (REST parity)."""
    from unittest.mock import MagicMock

    import polars as pl

    from crypcodile.mcp_server import handle_list_symbols

    client = MagicMock()
    client.inventory.return_value = pl.DataFrame(
        {
            "exchange": ["deribit", "deribit", "binance"],
            "channel": ["trade", "book_snapshot", "trade"],
            "symbol": [
                "deribit:BTC-PERPETUAL",
                "deribit:BTC-PERPETUAL",
                "binance:BTCUSDT",
            ],
            "min_ts": [1, 2, 3],
            "max_ts": [2, 3, 4],
            "row_count": [10, 5, 7],
        }
    )
    result = handle_list_symbols(client, channel="  ", exchange="")
    assert result == ["binance:BTCUSDT", "deribit:BTC-PERPETUAL"]
    client.inventory.assert_called_once_with(channel=None, exchange=None)


def test_list_symbols_strips_padded_filters() -> None:
    """Padded channel/exchange are stripped before inventory call."""
    from unittest.mock import MagicMock

    import polars as pl

    from crypcodile.mcp_server import handle_list_symbols

    client = MagicMock()
    client.inventory.return_value = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [1],
            "max_ts": [2],
            "row_count": [10],
        }
    )
    result = handle_list_symbols(
        client, channel="  trade  ", exchange="  deribit  "
    )
    assert result == ["deribit:BTC-PERPETUAL"]
    client.inventory.assert_called_once_with(
        channel="trade", exchange="deribit"
    )


def test_list_symbols_delegates_to_inventory() -> None:
    """Handler is a thin inventory → distinct sorted symbols wrapper."""
    from unittest.mock import MagicMock

    import polars as pl

    from crypcodile.mcp_server import handle_list_symbols

    client = MagicMock()
    client.inventory.return_value = pl.DataFrame(
        schema={
            "exchange": pl.Utf8,
            "channel": pl.Utf8,
            "symbol": pl.Utf8,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    assert handle_list_symbols(client) == []
    client.inventory.assert_called_once_with(channel=None, exchange=None)

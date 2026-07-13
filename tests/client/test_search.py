"""Tests for CrypcodileClient search façade + resolve_symbols (Wave 2 Task 3)."""

from __future__ import annotations

import pathlib

import polars as pl
import pytest

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, Trade
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14

_INVENTORY_COLS = ["exchange", "channel", "symbol", "min_ts", "max_ts", "row_count"]
_SEARCH_COLS = ["symbol", "exchange", "channels", "score", "min_ts", "max_ts", "row_count"]


def _trade(
    price: float = 1.0,
    local_ts: int = _BASE_TS,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
    symbol_raw: str | None = None,
) -> Trade:
    if symbol_raw is None:
        symbol_raw = symbol.rsplit(":", 1)[-1]
    return Trade(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol_raw,
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(price),
        price=price,
        amount=2.0,
        side=Side.BUY,
    )


def _snap(
    local_ts: int = _BASE_TS,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
) -> BookSnapshot:
    return BookSnapshot(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol.rsplit(":", 1)[-1],
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
    await sink.put(_trade(100.0, local_ts=_BASE_TS))
    await sink.put(_trade(200.0, local_ts=_BASE_TS + 1_000_000_000))
    await sink.put(_trade(300.0, local_ts=_BASE_TS + 2_000_000_000))
    await sink.put(_snap(local_ts=_BASE_TS))
    await sink.flush()


async def _write_multi(data_dir: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=20, flush_interval_seconds=9999)
    for i, sym in enumerate(
        [
            "deribit:BTC-PERPETUAL",
            "deribit:ETH-PERPETUAL",
            "binance:BTCUSDT",
        ]
    ):
        exchange = sym.split(":")[0]
        await sink.put(
            _trade(
                float(i + 1),
                exchange=exchange,
                symbol=sym,
                symbol_raw=sym.split(":", 1)[1],
            )
        )
    await sink.flush()


# ---------------------------------------------------------------------------
# Thin wrappers
# ---------------------------------------------------------------------------


def test_list_channels_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_channels() == []


async def test_list_channels_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_channels() == ["book_snapshot", "trade"]


def test_list_exchanges_on_disk_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_exchanges_on_disk() == []


async def test_list_exchanges_on_disk_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_exchanges_on_disk() == ["deribit"]


def test_catalog_summary_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    assert client.catalog_summary() == {
        "channels": [],
        "exchanges_on_disk": [],
        "exchange_count": 0,
        "channel_count": 0,
    }


async def test_catalog_summary_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert client.catalog_summary() == {
        "channels": ["book_snapshot", "trade"],
        "exchanges_on_disk": ["deribit"],
        "exchange_count": 1,
        "channel_count": 2,
    }


def test_catalog_summary_composes_list_methods(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """catalog_summary builds counts from list_channels + list_exchanges_on_disk."""
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    monkeypatch.setattr(client, "list_channels", lambda: ["trade", "funding"])
    monkeypatch.setattr(
        client, "list_exchanges_on_disk", lambda: ["binance", "deribit"]
    )
    assert client.catalog_summary() == {
        "channels": ["trade", "funding"],
        "exchanges_on_disk": ["binance", "deribit"],
        "exchange_count": 2,
        "channel_count": 2,
    }


def test_catalog_stats_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    assert client.catalog_stats() == {
        "row_counts": {},
        "channel_count": 0,
    }


async def test_catalog_stats_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    result = client.catalog_stats()
    assert result["channel_count"] == 2
    assert set(result["row_counts"].keys()) == {"book_snapshot", "trade"}  # type: ignore[union-attr]
    # fixtures: 3 trades + 1 book_snapshot
    assert result["row_counts"]["trade"] == 3  # type: ignore[index]
    assert result["row_counts"]["book_snapshot"] == 1  # type: ignore[index]


def test_catalog_stats_count_query(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """catalog_stats runs COUNT(*) per channel via query; escapes quotes."""
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    monkeypatch.setattr(client, "list_channels", lambda: ["book_snapshot", "trade"])

    calls: list[str] = []

    def _query(sql: str) -> pl.DataFrame:
        calls.append(sql)
        if "book_snapshot" in sql:
            return pl.DataFrame({"n": [42]})
        if "trade" in sql:
            return pl.DataFrame({"n": [7]})
        raise AssertionError(f"unexpected sql: {sql}")

    monkeypatch.setattr(client, "query", _query)
    assert client.catalog_stats() == {
        "row_counts": {"book_snapshot": 42, "trade": 7},
        "channel_count": 2,
    }
    assert len(calls) == 2
    assert any('FROM "book_snapshot"' in s for s in calls)
    assert any('FROM "trade"' in s for s in calls)


def test_catalog_stats_query_failure_reports_minus_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    monkeypatch.setattr(client, "list_channels", lambda: ["trade", "funding"])

    def _query(sql: str) -> pl.DataFrame:
        if "trade" in sql:
            return pl.DataFrame({"n": [10]})
        raise RuntimeError("view missing")

    monkeypatch.setattr(client, "query", _query)
    assert client.catalog_stats() == {
        "row_counts": {"trade": 10, "funding": -1},
        "channel_count": 2,
    }


def test_catalog_stats_escapes_double_quotes_in_channel(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    monkeypatch.setattr(client, "list_channels", lambda: ['odd"chan'])
    calls: list[str] = []

    def _query(sql: str) -> pl.DataFrame:
        calls.append(sql)
        return pl.DataFrame({"n": [1]})

    monkeypatch.setattr(client, "query", _query)
    result = client.catalog_stats()
    assert result["row_counts"] == {'odd"chan': 1}
    assert len(calls) == 1
    assert 'FROM "odd""chan"' in calls[0]


def test_list_symbols_empty(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_symbols() == []


async def test_list_symbols_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_symbols() == ["deribit:BTC-PERPETUAL"]


async def test_list_symbols_channel_and_exchange_filters(
    tmp_path: pathlib.Path,
) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_multi(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert client.list_symbols(channel="trade") == [
        "binance:BTCUSDT",
        "deribit:BTC-PERPETUAL",
        "deribit:ETH-PERPETUAL",
    ]
    assert client.list_symbols(exchange="deribit") == [
        "deribit:BTC-PERPETUAL",
        "deribit:ETH-PERPETUAL",
    ]
    assert client.list_symbols(exchange="binance") == ["binance:BTCUSDT"]
    assert client.list_symbols(channel="no_such") == []


def test_list_symbols_strips_empty_filters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty/whitespace channel/exchange treated as no filter."""
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    inv = pl.DataFrame(
        {
            "exchange": ["deribit", "binance"],
            "channel": ["trade", "trade"],
            "symbol": ["deribit:BTC-PERPETUAL", "binance:BTCUSDT"],
            "min_ts": [1, 2],
            "max_ts": [2, 3],
            "row_count": [10, 7],
        }
    )
    calls: list[tuple[str | None, str | None]] = []

    def _inventory(
        channel: str | None = None, exchange: str | None = None
    ) -> pl.DataFrame:
        calls.append((channel, exchange))
        return inv

    monkeypatch.setattr(client, "inventory", _inventory)
    assert client.list_symbols(channel="  ", exchange="") == [
        "binance:BTCUSDT",
        "deribit:BTC-PERPETUAL",
    ]
    assert calls == [(None, None)]


def test_list_symbols_strips_padded_filters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    inv = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [1],
            "max_ts": [2],
            "row_count": [10],
        }
    )
    calls: list[tuple[str | None, str | None]] = []

    def _inventory(
        channel: str | None = None, exchange: str | None = None
    ) -> pl.DataFrame:
        calls.append((channel, exchange))
        return inv

    monkeypatch.setattr(client, "inventory", _inventory)
    assert client.list_symbols(channel="  trade  ", exchange="  deribit  ") == [
        "deribit:BTC-PERPETUAL"
    ]
    assert calls == [("trade", "deribit")]


def test_data_coverage_empty_symbol(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    for blank in ("", "   ", None):  # type: ignore[arg-type]
        df = client.data_coverage(blank)  # type: ignore[arg-type]
        assert isinstance(df, pl.DataFrame)
        assert len(df) == 0
        assert df.columns == _INVENTORY_COLS


def test_data_coverage_empty_lake(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    df = client.data_coverage("deribit:BTC-PERPETUAL")
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0
    assert df.columns == _INVENTORY_COLS


async def test_data_coverage_with_data(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    df = client.data_coverage("deribit:BTC-PERPETUAL")
    assert len(df) >= 2  # trade + book_snapshot
    assert set(df["channel"].to_list()) >= {"trade", "book_snapshot"}
    assert set(df["symbol"].to_list()) == {"deribit:BTC-PERPETUAL"}


async def test_data_coverage_channel_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    df = client.data_coverage("deribit:BTC-PERPETUAL", channel="trade")
    assert len(df) == 1
    assert df["channel"][0] == "trade"
    assert df["row_count"][0] == 3


def test_data_coverage_strips_and_filters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strips symbol/channel; exact symbol filter over inventory."""
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    inv = pl.DataFrame(
        {
            "exchange": ["deribit", "deribit", "binance"],
            "channel": ["trade", "book_snapshot", "trade"],
            "symbol": [
                "deribit:BTC-PERPETUAL",
                "deribit:BTC-PERPETUAL",
                "binance:BTCUSDT",
            ],
            "min_ts": [1, 2, 3],
            "max_ts": [10, 20, 30],
            "row_count": [5, 8, 99],
        }
    )
    calls: list[str | None] = []

    def _inventory(
        channel: str | None = None, exchange: str | None = None
    ) -> pl.DataFrame:
        calls.append(channel)
        return inv

    monkeypatch.setattr(client, "inventory", _inventory)
    df = client.data_coverage("  deribit:BTC-PERPETUAL  ", channel="  ")
    assert len(df) == 2
    assert set(df["channel"].to_list()) == {"trade", "book_snapshot"}
    assert calls == [None]

    calls.clear()
    df2 = client.data_coverage("deribit:BTC-PERPETUAL", channel="  trade  ")
    # inventory already channel-filtered in real path; mock returns full inv,
    # so client still applies exact symbol match only (channel forwarded).
    assert calls == ["trade"]
    assert set(df2["symbol"].to_list()) == {"deribit:BTC-PERPETUAL"}


def test_data_coverage_no_symbol_match(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    monkeypatch.setattr(
        client,
        "inventory",
        lambda channel=None, exchange=None: pl.DataFrame(
            {
                "exchange": ["binance"],
                "channel": ["trade"],
                "symbol": ["binance:BTCUSDT"],
                "min_ts": [1],
                "max_ts": [2],
                "row_count": [3],
            }
        ),
    )
    df = client.data_coverage("deribit:BTC-PERPETUAL")
    assert len(df) == 0
    assert df.columns == _INVENTORY_COLS


def test_inventory_empty_schema(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    df = client.inventory()
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0
    assert df.columns == _INVENTORY_COLS


async def test_inventory_and_search_delegate(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)

    inv = client.inventory()
    assert len(inv) >= 1
    assert set(inv["symbol"].to_list()) == {"deribit:BTC-PERPETUAL"}

    inv_trade = client.inventory(channel="trade")
    assert all(c == "trade" for c in inv_trade["channel"].to_list())

    df = client.search_symbols("BTC-PERPETUAL")
    assert df.columns == _SEARCH_COLS
    assert len(df) >= 1
    assert df["symbol"][0] == "deribit:BTC-PERPETUAL"
    assert df["score"][0] == 90


def test_search_symbols_empty_q(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    df = client.search_symbols("")
    assert len(df) == 0
    assert df.columns == _SEARCH_COLS


# ---------------------------------------------------------------------------
# resolve_symbols
# ---------------------------------------------------------------------------


def test_resolve_empty_input(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    assert client.resolve_symbols([]) == []


async def test_resolve_exact_passthrough(tmp_path: pathlib.Path) -> None:
    """Canonical symbol with ':' present in inventory is returned as-is."""
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    out = client.resolve_symbols(["deribit:BTC-PERPETUAL"])
    assert out == ["deribit:BTC-PERPETUAL"]


async def test_resolve_raw_unique_match(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    out = client.resolve_symbols(["BTC-PERPETUAL"])
    assert out == ["deribit:BTC-PERPETUAL"]


async def test_resolve_ambiguous_error(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_multi(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    with pytest.raises(ValueError, match="Ambiguous symbol"):
        client.resolve_symbols(["PERPETUAL"], ambiguous="error")


async def test_resolve_ambiguous_first(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_multi(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    out = client.resolve_symbols(["PERPETUAL"], ambiguous="first")
    assert len(out) == 1
    assert out[0] in ("deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL")


async def test_resolve_ambiguous_all(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_multi(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    out = client.resolve_symbols(["PERPETUAL"], ambiguous="all")
    assert set(out) == {"deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"}


async def test_resolve_no_match_raises(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    with pytest.raises(ValueError, match="No symbols matched"):
        client.resolve_symbols(["ZZZZ-NO-MATCH"])


async def test_resolve_canonical_not_in_lake_falls_to_search(
    tmp_path: pathlib.Path,
) -> None:
    """':' present but not exact in inventory → search path."""
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    # Typo-ish canonical form that substring-matches nothing useful may fail;
    # a close raw form should still resolve via search if we use raw.
    out = client.resolve_symbols(["BTC-PERPETUAL"])
    assert out == ["deribit:BTC-PERPETUAL"]
    with pytest.raises(ValueError, match="No symbols matched"):
        client.resolve_symbols(["other-ex:DOES-NOT-EXIST"])


def test_resolve_invalid_ambiguous_mode(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    client = CrypcodileClient(data_dir=tmp_path)
    with pytest.raises(ValueError, match="ambiguous must be"):
        client.resolve_symbols(["BTC"], ambiguous="maybe")  # type: ignore[arg-type]


async def test_resolve_channel_filter(tmp_path: pathlib.Path) -> None:
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    out = client.resolve_symbols(["BTC-PERPETUAL"], channel="trade")
    assert out == ["deribit:BTC-PERPETUAL"]


async def test_resolve_empty_channel_treated_as_none(tmp_path: pathlib.Path) -> None:
    """Empty / whitespace channel must not filter inventory to nothing."""
    from crypcodile.client.client import CrypcodileClient

    await _write_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    assert client.resolve_symbols(["BTC-PERPETUAL"], channel="") == [
        "deribit:BTC-PERPETUAL"
    ]
    assert client.resolve_symbols(["BTC-PERPETUAL"], channel="   ") == [
        "deribit:BTC-PERPETUAL"
    ]

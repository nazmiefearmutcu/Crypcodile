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

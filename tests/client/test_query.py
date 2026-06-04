"""Acceptance tests for CrocodileClient.query / scan (Task 3.1)."""

from __future__ import annotations

import pathlib

import polars as pl

from crocodile.schema.enums import Side
from crocodile.schema.records import BookSnapshot, Trade
from crocodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


def _trade(
    price: float = 1.0,
    local_ts: int = _BASE_TS,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
) -> Trade:
    return Trade(
        exchange=exchange,
        symbol=symbol,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(price),
        price=price,
        amount=2.0,
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
    await sink.put(_trade(100.0, local_ts=_BASE_TS))
    await sink.put(_trade(200.0, local_ts=_BASE_TS + 1_000_000_000))
    await sink.put(_trade(300.0, local_ts=_BASE_TS + 2_000_000_000))
    await sink.put(_snap(local_ts=_BASE_TS))
    await sink.flush()


async def test_client_query_returns_polars_dataframe(tmp_path: pathlib.Path) -> None:
    """client.query(sql) delegates to Catalog and returns a Polars DataFrame."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    df = client.query("SELECT count(*) AS n FROM trade")
    assert isinstance(df, pl.DataFrame)
    assert df["n"][0] == 3


async def test_client_scan_single_symbol_returns_rows(tmp_path: pathlib.Path) -> None:
    """client.scan with one symbol returns rows matching catalog.scan output."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    df = client.scan(
        "trade",
        ["deribit:BTC-PERPETUAL"],
        _BASE_TS,
        _BASE_TS + 3_000_000_000,
    )
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3
    ts_col = df["local_ts"].to_list()
    assert ts_col == sorted(ts_col), "Not sorted by local_ts"
    assert set(df["price"].to_list()) == {100.0, 200.0, 300.0}


async def test_client_scan_multi_symbol_unions_results(tmp_path: pathlib.Path) -> None:
    """client.scan with multiple symbols concatenates results ordered by local_ts."""
    from crocodile.client.client import CrocodileClient

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    # Two different symbols
    await sink.put(_trade(1.0, local_ts=_BASE_TS, symbol="deribit:BTC-PERPETUAL"))
    await sink.put(
        Trade(
            exchange="deribit",
            symbol="deribit:ETH-PERPETUAL",
            symbol_raw="ETH-PERPETUAL",
            exchange_ts=_BASE_TS + 500_000_000,
            local_ts=_BASE_TS + 500_000_000,
            id="eth1",
            price=2000.0,
            amount=1.0,
            side=Side.SELL,
        )
    )
    await sink.flush()

    client = CrocodileClient(data_dir=tmp_path)
    df = client.scan(
        "trade",
        ["deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"],
        _BASE_TS,
        _BASE_TS + 2_000_000_000,
    )
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2
    ts_col = df["local_ts"].to_list()
    assert ts_col == sorted(ts_col), "Multi-symbol results must be sorted by local_ts"


async def test_client_scan_empty_symbols_returns_empty(tmp_path: pathlib.Path) -> None:
    """client.scan with empty symbols list returns an empty DataFrame."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    df = client.scan("trade", [], _BASE_TS, _BASE_TS + 3_000_000_000)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


async def test_client_scan_no_matching_rows_returns_empty(tmp_path: pathlib.Path) -> None:
    """client.scan with out-of-range time returns empty DataFrame."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    # Far future
    df = client.scan(
        "trade",
        ["deribit:BTC-PERPETUAL"],
        _BASE_TS + 1_000_000_000_000,
        _BASE_TS + 2_000_000_000_000,
    )
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0

"""Acceptance tests for DuckDB Catalog (Task 2.3)."""

from __future__ import annotations

import pathlib

import polars as pl

from crocodile.schema.enums import Side
from crocodile.schema.records import BookSnapshot, Trade
from crocodile.store.catalog import Catalog
from crocodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14, a known UTC date


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
        bids=[(100.0, 5.0), (99.0, 0.0)],
        asks=[(101.0, 4.0)],
        depth=2,
        sequence_id=42,
        is_snapshot=True,
    )


async def _write_fixtures(data_dir: pathlib.Path) -> None:
    """Write 3 trades + 1 book_snapshot using ParquetSink."""
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade(100.0, local_ts=_BASE_TS))
    await sink.put(_trade(200.0, local_ts=_BASE_TS + 1_000_000_000))
    await sink.put(_trade(300.0, local_ts=_BASE_TS + 2_000_000_000))
    await sink.put(_snap(local_ts=_BASE_TS))
    await sink.flush()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_catalog_scan_returns_rows_ordered_by_local_ts(
    tmp_path: pathlib.Path,
) -> None:
    """catalog.scan("trade", symbol, start, end) returns rows ordered by local_ts."""
    await _write_fixtures(tmp_path)

    cat = Catalog(tmp_path)
    start = _BASE_TS
    end = _BASE_TS + 3_000_000_000

    df = cat.scan("trade", "deribit:BTC-PERPETUAL", start, end)

    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3
    # Must be ordered by local_ts ascending
    ts_col = df["local_ts"].to_list()
    assert ts_col == sorted(ts_col), f"Not sorted by local_ts: {ts_col}"
    # All prices are present
    prices = set(df["price"].to_list())
    assert {100.0, 200.0, 300.0} == prices


async def test_catalog_query_count_matches(tmp_path: pathlib.Path) -> None:
    """catalog.query('SELECT count(*) FROM trade') matches the row count from scan."""
    await _write_fixtures(tmp_path)

    cat = Catalog(tmp_path)
    start = _BASE_TS
    end = _BASE_TS + 3_000_000_000

    df_scan = cat.scan("trade", "deribit:BTC-PERPETUAL", start, end)
    df_count = cat.query("SELECT count(*) AS n FROM trade")

    assert isinstance(df_count, pl.DataFrame)
    total_count = df_count["n"][0]
    # Scan returned 3 rows; the full table count must be >= that (could include other symbols)
    assert total_count >= len(df_scan)
    # For this fixture, there are exactly 3 trades overall
    assert total_count == 3


async def test_catalog_scan_filters_by_time_range(tmp_path: pathlib.Path) -> None:
    """scan with a narrow time range returns only matching rows."""
    await _write_fixtures(tmp_path)

    cat = Catalog(tmp_path)
    # Only ask for the first record
    start = _BASE_TS
    end = _BASE_TS + 500_000_000  # 0.5 seconds — only first trade falls in range

    df = cat.scan("trade", "deribit:BTC-PERPETUAL", start, end)
    assert len(df) == 1
    assert df["price"][0] == 100.0


async def test_catalog_scan_empty_result_for_out_of_range(tmp_path: pathlib.Path) -> None:
    """scan with a time range that has no matching rows returns an empty DataFrame."""
    await _write_fixtures(tmp_path)

    cat = Catalog(tmp_path)
    # Far future — no rows
    start = _BASE_TS + 1_000_000_000_000
    end = _BASE_TS + 2_000_000_000_000

    df = cat.scan("trade", "deribit:BTC-PERPETUAL", start, end)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0


async def test_catalog_query_returns_polars_dataframe(tmp_path: pathlib.Path) -> None:
    """query() returns a Polars DataFrame regardless of SQL shape."""
    await _write_fixtures(tmp_path)

    cat = Catalog(tmp_path)
    df = cat.query("SELECT symbol, price, local_ts FROM trade ORDER BY local_ts")
    assert isinstance(df, pl.DataFrame)
    assert "symbol" in df.columns
    assert "price" in df.columns
    assert len(df) == 3


async def test_catalog_scan_unknown_channel_returns_empty(tmp_path: pathlib.Path) -> None:
    """scan on a channel with no files returns an empty DataFrame, no exception."""
    await _write_fixtures(tmp_path)

    cat = Catalog(tmp_path)
    df = cat.scan("liquidation", "deribit:BTC-PERPETUAL", _BASE_TS, _BASE_TS + 9_999_999_999)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0

from __future__ import annotations

import asyncio
from pathlib import Path
import polars as pl
import pytest

from crypcodile.analytics.oi_aggregator import aggregate_open_interest
from crypcodile.schema.records import OpenInterest
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

_BASE_NS = 1_704_067_200_000_000_000
_T1 = _BASE_NS + 1_000
_T2 = _BASE_NS + 2_000
_T3 = _BASE_NS + 3_000
_T4 = _BASE_NS + 4_000

def _make_oi(ts: int, exchange: str, symbol: str, oi: float) -> OpenInterest:
    return OpenInterest(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol.split(":")[-1],
        exchange_ts=ts,
        local_ts=ts,
        open_interest=oi,
    )

async def _write_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)
    await sink.flush()

def test_oi_aggregator_aligned(tmp_path: Path) -> None:
    records = [
        _make_oi(_T1, "binance", "binance:BTCUSDT", 100.0),
        _make_oi(_T1, "okx", "okx:BTC-USDT-SWAP", 50.0),
        _make_oi(_T2, "binance", "binance:BTCUSDT", 110.0),
        _make_oi(_T2, "okx", "okx:BTC-USDT-SWAP", 60.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    
    df = aggregate_open_interest(catalog, "BTC", _T1, _T2)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2
    
    assert "binance" in df.columns
    assert "okx" in df.columns
    assert "total_oi" in df.columns
    
    row0 = df.row(0, named=True)
    assert row0["local_ts"] == _T1
    assert row0["binance"] == 100.0
    assert row0["okx"] == 50.0
    assert row0["total_oi"] == 150.0
    
    row1 = df.row(1, named=True)
    assert row1["local_ts"] == _T2
    assert row1["binance"] == 110.0
    assert row1["okx"] == 60.0
    assert row1["total_oi"] == 170.0

def test_oi_aggregator_unaligned(tmp_path: Path) -> None:
    records = [
        _make_oi(_T1, "binance", "binance:BTCUSDT", 100.0),
        _make_oi(_T2, "okx", "okx:BTC-USDT-SWAP", 50.0),
        _make_oi(_T3, "binance", "binance:BTCUSDT", 110.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    
    df = aggregate_open_interest(catalog, "BTC", _T1, _T3)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3
    
    row0 = df.row(0, named=True)
    assert row0["local_ts"] == _T1
    assert row0["binance"] == 100.0
    assert row0["okx"] == 0.0
    assert row0["total_oi"] == 100.0
    
    row1 = df.row(1, named=True)
    assert row1["local_ts"] == _T2
    assert row1["binance"] == 100.0
    assert row1["okx"] == 50.0
    assert row1["total_oi"] == 150.0
    
    row2 = df.row(2, named=True)
    assert row2["local_ts"] == _T3
    assert row2["binance"] == 110.0
    assert row2["okx"] == 50.0
    assert row2["total_oi"] == 160.0


def test_oi_aggregator_multi_symbol_same_exchange(tmp_path: Path) -> None:
    """Multiple symbols on one exchange at the same ts must not overwrite."""
    records = [
        _make_oi(_T1, "binance", "binance:BTCUSDT", 100.0),
        _make_oi(_T1, "binance", "binance:ETHUSDT", 40.0),
        _make_oi(_T1, "okx", "okx:BTC-USDT-SWAP", 50.0),
        _make_oi(_T2, "binance", "binance:BTCUSDT", 110.0),
        # ETH not updated at T2 — forward-filled; must still contribute 40
        _make_oi(_T2, "okx", "okx:BTC-USDT-SWAP", 60.0),
        _make_oi(_T2, "okx", "okx:ETH-USDT-SWAP", 25.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)

    df = aggregate_open_interest(catalog, None, _T1, _T2)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2

    row0 = df.row(0, named=True)
    assert row0["local_ts"] == _T1
    assert row0["binance"] == 140.0  # 100 + 40, not overwrite
    assert row0["okx"] == 50.0
    assert row0["total_oi"] == 190.0

    row1 = df.row(1, named=True)
    assert row1["local_ts"] == _T2
    assert row1["binance"] == 150.0  # 110 + 40 (ETH ffilled)
    assert row1["okx"] == 85.0  # 60 + 25
    assert row1["total_oi"] == 235.0


def test_oi_aggregator_skips_null_oi_preserves_forward_fill() -> None:
    """Null open_interest must not become 0.0 and wipe forward-fill."""
    raw = pl.DataFrame(
        {
            "local_ts": [_T1, _T2, _T3, _T1, _T2, _T3],
            "exchange": ["binance", "binance", "binance", "okx", "okx", "okx"],
            "symbol": [
                "binance:BTCUSDT",
                "binance:BTCUSDT",
                "binance:BTCUSDT",
                "okx:BTC-USDT-SWAP",
                "okx:BTC-USDT-SWAP",
                "okx:BTC-USDT-SWAP",
            ],
            "open_interest": [100.0, None, 110.0, 50.0, 55.0, None],
        }
    )

    class _FakeCatalog:
        def refresh_views(self) -> None:
            return None

        def query(self, sql: str) -> pl.DataFrame:
            return raw

    df = aggregate_open_interest(_FakeCatalog(), "BTC", _T1, _T3)  # type: ignore[arg-type]
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 3

    row0 = df.row(0, named=True)
    assert row0["local_ts"] == _T1
    assert row0["binance"] == 100.0
    assert row0["okx"] == 50.0
    assert row0["total_oi"] == 150.0

    # T2: binance null skipped → keep 100; okx updated to 55
    row1 = df.row(1, named=True)
    assert row1["local_ts"] == _T2
    assert row1["binance"] == 100.0
    assert row1["okx"] == 55.0
    assert row1["total_oi"] == 155.0

    # T3: binance updated to 110; okx null skipped → keep 55
    row2 = df.row(2, named=True)
    assert row2["local_ts"] == _T3
    assert row2["binance"] == 110.0
    assert row2["okx"] == 55.0
    assert row2["total_oi"] == 165.0


def test_oi_aggregator_symbol_filter_literal_not_regex() -> None:
    """Dots in filter tokens must be literal substrings, not regex wildcards."""
    raw = pl.DataFrame(
        {
            "local_ts": [_T1, _T1, _T1],
            "exchange": ["binance", "binance", "derive"],
            "symbol": [
                "binance:BTCXUSDT",
                "binance:BTC-USDT",
                "derive:BTC.USDT",
            ],
            "open_interest": [10.0, 20.0, 30.0],
        }
    )

    class _FakeCatalog:
        def refresh_views(self) -> None:
            return None

        def query(self, sql: str) -> pl.DataFrame:
            return raw

    df = aggregate_open_interest(
        _FakeCatalog(), "BTC.USDT", _T1, _T1  # type: ignore[arg-type]
    )
    assert len(df) == 1
    row = df.row(0, named=True)
    assert row["derive"] == 30.0
    assert "binance" not in df.columns or row.get("binance", 0.0) == 0.0
    assert row["total_oi"] == 30.0


def test_oi_aggregator_empty_symbol_tokens_mean_no_filter() -> None:
    """Empty / whitespace filter tokens must not become contains('')."""
    raw = pl.DataFrame(
        {
            "local_ts": [_T1, _T1],
            "exchange": ["binance", "okx"],
            "symbol": ["binance:BTCUSDT", "okx:ETH-USDT-SWAP"],
            "open_interest": [100.0, 50.0],
        }
    )

    class _FakeCatalog:
        def refresh_views(self) -> None:
            return None

        def query(self, sql: str) -> pl.DataFrame:
            return raw

    # Empty string / whitespace list items → treat as no filter (all symbols).
    df = aggregate_open_interest(_FakeCatalog(), "", _T1, _T1)  # type: ignore[arg-type]
    assert len(df) == 1
    assert df.row(0, named=True)["total_oi"] == 150.0

    df2 = aggregate_open_interest(
        _FakeCatalog(), ["", "  "], _T1, _T1  # type: ignore[arg-type]
    )
    assert len(df2) == 1
    assert df2.row(0, named=True)["total_oi"] == 150.0

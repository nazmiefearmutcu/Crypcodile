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

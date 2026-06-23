from __future__ import annotations

import asyncio
from pathlib import Path
import polars as pl
import pytest

from crypcodile.analytics.basis import spot_perp_basis
from crypcodile.schema.records import BookSnapshot, DerivativeTicker
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

_BASE_NS = 1_704_067_200_000_000_000
_T1 = _BASE_NS + 1_000
_T2 = _BASE_NS + 2_000
_T3 = _BASE_NS + 3_000
_T4 = _BASE_NS + 4_000

_SPOT_SYMBOL = "gmx:ETH-USD"
_PERP_SYMBOL = "gmx:ETH-PERP"

def _make_book_snapshot(ts: int, symbol: str, bid: float, ask: float) -> BookSnapshot:
    return BookSnapshot(
        exchange="gmx",
        symbol=symbol,
        symbol_raw=symbol.split(":")[-1],
        exchange_ts=ts,
        local_ts=ts,
        bids=[(bid, 1.0)],
        asks=[(ask, 1.0)],
        depth=1,
    )

def _make_derivative_ticker(ts: int, mark: float, index: float) -> DerivativeTicker:
    return DerivativeTicker(
        exchange="gmx",
        symbol=_PERP_SYMBOL,
        symbol_raw="ETH-PERP",
        exchange_ts=ts,
        local_ts=ts,
        mark_price=mark,
        index_price=index,
    )

async def _write_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)
    await sink.flush()

def test_spot_perp_basis_with_book_snapshot(tmp_path: Path) -> None:
    records = [
        # Spot as book_snapshot: t=1000 mid=100 (99 bid, 101 ask), t=3000 mid=102 (101 bid, 103 ask)
        _make_book_snapshot(_T1, _SPOT_SYMBOL, 99.0, 101.0),
        _make_book_snapshot(_T3, _SPOT_SYMBOL, 101.0, 103.0),
        # Perp Tickers: t=2000 mark=101.5, t=4000 mark=104.5
        _make_derivative_ticker(_T2, 101.5, 101.0),
        _make_derivative_ticker(_T4, 104.5, 104.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    catalog = Catalog(tmp_path)
    
    df = spot_perp_basis(catalog, _SPOT_SYMBOL, _PERP_SYMBOL, _T1, _T4)
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 2
    
    row0 = df.row(0, named=True)
    assert row0["local_ts"] == _T2
    assert abs(row0["perp_price"] - 101.5) < 1e-9
    assert abs(row0["spot_price"] - 100.0) < 1e-9
    assert abs(row0["basis"] - 1.5) < 1e-9
    assert abs(row0["basis_pct"] - 0.015) < 1e-9
    
    row1 = df.row(1, named=True)
    assert row1["local_ts"] == _T4
    assert abs(row1["perp_price"] - 104.5) < 1e-9
    assert abs(row1["spot_price"] - 102.0) < 1e-9
    assert abs(row1["basis"] - 2.5) < 1e-9
    assert abs(row1["basis_pct"] - (2.5 / 102.0)) < 1e-9

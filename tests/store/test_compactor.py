"""Acceptance tests for ParquetCompactor (Task 1.1)."""

from __future__ import annotations

import asyncio
import pathlib
import time
import pytest
import polars as pl

from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade
from crypcodile.store.parquet_sink import ParquetSink
from crypcodile.store.compactor import ParquetCompactor


def _trade(price: float = 1.0, local_ts: int = 1700000000000000000) -> Trade:
    return Trade(
        exchange="binance",
        symbol="binance:BTC-USDT",
        symbol_raw="BTCUSDT",
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(price),
        price=price,
        amount=1.5,
        side=Side.BUY,
    )


@pytest.mark.asyncio
async def test_parquet_compactor_merges_multiple_files(tmp_path: pathlib.Path) -> None:
    # 1. Create a ParquetSink to write files
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=2, flush_interval_seconds=9999)

    # Put trades in different flushes to create multiple files
    await sink.put(_trade(10.0))
    await sink.put(_trade(20.0))
    await sink.flush()

    await sink.put(_trade(30.0))
    await sink.put(_trade(40.0))
    await sink.flush()

    # Find parquet files before compaction
    pre_files = list(tmp_path.rglob("part-*.parquet"))
    assert len(pre_files) >= 2, f"Expected at least 2 files, found {len(pre_files)}"

    # 2. Run Compactor with min_age_seconds=0 to force compaction of new files
    compactor = ParquetCompactor(data_dir=tmp_path, min_age_seconds=0.0, poll_interval=1.0)
    await compactor.compact()

    # Find files after compaction
    post_files = list(tmp_path.rglob("part-*.parquet"))
    assert len(post_files) == 1, f"Expected exactly 1 compacted file, found {len(post_files)}"
    assert post_files[0].name.startswith("part-compacted-")

    # 3. Read back and verify all data is present
    df = pl.read_parquet(post_files[0])
    assert len(df) == 4
    prices = sorted(df["price"].to_list())
    assert prices == [10.0, 20.0, 30.0, 40.0]


@pytest.mark.asyncio
async def test_parquet_compactor_ignores_recent_files(tmp_path: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=2, flush_interval_seconds=9999)
    await sink.put(_trade(10.0))
    await sink.put(_trade(20.0))
    await sink.flush()

    await sink.put(_trade(30.0))
    await sink.put(_trade(40.0))
    await sink.flush()

    # Run compactor with high min_age_seconds, which should ignore the files
    compactor = ParquetCompactor(data_dir=tmp_path, min_age_seconds=60.0, poll_interval=1.0)
    await compactor.compact()

    post_files = list(tmp_path.rglob("part-*.parquet"))
    assert len(post_files) >= 2, "Expected files to be ignored by compactor"


@pytest.mark.asyncio
async def test_parquet_compactor_async_loop(tmp_path: pathlib.Path) -> None:
    compactor = ParquetCompactor(data_dir=tmp_path, min_age_seconds=0.0, poll_interval=0.1)
    compactor.start()
    assert compactor._running

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=2, flush_interval_seconds=9999)
    await sink.put(_trade(10.0))
    await sink.put(_trade(20.0))
    await sink.flush()

    await sink.put(_trade(30.0))
    await sink.put(_trade(40.0))
    await sink.flush()

    # Wait for background loop to run at least once
    await asyncio.sleep(0.5)
    await compactor.stop()
    assert not compactor._running

    post_files = list(tmp_path.rglob("part-*.parquet"))
    assert len(post_files) == 1
    assert post_files[0].name.startswith("part-compacted-")

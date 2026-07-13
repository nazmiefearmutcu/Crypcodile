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
    # stop() must await in-flight executor work — no residual race wait needed.
    assert compactor._inflight is None or compactor._inflight.done()

    post_files = list(tmp_path.rglob("part-*.parquet"))
    assert len(post_files) == 1
    assert post_files[0].name.startswith("part-compacted-")


@pytest.mark.asyncio
async def test_compact_rename_before_delete_on_rename_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If rename of temp→final fails, originals must remain (no data loss)."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=2, flush_interval_seconds=9999)
    await sink.put(_trade(10.0))
    await sink.put(_trade(20.0))
    await sink.flush()
    await sink.put(_trade(30.0))
    await sink.put(_trade(40.0))
    await sink.flush()

    pre_files = sorted(p.resolve() for p in tmp_path.rglob("part-*.parquet"))
    assert len(pre_files) >= 2
    pre_names = {p.name for p in pre_files}

    original_rename = pathlib.Path.rename

    def failing_rename(self: pathlib.Path, target: pathlib.Path | str) -> pathlib.Path:
        # Only fail the temp → part-compacted rename, not other renames.
        if self.name.startswith("temp-compact-"):
            raise OSError("simulated rename failure")
        return original_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", failing_rename)

    compactor = ParquetCompactor(data_dir=tmp_path, min_age_seconds=0.0, poll_interval=1.0)
    await compactor.compact()

    # Originals must still exist — never deleted before durable compact file.
    post_files = list(tmp_path.rglob("part-*.parquet"))
    post_names = {p.name for p in post_files}
    assert pre_names.issubset(post_names), (
        f"Originals lost after failed rename: missing {pre_names - post_names}"
    )
    # No successful compacted file should have been published.
    assert not any(n.startswith("part-compacted-") for n in post_names)
    # Temp file must be cleaned up on failure.
    assert list(tmp_path.rglob("temp-compact-*.parquet")) == []


@pytest.mark.asyncio
async def test_stop_awaits_inflight_compact(tmp_path: pathlib.Path) -> None:
    """stop() must wait for the executor compact job, not just cancel the loop task."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=2, flush_interval_seconds=9999)
    await sink.put(_trade(10.0))
    await sink.put(_trade(20.0))
    await sink.flush()
    await sink.put(_trade(30.0))
    await sink.put(_trade(40.0))
    await sink.flush()

    compactor = ParquetCompactor(data_dir=tmp_path, min_age_seconds=0.0, poll_interval=60.0)
    started = asyncio.Event()
    release = asyncio.Event()
    original_compact_sync = compactor._compact_sync

    def slow_compact_sync() -> None:
        # Signal the async test that we're inside the executor thread.
        loop = compactor._loop_for_test  # type: ignore[attr-defined]
        loop.call_soon_threadsafe(started.set)
        # Block the worker until the test has called stop() and is awaiting it.
        while not release.is_set():
            time.sleep(0.01)
        original_compact_sync()

    # Stash the running loop so the worker thread can signal the event.
    compactor._loop_for_test = asyncio.get_running_loop()  # type: ignore[attr-defined]
    compactor._compact_sync = slow_compact_sync  # type: ignore[method-assign]

    compact_task = asyncio.create_task(compactor.compact())
    await started.wait()

    # Kick off stop while compact is in-flight; it must not return until compact finishes.
    stop_task = asyncio.create_task(compactor.stop())
    await asyncio.sleep(0.05)
    assert not stop_task.done(), "stop() returned before in-flight compact finished"

    release.set()
    await stop_task
    await compact_task

    post_files = list(tmp_path.rglob("part-*.parquet"))
    assert len(post_files) == 1
    assert post_files[0].name.startswith("part-compacted-")


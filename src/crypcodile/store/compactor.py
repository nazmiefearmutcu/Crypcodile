"""Compactor service to merge small Parquet files in the data lake (Task 1.1).

Periodically scans the data directory, finds partitions with multiple small
Parquet files, and merges them into a single compacted file to avoid I/O bottlenecks.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import polars as pl

log = logging.getLogger(__name__)


class ParquetCompactor:
    """Background service that merges small Parquet files into a single file per bucket.

    Args:
        data_dir: Root directory of the data lake.
        min_age_seconds: Minimum age of files to be considered for compaction
            (prevents compacting files currently being written).
        poll_interval: Interval in seconds between compaction runs when running in background.
    """

    def __init__(
        self,
        data_dir: Path | str,
        min_age_seconds: float = 5.0,
        poll_interval: float = 10.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.min_age_seconds = min_age_seconds
        self.poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # Serialize concurrent compact() calls and let stop() await in-flight work.
        self._compact_lock = asyncio.Lock()
        self._inflight: asyncio.Future[Any] | None = None

    def start(self) -> None:
        """Start the background compaction loop."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            log.info("ParquetCompactor service started.")

    async def stop(self) -> None:
        """Stop the background compaction loop and wait for in-flight compact work."""
        if not self._running and self._task is None and self._inflight is None:
            return

        self._running = False
        # Snapshot BEFORE cancelling the loop task. Cancel can tear down compact()
        # finally and clear self._inflight while the executor thread still runs.
        inflight = self._inflight
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Await the snapshotted future so we never leave half-done compact state.
        if inflight is not None and not inflight.done():
            try:
                await inflight
            except Exception:
                # Compact errors are already logged inside compact/_compact_sync.
                pass

        log.info("ParquetCompactor service stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.compact()
            except Exception as e:
                log.error(f"Error in ParquetCompactor: {e}")
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def compact(self) -> None:
        """Scan the lake and compact folders containing multiple Parquet files."""
        if not self.data_dir.exists():
            return

        async with self._compact_lock:
            loop = asyncio.get_running_loop()
            # Track the executor Future so stop() can await in-flight work.
            fut = loop.run_in_executor(None, self._compact_sync)
            self._inflight = fut
            try:
                # shield: cancel of the outer loop task must not drop the await
                # of the executor Future; we still hold the lock until done.
                await asyncio.shield(fut)
            except asyncio.CancelledError:
                # Still wait for executor work to finish before releasing the lock.
                if not fut.done():
                    await fut
                raise
            finally:
                if self._inflight is fut:
                    self._inflight = None

    def _compact_sync(self) -> None:
        if not self.data_dir.exists():
            return

        now = time.time()
        # Find all leaf directories: exchange=*/channel=*/date=*/bucket=*
        bucket_dirs = list(self.data_dir.glob("exchange=*/channel=*/date=*/bucket=*"))

        for bucket_dir in bucket_dirs:
            if not bucket_dir.is_dir():
                continue

            # List all part-*.parquet files in this bucket
            files = list(bucket_dir.glob("part-*.parquet"))
            if len(files) < 2:
                continue

            # Check if any file was written too recently
            too_recent = False
            for f in files:
                try:
                    mtime = f.stat().st_mtime
                    if now - mtime < self.min_age_seconds:
                        too_recent = True
                        break
                except FileNotFoundError:
                    # File might have been renamed or deleted
                    too_recent = True
                    break

            if too_recent:
                continue

            # Identify the channel from path
            channel = None
            for parent in files[0].parents:
                if parent.name.startswith("channel="):
                    channel = parent.name[len("channel=") :]
                    break

            if not channel:
                continue

            log.info(f"Compacting {len(files)} files in {bucket_dir} for channel {channel}")

            temp_file: Path | None = None
            try:
                dfs = []
                for f in files:
                    try:
                        dfs.append(pl.read_parquet(f))
                    except Exception as err:
                        log.error(f"Error reading parquet file {f}: {err}")
                        # Skip this bucket on read error to prevent data loss
                        dfs = []
                        break

                if not dfs:
                    continue

                combined_df = pl.concat(dfs)

                # Write to temporary file (not part-* so catalog won't see it mid-write)
                temp_file = bucket_dir / f"temp-compact-{uuid.uuid4().hex}.parquet"
                combined_df.write_parquet(
                    temp_file,
                    compression="zstd",
                    compression_level=5,
                    row_group_size=250_000,
                )

                # Atomic durability order: rename temp → final part-compacted-* FIRST,
                # only then unlink originals. If rename fails/crash, originals remain.
                compacted_file = bucket_dir / f"part-compacted-{uuid.uuid4().hex}.parquet"
                temp_file.rename(compacted_file)
                temp_file = None  # rename succeeded; no temp left to clean up
                log.info(f"Compacted to {compacted_file.name} (rows: {len(combined_df)})")

                # Only after durable compact file exists, remove source parts.
                # Skip the compacted file itself if it were in `files` (it isn't —
                # we rename after listing originals).
                for f in files:
                    if f.resolve() == compacted_file.resolve():
                        continue
                    try:
                        f.unlink(missing_ok=True)
                    except Exception as err:
                        log.error(f"Failed to delete original file {f}: {err}")
            except Exception as e:
                log.error(f"Failed compaction for {bucket_dir}: {e}")
                # On any failure after writing temp: clean up temp; never delete originals
                # if compacted file is not durable.
                if temp_file is not None:
                    try:
                        temp_file.unlink(missing_ok=True)
                    except Exception as cleanup_err:
                        log.error(f"Failed to clean up temp file {temp_file}: {cleanup_err}")

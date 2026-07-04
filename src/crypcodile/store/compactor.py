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

    def start(self) -> None:
        """Start the background compaction loop."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
            log.info("ParquetCompactor service started.")

    async def stop(self) -> None:
        """Stop the background compaction loop."""
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
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

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._compact_sync)

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
                    channel = parent.name[len("channel="):]
                    break

            if not channel:
                continue

            log.info(f"Compacting {len(files)} files in {bucket_dir} for channel {channel}")

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

                # Write to temporary file
                temp_file = bucket_dir / f"temp-compact-{uuid.uuid4().hex}.parquet"
                combined_df.write_parquet(
                    temp_file,
                    compression="zstd",
                    compression_level=5,
                    row_group_size=250_000,
                )

                # Rename original files to hide them from DuckDB globs during transaction
                old_files = []
                for f in files:
                    try:
                        old_f = f.with_name(f.name.replace("part-", "old-"))
                        f.rename(old_f)
                        old_files.append(old_f)
                    except Exception as err:
                        log.error(f"Failed to hide original file {f} during compaction: {err}")

                # Rename to final compacted file (starts with part- so DuckDB reads it)
                compacted_file = bucket_dir / f"part-compacted-{uuid.uuid4().hex}.parquet"
                try:
                    temp_file.rename(compacted_file)
                except Exception as err:
                    log.error(f"Failed to rename temp file to final compacted file: {err}")
                    # Recovery: rename old files back to original part- filenames
                    for old_f in old_files:
                        try:
                            orig_f = old_f.with_name(old_f.name.replace("old-", "part-"))
                            old_f.rename(orig_f)
                        except Exception:
                            pass
                    raise

                # Safely delete original old files now that transaction is completed
                for old_f in old_files:
                    try:
                        old_f.unlink(missing_ok=True)
                    except Exception as err:
                        log.error(f"Failed to delete backup file {old_f}: {err}")

                log.info(f"Compacted to {compacted_file.name} (rows: {len(combined_df)})")
            except Exception as e:
                log.error(f"Failed compaction for {bucket_dir}: {e}")

"""Buffered, hive-partitioned Parquet sink for canonical Records.

Partition layout (Appendix §4):
    data/exchange={E}/channel={C}/date=YYYY-MM-DD/bucket={0..127}/part-{uuid}.parquet

Write policy:
  - Buffers rows per channel.
  - Auto-flushes when a channel buffer reaches ``max_buffer_rows`` rows.
  - Time-based flush is triggered on the next ``put`` after ``flush_interval_seconds``
    has elapsed, or explicitly via ``flush()`` / ``close()``.
  - A new ``part-{uuid}.parquet`` file is written on every flush; existing files are
    **never appended to** (Parquet footers are immutable).

Compression: ZSTD level 5 (streaming sweet spot per Appendix §4).
Row group size: 250 000 rows.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import polars as pl

from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink
from crypcodile.store.rows import to_row

# ---------------------------------------------------------------------------
# Polars schema definitions per channel
# ---------------------------------------------------------------------------
# Fields that every record carries.
_COMMON_FIELDS: dict[str, Any] = {
    "exchange": pl.Utf8,
    "symbol": pl.Utf8,
    "symbol_raw": pl.Utf8,
    "exchange_ts": pl.Int64,
    "local_ts": pl.Int64,
    # Partition columns (written as path components, kept in the row for DuckDB
    # hive reads that include them).
    "channel": pl.Utf8,
    "date": pl.Utf8,
    "bucket": pl.Int32,
}

# A named-struct dtype for a single (price, amount) level.
_LEVEL_STRUCT = pl.Struct({"price": pl.Float64, "amount": pl.Float64})

# Per-channel extra columns.
_CHANNEL_EXTRA: dict[str, dict[str, Any]] = {
    "trade": {
        "id": pl.Utf8,
        "price": pl.Float64,
        "amount": pl.Float64,
        "side": pl.Utf8,
        "liquidation": pl.Utf8,
        "l1_gas_fee": pl.Float64,
        "l2_gas_fee": pl.Float64,
        "gas_price": pl.Float64,
        "sender": pl.Utf8,
        "is_smart_wallet": pl.Boolean,
    },
    "farcaster_correlation": {
        "mentions_24h": pl.Int64,
        "dev_activity_score": pl.Float64,
        "trending_rank": pl.Int64,
    },
    "reserve_data_updated": {
        "reserve": pl.Utf8,
        "liquidity_rate": pl.Float64,
        "stable_borrow_rate": pl.Float64,
        "variable_borrow_rate": pl.Float64,
        "liquidity_index": pl.Int64,
        "variable_borrow_index": pl.Int64,
    },
    "liquidation_call": {
        "collateral_asset": pl.Utf8,
        "debt_asset": pl.Utf8,
        "user": pl.Utf8,
        "debt_to_cover": pl.Float64,
        "liquidated_collateral_amount": pl.Float64,
        "liquidator": pl.Utf8,
        "receive_a_token": pl.Boolean,
    },
    "book_snapshot": {
        "bids": pl.List(_LEVEL_STRUCT),
        "asks": pl.List(_LEVEL_STRUCT),
        "depth": pl.Int64,
        "sequence_id": pl.Int64,
        "is_snapshot": pl.Boolean,
    },
    "book_delta": {
        "bids": pl.List(_LEVEL_STRUCT),
        "asks": pl.List(_LEVEL_STRUCT),
        "seq_id": pl.Int64,
        "prev_seq_id": pl.Int64,
        "is_snapshot": pl.Boolean,
    },
    "book_ticker": {
        "bid_px": pl.Float64,
        "bid_sz": pl.Float64,
        "ask_px": pl.Float64,
        "ask_sz": pl.Float64,
        "update_id": pl.Int64,
    },
    "derivative_ticker": {
        "last_price": pl.Float64,
        "mark_price": pl.Float64,
        "index_price": pl.Float64,
        "funding_rate": pl.Float64,
        "predicted_funding_rate": pl.Float64,
        "funding_timestamp": pl.Int64,
        "open_interest": pl.Float64,
    },
    "options_chain": {
        "underlying": pl.Utf8,
        "underlying_price": pl.Float64,
        "strike": pl.Float64,
        "expiry": pl.Int64,
        "opt_type": pl.Utf8,
        "mark_price": pl.Float64,
        "mark_iv": pl.Float64,
        "bid_px": pl.Float64,
        "bid_sz": pl.Float64,
        "bid_iv": pl.Float64,
        "ask_px": pl.Float64,
        "ask_sz": pl.Float64,
        "ask_iv": pl.Float64,
        "last_price": pl.Float64,
        "open_interest": pl.Float64,
        "delta": pl.Float64,
        "gamma": pl.Float64,
        "vega": pl.Float64,
        "theta": pl.Float64,
        "rho": pl.Float64,
    },
    "funding": {
        "funding_rate": pl.Float64,
        "funding_timestamp": pl.Int64,
        "predicted_funding_rate": pl.Float64,
        "interval_hours": pl.Int64,
    },
    "open_interest": {
        "open_interest": pl.Float64,
        "open_interest_value": pl.Float64,
    },
    "liquidation": {
        "price": pl.Float64,
        "amount": pl.Float64,
        "side": pl.Utf8,
        "id": pl.Utf8,
    },
    "ohlcv": {
        "interval": pl.Utf8,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
        "buy_volume": pl.Float64,
        "sell_volume": pl.Float64,
        "num_trades": pl.Int64,
    },
}


def _channel_schema(channel: str) -> dict[str, Any]:
    """Return the full Polars schema for the given channel."""
    extra = _CHANNEL_EXTRA.get(channel, {})
    return {**_COMMON_FIELDS, **extra}


def _sanitize_path_segment(value: str, *, field: str) -> str:
    """Sanitize a hive-partition path segment before joining under data_dir.

    Rejects empty values, path separators, null bytes, and ``.`` / ``..`` so a
    hostile exchange/channel/date cannot escape the lake root via path
    traversal when building ``part_dir``.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {field} path segment: {value!r}")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(
            f"Invalid {field} path segment (contains separator or null): {value!r}"
        )
    if value in (".", ".."):
        raise ValueError(f"Invalid {field} path segment: {value!r}")
    return value


def _coerce_levels(
    rows: list[dict[str, Any]], field: str
) -> None:
    """Convert list-of-tuples book levels to list-of-dicts in-place.

    Polars ``pl.List(pl.Struct(...))`` requires dicts, not tuples.
    Idempotent: already-coerced dict levels are left unchanged so a retry
    after a failed write (which may have partially coerced rows) is safe.
    """
    for row in rows:
        levels = row.get(field)
        if levels is not None and levels and not isinstance(levels[0], dict):
            row[field] = [{"price": px, "amount": amt} for px, amt in levels]


# ---------------------------------------------------------------------------
# ParquetSink
# ---------------------------------------------------------------------------


class ParquetSink(Sink):
    """Buffered async sink that writes hive-partitioned Parquet files.

    Args:
        data_dir: Root directory for the data lake.
        max_buffer_rows: Flush a channel buffer when it reaches this many rows.
        flush_interval_seconds: Maximum seconds before a time-triggered flush.
            Pass a large number (e.g. 9999) to disable time-based flushing in
            tests.
    """

    def __init__(
        self,
        data_dir: Path | str,
        max_buffer_rows: int = 100_000,
        flush_interval_seconds: float = 5.0,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._max_buffer_rows = max_buffer_rows
        self._flush_interval = flush_interval_seconds
        # channel → list[row dicts]
        self._buffers: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        self._last_flush: float = time.monotonic()

    # ------------------------------------------------------------------
    # Sink interface
    # ------------------------------------------------------------------

    async def put(self, record: Record) -> None:
        """Buffer a record; auto-flush if thresholds are exceeded."""
        row = to_row(record)
        channel: str = row["channel"]
        self._buffers[channel].append(row)

        # Flush on row-count threshold
        if len(self._buffers[channel]) >= self._max_buffer_rows:
            await self._flush_channel(channel)
            self._last_flush = time.monotonic()
            return

        # Flush on time threshold (checked lazily on the next put)
        elapsed = time.monotonic() - self._last_flush
        if elapsed >= self._flush_interval:
            await self.flush()

    async def flush(self) -> None:
        """Flush all buffered channels to Parquet."""
        channels = list(self._buffers.keys())
        for channel in channels:
            if self._buffers[channel]:
                await self._flush_channel(channel)
        self._last_flush = time.monotonic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _flush_channel(self, channel: str) -> None:
        """Write a channel's buffer to one or more Parquet files.

        Rows are detached for the write attempt but only dropped after every
        partition write succeeds.  On any non-success path (write failure,
        ``CancelledError``, or other ``BaseException``) the snapshot is
        re-buffered (prepended ahead of any rows concurrent ``put()`` may
        have added while the write was in flight) so callers never see silent
        data loss.
        """
        # Detach current buffer so concurrent put() appends to a fresh list
        # via defaultdict, while we own the snapshot for this flush.
        rows = self._buffers.pop(channel, [])
        if not rows:
            return

        # Use try/finally + success flag so rows are re-buffered on ANY
        # non-success path, including CancelledError / BaseException (which
        # ``except Exception`` does not catch).
        ok = False
        try:
            # Group rows by (exchange, date, bucket) — each group → one file
            groups: defaultdict[tuple[str, str, int], list[dict[str, Any]]] = (
                defaultdict(list)
            )
            for row in rows:
                key = (row["exchange"], row["date"], row["bucket"])
                groups[key].append(row)

            for (exchange, date, bucket), group_rows in groups.items():
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._write_parquet_sync,
                    channel,
                    exchange,
                    date,
                    bucket,
                    group_rows,
                )
            ok = True
        finally:
            if not ok:
                # Write failed or task cancelled: restore snapshot without
                # losing any rows that arrived via put() while the flush was
                # in progress.
                pending = self._buffers.get(channel, [])
                self._buffers[channel] = rows + pending

    def _write_parquet_sync(
        self,
        channel: str,
        exchange: str,
        date: str,
        bucket: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Synchronous Parquet write (runs in executor to avoid blocking the loop)."""
        # Sanitize path components from record fields before joining under data_dir.
        exchange = _sanitize_path_segment(exchange, field="exchange")
        channel = _sanitize_path_segment(channel, field="channel")
        date = _sanitize_path_segment(date, field="date")

        data_dir = self._data_dir.resolve()
        part_dir = (
            data_dir
            / f"exchange={exchange}"
            / f"channel={channel}"
            / f"date={date}"
            / f"bucket={bucket}"
        )
        part_dir = part_dir.resolve()
        if not part_dir.is_relative_to(data_dir):
            raise ValueError(
                f"Partition path escapes data_dir: {part_dir} not under {data_dir}"
            )
        part_dir.mkdir(parents=True, exist_ok=True)
        out_path = part_dir / f"part-{uuid.uuid4().hex}.parquet"

        # Coerce book levels (list-of-tuples → list-of-dicts)
        if channel in ("book_snapshot", "book_delta"):
            _coerce_levels(rows, "bids")
            _coerce_levels(rows, "asks")

        # Build DataFrame with explicit schema to ensure type consistency
        schema = _channel_schema(channel)
        # Keep only columns that appear in the schema (unknown extras dropped)
        filtered_rows: list[dict[str, Any]] = [
            {k: row.get(k) for k in schema} for row in rows
        ]
        df = pl.DataFrame(filtered_rows, schema=schema)

        df.write_parquet(
            out_path,
            compression="zstd",
            compression_level=5,
            row_group_size=250_000,
        )

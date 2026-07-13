"""Acceptance tests for ParquetSink (Task 2.2)."""

from __future__ import annotations

import pathlib

import polars as pl
import pytest

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, Trade
from crypcodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(price: float = 1.0, local_ts: int = 1700000000000000000) -> Trade:
    return Trade(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(price),
        price=price,
        amount=2.0,
        side=Side.BUY,
    )


def _snap(local_ts: int = 1700000000000000000) -> BookSnapshot:
    return BookSnapshot(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        bids=[(100.0, 5.0), (99.0, 0.0)],  # (99.0, 0.0) is a canonical removal
        asks=[(101.0, 4.0)],
        depth=2,
        sequence_id=42,
        is_snapshot=True,
    )


def _find_parquets(base: pathlib.Path, pattern: str = "*.parquet") -> list[pathlib.Path]:
    """Collect parquet files synchronously (safe to call from sync context)."""
    return list(base.rglob(pattern))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_parquet_sink_writes_files_by_channel(tmp_path: pathlib.Path) -> None:
    """3 trades + 1 book_snapshot → files under trade/ and book_snapshot/ dirs."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)

    await sink.put(_trade(1.0))
    await sink.put(_trade(2.0))
    await sink.put(_trade(3.0))
    await sink.put(_snap())

    await sink.flush()

    all_files = _find_parquets(tmp_path)
    trade_files = [p for p in all_files if "channel=trade" in str(p)]
    snap_files = [p for p in all_files if "channel=book_snapshot" in str(p)]

    assert len(trade_files) >= 1, "Expected at least one trade parquet file"
    assert len(snap_files) >= 1, "Expected at least one book_snapshot parquet file"


async def test_parquet_sink_path_structure(tmp_path: pathlib.Path) -> None:
    """Hive path: exchange=.../channel=.../date=.../bucket=.../part-*.parquet."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade())
    await sink.flush()

    all_parquets = _find_parquets(tmp_path)
    assert all_parquets, "No parquet files written"
    for p in all_parquets:
        parts = p.parts
        # Each path segment should contain hive key=value pairs
        assert any("exchange=" in part for part in parts)
        assert any("channel=" in part for part in parts)
        assert any("date=" in part for part in parts)
        assert any("bucket=" in part for part in parts)


async def test_parquet_sink_read_back_rows(tmp_path: pathlib.Path) -> None:
    """Row count + field values survive round-trip."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    t1 = _trade(50000.1)
    t2 = _trade(50001.2)
    await sink.put(t1)
    await sink.put(t2)
    await sink.flush()

    # Collect actual file paths (pl.read_parquet needs a list, not a generator)
    all_files = _find_parquets(tmp_path)
    trade_files = [p for p in all_files if "channel=trade" in str(p)]
    assert trade_files, "No trade parquet files found"
    df = pl.read_parquet(trade_files)
    assert len(df) == 2
    prices = set(df["price"].to_list())
    assert 50000.1 in prices
    assert 50001.2 in prices
    assert df["side"][0] == "buy"


async def test_parquet_sink_book_removal_level_round_trips(tmp_path: pathlib.Path) -> None:
    """A canonical removal level (px, 0.0) must round-trip through Parquet."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_snap())
    await sink.flush()

    all_files = _find_parquets(tmp_path)
    snap_files = [p for p in all_files if "channel=book_snapshot" in str(p)]
    assert snap_files, "No book_snapshot parquet files found"
    df = pl.read_parquet(snap_files)
    assert len(df) == 1
    # bids col is stored as list[struct{price,amount}] — Polars returns list of dicts
    bids = df["bids"][0]
    price_amount_pairs = [(b["price"], b["amount"]) for b in bids]
    assert (99.0, 0.0) in price_amount_pairs  # canonical removal survives


async def test_parquet_sink_auto_flush_on_row_limit(tmp_path: pathlib.Path) -> None:
    """Buffer auto-flushes when max_buffer_rows is reached."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=3, flush_interval_seconds=9999)

    # After 3 puts the buffer should flush automatically
    await sink.put(_trade(1.0))
    await sink.put(_trade(2.0))
    await sink.put(_trade(3.0))

    # Without explicit flush, files should already exist
    trade_files = [p for p in _find_parquets(tmp_path) if "channel=trade" in str(p)]
    assert len(trade_files) >= 1, "Expected auto-flush after max_buffer_rows"


async def test_parquet_sink_never_appends_new_part_files(tmp_path: pathlib.Path) -> None:
    """Two separate flushes produce two distinct part-*.parquet files."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)

    await sink.put(_trade(1.0))
    await sink.flush()
    files_after_first = set(_find_parquets(tmp_path))

    await sink.put(_trade(2.0))
    await sink.flush()
    files_after_second = set(_find_parquets(tmp_path))

    new_files = files_after_second - files_after_first
    assert len(new_files) >= 1, "Second flush should write a new part file, not append"


# ---------------------------------------------------------------------------
# Regression: _last_flush updated after row-count-triggered flush (bug 3)
# ---------------------------------------------------------------------------


async def test_parquet_sink_last_flush_updated_after_row_count_flush(
    tmp_path: pathlib.Path,
) -> None:
    """A row-count-triggered flush must update _last_flush.

    If _last_flush is NOT updated after _flush_channel(), it retains the
    value set at construction time.  A subsequent put() that checks
    ``time.monotonic() - self._last_flush >= self._flush_interval`` would
    then see a large elapsed value and fire a spurious full flush().

    We verify the fix directly: immediately after the row-count flush fires
    (on the 3rd put), ``_last_flush`` must be recent (< 1 second old).
    Before the fix it would still reflect the construction-time value,
    making elapsed grow unboundedly.
    """
    import time as _time

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=3, flush_interval_seconds=9999)

    await sink.put(_trade(1.0))
    await sink.put(_trade(2.0))

    # Capture the lower-bound timestamp AFTER the first two puts and BEFORE the
    # third.  The third put triggers a row-count flush; _last_flush MUST be
    # updated in that branch to a value >= t_after_puts.  Without the fix,
    # _last_flush would still hold the construction-time value (set before
    # t_after_puts), making the assertion below fail.
    t_after_puts = _time.monotonic()

    await sink.put(_trade(3.0))  # row-count flush fires here

    t_upper = _time.monotonic()
    assert sink._last_flush >= t_after_puts, (
        "_last_flush was not updated after a row-count-triggered flush; "
        "it must be set to time.monotonic() so time-based flush logic "
        "does not fire spuriously on the very next put()."
    )
    assert sink._last_flush <= t_upper + 0.1, (
        "_last_flush is set to an implausibly future value"
    )


# ---------------------------------------------------------------------------
# Regression: buffer safety — only drop rows after durable write (Wave 3)
# ---------------------------------------------------------------------------


async def test_parquet_sink_write_failure_does_not_lose_rows(
    tmp_path: pathlib.Path,
    monkeypatch,
) -> None:
    """If Parquet write fails, buffered rows must still be present for retry.

    Before the fix, ``_flush_channel`` popped the buffer *before* writing;
    a failed write permanently discarded the data.  After the fix, rows are
    re-buffered and the exception is re-raised.
    """
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)

    await sink.put(_trade(1.0))
    await sink.put(_trade(2.0))
    assert len(sink._buffers["trade"]) == 2

    def _boom(*_args, **_kwargs):
        raise OSError("simulated durable write failure")

    monkeypatch.setattr(sink, "_write_parquet_sync", _boom)

    with pytest.raises(OSError, match="simulated durable write failure"):
        await sink.flush()

    # Rows must still be buffered — no silent data loss
    assert "trade" in sink._buffers
    assert len(sink._buffers["trade"]) == 2
    prices = {row["price"] for row in sink._buffers["trade"]}
    assert prices == {1.0, 2.0}

    # No parquet files should have been produced
    assert _find_parquets(tmp_path) == []

    # After the write path is restored, a subsequent flush must succeed and
    # persist the previously failed rows.
    monkeypatch.undo()
    await sink.flush()
    assert sink._buffers.get("trade", []) == []
    trade_files = [p for p in _find_parquets(tmp_path) if "channel=trade" in str(p)]
    assert trade_files, "retry flush should write parquet after write path restored"
    df = pl.read_parquet(trade_files)
    assert len(df) == 2
    assert set(df["price"].to_list()) == {1.0, 2.0}


async def test_parquet_sink_successful_flush_clears_buffer(
    tmp_path: pathlib.Path,
) -> None:
    """Successful flush still drops rows from the buffer and writes files."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)

    await sink.put(_trade(10.0))
    await sink.put(_trade(20.0))
    assert len(sink._buffers["trade"]) == 2

    await sink.flush()

    assert sink._buffers.get("trade", []) == []
    trade_files = [p for p in _find_parquets(tmp_path) if "channel=trade" in str(p)]
    assert len(trade_files) >= 1
    df = pl.read_parquet(trade_files)
    assert len(df) == 2
    assert set(df["price"].to_list()) == {10.0, 20.0}


async def test_parquet_sink_write_failure_preserves_concurrent_puts(
    tmp_path: pathlib.Path,
    monkeypatch,
) -> None:
    """Rows added via put() during a failed flush must not be lost either.

    Concurrent puts append while the flush snapshot is detached; on failure
    the snapshot is prepended in front of any pending rows.
    """
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)

    await sink.put(_trade(1.0))

    original_write = sink._write_parquet_sync

    def _fail_then_note(*args, **kwargs):
        # Simulate a concurrent put that lands while write is in progress.
        # Direct buffer append mirrors put()'s post-to_row path for this channel.
        from crypcodile.store.rows import to_row

        sink._buffers["trade"].append(to_row(_trade(99.0)))
        raise OSError("write failed mid-flush")

    monkeypatch.setattr(sink, "_write_parquet_sync", _fail_then_note)

    with pytest.raises(OSError, match="write failed mid-flush"):
        await sink.flush()

    buf = sink._buffers["trade"]
    prices = [row["price"] for row in buf]
    # Original snapshot first, then concurrent put
    assert prices == [1.0, 99.0]
    # original_write kept only to document intent / silence unused lint
    assert original_write is not None


# ---------------------------------------------------------------------------
# Security: partition path components must not escape data_dir
# ---------------------------------------------------------------------------


async def test_parquet_sink_rejects_malicious_exchange_path_traversal(
    tmp_path: pathlib.Path,
) -> None:
    """A malicious exchange name must not write outside data_dir (raises).

    exchange values are used as hive path segments. Without sanitization,
    values containing ``/`` or ``..`` could escape the lake root.
    """
    data_dir = tmp_path / "lake"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=100, flush_interval_seconds=9999)

    # Inject a path-traversal exchange into the buffered row.
    from crypcodile.store.rows import to_row

    row = to_row(_trade(1.0))
    row["exchange"] = "../../outside"
    sink._buffers["trade"].append(row)

    with pytest.raises(ValueError, match="path segment|escapes data_dir"):
        await sink.flush()

    # Nothing written outside data_dir
    assert list(outside.rglob("*")) == []
    # No parquet under data_dir either (write aborted)
    assert _find_parquets(data_dir) == []
    # Rows re-buffered for retry (flush failure path)
    assert len(sink._buffers["trade"]) == 1


async def test_parquet_sink_rejects_malicious_channel_and_date_segments(
    tmp_path: pathlib.Path,
) -> None:
    """Channel and date path segments are sanitized the same way as exchange."""
    from crypcodile.store.parquet_sink import _sanitize_path_segment

    for bad in ("../x", "a/b", "a\\b", "..", ".", "", "foo/../../../etc"):
        with pytest.raises(ValueError):
            _sanitize_path_segment(bad, field="test")

    assert _sanitize_path_segment("deribit", field="exchange") == "deribit"
    assert _sanitize_path_segment("trade", field="channel") == "trade"
    assert _sanitize_path_segment("2024-01-15", field="date") == "2024-01-15"

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)
    with pytest.raises(ValueError, match="path segment|escapes data_dir"):
        sink._write_parquet_sync(
            channel="trade/../evil",
            exchange="deribit",
            date="2024-01-01",
            bucket=0,
            rows=[{"exchange": "deribit", "channel": "trade", "date": "2024-01-01"}],
        )
    with pytest.raises(ValueError, match="path segment|escapes data_dir"):
        sink._write_parquet_sync(
            channel="trade",
            exchange="deribit",
            date="2024-01-01/../../escape",
            bucket=0,
            rows=[{"exchange": "deribit", "channel": "trade", "date": "2024-01-01"}],
        )

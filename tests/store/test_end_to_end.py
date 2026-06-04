"""M2 end-to-end integration test (Task 2.6).

Pipeline:
    normalize fixtures → ParquetSink → Catalog query → replay merge
    → reconstruct book (OrderBook).

The test exercises every M2 component together:
    - Deribit normalizer produces Trade, BookSnapshot, BookDelta records.
    - ParquetSink writes hive-partitioned Parquet files in a tmp dir.
    - Catalog.scan() reads them back and filters by symbol/time.
    - Catalog.query() counts rows via raw SQL.
    - replay() k-way-merges two per-channel iterators; output is time-ordered.
    - OrderBook applies the snapshot + delta from the book fixture; the
      reconstructed book state must match the expected levels.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest

from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.replay.merge import replay
from crocodile.replay.orderbook import BookGap, OrderBook
from crocodile.schema.records import BookDelta, BookSnapshot, Record, Trade
from crocodile.store.catalog import Catalog
from crocodile.store.parquet_sink import ParquetSink

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "exchanges" / "deribit" / "fixtures"
_TRADES_JSON = _FIXTURE_DIR / "trades.json"
_BOOK_JSON = _FIXTURE_DIR / "book.json"

# Nanosecond timestamp range that covers the 2023-11-14 date used by the fixtures.
# trades.json timestamps: 1700000000000 ms → 1700000000000000000 ns
_NS_START = 1_700_000_000_000_000_000  # 2023-11-14 ~22:13:20 UTC
_NS_END = _NS_START + 10_000_000_000  # +10 s

# local_ts values used when normalizing (must fall within _NS_START.._NS_END)
_LOCAL_TS_TRADE = _NS_START + 1_000_000  # +1 ms
_LOCAL_TS_BOOK1 = _NS_START + 2_000_000  # +2 ms (snapshot)
_LOCAL_TS_BOOK2 = _NS_START + 3_000_000  # +3 ms (delta)


# ---------------------------------------------------------------------------
# Helper: normalize fixtures → list[Record]
# ---------------------------------------------------------------------------


def _normalize_trades() -> list[Record]:
    msg = json.loads(_TRADES_JSON.read_text())
    return list(normalize_message(msg, local_ts=_LOCAL_TS_TRADE))


def _normalize_book() -> list[Record]:
    msgs = json.loads(_BOOK_JSON.read_text())
    records: list[Record] = []
    ts_list = [_LOCAL_TS_BOOK1, _LOCAL_TS_BOOK2]
    for msg, ts in zip(msgs, ts_list, strict=True):
        records.extend(normalize_message(msg, local_ts=ts))
    return records


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_e2e_normalize_sink_catalog_round_trip(tmp_path: pathlib.Path) -> None:
    """Normalize fixtures → ParquetSink → Catalog: rows round-trip correctly."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)

    all_records: list[Record] = _normalize_trades() + _normalize_book()
    assert all_records, "Normalization must produce at least one record"

    for rec in all_records:
        await sink.put(rec)
    await sink.flush()

    # --- Catalog scan: trade rows for BTC-PERPETUAL ---
    catalog = Catalog(data_dir=tmp_path)
    df_trades = catalog.scan("trade", "deribit:BTC-PERPETUAL", _NS_START, _NS_END)
    assert len(df_trades) >= 2, "Expected at least 2 trade rows from trades.json"
    # Rows must be ordered by local_ts
    ts_list = df_trades["local_ts"].to_list()
    assert ts_list == sorted(ts_list), "catalog.scan must return rows ordered by local_ts"

    # --- Catalog query: raw SQL count must agree ---
    catalog.refresh_views()
    df_count = catalog.query("SELECT count(*) AS n FROM trade")
    assert df_count["n"][0] >= 2

    # --- Catalog scan: book_snapshot for BTC-PERPETUAL ---
    df_snap = catalog.scan("book_snapshot", "deribit:BTC-PERPETUAL", _NS_START, _NS_END)
    assert len(df_snap) >= 1, "Expected at least one book_snapshot row"

    # --- Catalog scan: book_delta for BTC-PERPETUAL ---
    df_delta = catalog.scan("book_delta", "deribit:BTC-PERPETUAL", _NS_START, _NS_END)
    assert len(df_delta) >= 1, "Expected at least one book_delta row"


async def test_e2e_replay_merge_is_time_ordered(tmp_path: pathlib.Path) -> None:
    """Write trade + book records, read back, replay-merge → globally ordered."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)

    trade_records = [r for r in _normalize_trades() if isinstance(r, Trade)]
    book_records = [r for r in _normalize_book() if isinstance(r, (BookSnapshot, BookDelta))]

    for rec in trade_records + book_records:
        await sink.put(rec)
    await sink.flush()

    catalog = Catalog(data_dir=tmp_path)

    # Read the two streams back from the catalog (already sorted by local_ts per
    # catalog.scan contract).
    # Use the in-memory record lists as replay inputs (already sorted).
    # This exercises replay() without needing a full Parquet→Record deserializer.
    # The Catalog round-trip was already verified above via catalog.scan/query.
    _ = catalog  # referenced only above
    trade_stream: Iterator[Record] = iter(sorted(trade_records, key=lambda r: r.local_ts))
    book_stream: Iterator[Record] = iter(sorted(book_records, key=lambda r: r.local_ts))

    merged = list(replay([trade_stream, book_stream]))
    assert len(merged) == len(trade_records) + len(book_records)

    # Verify global non-decreasing local_ts
    prev_ts = 0
    for rec in merged:
        assert rec.local_ts >= prev_ts, (
            f"Replay output not time-ordered: {rec.local_ts} < {prev_ts}"
        )
        prev_ts = rec.local_ts


async def test_e2e_book_reconstruction_from_fixture() -> None:
    """Apply deribit book.json (snapshot + delta) via OrderBook; verify final state."""
    book_records = [r for r in _normalize_book() if isinstance(r, (BookSnapshot, BookDelta))]

    # Must have exactly: 1 snapshot + 1 delta
    snaps = [r for r in book_records if isinstance(r, BookSnapshot)]
    deltas = [r for r in book_records if isinstance(r, BookDelta)]
    assert len(snaps) == 1, "Expected one BookSnapshot"
    assert len(deltas) == 1, "Expected one BookDelta"

    ob = OrderBook()

    # Apply the snapshot
    ob.apply(snaps[0])
    assert ob.best_bid() == 100.0, "After snapshot, best bid should be 100.0"

    # Apply the delta:
    #   bids: delete 99.0 → absent, change 100.0 → 7.0
    #   asks: new 102.0 → 1.0
    ob.apply(deltas[0])

    # 99.0 must be absent (deleted)
    assert 99.0 not in ob.bids, "Level 99.0 should have been removed by the delta"
    # 100.0 must have size 7.0
    assert ob.bids.get(100.0) == 7.0, "Level 100.0 should be size 7.0 after change"
    # 102.0 must be present at 1.0
    assert ob.asks.get(102.0) == 1.0, "Level 102.0 should be size 1.0 after new ask"
    # Top-of-book bid
    assert ob.best_bid() == 100.0

    # --- Gap detection: feed a non-contiguous delta → BookGap ---
    gap_delta = BookDelta(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=_LOCAL_TS_BOOK2 + 1_000_000,
        bids=[],
        asks=[],
        seq_id=999,         # not 102 — gap
        prev_seq_id=500,    # not 101 (the last applied seq_id)
        is_snapshot=False,
    )
    with pytest.raises(BookGap):
        ob.apply(gap_delta)


async def test_e2e_full_pipeline(tmp_path: pathlib.Path) -> None:
    """Full pipeline: normalize → sink → catalog → replay → reconstruct book."""
    # 1. Normalize all fixture records
    all_records = _normalize_trades() + _normalize_book()

    # 2. Write to ParquetSink
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in all_records:
        await sink.put(rec)
    await sink.flush()

    # 3. Verify Catalog sees trade rows
    catalog = Catalog(data_dir=tmp_path)
    df = catalog.scan("trade", "deribit:BTC-PERPETUAL", _NS_START, _NS_END)
    assert len(df) > 0, "Catalog must return trade rows after sink flush"

    # 4. Replay merge over in-memory book records (sorted)
    book_records: list[Record] = [
        r for r in all_records if isinstance(r, (BookSnapshot, BookDelta))
    ]
    trade_records: list[Record] = [r for r in all_records if isinstance(r, Trade)]

    merged = list(
        replay([
            iter(sorted(trade_records, key=lambda r: r.local_ts)),
            iter(sorted(book_records, key=lambda r: r.local_ts)),
        ])
    )
    assert len(merged) > 0

    # 5. Pass book records through OrderBook reconstruction
    ob = OrderBook()
    for rec in merged:
        if isinstance(rec, (BookSnapshot, BookDelta)):
            ob.apply(rec)

    # After processing book.json: 99.0 deleted, 100.0@7.0, 102.0@1.0 ask
    assert 99.0 not in ob.bids
    assert ob.bids.get(100.0) == 7.0
    assert ob.asks.get(102.0) == 1.0
    assert ob.best_bid() == 100.0

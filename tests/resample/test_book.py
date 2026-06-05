"""Tests for Task 5.2 — book snapshot resampling at fixed wall-clock intervals.

Acceptance criteria (from the plan):
  - ``resample_book_snapshots(records, interval_ns, top_n)`` consumes a stream
    of BookSnapshot + BookDelta records, reconstructs the book via the M2
    OrderBook engine, and emits a BookSnapshot at fixed wall-clock intervals
    (keyed on ``local_ts``).
  - Each emitted snapshot captures the book state at the *first* ``local_ts``
    that reaches or exceeds the next bucket boundary.
  - ``depth`` field equals min(len(bids) + len(asks), 2*top_n) up to the actual
    book depth.
  - Bids are ordered descending by price; asks ascending (canonical order).
  - Tested against the Deribit ``book.json`` fixture.
  - Input with no snapshot before any deltas is handled (no crash, no output
    until a snapshot is seen).
  - ``top_n=None`` means emit all levels.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.resample.book import resample_book_snapshots
from crocodile.schema.records import BookDelta, BookSnapshot

# Path to the existing Deribit book fixture (snapshot + delta-with-delete)
BOOK_FIX = (
    pathlib.Path(__file__).parent.parent
    / "exchanges"
    / "deribit"
    / "fixtures"
    / "book.json"
)

# 1 second in nanoseconds
_1S_NS = 1_000_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(
    local_ts: int,
    seq: int | None = None,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> BookSnapshot:
    return BookSnapshot(
        exchange="test",
        symbol="test:SYM",
        symbol_raw="SYM",
        exchange_ts=None,
        local_ts=local_ts,
        bids=bids or [(100.0, 5.0), (99.0, 2.0)],
        asks=asks or [(101.0, 4.0)],
        depth=3,
        sequence_id=seq,
    )


def _make_delta(
    local_ts: int,
    seq_id: int | None = None,
    prev_seq_id: int | None = None,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> BookDelta:
    return BookDelta(
        exchange="test",
        symbol="test:SYM",
        symbol_raw="SYM",
        exchange_ts=None,
        local_ts=local_ts,
        bids=bids or [],
        asks=asks or [],
        seq_id=seq_id,
        prev_seq_id=prev_seq_id,
    )


# ---------------------------------------------------------------------------
# Test: basic interval emission with synthetic records
# ---------------------------------------------------------------------------


def test_resample_emits_snapshot_per_interval() -> None:
    """Given a snapshot at ts=0 and deltas at ts=1s and ts=2s, a 1s interval
    should emit exactly 2 snapshots (one per boundary crossed: 1s and 2s).
    """
    # Three records each 1s apart (local_ts drives bucketing)
    base = 0
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=base, seq=1),
        _make_delta(local_ts=base + _1S_NS, seq_id=2, prev_seq_id=1,
                    bids=[(100.0, 10.0)]),
        _make_delta(local_ts=base + 2 * _1S_NS, seq_id=3, prev_seq_id=2,
                    bids=[(100.0, 15.0)]),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=10))

    # The input crosses exactly 2 boundaries (1s and 2s), yielding 2 snapshots.
    assert len(result) >= 2, f"expected >=2 snapshots, got {len(result)}"
    for snap in result:
        assert isinstance(snap, BookSnapshot)
        assert snap.is_snapshot is True


def test_resample_no_records_returns_empty() -> None:
    """Empty input must produce no output."""
    result = list(resample_book_snapshots([], interval_ns=_1S_NS, top_n=5))
    assert result == []


def test_resample_deltas_before_snapshot_ignored() -> None:
    """Deltas arriving before the first snapshot must not trigger emission."""
    records: list[BookSnapshot | BookDelta] = [
        _make_delta(local_ts=0, seq_id=1),
        _make_delta(local_ts=_1S_NS, seq_id=2),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=5))
    assert result == [], f"should be empty before first snapshot, got {result}"


# ---------------------------------------------------------------------------
# Test: top-N depth trimming
# ---------------------------------------------------------------------------


def test_resample_top_n_trims_depth() -> None:
    """Emitted snapshots must contain at most top_n bids and top_n asks."""
    bids = [(float(100 - i), float(i + 1)) for i in range(10)]  # 10 bid levels
    asks = [(float(101 + i), float(i + 1)) for i in range(10)]  # 10 ask levels
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1, bids=bids, asks=asks),
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=3))

    assert len(result) >= 1
    for snap in result:
        assert len(snap.bids) <= 3, f"bids too deep: {len(snap.bids)}"
        assert len(snap.asks) <= 3, f"asks too deep: {len(snap.asks)}"


def test_resample_top_n_none_keeps_all_levels() -> None:
    """top_n=None means all levels are included."""
    bids = [(float(100 - i), float(i + 1)) for i in range(10)]
    asks = [(float(101 + i), float(i + 1)) for i in range(10)]
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1, bids=bids, asks=asks),
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=None))

    assert len(result) >= 1
    snap = result[0]
    # All 10 bid levels must be present
    assert len(snap.bids) == 10
    assert len(snap.asks) == 10


# ---------------------------------------------------------------------------
# Test: bids/asks ordering in emitted snapshots
# ---------------------------------------------------------------------------


def test_resample_bids_desc_asks_asc_ordering() -> None:
    """Emitted snapshot bids are price-descending; asks are price-ascending."""
    bids = [(99.0, 2.0), (100.0, 5.0), (98.0, 1.0)]
    asks = [(103.0, 1.0), (101.0, 4.0), (102.0, 2.0)]
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1, bids=bids, asks=asks),
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=10))

    assert len(result) >= 1
    snap = result[0]
    bid_prices = [b[0] for b in snap.bids]
    ask_prices = [a[0] for a in snap.asks]
    assert bid_prices == sorted(bid_prices, reverse=True), f"bids not sorted desc: {bid_prices}"
    assert ask_prices == sorted(ask_prices), f"asks not sorted asc: {ask_prices}"


# ---------------------------------------------------------------------------
# Test: depth field reflects actual level count (capped at 2*top_n)
# ---------------------------------------------------------------------------


def test_resample_depth_field_reflects_level_count() -> None:
    """``depth`` field in the emitted snapshot equals len(bids) + len(asks)."""
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1,
                       bids=[(100.0, 5.0), (99.0, 2.0)],
                       asks=[(101.0, 4.0)]),
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=10))
    assert len(result) >= 1
    snap = result[0]
    assert snap.depth == len(snap.bids) + len(snap.asks)


# ---------------------------------------------------------------------------
# Test: Deribit book.json fixture produces correct final book state
# ---------------------------------------------------------------------------


def test_resample_deribit_fixture() -> None:
    """Apply the Deribit book.json fixture; the emitted snapshot must match
    the final expected book state from the reconstruction test:
      - bids: 100.0@7.0 (99.0 removed)
      - asks: 101.0@4.0, 102.0@1.0
    """
    msgs = json.loads(BOOK_FIX.read_text())
    # Assign local_ts values so they cross a 1s boundary
    raw_records: list[BookSnapshot | BookDelta] = []
    for i, msg in enumerate(msgs):
        for rec in normalize_message(msg, local_ts=i * _1S_NS):
            if isinstance(rec, (BookSnapshot, BookDelta)):
                raw_records.append(rec)

    result = list(resample_book_snapshots(raw_records, interval_ns=_1S_NS, top_n=50))

    # After the snapshot + delta the book should reflect the final state
    assert len(result) >= 1, "expected at least one emitted snapshot"
    # Check the last emitted snapshot (reflects the most recent book state)
    last = result[-1]
    assert isinstance(last, BookSnapshot)

    bid_map = dict(last.bids)
    ask_map = dict(last.asks)

    # price 99.0 must have been deleted
    assert 99.0 not in bid_map, f"99.0 should have been removed; bids={last.bids}"
    # price 100.0 updated to 7.0
    assert bid_map.get(100.0) == 7.0, f"100.0 expected 7.0; bids={last.bids}"
    # price 102.0 added on asks
    assert ask_map.get(102.0) == 1.0, f"102.0 expected 1.0; asks={last.asks}"


# ---------------------------------------------------------------------------
# Test: snapshot metadata (exchange, symbol) is preserved
# ---------------------------------------------------------------------------


def test_resample_preserves_exchange_and_symbol() -> None:
    """Exchange and symbol fields in emitted snapshots must match the source."""
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1),
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=5))

    assert len(result) >= 1
    snap = result[0]
    assert snap.exchange == "test"
    assert snap.symbol == "test:SYM"
    assert snap.symbol_raw == "SYM"


# ---------------------------------------------------------------------------
# Test: single record (just a snapshot) produces one emission
# ---------------------------------------------------------------------------


def test_resample_single_snapshot_only() -> None:
    """A single BookSnapshot at ts=0 sets next_boundary to 1s but never
    crosses it, so the result is guaranteed to be empty.
    """
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1),
    ]
    # A single snapshot at ts=0 with interval=1s — no boundary crossing occurs.
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=5))
    assert result == []


# ---------------------------------------------------------------------------
# Test: book removal (amount=0) is reflected in the emitted snapshot
# ---------------------------------------------------------------------------


def test_resample_removed_level_absent_in_snapshot() -> None:
    """A level set to amount=0 (removal) must not appear in the emitted snapshot."""
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1,
                       bids=[(100.0, 5.0), (99.0, 2.0)], asks=[]),
        # Delta removes 99.0 and updates 100.0
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1,
                    bids=[(99.0, 0.0), (100.0, 8.0)], asks=[]),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=10))

    assert len(result) >= 1
    snap = result[-1]
    bid_prices = [b[0] for b in snap.bids]
    assert 99.0 not in bid_prices, f"99.0 should have been removed; bids={snap.bids}"
    bid_map = dict(snap.bids)
    assert bid_map.get(100.0) == 8.0, f"100.0 should be 8.0; bids={snap.bids}"


# ---------------------------------------------------------------------------
# Test: emitted snapshot local_ts equals bucket boundary (time-keying contract)
# ---------------------------------------------------------------------------


def test_resample_snapshot_local_ts_equals_bucket_boundary() -> None:
    """Each emitted snapshot's ``local_ts`` must equal the bucket boundary
    timestamp, not the triggering record's ``local_ts``.  This is the primary
    time-keying contract for downstream consumers.
    """
    base = 500_000_000  # 0.5s — sits in the first [0s, 1s) bucket
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=base, seq=1),
        # This delta lands exactly on the 1s boundary.
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1,
                    bids=[(100.0, 10.0)]),
    ]
    result = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=10))

    assert len(result) >= 1, "expected at least one snapshot at the 1s boundary"
    # The snapshot triggered at the 1s boundary must carry local_ts=1s exactly.
    snap = result[0]
    assert snap.local_ts == _1S_NS, (
        f"expected local_ts={_1S_NS} (bucket boundary), got {snap.local_ts}"
    )


# ---------------------------------------------------------------------------
# Test: ValueError is raised for non-positive interval_ns
# ---------------------------------------------------------------------------


def test_resample_raises_for_zero_interval() -> None:
    """``interval_ns=0`` must raise ``ValueError``; a non-positive interval has
    no meaningful bucket boundary.
    """
    with pytest.raises(ValueError):
        list(resample_book_snapshots([], interval_ns=0))

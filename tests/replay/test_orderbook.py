"""Acceptance tests for order-book reconstruction (Task 2.5).

Tests apply the Deribit book.json fixture (snapshot + delta-with-delete) through
the OrderBook state machine and verify:
  - Before first snapshot: records are skipped.
  - Snapshot resets the book.
  - Delta with amount>0 sets level; amount==0 removes level (canonical rule).
  - Deltas sharing one local_ts are applied atomically.
  - After applying the fixture: 99.0 absent, 100.0 @ 7.0, 102.0 @ 1.0, top bid = 100.0.
  - Non-contiguous seq_id raises BookGap.
"""

import json
import pathlib

import pytest

from crocodile.replay.orderbook import BookGap, OrderBook
from crocodile.schema.records import BookDelta, BookSnapshot

BOOK_FIX = pathlib.Path(__file__).parent.parent / "exchanges" / "deribit" / "fixtures" / "book.json"


def _make_snapshot(
    seq: int | None = 1,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
    local_ts: int = 1,
) -> BookSnapshot:
    return BookSnapshot(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=local_ts,
        bids=bids or [(100.0, 5.0), (99.0, 2.0)],
        asks=asks or [(101.0, 4.0)],
        depth=3,
        sequence_id=seq,
        is_snapshot=True,
    )


def _make_delta(
    seq_id: int | None,
    prev_seq_id: int | None,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
    local_ts: int = 2,
) -> BookDelta:
    return BookDelta(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=local_ts,
        bids=bids or [],
        asks=asks or [],
        seq_id=seq_id,
        prev_seq_id=prev_seq_id,
        is_snapshot=False,
    )


# ---------------------------------------------------------------------------
# Core fixture test: apply Deribit book.json snapshot + delta
# ---------------------------------------------------------------------------


def test_deribit_fixture_snapshot_then_delta_with_delete() -> None:
    """Applying the Deribit book.json fixture must yield the exact final state."""
    msgs = json.loads(BOOK_FIX.read_text())

    from crocodile.exchanges.deribit.normalize import normalize_message

    snap = next(iter(normalize_message(msgs[0], local_ts=1)))
    delta = next(iter(normalize_message(msgs[1], local_ts=2)))

    assert isinstance(snap, BookSnapshot)
    assert isinstance(delta, BookDelta)

    book = OrderBook()
    book.apply(snap)
    book.apply(delta)

    # price 99.0 was deleted by action=delete (canonical amount=0.0)
    assert 99.0 not in book.bids, "99.0 should have been removed"

    # price 100.0 updated to size 7.0
    assert book.bids.get(100.0) == 7.0

    # price 102.0 added on asks side
    assert book.asks.get(102.0) == 1.0

    # top-of-book bid
    assert book.best_bid() == 100.0


# ---------------------------------------------------------------------------
# Pre-snapshot rows are skipped
# ---------------------------------------------------------------------------


def test_skip_rows_before_first_snapshot() -> None:
    """Deltas arriving before the first snapshot must be ignored silently."""
    book = OrderBook()
    delta = _make_delta(seq_id=5, prev_seq_id=4, bids=[(100.0, 10.0)], local_ts=1)
    book.apply(delta)  # must not raise, must not corrupt state

    # Book is still empty — no snapshot yet
    assert len(book.bids) == 0
    assert len(book.asks) == 0
    assert book.best_bid() is None


# ---------------------------------------------------------------------------
# Snapshot resets state
# ---------------------------------------------------------------------------


def test_snapshot_resets_book() -> None:
    """A later BookSnapshot must discard all prior book state."""
    book = OrderBook()
    snap1 = _make_snapshot(seq=1, bids=[(200.0, 5.0)], asks=[(201.0, 3.0)])
    book.apply(snap1)
    assert book.bids.get(200.0) == 5.0

    # A new snapshot completely replaces state
    snap2 = _make_snapshot(seq=10, bids=[(100.0, 1.0)], asks=[(101.0, 1.0)])
    book.apply(snap2)

    assert 200.0 not in book.bids  # old levels gone
    assert book.bids.get(100.0) == 1.0


# ---------------------------------------------------------------------------
# amount=0 removes level
# ---------------------------------------------------------------------------


def test_delta_amount_zero_removes_level() -> None:
    """A delta level with amount=0.0 must remove that price from the book."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=1, bids=[(100.0, 5.0), (99.0, 2.0)], asks=[]))
    book.apply(_make_delta(seq_id=2, prev_seq_id=1, bids=[(99.0, 0.0)]))

    assert 99.0 not in book.bids
    assert book.bids.get(100.0) == 5.0  # unchanged


# ---------------------------------------------------------------------------
# amount>0 sets absolute size
# ---------------------------------------------------------------------------


def test_delta_amount_positive_sets_level() -> None:
    """A delta level with amount>0 must set the absolute size (not add)."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=1, bids=[(100.0, 5.0)], asks=[]))
    book.apply(_make_delta(seq_id=2, prev_seq_id=1, bids=[(100.0, 99.0)]))

    assert book.bids.get(100.0) == 99.0


# ---------------------------------------------------------------------------
# Gap detection: prev_seq_id continuity (Deribit / futures shape)
# ---------------------------------------------------------------------------


def test_gap_raises_book_gap_on_non_contiguous_seq() -> None:
    """A delta whose prev_seq_id != last applied seq_id must raise BookGap."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=100))
    book.apply(_make_delta(seq_id=101, prev_seq_id=100))  # OK

    # Now feed a gap: prev_seq_id=999 ≠ 101
    with pytest.raises(BookGap):
        book.apply(_make_delta(seq_id=200, prev_seq_id=999))


def test_no_gap_when_prev_seq_matches() -> None:
    """Continuous deltas with matching prev_seq_id must be applied without error."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=1))
    book.apply(_make_delta(seq_id=2, prev_seq_id=1))
    book.apply(_make_delta(seq_id=3, prev_seq_id=2))  # must not raise


# ---------------------------------------------------------------------------
# Gap detection: spot shape (prev_seq_id=None, U/u continuity)
# For spot-shaped deltas, prev_seq_id is None; continuity is tracked via seq_id.
# We expose this via apply_spot() or by detecting None prev_seq_id.
# ---------------------------------------------------------------------------


def test_gap_spot_shape_non_contiguous_raises() -> None:
    """For spot-shaped records (prev_seq_id=None), gaps in seq_id must raise BookGap."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=100, local_ts=1))

    # Feed two spot-shaped deltas: seq 101, then 103 (skip 102 = gap)
    book.apply(_make_delta(seq_id=101, prev_seq_id=None, local_ts=2))
    with pytest.raises(BookGap):
        book.apply(_make_delta(seq_id=103, prev_seq_id=None, local_ts=3))


def test_gap_spot_shape_continuous_is_ok() -> None:
    """Spot-shaped continuous deltas (seq_id increments by 1) must not raise."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=100, local_ts=1))
    book.apply(_make_delta(seq_id=101, prev_seq_id=None, local_ts=2))
    book.apply(_make_delta(seq_id=102, prev_seq_id=None, local_ts=3))  # must not raise


# ---------------------------------------------------------------------------
# Atomic batching: deltas sharing the same local_ts are applied together
# ---------------------------------------------------------------------------


def test_atomic_batch_same_local_ts() -> None:
    """apply_batch() applies a group of deltas sharing one local_ts atomically."""
    book = OrderBook()
    book.apply(_make_snapshot(seq=1, bids=[(100.0, 5.0), (99.0, 2.0)], asks=[], local_ts=1))

    delta_a = _make_delta(seq_id=2, prev_seq_id=1, bids=[(99.0, 0.0)], local_ts=2)
    delta_b = _make_delta(seq_id=None, prev_seq_id=None, bids=[(98.0, 3.0)], local_ts=2)

    book.apply_batch([delta_a, delta_b])

    assert 99.0 not in book.bids
    assert book.bids.get(98.0) == 3.0
    assert book.bids.get(100.0) == 5.0


# ---------------------------------------------------------------------------
# best_bid / best_ask helpers
# ---------------------------------------------------------------------------


def test_best_bid_ask_empty() -> None:
    book = OrderBook()
    assert book.best_bid() is None
    assert book.best_ask() is None


def test_best_bid_ask_after_snapshot() -> None:
    book = OrderBook()
    book.apply(
        _make_snapshot(
            seq=1,
            bids=[(100.0, 5.0), (99.0, 2.0), (101.0, 3.0)],
            asks=[(102.0, 1.0), (103.0, 2.0)],
        )
    )
    assert book.best_bid() == 101.0   # highest bid
    assert book.best_ask() == 102.0   # lowest ask


# ---------------------------------------------------------------------------
# T5a regression: _check_gap guard when _last_seq_id is None after None-seq snapshot
# ---------------------------------------------------------------------------


def test_none_seq_snapshot_futures_shape_delta_does_not_silently_pass() -> None:
    """After a snapshot with sequence_id=None, a futures-shape delta (prev_seq_id
    is not None) must NOT silently pass the gap check.

    The guard must signal that continuity cannot be established and raise BookGap
    (or skip the delta), rather than treating the stream as in-sync.
    """
    book = OrderBook()
    # Apply a snapshot with sequence_id=None -> _last_seq_id stays None
    book.apply(_make_snapshot(seq=None))
    assert book._last_seq_id is None  # pre-condition

    # A futures-shape delta: prev_seq_id is not None but last_seq_id is None.
    # With the old code this silently passes because
    #   `self._last_seq_id is not None` is False.
    # With the fix it must raise BookGap (cannot validate continuity).
    with pytest.raises(BookGap):
        book.apply(_make_delta(seq_id=101, prev_seq_id=100))

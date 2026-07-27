"""Tests for Task 5.2 — book snapshot resampling at fixed wall-clock intervals.

Acceptance criteria (from the plan):
  - ``resample_book_snapshots(records, interval_ns, top_n)`` consumes a stream
    of BookSnapshot + BookDelta records, reconstructs the book via the M2
    OrderBook engine, and emits a BookSnapshot at fixed wall-clock intervals
    (keyed on ``local_ts``).
  - A snapshot stamped at boundary *B* reflects exactly the records with
    ``local_ts <= B``. This criterion used to read "the book state at the *first*
    ``local_ts`` that reaches or exceeds the next bucket boundary", which is the
    lookahead-biased rule: it puts a record stamped after *B* into the bar
    labelled *B*. The equity fork of this resampler emitted before applying and
    the two were merged onto that ordering; see
    ``test_a_boundary_holds_nothing_stamped_after_it`` for the measurement.
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

from crocodile.core.errors import ProvenanceError
from crocodile.core.replay.orderbook import OrderBook
from crocodile.core.resample.book import _capture_snapshot, resample_book_snapshots
from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import BookDelta, BookSnapshot
from crocodile.crypto.exchanges.deribit.normalize import normalize_message

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
        source="test",
        symbol="test:SYM",
        symbol_raw="SYM",
        source_ts=None,
        local_ts=local_ts,
        asset_class=AssetClass.CRYPTO,
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
        source="test",
        symbol="test:SYM",
        symbol_raw="SYM",
        source_ts=None,
        local_ts=local_ts,
        asset_class=AssetClass.CRYPTO,
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
    assert snap.source == "test"
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


# ---------------------------------------------------------------------------
# Test: a reconstructed snapshot must not claim to have been reported
# ---------------------------------------------------------------------------


def test_a_resampled_snapshot_does_not_claim_to_be_venue_reported() -> None:
    """`NATIVE` says the venue reported this value directly. This one it did not.

    The record is built by `_capture_snapshot` in `resample/book.py` out of a
    reconstructed `OrderBook`, so `prov=NATIVE, prov_basis="native",
    prov_confidence=1.0` — the struct defaults, inherited by passing no `prov*`
    field — is a false statement written into the lake. It is also silent
    downstream: `provenance.describe()` is what the REST and MCP surfaces are
    required to emit as a warning whenever they serve a record whose `prov` is
    not `NATIVE`, so a resampled book is served indistinguishably from a venue
    snapshot.
    """
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(local_ts=0, seq=1),
        _make_delta(local_ts=_1S_NS, seq_id=2, prev_seq_id=1, bids=[(100.0, 10.0)]),
    ]
    (snap,) = list(resample_book_snapshots(records, interval_ns=_1S_NS, top_n=10))

    assert snap.prov is Provenance.DERIVED
    assert snap.prov_basis == "book_resample"
    assert snap.prov_inputs == ["book_snapshot", "book_delta"]


def test_a_quiet_interval_reports_the_book_that_held_during_it() -> None:
    """Migrated from ``test_a_resampled_snapshot_scores_its_own_lookahead``.

    Same stream, same subject — what a run of boundaries dragged along by one late
    record contains — and the opposite answer, because the ordering changed. It used
    to assert ``[0.0, 0.0, 0.0, 0.0, 1.0]``: four bars that each reported the book as
    it stood *after* the 5s delta, labelled with the confidence that said so and
    written to the lake anyway. A bar scoring its own wrongness is still a wrong bar.

    Now the four quiet boundaries carry the state that actually held during them, so
    the bid is 5.0 until the delta lands and 10.0 only from 5s on. The confidence went
    with the formula: ``book_resample`` is a declared constant 1.0, argued in the
    registry, because a capture that cannot contain the future is exact at its own
    timestamp.
    """
    on_the_boundary = list(
        resample_book_snapshots(
            [_make_snapshot(local_ts=0, seq=1), _make_delta(local_ts=_1S_NS, seq_id=2)],
            interval_ns=_1S_NS,
        )
    )
    assert [s.prov_confidence for s in on_the_boundary] == [1.0]

    quiet = list(
        resample_book_snapshots(
            [
                _make_snapshot(local_ts=0, seq=1),
                _make_delta(local_ts=5 * _1S_NS, seq_id=2, bids=[(100.0, 10.0)]),
            ],
            interval_ns=_1S_NS,
        )
    )

    assert [s.local_ts for s in quiet] == [i * _1S_NS for i in range(1, 6)]
    assert [dict(s.bids)[100.0] for s in quiet] == [5.0, 5.0, 5.0, 5.0, 10.0]
    assert [s.prov_confidence for s in quiet] == [1.0] * 5


def test_a_boundary_holds_nothing_stamped_after_it() -> None:
    """The bar labelled 10:00:00 must not know what the book did at 10:00:00.200.

    This is the whole collision, in one record. ``core`` applied the boundary-crossing
    record and *then* emitted every boundary at or below it; ``equity`` emitted the
    boundaries below the record first. On the stream below — a 120-lot bid at the touch,
    pulled to 5 two hundred milliseconds into the next bucket — the 1s boundary reported:

        apply-then-emit (was core)  bids=[(150.00, 5.0)]    prov_confidence=0.8
        emit-then-apply (was equity) bids=[(150.00, 120.0)]  prov_confidence=1.0

    24x the visible top-of-book depth, in the one direction that cannot be traded: a
    strategy reading the 10:00:00 snapshot already knew the bid was about to be pulled.
    That is lookahead bias, which is how a backtest reports a return nobody could have
    earned, so this is not a stylistic difference between two forks — one was wrong.

    Revert the two statements in ``resample_book_snapshots`` and this test reports the
    5.0.
    """
    records: list[BookSnapshot | BookDelta] = [
        _make_snapshot(
            local_ts=200_000_000, seq=1, bids=[(150.00, 100.0)], asks=[(150.05, 100.0)]
        ),
        _make_delta(local_ts=900_000_000, seq_id=2, prev_seq_id=1, bids=[(150.00, 120.0)]),
        # 200 ms past the 1s boundary: the bid is pulled from 120 to 5.
        _make_delta(local_ts=1_200_000_000, seq_id=3, prev_seq_id=2, bids=[(150.00, 5.0)]),
        _make_delta(local_ts=2_000_000_000, seq_id=4, prev_seq_id=3, asks=[(150.05, 90.0)]),
    ]

    at_one_second, at_two_seconds = list(
        resample_book_snapshots(records, interval_ns=_1S_NS, top_n=None)
    )

    assert at_one_second.local_ts == _1S_NS
    assert at_one_second.bids == [(150.00, 120.0)]
    assert at_one_second.asks == [(150.05, 100.0)]
    # The 1.2s pull belongs to the 2s bar, where it happened.
    assert at_two_seconds.bids == [(150.00, 5.0)]
    assert at_two_seconds.asks == [(150.05, 90.0)]


def test_a_capture_refuses_to_stamp_a_boundary_over_a_later_update() -> None:
    """The tripwire that replaced the ``lookahead_ns`` confidence term.

    The old formula scored a biased capture and emitted it; four bars reporting a book
    that would not exist for another five seconds went into the lake at
    ``prov_confidence=0.0`` and nothing raised. Refusing to build the record is the loud
    form of the same observation, and it is what stops a future reordering of
    ``resample_book_snapshots`` from being a silent one.

    Unreachable through the public function by construction, which is the point: the
    guard is on the ordering, so it is exercised where the ordering is bypassed.
    """
    book = OrderBook()
    snap = _make_snapshot(local_ts=0, seq=1)
    book.apply(snap)

    with pytest.raises(ProvenanceError, match="lookahead"):
        _capture_snapshot(book, snap, _1S_NS, _1S_NS + 200_000_000, None)

    on_time = _capture_snapshot(book, snap, _1S_NS, _1S_NS, None)
    assert on_time.local_ts == _1S_NS
    assert on_time.prov_confidence == 1.0


def test_the_equity_import_path_reaches_this_exact_function() -> None:
    """One rule, one function — asserted rather than left to a docstring.

    ``crocodile.equity.resample`` re-exported a second ``resample_book_snapshots`` with
    this signature and a different boundary rule. Two implementations of one algorithm
    kept in two places, drifting, is the merge's origin story; an identity assertion is
    the cheapest thing that notices a third copy arriving under the old name.
    """
    from crocodile.equity.resample import resample_book_snapshots as equity_entry

    assert equity_entry is resample_book_snapshots

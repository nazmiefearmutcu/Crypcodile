"""Tests for the gap-detect → backfill bridge (Task 4.3).

Acceptance criteria (appendix §6):
- When OrderBookSync returns RESYNC, the bridge buffers live deltas,
  fires a REST book snapshot fetch, applies the snapshot, then applies
  buffered deltas with seq >= snapshot.seq (drops earlier ones).
- The race: live deltas that arrive *during* a resync are buffered, not
  discarded, and applied correctly after the new snapshot.
- Trade-sequence gap detection: when Deribit trade_seq skips, a gap is
  recorded/logged; the bridge does not crash.
- set_snapshot / resync clears the stale buffer.
"""

from __future__ import annotations

import pytest

from crypcodile.exchanges.binance.book import OrderBookSync, SyncResult
from crypcodile.ingest.book_sync import (
    filter_buffered_book_deltas,
    keep_delta_after_snapshot,
)
from crypcodile.ingest.gap_bridge import BookResyncBridge, TradeSeqGap
from crypcodile.schema.records import BookDelta, BookSnapshot

# ---------------------------------------------------------------------------
# Helpers: fake REST-snapshot fetchers (no network)
# ---------------------------------------------------------------------------


def _make_snapshot(last_update_id: int) -> BookSnapshot:
    """Build a minimal BookSnapshot with a given sequence_id."""
    return BookSnapshot(
        exchange="binance-spot",
        symbol="binance-spot:BTCUSDT",
        symbol_raw="BTCUSDT",
        exchange_ts=None,
        local_ts=0,
        bids=[(50000.0, 1.0)],
        asks=[(50001.0, 1.0)],
        depth=2,
        sequence_id=last_update_id,
        is_snapshot=True,
    )


def _make_delta(
    U: int,
    u: int,
    pu: int | None = None,
    *,
    price: float = 50000.0,
    amount: float = 2.0,
) -> BookDelta:
    return BookDelta(
        exchange="binance-spot",
        symbol="binance-spot:BTCUSDT",
        symbol_raw="BTCUSDT",
        exchange_ts=None,
        local_ts=u,  # reuse u as local_ts for ordering clarity
        bids=[(price, amount)],
        asks=[],
        seq_id=u,
        prev_seq_id=pu,
        is_snapshot=False,
    )


# ---------------------------------------------------------------------------
# Tests: BookResyncBridge
# ---------------------------------------------------------------------------


class TestBookResyncBridge:
    """Full state-machine tests for BookResyncBridge."""

    def test_normal_apply_path_no_resync(self) -> None:
        """Happy path: no gap → all deltas reach the sink directly."""
        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)

        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            return _make_snapshot(200)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        # Process deltas in the happy path (APPLY result each time)
        results = []
        results.append(bridge.feed_sync_result(SyncResult.APPLY, _make_delta(101, 105)))
        results.append(bridge.feed_sync_result(SyncResult.APPLY, _make_delta(106, 110)))

        # Not resyncing, no buffering
        assert bridge.is_resyncing is False
        # All deltas should be emitted (not None)
        assert all(r is not None for r in results)

    def test_drop_path_returns_none(self) -> None:
        """DROP result: bridge returns None (caller should not emit this delta)."""
        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)

        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            return _make_snapshot(200)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        result = bridge.feed_sync_result(SyncResult.DROP, _make_delta(50, 100))
        assert result is None
        assert bridge.is_resyncing is False

    def test_resync_triggers_buffering(self) -> None:
        """On RESYNC: bridge enters resyncing mode and buffers subsequent deltas."""
        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)

        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            return _make_snapshot(200)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        # Trigger RESYNC (gap in sequence)
        result = bridge.feed_sync_result(SyncResult.RESYNC, _make_delta(200, 300))
        # The delta that triggered RESYNC is buffered (not emitted)
        assert bridge.is_resyncing is True
        assert result is None

        # A subsequent delta during resyncing is also buffered
        result2 = bridge.feed_sync_result(SyncResult.DROP, _make_delta(301, 305))
        assert bridge.is_resyncing is True
        # During resync, all incoming are buffered (even if sync says DROP)
        assert result2 is None

    async def test_complete_resync_cycle_with_race(self) -> None:
        """Race scenario (appendix §6):
        1. Gap detected → enter resync mode, buffer live deltas
        2. REST snapshot arrives (last_update_id=200)
        3. Buffered deltas: seq 195 (dropped, seq < 200), seq 201 (kept), seq 202 (kept)
        4. After resync, sync state machine is reset to new snapshot
        5. Further deltas apply normally
        """
        snapshots_fetched: list[str] = []

        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            snapshots_fetched.append(symbol)
            # REST snapshot seeds at lastUpdateId=200
            return _make_snapshot(200)

        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)
        # Apply a couple of good events
        assert sync.feed(U=101, u=105, pu=None) == SyncResult.APPLY
        assert sync.feed(U=106, u=110, pu=None) == SyncResult.APPLY

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        # --- Gap event that triggers RESYNC ---
        gap_delta = _make_delta(200, 300)
        r = bridge.feed_sync_result(SyncResult.RESYNC, gap_delta)
        assert bridge.is_resyncing is True
        assert r is None

        # --- Buffer deltas that arrive during resync ---
        # These arrive while REST snapshot is "in flight"
        stale_delta = _make_delta(190, 195)  # seq 195 < snapshot 200 → must be dropped
        live_delta1 = _make_delta(201, 201)  # seq 201 >= 200 → kept
        live_delta2 = _make_delta(202, 202)  # seq 202 → kept

        # The bridge is resyncing; any feed_sync_result calls buffer the delta
        bridge.buffer_delta(stale_delta)
        bridge.buffer_delta(live_delta1)
        bridge.buffer_delta(live_delta2)

        # --- Complete the resync: provide the REST snapshot ---
        applied = await bridge.complete_resync()

        # Snapshot was fetched once
        assert len(snapshots_fetched) == 1
        assert snapshots_fetched[0] == "BTCUSDT"

        # Bridge is no longer resyncing
        assert bridge.is_resyncing is False

        # applied contains: [snapshot] + kept buffered deltas (not stale)
        snap_records = [r for r in applied if isinstance(r, BookSnapshot)]
        delta_records = [r for r in applied if isinstance(r, BookDelta)]
        assert len(snap_records) == 1
        assert snap_records[0].sequence_id == 200
        # stale_delta (seq=195 < 200) must be dropped.
        # gap_delta (seq=300), live_delta1 (seq=201), live_delta2 (seq=202) >= 200 → kept.
        # Buffer order: [gap_delta, stale, live1, live2]
        # After dropping stale: [gap_delta, live1, live2]
        assert len(delta_records) == 3
        seq_ids = [d.seq_id for d in delta_records]
        assert 195 not in seq_ids
        assert 300 in seq_ids  # RESYNC-triggering delta (buffered first)
        assert 201 in seq_ids
        assert 202 in seq_ids

    async def test_resync_clears_old_buffer_on_second_resync(self) -> None:
        """A second RESYNC while already resyncing clears the stale buffer and starts fresh."""
        call_count = 0

        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            nonlocal call_count
            call_count += 1
            return _make_snapshot(300)

        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        # First RESYNC
        bridge.feed_sync_result(SyncResult.RESYNC, _make_delta(200, 300))
        assert bridge.is_resyncing is True

        # Buffer some deltas
        bridge.buffer_delta(_make_delta(201, 201))
        bridge.buffer_delta(_make_delta(202, 202))

        # Second RESYNC (another gap) while already resyncing → clears buffer
        bridge.feed_sync_result(SyncResult.RESYNC, _make_delta(500, 600))

        # complete_resync fetches snapshot at 300, keeps deltas with seq >= 300
        applied = await bridge.complete_resync()
        # deltas 201, 202 were in the first buffer; 500 was in the second RESYNC trigger
        # all have seq < 300 → they should be dropped
        delta_records = [r for r in applied if isinstance(r, BookDelta)]
        # The key invariant: the second RESYNC clears the old buffer entirely.
        # After second RESYNC only the second RESYNC delta (seq=600) is in buffer.
        # Snapshot is 300, so delta with seq_id=600 >= 300 → kept.
        # Old buffer deltas (201, 202) must not appear.
        seq_ids = [d.seq_id for d in delta_records]
        assert 201 not in seq_ids
        assert 202 not in seq_ids
        # The second-RESYNC-triggering delta (seq_id=600 >= snapshot 300) MUST be present.
        assert 600 in seq_ids

    async def test_complete_resync_with_none_sequence_id_keeps_all_deltas(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When fetch_snapshot returns sequence_id=None the bridge must:
        - emit a WARNING (sync machine is not re-anchored)
        - keep ALL buffered deltas (no seq filter possible)
        - still clear the buffer and exit resyncing mode
        """
        import logging

        def _make_snapshot_no_seq() -> BookSnapshot:
            return BookSnapshot(
                exchange="binance-spot",
                symbol="binance-spot:BTCUSDT",
                symbol_raw="BTCUSDT",
                exchange_ts=None,
                local_ts=0,
                bids=[(50000.0, 1.0)],
                asks=[(50001.0, 1.0)],
                depth=2,
                sequence_id=None,
                is_snapshot=True,
            )

        async def fetch_snapshot_none(symbol: str) -> BookSnapshot:
            return _make_snapshot_no_seq()

        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot_none,
            symbol="BTCUSDT",
        )

        # Trigger resync and buffer a couple of deltas
        bridge.feed_sync_result(SyncResult.RESYNC, _make_delta(200, 300))
        bridge.buffer_delta(_make_delta(195, 195))  # would normally be stale
        bridge.buffer_delta(_make_delta(201, 201))

        with caplog.at_level(logging.WARNING, logger="crypcodile.ingest.gap_bridge"):
            applied = await bridge.complete_resync()

        # Bridge exits resyncing mode
        assert bridge.is_resyncing is False

        # A warning about sequence_id=None must have been emitted
        assert any("sequence_id=None" in rec.message for rec in caplog.records), (
            "Expected warning about sequence_id=None; got: "
            + str([r.message for r in caplog.records])
        )

        # All buffered deltas (including the normally-stale seq=195 one) are kept
        delta_records = [r for r in applied if isinstance(r, BookDelta)]
        seq_ids = [d.seq_id for d in delta_records]
        assert 300 in seq_ids   # triggering delta
        assert 195 in seq_ids   # kept because snap_seq is None
        assert 201 in seq_ids


# ---------------------------------------------------------------------------
# Tests: TradeSeqGap
# ---------------------------------------------------------------------------


class TestTradeSeqGap:
    """Trade-sequence gap detector."""

    def test_no_gap_on_first_trade(self) -> None:
        detector = TradeSeqGap()
        # First trade: no previous seq → no gap
        assert detector.feed(trade_seq=1) is False

    def test_no_gap_on_consecutive(self) -> None:
        detector = TradeSeqGap()
        detector.feed(trade_seq=100)
        assert detector.feed(trade_seq=101) is False
        assert detector.feed(trade_seq=102) is False

    def test_detects_gap(self) -> None:
        detector = TradeSeqGap()
        detector.feed(trade_seq=100)
        # skip 101 → gap
        assert detector.feed(trade_seq=102) is True

    def test_gap_advances_last_seq(self) -> None:
        """After a gap, the detector should advance past it so the next event is clean."""
        detector = TradeSeqGap()
        detector.feed(trade_seq=100)
        detector.feed(trade_seq=102)  # gap: True
        # Next sequential after 102 should not be a gap
        assert detector.feed(trade_seq=103) is False

    def test_reset_clears_state(self) -> None:
        detector = TradeSeqGap()
        detector.feed(trade_seq=100)
        detector.reset()
        # After reset, next trade is treated as the first → no gap
        assert detector.feed(trade_seq=200) is False

    def test_backward_seq_still_reports_gap_and_logs_correctly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Backward sequence (reconnect without reset) is flagged as a gap.

        The log message must say 'backward' rather than 'skipped -997' to avoid
        confusing log consumers.
        """
        import logging

        detector = TradeSeqGap()
        detector.feed(trade_seq=1000)

        with caplog.at_level(logging.WARNING, logger="crypcodile.ingest.gap_bridge"):
            result = detector.feed(trade_seq=5)

        # Still a gap (sequence is not consecutive)
        assert result is True
        # Log must use the 'backward' branch, not the 'skipped -NNN' branch
        assert any("backward" in rec.message for rec in caplog.records), (
            "Expected 'backward' in log message but got: "
            + str([r.message for r in caplog.records])
        )


# ---------------------------------------------------------------------------
# T5a regression: venue-aware boundary delta filtering
# ---------------------------------------------------------------------------


class TestCompleteResyncBoundaryVenueAware:
    """Regression tests for the off-by-one boundary delta handling.

    Binance SPOT rule:  drop deltas with seq_id <= snap_seq  (boundary in snapshot)
    Binance FUTURES rule: drop deltas with seq_id < snap_seq  (boundary is first valid)
    """

    async def test_spot_boundary_delta_is_dropped(self) -> None:
        """For SPOT, a buffered delta exactly at snap_seq must be DROPPED.

        snap_seq = 200; delta.seq_id = 200 -> stale boundary, belongs to snapshot.
        """
        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            return _make_snapshot(200)

        sync = OrderBookSync(venue="spot")
        sync.set_snapshot(last_update_id=100)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        bridge.feed_sync_result(SyncResult.RESYNC, _make_delta(200, 300))
        # Buffer a delta exactly at snap_seq=200 (boundary - must be dropped for spot)
        boundary_delta = _make_delta(200, 200)
        bridge.buffer_delta(boundary_delta)
        # Also buffer a delta strictly above snap_seq (must be kept)
        kept_delta = _make_delta(201, 201)
        bridge.buffer_delta(kept_delta)

        applied = await bridge.complete_resync()
        seq_ids = [r.seq_id for r in applied if isinstance(r, BookDelta)]
        # boundary delta (seq=200) must NOT appear in the output
        assert 200 not in seq_ids, (
            f"SPOT: boundary delta at snap_seq should be dropped, but seq_ids={seq_ids}"
        )
        # delta above snap_seq must be kept
        assert 201 in seq_ids

    async def test_futures_boundary_delta_is_kept(self) -> None:
        """For FUTURES, a buffered delta exactly at snap_seq must be KEPT.

        snap_seq = 200; delta.seq_id = 200 -> this is the first valid futures event.
        """
        async def fetch_snapshot(symbol: str) -> BookSnapshot:
            return _make_snapshot(200)

        sync = OrderBookSync(venue="futures")
        sync.set_snapshot(last_update_id=100)

        bridge = BookResyncBridge(
            sync=sync,
            fetch_snapshot=fetch_snapshot,
            symbol="BTCUSDT",
        )

        bridge.feed_sync_result(SyncResult.RESYNC, _make_delta(200, 300))
        # Buffer a delta exactly at snap_seq=200 (boundary - must be KEPT for futures)
        boundary_delta = _make_delta(200, 200)
        bridge.buffer_delta(boundary_delta)
        # Also buffer a stale delta below snap_seq (must be dropped)
        stale_delta = _make_delta(199, 199)
        bridge.buffer_delta(stale_delta)

        applied = await bridge.complete_resync()
        seq_ids = [r.seq_id for r in applied if isinstance(r, BookDelta)]
        # boundary delta (seq=200) MUST appear in the output for futures
        assert 200 in seq_ids, (
            f"FUTURES: boundary delta at snap_seq should be kept, but seq_ids={seq_ids}"
        )
        # stale delta (seq=199 < 200) must be dropped
        assert 199 not in seq_ids


# ---------------------------------------------------------------------------
# Shared helper extraction (book_sync) — multi-venue buffer filter
# ---------------------------------------------------------------------------


class TestFilterBufferedBookDeltas:
    """Unit tests for :func:`filter_buffered_book_deltas` / keep helper."""

    def test_spot_and_futures_boundary(self) -> None:
        snap = 100
        below = _make_delta(99, 99)
        at = _make_delta(100, 100)
        above = _make_delta(101, 101)
        buf = [below, at, above]

        spot_kept = filter_buffered_book_deltas(buf, snap, venue="spot")
        assert [d.seq_id for d in spot_kept] == [101]

        fut_kept = filter_buffered_book_deltas(buf, snap, venue="futures")
        assert [d.seq_id for d in fut_kept] == [100, 101]

    def test_none_snap_keeps_all(self) -> None:
        buf = [_make_delta(1, 1), _make_delta(2, 2)]
        kept = filter_buffered_book_deltas(buf, None, venue="spot")
        assert len(kept) == 2

    def test_keep_helper_matches_filter(self) -> None:
        d = _make_delta(50, 50)
        assert keep_delta_after_snapshot(d, 50, venue="spot") is False
        assert keep_delta_after_snapshot(d, 50, venue="futures") is True

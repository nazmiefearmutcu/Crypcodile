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

from crocodile.exchanges.binance.book import OrderBookSync, SyncResult
from crocodile.ingest.gap_bridge import BookResyncBridge, TradeSeqGap
from crocodile.schema.records import BookDelta, BookSnapshot

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

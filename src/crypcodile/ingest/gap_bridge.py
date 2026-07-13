"""Gap-detect → backfill bridge (Task 4.3).

Wires OrderBookSync.RESYNC to a REST snapshot fetch, buffers live deltas
during the resync window, and applies them after the snapshot arrives
(dropping any with seq < snapshot.seq_id).

Appendix §6:
  Gap ⇒ buffer live deltas, fire REST snapshot, apply REST then buffered
  deltas in order (handle in-flight race).  REST snapshot seeds the
  continuity chain.

Usage example (inside a connector's message loop)::

    bridge = BookResyncBridge(
        sync=OrderBookSync(venue="spot"),
        fetch_snapshot=my_rest_fetch,
        symbol="BTCUSDT",
    )
    sync.set_snapshot(last_update_id=rest_snapshot_id)

    async for raw in transport:
        delta = normalize_depth(raw, ...)
        # U comes from the wire depthUpdate (not stored on BookDelta).
        result = sync.feed(U=U, u=delta.seq_id, pu=delta.prev_seq_id)
        record = bridge.feed_sync_result(result, delta)
        if record is not None:
            await sink.put(record)

        if bridge.is_resyncing:
            # Inline-await complete_resync() in the message loop; no concurrent
            # background Task is needed for single-coroutine asyncio use.
            applied = await bridge.complete_resync()
            for r in applied:
                await sink.put(r)

Primary production wiring: ``BinanceConnector._handle_message`` (when
``book_delta`` / ``book_snapshot`` channels are subscribed) — see
``crypcodile.exchanges.binance.connector``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from crypcodile.exchanges.binance.book import OrderBookSync, SyncResult
from crypcodile.schema.records import BookDelta, BookSnapshot

log = logging.getLogger(__name__)

# Type alias: an async callable that fetches a REST book snapshot for a symbol.
FetchSnapshotFn = Callable[[str], Awaitable[BookSnapshot]]

# Records that the bridge can return / emit.
BookRecord = BookSnapshot | BookDelta


class BookResyncBridge:
    """Stateful bridge between OrderBookSync and REST-snapshot resync logic.

    Responsibilities:
    - Translate ``SyncResult`` (APPLY/DROP/RESYNC) into emit-or-buffer decisions.
    - On RESYNC: enter resyncing mode, buffer subsequent deltas.
    - On ``complete_resync()``: fetch REST snapshot, update the sync state machine,
      apply buffered deltas with ``seq_id >= snapshot.sequence_id``, return
      the ordered list of records to emit.
    - A second RESYNC while already resyncing clears the stale buffer and restarts.

    Thread-safety: not thread-safe; designed for single-coroutine asyncio use.
    """

    def __init__(
        self,
        sync: OrderBookSync,
        fetch_snapshot: FetchSnapshotFn,
        symbol: str,
    ) -> None:
        self._sync = sync
        self._fetch_snapshot = fetch_snapshot
        self._symbol = symbol
        self._resyncing: bool = False
        # Buffer of deltas accumulated during a resync window.
        self._buffer: list[BookDelta] = []

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_resyncing(self) -> bool:
        """True while waiting for a REST snapshot to complete a resync."""
        return self._resyncing

    # ------------------------------------------------------------------
    # Public methods — hot path
    # ------------------------------------------------------------------

    def feed_sync_result(
        self,
        result: SyncResult,
        delta: BookDelta,
    ) -> BookDelta | None:
        """Process one sync result from OrderBookSync.feed().

        Parameters
        ----------
        result:
            The ``SyncResult`` returned by ``OrderBookSync.feed()`` for *delta*.
        delta:
            The ``BookDelta`` that was fed into the sync state machine.

        Returns
        -------
        ``delta`` if it should be emitted to the sink immediately, or
        ``None`` if it was dropped or buffered.

        Side-effects:
        - RESYNC: enters resyncing mode; *delta* is buffered (not emitted).
        - DROP / RESYNC while resyncing: delta is buffered; returns ``None``.
        - APPLY while not resyncing: returns *delta* for the caller to emit.
        """
        if result == SyncResult.RESYNC:
            if self._resyncing:
                # Second RESYNC while already resyncing: clear stale buffer,
                # buffer the new RESYNC-triggering delta.
                log.warning(
                    "BookResyncBridge [%s]: second RESYNC while already resyncing; "
                    "clearing stale buffer (had %d deltas).",
                    self._symbol,
                    len(self._buffer),
                )
                self._buffer = []
            else:
                log.warning(
                    "BookResyncBridge [%s]: RESYNC triggered at seq=%s; "
                    "entering resync mode.",
                    self._symbol,
                    delta.seq_id,
                )
                self._resyncing = True
            # Buffer the triggering delta — it may be valid post-resync.
            self._buffer.append(delta)
            return None

        if self._resyncing:
            # While resyncing, buffer everything (even DROPs).  Note: these
            # deltas are emitted as-is after seq-filtering on complete_resync();
            # the sync machine is re-anchored to snap_seq but the kept deltas
            # bypass it.
            self._buffer.append(delta)
            return None

        if result == SyncResult.DROP:
            return None

        # APPLY and not resyncing → emit directly.
        return delta

    def buffer_delta(self, delta: BookDelta) -> None:
        """Manually buffer a delta (e.g. arrived while resync I/O is in flight).

        Callers that manage the resync as a background coroutine can push
        deltas that arrive *after* RESYNC was signalled but *before*
        ``complete_resync()`` has finished.
        """
        self._buffer.append(delta)

    # ------------------------------------------------------------------
    # Resync completion
    # ------------------------------------------------------------------

    async def complete_resync(self) -> list[BookRecord]:
        """Fetch a REST snapshot and apply buffered deltas.

        Must be called when ``is_resyncing`` is True to complete the resync
        cycle.  Fetches the REST snapshot, updates the internal OrderBookSync
        state machine, applies buffered deltas with ``seq_id >=
        snapshot.sequence_id`` (drops stale ones), then clears the buffer and
        exits resyncing mode.

        Returns
        -------
        An ordered list of records to emit: [BookSnapshot] + kept deltas.
        """
        snapshot = await self._fetch_snapshot(self._symbol)
        snap_seq = snapshot.sequence_id  # int | None

        log.info(
            "BookResyncBridge [%s]: REST snapshot fetched, sequence_id=%s; "
            "filtering %d buffered deltas.",
            self._symbol,
            snap_seq,
            len(self._buffer),
        )

        # Update the sync state machine with the new snapshot anchor.
        if snap_seq is not None:
            self._sync.set_snapshot(last_update_id=snap_seq)
        else:
            # snapshot.sequence_id is None: the REST snapshot carries no usable
            # anchor.  set_snapshot() is skipped, so the sync machine retains
            # its previous (pre-resync) snapshot_id.  All buffered deltas pass
            # the seq filter (snap_seq is None → keep everything), but
            # subsequent WS deltas will be evaluated against the stale anchor.
            # This is a best-effort recovery; callers should treat the output
            # with caution and consider triggering a second resync.
            log.warning(
                "BookResyncBridge [%s]: REST snapshot has sequence_id=None; "
                "sync state machine NOT re-anchored (stale anchor retained). "
                "All buffered deltas will be kept and continuity may be wrong.",
                self._symbol,
            )

        # Filter buffered deltas using the venue-aware threshold:
        #   Binance SPOT:    drop seq_id <= snap_seq (boundary already in snapshot)
        #   Binance FUTURES: drop seq_id <  snap_seq (boundary is the first valid event)
        # Access venue from the injected OrderBookSync._venue attribute.
        venue = getattr(self._sync, "_venue", "spot")
        kept_deltas: list[BookDelta] = []
        for delta in self._buffer:
            if snap_seq is None or delta.seq_id is None:
                kept_deltas.append(delta)
            elif venue == "futures":
                # Keep when seq_id >= snap_seq (boundary inclusive)
                if delta.seq_id >= snap_seq:
                    kept_deltas.append(delta)
                else:
                    log.debug(
                        "BookResyncBridge [%s]: dropping buffered delta seq=%s (< snapshot %s).",
                        self._symbol,
                        delta.seq_id,
                        snap_seq,
                    )
            else:
                # spot (default): keep when seq_id > snap_seq (boundary exclusive)
                if delta.seq_id > snap_seq:
                    kept_deltas.append(delta)
                else:
                    log.debug(
                        "BookResyncBridge [%s]: dropping buffered delta seq=%s (<= snapshot %s).",
                        self._symbol,
                        delta.seq_id,
                        snap_seq,
                    )

        # Clear state
        self._buffer = []
        self._resyncing = False

        # Buffered deltas are emitted without re-running OrderBookSync.feed().
        # Advance continuity so the next live event checks against the last
        # applied u (not first-event rules against the snapshot alone).
        if kept_deltas:
            last_seq = kept_deltas[-1].seq_id
            if last_seq is not None and hasattr(self._sync, "note_applied"):
                self._sync.note_applied(last_seq)

        # Return snapshot first, then kept deltas (in buffered order).
        result: list[BookRecord] = [snapshot]
        result.extend(kept_deltas)
        return result


# ---------------------------------------------------------------------------
# Trade-sequence gap detector
# ---------------------------------------------------------------------------


class TradeSeqGap:
    """Detect gaps in a monotonic trade sequence (e.g. Deribit ``trade_seq``).

    Deribit ``trade_seq`` is monotonically increasing per instrument and may
    skip (appendix §3.1: "may skip").  A skip signals missed trades and
    should trigger a REST backfill for the missing range.

    Usage::

        gap = TradeSeqGap()
        for trade in stream:
            if gap.feed(trade.seq):
                # gap detected — trigger backfill
                await backfill(...)
    """

    def __init__(self) -> None:
        self._last_seq: int | None = None

    def feed(self, trade_seq: int) -> bool:
        """Process one trade sequence number.

        Parameters
        ----------
        trade_seq:
            The ``trade_seq`` field from the incoming trade message.

        Returns
        -------
        ``True`` if a gap was detected (previous seq is not None and
        ``trade_seq != last_seq + 1``), ``False`` otherwise.
        """
        if self._last_seq is None:
            # First trade: establish baseline, no gap.
            self._last_seq = trade_seq
            return False

        is_gap = trade_seq != self._last_seq + 1
        if is_gap:
            skipped = trade_seq - self._last_seq - 1
            if skipped < 0:
                # Backward / reset: sequence went backwards (e.g. exchange
                # restarted seq numbering after a reconnect).  Log clearly
                # instead of reporting a negative "skipped" count.
                log.warning(
                    "TradeSeqGap: backward seq — expected seq=%d, got seq=%d "
                    "(reset or reconnect without TradeSeqGap.reset() call?).",
                    self._last_seq + 1,
                    trade_seq,
                )
            else:
                log.warning(
                    "TradeSeqGap: gap detected — expected seq=%d, got seq=%d (skipped %d).",
                    self._last_seq + 1,
                    trade_seq,
                    skipped,
                )
        self._last_seq = trade_seq
        return is_gap

    def reset(self) -> None:
        """Reset the gap detector (e.g. after a reconnect)."""
        self._last_seq = None

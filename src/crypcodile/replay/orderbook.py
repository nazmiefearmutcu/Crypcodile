"""Order-book reconstruction state machine (Task 2.5).

Applies a stream of BookSnapshot and BookDelta records to maintain a live
best-bid/ask L2 book.

Rules (from appendix §5):
  1. Skip all rows before the first snapshot (is_snapshot=True / BookSnapshot).
  2. On snapshot: reset book state entirely.
  3. Delta level: amount>0 → set absolute size; amount==0 → remove level.
  4. Batch deltas sharing one local_ts atomically via apply_batch().
  5. Gap detection — two shapes:
     a. Futures/Deribit shape (prev_seq_id is not None):
        prev_seq_id must equal the last applied seq_id.
     b. Spot shape (prev_seq_id is None):
        seq_id must equal last_seq_id + 1 (U == prev_u + 1).
     Gap ⇒ raise BookGap (caller must resync from REST snapshot).

Removal semantics: amount==0 is the canonical removal signal.  Venue-specific
wire signals (Deribit action=delete) must have been normalised to amount=0
before reaching this class (done in the connector normalizers).
"""

from __future__ import annotations

from collections.abc import Sequence

from crypcodile.schema.records import BookDelta, BookSnapshot


class BookGap(Exception):
    """Raised when a sequence continuity check fails.

    Callers should catch this and trigger a REST resync to rebuild the book
    from a fresh snapshot.
    """


class OrderBook:
    """Snapshot-anchored order-book reconstruction state machine.

    Maintains bids and asks as plain dicts mapping price → size.
    ``best_bid()`` and ``best_ask()`` return the top-of-book prices.

    Usage::

        book = OrderBook()
        for record in replay_stream:
            if isinstance(record, (BookSnapshot, BookDelta)):
                try:
                    book.apply(record)
                except BookGap:
                    # trigger REST resync
                    ...
    """

    def __init__(self) -> None:
        # price → size (positive float)
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

        # True once the first snapshot has been processed
        self._initialized: bool = False

        # Last seq_id applied (from a snapshot or delta); None if not set
        self._last_seq_id: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, record: BookSnapshot | BookDelta) -> None:
        """Apply a single record to the book.

        Args:
            record: A ``BookSnapshot`` (resets the book) or a ``BookDelta``
                    (incremental update, subject to gap detection).

        Raises:
            BookGap: If sequence continuity is violated on a delta.
        """
        if isinstance(record, BookSnapshot):
            self._apply_snapshot(record)
        else:
            self._apply_delta(record)

    def apply_batch(self, deltas: Sequence[BookDelta]) -> None:
        """Apply a batch of deltas that share one ``local_ts`` atomically.

        Gap detection is performed on the *first* delta in the batch (the one
        that carries the continuity information).  Subsequent deltas in the
        same batch are applied without re-checking seq continuity (they are
        part of the same logical transaction).

        Args:
            deltas: One or more BookDelta records from the same timestamp.

        Raises:
            BookGap: If the first delta's seq_id violates continuity.
        """
        if not deltas:
            return
        for i, delta in enumerate(deltas):
            if i == 0:
                # Full gap-check on the first delta
                self._apply_delta(delta)
            else:
                # Skip gap check for subsequent deltas in the same batch;
                # they don't carry prev_seq_id continuity across each other.
                if self._initialized:
                    self._apply_levels(delta.bids, self.bids)
                    self._apply_levels(delta.asks, self.asks)
                    # Update last_seq_id if this delta carries one
                    if delta.seq_id is not None:
                        self._last_seq_id = delta.seq_id

    def best_bid(self) -> float | None:
        """Return the highest bid price, or None if the bids side is empty."""
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        """Return the lowest ask price, or None if the asks side is empty."""
        return min(self.asks) if self.asks else None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_snapshot(self, snap: BookSnapshot) -> None:
        """Reset book state from a snapshot record."""
        self.bids = {}
        self.asks = {}
        self._apply_levels(snap.bids, self.bids)
        self._apply_levels(snap.asks, self.asks)
        self._initialized = True
        self._last_seq_id = snap.sequence_id

    def _apply_delta(self, delta: BookDelta) -> None:
        """Apply one delta, performing gap detection first."""
        if not self._initialized:
            # Skip rows before the first snapshot (appendix §5 rule 1)
            return

        self._check_gap(delta)
        self._apply_levels(delta.bids, self.bids)
        self._apply_levels(delta.asks, self.asks)

        # Advance the tracked sequence pointer
        if delta.seq_id is not None:
            self._last_seq_id = delta.seq_id

    def _check_gap(self, delta: BookDelta) -> None:
        """Validate sequence continuity; raise BookGap if broken.

        Two gap-detection shapes per appendix §5 rule 6:
        - **Futures/Deribit shape** (``prev_seq_id is not None``):
          ``delta.prev_seq_id`` must equal ``self._last_seq_id``.
        - **Spot shape** (``prev_seq_id is None``):
          ``delta.seq_id`` must equal ``self._last_seq_id + 1``
          (U == prev_u + 1 rule).
        """
        if delta.prev_seq_id is not None:
            # Futures/Deribit shape: explicit prev pointer
            if self._last_seq_id is None:
                # Snapshot had sequence_id=None — no anchor to validate against.
                # Continuity cannot be established; treat as a gap so the caller
                # can trigger a resync rather than silently accepting the delta.
                raise BookGap(
                    f"Sequence gap: delta.prev_seq_id={delta.prev_seq_id!r} "
                    f"but last_seq_id is None (snapshot had no sequence_id)"
                )
            if delta.prev_seq_id != self._last_seq_id:
                raise BookGap(
                    f"Sequence gap: delta.prev_seq_id={delta.prev_seq_id!r} "
                    f"!= last_seq_id={self._last_seq_id!r}"
                )
        else:
            # Spot shape: no prev_seq_id; validate seq_id == last + 1
            if (
                delta.seq_id is not None
                and self._last_seq_id is not None
                and delta.seq_id != self._last_seq_id + 1
            ):
                raise BookGap(
                    f"Spot-shape sequence gap: delta.seq_id={delta.seq_id!r} "
                    f"!= last_seq_id+1={self._last_seq_id + 1!r}"
                )

    @staticmethod
    def _apply_levels(
        levels: list[tuple[float, float]],
        side: dict[float, float],
    ) -> None:
        """Apply a list of (price, amount) levels to a book side.

        amount == 0.0 → remove the price level (canonical removal signal).
        amount >  0.0 → set/overwrite the absolute size at that price.
        """
        for price, amount in levels:
            if price <= 0:
                raise ValueError(f"price must be positive, got {price}")
            if amount < 0:
                raise ValueError(f"amount must be non-negative, got {amount}")
            if amount == 0.0:
                side.pop(price, None)  # silent no-op if level is already absent
            else:
                side[price] = amount

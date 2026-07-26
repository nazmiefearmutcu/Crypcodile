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
from typing import cast

from crocodile.core.errors import BookGap
from crocodile.core.schema.legacy.records import BookDelta, BookSnapshot

# ``BookGap`` used to be declared here as a bare ``Exception``, so an
# ``except BookGap`` written against ``crocodile.core.errors`` caught nothing
# raised by this module and vice versa. The canonical class lives in
# ``errors``; this module only re-exports it.
__all__ = ["BookGap", "OrderBook"]

# The two forks each have their own BookSnapshot/BookDelta structs, and this
# engine was written against the crypto pair. Dispatching on ``isinstance``
# meant an equity snapshot fell through to the delta branch, which returns
# early on an uninitialised book: the book stayed empty and nothing raised.
# Silence is the worst failure mode a reconstruction engine has, so dispatch
# is on the msgspec tag instead — the same channel discriminator the store
# partitions and replays by, and the one thing both families already agree on.
_SNAPSHOT_TAG = "book_snapshot"


def _channel_tag(record: object) -> str | None:
    """Return the record's msgspec tag, or None if it is not a tagged struct."""
    config = getattr(type(record), "__struct_config__", None)
    tag = getattr(config, "tag", None)
    return tag if isinstance(tag, str) else None


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

        # True until the first delta after a snapshot is applied — that one
        # delta straddles the snapshot point and is validated as a range.
        self._first_delta_after_snapshot: bool = False

        # The delta last applied, kept only to tell a harmless redelivery of a
        # sequence from a genuine content conflict under the same number.
        self._last_delta: BookDelta | None = None

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
            TypeError: If the record is not a tagged book record at all. It used
                to fall through to the delta branch and be dropped in silence.
        """
        tag = _channel_tag(record)
        if tag is None:
            raise TypeError(
                f"{type(record).__name__} is not a tagged book record; "
                "OrderBook.apply accepts a book_snapshot or a book_delta"
            )
        if tag == _SNAPSHOT_TAG:
            self._apply_snapshot(cast("BookSnapshot", record))
        else:
            self._apply_delta(cast("BookDelta", record))

    def apply_batch(self, deltas: Sequence[BookDelta]) -> None:
        """Apply a batch of deltas that share one ``local_ts`` atomically.

        Every delta is gap-checked, not just the first. Crypto checked only the
        first and applied the rest unvalidated, on the reasoning that deltas
        sharing a timestamp are one logical transaction; equity checked all of
        them. Sharing a capture timestamp does not make two updates contiguous,
        so the crypto form accepted a jump from 103 to 105 inside a batch and
        rebuilt a book that had silently lost 104. A reconstruction engine that
        stays quiet about loss is the one failure this merge keeps finding, so
        the checking form wins. Deltas without a ``seq_id`` — the common case
        for the trailing entries in a batch — carry no continuity claim and
        still pass, which is why the crypto expectations are unaffected.

        Args:
            deltas: One or more BookDelta records from the same timestamp.

        Raises:
            BookGap: If any delta's seq_id violates continuity.
        """
        for delta in deltas:
            self._apply_delta(delta)

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
        self._first_delta_after_snapshot = True
        self._last_delta = None

    def _apply_delta(self, delta: BookDelta) -> None:
        """Apply one delta, performing gap detection first."""
        if not self._initialized:
            # Skip rows before the first snapshot (appendix §5 rule 1)
            return

        self._check_gap(delta)

        # A byte-identical redelivery of the sequence already applied is not new
        # information; _check_gap let it through, and applying it twice would be
        # a no-op on absolute levels but is skipped for clarity.
        if delta.seq_id is not None and delta.seq_id == self._last_seq_id:
            return

        self._apply_levels(delta.bids, self.bids)
        self._apply_levels(delta.asks, self.asks)

        # Advance the tracked sequence pointer
        if delta.seq_id is not None:
            self._last_seq_id = delta.seq_id
        self._first_delta_after_snapshot = False
        self._last_delta = delta

    def _check_gap(self, delta: BookDelta) -> None:
        """Validate sequence continuity; raise BookGap if broken.

        Two gap-detection shapes per appendix §5 rule 6:
        - **Futures/Deribit shape** (``prev_seq_id is not None``):
          ``delta.prev_seq_id`` must equal ``self._last_seq_id``.
        - **Spot shape** (``prev_seq_id is None``):
          ``delta.seq_id`` must equal ``self._last_seq_id + 1``
          (U == prev_u + 1 rule).

        Two tolerances come from the equity fork, whose engine was stricter about
        what counts as evidence of loss and looser about what counts as a gap:

        - **A redelivered sequence is not a gap** if the delta is byte-identical
          to the one already applied. Venues and reconnects replay the last
          message; treating that as loss triggers a REST resync that recovers
          nothing. Content differing under the same sequence number *is* loss,
          and still raises.
        - **The first delta after a snapshot** is validated as
          ``prev_seq_id <= last_seq_id <= seq_id`` — the delta's range must
          cover the point the snapshot was taken at. A futures snapshot is taken
          mid-stream, so the first delta legitimately straddles it.

        That second rule is a reconciliation, not a side chosen. Crypto required
        ``prev_seq_id == last_seq_id`` and its tests pin it; equity required
        ``prev_seq_id < last_seq_id`` and rejected crypto's equality outright.
        Both are the same invariant seen from one venue each, and the range form
        admits both while still catching what either called loss: ``prev`` past
        the anchor, or ``seq`` before it.
        """
        if delta.seq_id is not None and delta.seq_id == self._last_seq_id:
            if self._last_delta is not None and (
                delta.bids != self._last_delta.bids or delta.asks != self._last_delta.asks
            ):
                raise BookGap(
                    f"Duplicate sequence number {delta.seq_id} with different delta content"
                )
            return

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
            if self._first_delta_after_snapshot:
                straddles_snapshot = (
                    delta.seq_id is not None
                    and delta.prev_seq_id <= self._last_seq_id <= delta.seq_id
                )
                if not straddles_snapshot:
                    raise BookGap(
                        f"Sequence gap on first delta after snapshot: expected "
                        f"prev_seq_id={delta.prev_seq_id!r} < "
                        f"last_seq_id={self._last_seq_id!r} <= seq_id={delta.seq_id!r}"
                    )
            elif delta.prev_seq_id != self._last_seq_id:
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

        Prices are rounded to 8 decimals before being used as dict keys, which
        is equity's rule. Crypto keyed on the raw float, so ``100.1`` arriving
        as ``100.10000000000001`` opened a second level at a price that is the
        same price, and a later removal at the clean value left the ghost
        behind. Eight decimals is finer than any venue quotes and coarser than
        float noise.
        """
        for price, amount in levels:
            if price <= 0:
                raise ValueError(f"price must be positive, got {price}")
            if amount < 0:
                raise ValueError(f"amount must be non-negative, got {amount}")
            key = round(price, 8)
            if amount == 0.0:
                side.pop(key, None)  # silent no-op if level is already absent
            else:
                side[key] = amount

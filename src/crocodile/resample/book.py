"""Book-snapshot resampling at fixed wall-clock intervals (Task 5.2).

``resample_book_snapshots(records, interval_ns, top_n)`` consumes a stream of
``BookSnapshot`` and ``BookDelta`` canonical records, reconstructs the running
order book via the M2 ``OrderBook`` engine, and emits a ``BookSnapshot`` at
fixed wall-clock intervals keyed on ``local_ts``.

Design (Appendix §5):

    Resampling builds on the M2 ``OrderBook`` reconstruction engine
    (``crocodile.replay.orderbook``).  It never queries exchanges directly.

Emission semantics
------------------
The interval is defined in nanoseconds (``interval_ns``).  Records are
processed in order of their ``local_ts`` field.  After *applying* each record,
we check whether its ``local_ts`` has crossed (or landed on) a boundary::

    record.local_ts >= next_boundary_ns

where ``next_boundary_ns`` is initialised on the *first snapshot seen* as::

    next_boundary_ns = floor(snap.local_ts / interval_ns) * interval_ns + interval_ns

That is, the first bucket boundary is the end of the interval that *contains*
the first snapshot.  The snapshot is emitted *after* the triggering record is
applied, so it captures the book state that *includes* all records with
``local_ts <= boundary``.  Each crossing advances the boundary by one
``interval_ns``.

This mirrors how time-bar generators work in production systems: the bar
reflects the state after all updates up to and including the boundary record
have been applied.

Skipping pre-snapshot records
------------------------------
Records arriving before the first ``BookSnapshot`` are forwarded to
``OrderBook.apply()`` — the engine already handles this correctly by silently
skipping deltas before the first snapshot (Appendix §5 rule 1).  Emission is
suppressed until the first snapshot has been processed by the engine.

Top-N depth
-----------
``top_n`` (default ``None`` = no limit) truncates the emitted bids/asks to the
``top_n`` best levels on each side:

    bids sorted descending by price → top_n entries
    asks sorted ascending by price  → top_n entries

The ``depth`` field in the emitted snapshot is set to
``len(bids) + len(asks)`` *after* truncation.

``exchange_ts`` in emitted snapshots
-------------------------------------
We cannot know the exchange timestamp for a synthesised snapshot; we set
``exchange_ts = None`` consistently with the ``local_ts``-only contract used
elsewhere in the pipeline for derived records.

Gap handling
-------------
``BookGap`` exceptions from the reconstruction engine are **re-raised** to the
caller.  The caller is responsible for resync (e.g. providing a fresh REST
snapshot).  This keeps the resampler stateless with respect to gap recovery.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from crocodile.replay.orderbook import OrderBook
from crocodile.schema.records import BookDelta, BookSnapshot

__all__ = ["resample_book_snapshots"]


def resample_book_snapshots(
    records: Iterable[BookSnapshot | BookDelta],
    interval_ns: int,
    top_n: int | None = None,
) -> Iterator[BookSnapshot]:
    """Reconstruct book from a stream of records and emit periodic snapshots.

    Args:
        records:     An iterable of ``BookSnapshot`` and/or ``BookDelta``
                     canonical records, ordered by ``local_ts``.
        interval_ns: Emit interval width in nanoseconds.
                     E.g. ``1_000_000_000`` for 1-second snapshots.
        top_n:       Maximum number of bid and ask levels to include in each
                     emitted snapshot.  ``None`` means include all levels.

    Yields:
        ``BookSnapshot`` records at every interval boundary, capturing the
        reconstructed book state *after* the boundary-crossing record has been
        applied.  Each emitted snapshot's ``local_ts`` is set to the bucket
        boundary timestamp, not the triggering record's ``local_ts``.

    Raises:
        crocodile.replay.orderbook.BookGap: Propagated from the underlying
                 ``OrderBook`` if a sequence continuity break is detected.
        ValueError: If ``interval_ns`` is not a positive integer.
    """
    if interval_ns <= 0:
        raise ValueError(f"interval_ns must be positive; got {interval_ns!r}")

    book = OrderBook()
    next_boundary_ns: int | None = None  # set when first snapshot is applied
    initialized = False  # True once the engine has seen its first BookSnapshot

    for record in records:
        ts = record.local_ts

        # Before the engine is initialised, we cannot emit anything useful.
        # We still forward the record to the engine so it can wait for its
        # first BookSnapshot (the engine silently drops pre-snapshot deltas).
        if not initialized:
            if isinstance(record, BookSnapshot):
                book.apply(record)
                initialized = True
                # Set the first boundary to the end of the interval that
                # contains this snapshot.
                next_boundary_ns = (
                    (ts // interval_ns) * interval_ns + interval_ns
                )
            # For deltas before the first snapshot, skip (engine would drop them)
            continue

        # Apply the record to the book (may raise BookGap — propagates to caller).
        book.apply(record)

        # After applying, check whether this record's local_ts has reached or
        # crossed one or more interval boundaries.  We emit *after* applying so
        # the snapshot includes the state contributed by this record.
        assert next_boundary_ns is not None  # guaranteed once initialized is True
        while ts >= next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, top_n)
            next_boundary_ns += interval_ns


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _capture_snapshot(
    book: OrderBook,
    trigger_record: BookSnapshot | BookDelta,
    boundary_ns: int,
    top_n: int | None,
) -> BookSnapshot:
    """Build a BookSnapshot from the current ``OrderBook`` state.

    Args:
        book:           The live ``OrderBook`` instance.
        trigger_record: The record whose ``local_ts`` crossed the boundary.
                        Used to copy ``exchange``, ``symbol``, and
                        ``symbol_raw``.
        boundary_ns:    The nanosecond timestamp of the bucket boundary.
                        Used as ``local_ts`` for the emitted snapshot.
        top_n:          Maximum bid/ask levels on each side; ``None`` = all.

    Returns:
        A ``BookSnapshot`` representing the book at ``boundary_ns``.
    """
    # Bids: sorted descending by price; top_n entries.
    bids_sorted = sorted(book.bids.items(), reverse=True)
    asks_sorted = sorted(book.asks.items())

    if top_n is not None:
        bids_sorted = bids_sorted[:top_n]
        asks_sorted = asks_sorted[:top_n]

    bids: list[tuple[float, float]] = [(p, s) for p, s in bids_sorted]
    asks: list[tuple[float, float]] = [(p, s) for p, s in asks_sorted]

    depth = len(bids) + len(asks)

    return BookSnapshot(
        exchange=trigger_record.exchange,
        symbol=trigger_record.symbol,
        symbol_raw=trigger_record.symbol_raw,
        exchange_ts=None,
        local_ts=boundary_ns,
        bids=bids,
        asks=asks,
        depth=depth,
        sequence_id=None,
        is_snapshot=True,
    )

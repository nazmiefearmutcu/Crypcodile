"""Book-snapshot resampling at fixed wall-clock intervals, for *equity* records.

Why this is not just ``crocodile.core.resample.book``
=====================================================

``crocodile.core.resample.book.resample_book_snapshots`` survived the merge,
but it is the CRYPTO copy and it does not do the same thing to a stream. Two of
the three reasons this module used to give have expired, and saying which is
the point of keeping them written down:

1. ``core``'s ``_capture_snapshot`` read ``trigger_record.source`` and
   ``trigger_record.asset_class``, which the equity records did not have.
   **Expired:** equity emits canonical records now, so both are there.

2. ``core``'s loop tested ``isinstance(record, BookSnapshot)`` against a
   *different* ``msgspec.Struct``, so an equity snapshot never matched, the
   resampler never initialised, and it yielded nothing while raising nothing.
   **Expired for the same reason**, and it is worth recording that it was the
   worst of the three while it lasted: silent empty output.

3. **Different boundary arithmetic.** This one is live, and it was always the
   real reason — a genuine behavioural fork, not an accident of typing:

   * ``core`` (crypto) applies the incoming record to the book **first**, then
     emits every boundary at or below that record's ``local_ts``. The snapshot
     stamped at boundary *B* therefore includes an update that happened
     **after** *B* — lookahead.
   * equity emits every boundary **strictly before** the record's ``local_ts``
     *before* applying it, then applies, then emits a boundary that lands
     exactly on ``local_ts``. The snapshot stamped at *B* contains only what
     was known at *B*.

   For the fixture in ``tests/equity/test_resample.py`` the two differ
   observably: at boundary ``1e9`` the crypto rule reports bids
   ``[(150, 12)]`` / asks ``[(152, 25), (153, 30)]`` because it has already
   folded in the ``local_ts=1.2e9`` delta, while the equity rule reports bids
   ``[(150, 12), (149, 20)]`` / asks ``[(152, 25)]``.

   This is the same category of divergence the merge already hit with the RSI
   warm-up window, the Bollinger ``ddof``, and the ``ms_to_ns`` truncation
   order, and — see ``crocodile.equity.resample._interval`` — with
   ``parse_interval``'s arity. Same name, different arithmetic. Which rule is
   *right* for a merged product is a later decision; for a lookahead-sensitive
   backtest the equity rule is the defensible one, but changing crypto's
   behaviour is out of scope for a merge that has to preserve both.

So the *resampling* logic is deliberately duplicated here — one boundary rule
each — and nothing else about this module is a duplicate any more.

What is NOT duplicated: the order-book engine
----------------------------------------------

The reconstruction state machine is **not** forked. This module drives the
shared ``crocodile.core.replay.orderbook.OrderBook`` and hands it the records it
was given, unchanged. It used to re-type them first, through a ``_to_core_record``
that existed only to satisfy the ``isinstance`` dispatch in point 2; with one
record union that translation had become a copy of a struct into itself, and a
copy is a place for a field to be dropped on the way past.

One consequence worth recording: the equity fork had its **own** ``OrderBook``
with different gap rules (it tolerated a repeated ``seq_id`` when the delta
content was byte-identical, and on the first delta after a snapshot it accepted
``prev_seq_id < last_seq_id <= seq_id`` rather than exact equality). That
engine was dropped when the crypto engine was promoted into ``core``, so equity
streams now get crypto gap arithmetic. That loss belongs to the replay layer,
not here; it is flagged rather than worked around.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from crocodile.core.replay.orderbook import OrderBook
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import BookDelta, BookSnapshot

__all__ = ["resample_book_snapshots"]


def resample_book_snapshots(
    records: Iterable[BookSnapshot | BookDelta],
    interval_ns: int,
    top_n: int | None = None,
) -> Iterator[BookSnapshot]:
    """Reconstruct book from a stream of equity records and emit periodic snapshots.

    Args:
        records:     An iterable of equity ``BookSnapshot`` and/or ``BookDelta``
                     canonical records, ordered by ``local_ts``.
        interval_ns: Emit interval width in nanoseconds.
                     E.g. ``1_000_000_000`` for 1-second snapshots.
        top_n:       Maximum number of bid and ask levels to include in each
                     emitted snapshot.  ``None`` means include all levels.

    Yields:
        ``BookSnapshot`` records at every interval boundary, stamped with the
        bucket boundary timestamp rather than the triggering record's
        ``local_ts``.  A snapshot at boundary *B* reflects only records with
        ``local_ts <= B``: boundaries strictly below the incoming record are
        flushed *before* that record is applied, so no future information leaks
        into a past bar.  (``crocodile.core.resample.book`` applies first and so
        does leak; see this module's docstring.)

    Raises:
        crocodile.core.errors.BookGap: Propagated from the underlying
                 ``OrderBook`` if a sequence continuity break is detected.
                 Also importable as ``crocodile.core.replay.orderbook.BookGap``.
        ValueError: If ``interval_ns`` is not a positive integer.
    """
    if interval_ns <= 0:
        raise ValueError(f"interval_ns must be positive; got {interval_ns!r}")

    book = OrderBook()
    next_boundary_ns: int | None = None  # set when first snapshot is applied
    initialized = False  # True once the engine has seen its first BookSnapshot
    # The newest record folded into the book, which is what the `book_resample`
    # confidence formula is a function of. Tracked rather than assumed: this rule emits
    # before it applies, so the answer is structurally zero lookahead — and the day
    # somebody reorders those two statements into the crypto rule, the number moves.
    applied_ts: int | None = None

    for record in records:
        ts = record.local_ts

        # Before the engine is initialised, we cannot emit anything useful.
        # Deltas arriving before the first snapshot are dropped, matching the
        # engine's own rule.
        if not initialized:
            if isinstance(record, BookSnapshot):
                book.apply(record)
                applied_ts = ts
                initialized = True
                # First boundary is the end of the interval containing this snapshot.
                next_boundary_ns = (ts // interval_ns) * interval_ns + interval_ns
            continue

        assert next_boundary_ns is not None  # guaranteed once initialized is True
        assert applied_ts is not None  # set with `initialized`

        # Flush every boundary strictly before this record, using the book state
        # as it stood before the record — this is what prevents lookahead bias.
        while ts > next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, applied_ts, interval_ns, top_n)
            next_boundary_ns += interval_ns

        # Apply the record to the book (may raise BookGap — propagates to caller).
        book.apply(record)
        applied_ts = ts

        # A record landing exactly on a boundary belongs to that boundary's bar.
        while ts >= next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, applied_ts, interval_ns, top_n)
            next_boundary_ns += interval_ns


def _capture_snapshot(
    book: OrderBook,
    trigger_record: BookSnapshot | BookDelta,
    boundary_ns: int,
    applied_ts: int,
    interval_ns: int,
    top_n: int | None,
) -> BookSnapshot:
    """Build an equity ``BookSnapshot`` from the current ``OrderBook`` state.

    Args:
        book:           The live ``OrderBook`` instance.
        trigger_record: The record whose ``local_ts`` crossed the boundary.
                        Used to copy ``source``, ``symbol`` and ``symbol_raw``.
        boundary_ns:    The nanosecond timestamp of the bucket boundary; used as
                        ``local_ts`` for the emitted snapshot.
        applied_ts:     ``local_ts`` of the newest record already folded into the book.
        interval_ns:    The emit interval, the denominator of the confidence formula.
        top_n:          Maximum bid/ask levels on each side; ``None`` = all.

    Returns:
        A ``BookSnapshot`` representing the book at ``boundary_ns``.  We cannot
        know a source timestamp for a synthesised snapshot, so ``source_ts`` is
        ``None``.

    This snapshot is a reconstruction: no venue ever published it, and a record built in
    a resampler with no ``prov=`` argument says the opposite. ``lookahead_ns`` is how far
    past the emitted boundary the newest applied update lies, so a book that is *behind*
    the boundary lies zero past it — this rule always takes that branch, because it
    flushes boundaries before applying the record that crossed them. Clamping is the
    correct evaluation of "how far past", not a floor hiding a negative.
    """
    bids_sorted = sorted(book.bids.items(), reverse=True)
    asks_sorted = sorted(book.asks.items())

    if top_n is not None:
        bids_sorted = bids_sorted[:top_n]
        asks_sorted = asks_sorted[:top_n]

    bids: list[tuple[float, float]] = [(p, s) for p, s in bids_sorted]
    asks: list[tuple[float, float]] = [(p, s) for p, s in asks_sorted]

    depth = len(bids) + len(asks)

    tail = provenance_fields(
        "book_resample",
        {"lookahead_ns": max(applied_ts - boundary_ns, 0), "interval_ns": interval_ns},
    )

    return BookSnapshot(
        source=trigger_record.source,
        symbol=trigger_record.symbol,
        symbol_raw=trigger_record.symbol_raw,
        source_ts=None,
        local_ts=boundary_ns,
        # Carried from the trigger rather than pinned: this resampler is reached with
        # equity input today, and the record it is handed knows its own market.
        asset_class=trigger_record.asset_class,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
        bids=bids,
        asks=asks,
        depth=depth,
        sequence_id=None,
        is_snapshot=True,
    )

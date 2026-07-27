"""Book-snapshot resampling at fixed wall-clock intervals — the only implementation.

``resample_book_snapshots(records, interval_ns, top_n)`` consumes a stream of
``BookSnapshot`` and ``BookDelta`` canonical records, reconstructs the running order book
via the M2 ``OrderBook`` engine (``crocodile.core.replay.orderbook``), and emits a
``BookSnapshot`` at fixed wall-clock intervals keyed on ``local_ts``. It never queries
exchanges directly.

There used to be two of these
-----------------------------
``crocodile.equity.resample.book`` held a second function of this name and this signature,
and the two disagreed about one thing: whether a bar may contain information from after its
own timestamp. This module's rule applied the boundary-crossing record **first** and then
emitted every boundary at or below that record's ``local_ts``; the equity rule emitted every
boundary strictly below the record **before** applying it.

Measured on a 1-second book carrying one delta stamped 200 ms past the 10:00:00 boundary —
a size cut from 120 to 5 at the touch — the boundary stamped 10:00:00.000 reported::

    this module, before  bids=[(150.00, 5.0)]      prov_confidence=0.8
    equity               bids=[(150.00, 120.0)]    prov_confidence=1.0

24x the visible top-of-book depth, in the direction that cannot be traded: a strategy
reading the 10:00:00 snapshot already knew the bid was about to be pulled. Lookahead bias
is the classic way a backtest reports a return nobody could have earned, so this is not a
stylistic fork between two copies — one of them was wrong, and it was this one. The equity
ordering was adopted here and the equity module deleted; ``crocodile.equity.resample`` now
re-exports this function, so the equity import path is unchanged.

Emission semantics
------------------
The interval is defined in nanoseconds (``interval_ns``). Records are processed in order of
their ``local_ts``. The first boundary is set on the *first snapshot seen*::

    next_boundary_ns = floor(snap.local_ts / interval_ns) * interval_ns + interval_ns

i.e. the end of the interval that contains the first snapshot. Thereafter, for each record:

1. every boundary **strictly below** the record's ``local_ts`` is emitted from the book as
   it stands *before* the record is applied;
2. the record is applied;
3. a boundary landing **exactly on** the record's ``local_ts`` is emitted after it, because
   an update stamped *B* is information that existed at *B*.

A snapshot stamped *B* therefore reflects exactly the records with ``local_ts <= B``. A
quiet stretch emits the state that held during it rather than the state that ended it: the
same book, five seconds unchanged, then a delta at 5s cutting the bid from 100 to 1, used to
emit four consecutive bars all reporting ``[(150.00, 1.0)]`` — a book that would not exist
for up to five seconds — and now reports ``[(150.00, 100.0)]`` for each of them.

Skipping pre-snapshot records
------------------------------
Deltas arriving before the first ``BookSnapshot`` are dropped, matching the engine's own
rule (Appendix §5 rule 1). Emission is suppressed until a snapshot has been applied.

Top-N depth
-----------
``top_n`` (default ``None`` = no limit) truncates the emitted bids/asks to the ``top_n`` best
levels on each side — bids descending by price, asks ascending. ``depth`` is set to
``len(bids) + len(asks)`` *after* truncation.

``source_ts`` in emitted snapshots
-----------------------------------
We cannot know the venue timestamp for a synthesised snapshot, so ``source_ts`` is ``None``,
consistent with the ``local_ts``-only contract used elsewhere for derived records.

Provenance
----------
An emitted snapshot is reconstructed from other records, so it says
:attr:`Provenance.DERIVED` on the ``book_resample`` basis. It used to say ``NATIVE`` — not by
choice but by omission, since a record built with no ``prov*`` argument inherits the defaults,
and the defaults mean *the venue reported this value directly*. That is a false statement
written into the lake, and a silent one: ``provenance.describe()`` is what the REST and MCP
surfaces are required to emit as a warning whenever they serve a record whose ``prov`` is not
``NATIVE``, so a reconstructed book was served indistinguishably from a venue snapshot.

The confidence used to be ``max(1 - lookahead_ns / interval_ns, 0.0)``, and its whole
observable was the lookahead the ordering above removes. With the flush before the apply,
``applied_ts <= boundary_ns`` holds at every capture by construction, so that formula could
only ever return 1.0 — a constant one indirection inside the registry, which is the one place
no call-site gate looks. ``book_resample`` is therefore re-registered as a declared constant
with the argument written out; see its entry in ``crocodile.core.schema.provenance``.

What replaces the measurement is a tripwire rather than a score: ``_capture_snapshot``
raises ``ProvenanceError`` if it is ever asked to stamp a boundary with a book that has
already absorbed a later update. Scoring lookahead 0.0 and shipping it is how the biased
bars got into the lake in the first place; refusing to build the record is the loud form of
the same observation.

Gap handling
-------------
``BookGap`` from the reconstruction engine is **re-raised** to the caller, which is
responsible for resync (e.g. a fresh REST snapshot). This keeps the resampler stateless with
respect to gap recovery.

One consequence of the engine merge worth recording: the equity fork had its **own**
``OrderBook`` with looser gap rules (it tolerated a repeated ``seq_id`` when the delta content
was byte-identical, and on the first delta after a snapshot accepted
``prev_seq_id < last_seq_id <= seq_id`` rather than exact equality). That engine was dropped
when the crypto engine was promoted into ``core``, so equity streams get crypto gap
arithmetic. That loss belongs to the replay layer, not here; it is flagged rather than worked
around.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from crocodile.core.errors import ProvenanceError
from crocodile.core.replay.orderbook import OrderBook
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import BookDelta, BookSnapshot

__all__ = ["resample_book_snapshots"]


def resample_book_snapshots(
    records: Iterable[BookSnapshot | BookDelta],
    interval_ns: int,
    top_n: int | None = None,
) -> Iterator[BookSnapshot]:
    """Reconstruct book from a stream of records and emit periodic snapshots.

    Args:
        records:     An iterable of ``BookSnapshot`` and/or ``BookDelta`` canonical
                     records, ordered by ``local_ts``. Either asset class: the emitted
                     snapshot carries the trigger record's own ``asset_class``.
        interval_ns: Emit interval width in nanoseconds.
                     E.g. ``1_000_000_000`` for 1-second snapshots.
        top_n:       Maximum number of bid and ask levels to include in each emitted
                     snapshot.  ``None`` means include all levels.

    Yields:
        ``BookSnapshot`` records at every interval boundary, stamped with the bucket
        boundary timestamp rather than the triggering record's ``local_ts``. A snapshot at
        boundary *B* reflects only records with ``local_ts <= B``: boundaries strictly below
        the incoming record are flushed *before* that record is applied, so no future
        information leaks into a past bar.

    Raises:
        crocodile.core.errors.BookGap: Propagated from the underlying ``OrderBook`` if a
                 sequence continuity break is detected. Also importable as
                 ``crocodile.core.replay.orderbook.BookGap``.
        crocodile.core.errors.ProvenanceError: If a capture would carry an update stamped
                 after the boundary it is labelled with. Structurally unreachable under the
                 ordering below; it is the tripwire on that ordering.
        ValueError: If ``interval_ns`` is not a positive integer.
    """
    if interval_ns <= 0:
        raise ValueError(f"interval_ns must be positive; got {interval_ns!r}")

    book = OrderBook()
    next_boundary_ns: int | None = None  # set when first snapshot is applied
    initialized = False  # True once the engine has seen its first BookSnapshot
    # `local_ts` of the newest record folded into the book. Tracked rather than inferred
    # from the trigger record, because the trigger is what crossed the boundary and the book
    # at that boundary is what came *before* it — conflating the two is the bias itself.
    applied_ts: int | None = None

    for record in records:
        ts = record.local_ts

        # Before the engine is initialised, we cannot emit anything useful. Deltas arriving
        # before the first snapshot are dropped, matching the engine's own rule.
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

        # Flush every boundary strictly before this record, using the book state as it stood
        # before the record. This is the statement order that prevents lookahead bias; the
        # apply below must stay below it.
        while ts > next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, applied_ts, top_n)
            next_boundary_ns += interval_ns

        # Apply the record to the book (may raise BookGap — propagates to caller).
        book.apply(record)
        applied_ts = ts

        # A record landing exactly on a boundary belongs to that boundary's bar: an update
        # stamped B is information that existed at B.
        while ts >= next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, applied_ts, top_n)
            next_boundary_ns += interval_ns


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _capture_snapshot(
    book: OrderBook,
    trigger_record: BookSnapshot | BookDelta,
    boundary_ns: int,
    applied_ts: int,
    top_n: int | None,
) -> BookSnapshot:
    """Build a ``BookSnapshot`` from the current ``OrderBook`` state.

    Args:
        book:           The live ``OrderBook`` instance.
        trigger_record: The record whose ``local_ts`` reached or crossed the boundary. Used
                        for ``source``, ``symbol``, ``symbol_raw`` and ``asset_class`` only
                        — never for the book contents, which are whatever has been applied.
        boundary_ns:    The nanosecond timestamp of the bucket boundary; used as ``local_ts``
                        for the emitted snapshot.
        applied_ts:     ``local_ts`` of the newest record already folded into the book. Must
                        not exceed ``boundary_ns``; see below.
        top_n:          Maximum bid/ask levels on each side; ``None`` = all.

    Returns:
        A ``BookSnapshot`` representing the book at ``boundary_ns``. We cannot know a source
        timestamp for a synthesised snapshot, so ``source_ts`` is ``None``.

    Raises:
        ProvenanceError: if the book already holds an update stamped after ``boundary_ns``.
            This is the tripwire that replaced the ``lookahead_ns`` confidence term. The
            caller's flush-then-apply ordering makes it unreachable, which is exactly why it
            is worth having: the old ordering scored such a capture 0.0 and emitted it
            anyway, so four consecutive bars could report a book that did not exist yet and
            nothing raised. A record whose provenance tail would be a lie is not built.
    """
    if applied_ts > boundary_ns:
        raise ProvenanceError(
            f"book_resample would stamp a snapshot {boundary_ns} while the book already "
            f"holds an update stamped {applied_ts} — {applied_ts - boundary_ns}ns of "
            f"lookahead. Emit boundaries below a record before applying it."
        )

    # Bids: sorted descending by price; asks ascending. top_n truncates each side.
    bids_sorted = sorted(book.bids.items(), reverse=True)
    asks_sorted = sorted(book.asks.items())

    if top_n is not None:
        bids_sorted = bids_sorted[:top_n]
        asks_sorted = asks_sorted[:top_n]

    bids: list[tuple[float, float]] = [(p, s) for p, s in bids_sorted]
    asks: list[tuple[float, float]] = [(p, s) for p, s in asks_sorted]

    depth = len(bids) + len(asks)

    tail = provenance_fields("book_resample")

    return BookSnapshot(
        source=trigger_record.source,
        symbol=trigger_record.symbol,
        symbol_raw=trigger_record.symbol_raw,
        source_ts=None,
        local_ts=boundary_ns,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
        # Carried from the trigger rather than pinned to CRYPTO: a resampled book is the
        # same market as the stream it was reconstructed from, and this engine is in `core`
        # precisely so it can serve either one.
        asset_class=trigger_record.asset_class,
        bids=bids,
        asks=asks,
        depth=depth,
        sequence_id=None,
        is_snapshot=True,
    )

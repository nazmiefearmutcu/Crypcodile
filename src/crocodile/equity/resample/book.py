"""Book-snapshot resampling at fixed wall-clock intervals, for *equity* records.

Why this is not just ``crocodile.core.resample.book``
=====================================================

``crocodile.core.resample.book.resample_book_snapshots`` survived the merge,
but it is the CRYPTO copy and it does not work on equity input. Three
independent reasons, each verified against this tree:

1. **Record construction.** ``core``'s ``_capture_snapshot`` emits
   ``crocodile.core.schema.records.BookSnapshot`` and reads
   ``trigger_record.source`` and ``trigger_record.asset_class``. Equity records
   (``crocodile.equity.schema.records``) still say ``provider`` and carry no
   ``asset_class`` at all, so the call raises ``AttributeError``. That stops
   being true the day the equity union moves onto the canonical one too.

2. **Nominal dispatch.** ``core``'s loop tests ``isinstance(record,
   BookSnapshot)`` against the crypto struct. An equity ``BookSnapshot`` is a
   different ``msgspec.Struct``, so the test is False, the snapshot is never
   treated as one, and the resampler never initialises — it yields nothing and
   raises nothing. Silent empty output, which is the worst failure mode of the
   three.

3. **Different boundary arithmetic.** This is the interesting one and it is a
   genuine behavioural fork, not an accident of typing:

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

So the *resampling* logic is deliberately duplicated here, in the equity layer,
where the equity record types live (``core`` must not import from ``equity``).

What is NOT duplicated: the order-book engine
----------------------------------------------

The reconstruction state machine is **not** forked. This module drives the
shared ``crocodile.core.replay.orderbook.OrderBook``. Because that engine
dispatches nominally on the crypto record classes (point 2 above), equity
records are translated into the core canonical structs at the engine boundary
by ``_to_core_record``. The translation is lossless with respect to everything
the engine reads — it only ever touches ``bids``, ``asks``, ``sequence_id``,
``seq_id`` and ``prev_seq_id``. Emitted snapshots are built from the engine's
public ``bids`` / ``asks`` dicts and are equity records throughout; no crypto
record ever escapes this module.

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
from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.records import BookDelta as CoreBookDelta
from crocodile.core.schema.records import BookSnapshot as CoreBookSnapshot
from crocodile.equity.schema.records import BookDelta, BookSnapshot

__all__ = ["resample_book_snapshots"]


def _to_core_record(record: BookSnapshot | BookDelta) -> CoreBookSnapshot | CoreBookDelta:
    """Re-type an equity book record as the core canonical struct.

    ``OrderBook`` dispatches with ``isinstance`` against the core records, so an
    equity struct has to be presented in those terms. Only the fields the engine
    actually reads are carried across; ``source`` and ``asset_class`` are filled
    from ``provider`` and from the fact that this module only ever sees equity
    input, purely to satisfy the constructor. They are never read back out —
    emitted snapshots are rebuilt from the engine's ``bids`` / ``asks`` dicts
    and the original equity record.
    """
    if isinstance(record, BookSnapshot):
        return CoreBookSnapshot(
            source=record.provider,
            symbol=record.symbol,
            symbol_raw=record.symbol_raw,
            source_ts=record.source_ts,
            local_ts=record.local_ts,
            asset_class=AssetClass.EQUITY,
            bids=record.bids,
            asks=record.asks,
            depth=record.depth,
            sequence_id=record.sequence_id,
            is_snapshot=True,
        )
    return CoreBookDelta(
        source=record.provider,
        symbol=record.symbol,
        symbol_raw=record.symbol_raw,
        source_ts=record.source_ts,
        local_ts=record.local_ts,
        asset_class=AssetClass.EQUITY,
        bids=record.bids,
        asks=record.asks,
        seq_id=record.seq_id,
        prev_seq_id=record.prev_seq_id,
        is_snapshot=False,
    )


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

    for record in records:
        ts = record.local_ts

        # Before the engine is initialised, we cannot emit anything useful.
        # Deltas arriving before the first snapshot are dropped, matching the
        # engine's own rule.
        if not initialized:
            if isinstance(record, BookSnapshot):
                book.apply(_to_core_record(record))
                initialized = True
                # First boundary is the end of the interval containing this snapshot.
                next_boundary_ns = (ts // interval_ns) * interval_ns + interval_ns
            continue

        assert next_boundary_ns is not None  # guaranteed once initialized is True

        # Flush every boundary strictly before this record, using the book state
        # as it stood before the record — this is what prevents lookahead bias.
        while ts > next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, top_n)
            next_boundary_ns += interval_ns

        # Apply the record to the book (may raise BookGap — propagates to caller).
        book.apply(_to_core_record(record))

        # A record landing exactly on a boundary belongs to that boundary's bar.
        while ts >= next_boundary_ns:
            yield _capture_snapshot(book, record, next_boundary_ns, top_n)
            next_boundary_ns += interval_ns


def _capture_snapshot(
    book: OrderBook,
    trigger_record: BookSnapshot | BookDelta,
    boundary_ns: int,
    top_n: int | None,
) -> BookSnapshot:
    """Build an equity ``BookSnapshot`` from the current ``OrderBook`` state.

    Args:
        book:           The live ``OrderBook`` instance.
        trigger_record: The record whose ``local_ts`` crossed the boundary.
                        Used to copy ``provider``, ``symbol`` and ``symbol_raw``.
        boundary_ns:    The nanosecond timestamp of the bucket boundary; used as
                        ``local_ts`` for the emitted snapshot.
        top_n:          Maximum bid/ask levels on each side; ``None`` = all.

    Returns:
        A ``BookSnapshot`` representing the book at ``boundary_ns``.  We cannot
        know a source timestamp for a synthesised snapshot, so ``source_ts`` is
        ``None``.
    """
    bids_sorted = sorted(book.bids.items(), reverse=True)
    asks_sorted = sorted(book.asks.items())

    if top_n is not None:
        bids_sorted = bids_sorted[:top_n]
        asks_sorted = asks_sorted[:top_n]

    bids: list[tuple[float, float]] = [(p, s) for p, s in bids_sorted]
    asks: list[tuple[float, float]] = [(p, s) for p, s in asks_sorted]

    depth = len(bids) + len(asks)

    return BookSnapshot(
        provider=trigger_record.provider,
        symbol=trigger_record.symbol,
        symbol_raw=trigger_record.symbol_raw,
        source_ts=None,
        local_ts=boundary_ns,
        bids=bids,
        asks=asks,
        depth=depth,
        sequence_id=None,
        is_snapshot=True,
    )

"""The crypto half of ``depth``: a ladder sliced out of a stored ``BookSnapshot``.

The equity half models a ladder because equities give it nothing else — a synthetic
volume-at-price profile out of Yahoo 1-minute bars, upgraded to a real top of book when
Alpaca keys are set (:mod:`crocodile.equity.depth`). Crypto venues stream the ladder
itself and :class:`~crocodile.core.store.parquet_sink.ParquetSink` has been writing it to
``channel=book_snapshot`` since before the merge, so the crypto half of this capability is
not a model of anything. It is a read, a re-cut and a re-stamp, and each of those three
words is doing work in the confidence formula this module reports against
(``book_snapshot_slice``, in :mod:`crocodile.core.schema.provenance`).

Why the slice takes an instant, and why that instant is a bound
--------------------------------------------------------------
``depth`` for equities answers at the moment it is called, because a live quote is the only
thing behind it. A lake read has no such excuse: the caller can ask for any instant the
store covers, so this takes ``as_of_ns`` and returns the newest snapshot at or before it.

At or *before* is not a detail. ``crocodile.core.resample.book`` records what the two book
resamplers disagreed about — whether a bar may contain information from after its own
timestamp — and what the wrong answer cost: a boundary stamped 10:00:00 reporting a bid
that had already been pulled at 10:00:00.200, twenty-four times the depth that could
actually be traded, in the direction that flatters a backtest. The resamplers were
collapsed onto the ordering that cannot do that. A depth slice is the same hazard in a
different shape — here the caller names the instant instead of an interval boundary — so
the bound is in the ``WHERE`` clause (``local_ts <= as_of_ns``) rather than applied to rows
after they arrive, and the confidence formula refuses a negative age as a second tripwire
on the same rule.

Why it takes a ``query`` callable rather than a ``Catalog``
-----------------------------------------------------------
:meth:`crocodile.core.capability.CapabilityContext.query` is the only lake access an
implementation is allowed, because it is where a surface's ``readonly`` and ``row_limit``
policy is applied — reaching :meth:`Catalog.query` directly compiles and runs and silently
ignores both, which is how the crypto CLI came to have no SQL guard while REST and MCP each
grew their own. Taking the bound method keeps that policy in force without dragging the
capability machinery into this module, and it is what lets the tests here run against a
handful of rows with no lake on disk at all.

The one cost is that ``CapabilityContext.query`` takes no bound parameters, so the symbol
is interpolated rather than placed. It goes through :func:`_sql_string_literal`, which is
the escaping rule :meth:`Catalog._read_expr` already applies to lake paths for the same
reason; ``sequencer-latency`` is the entry in this tree for what interpolating a free-text
value without one costs, and it was on a public route.
"""

from __future__ import annotations

from collections.abc import Callable

import duckdb
import polars as pl

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import DepthProfile, Level
from crocodile.core.store.rows import _coerce_levels_from_row

__all__ = ["LakeQuery", "depth_from_book_snapshots"]

LakeQuery = Callable[[str], pl.DataFrame]
"""A lake read under a surface's own policy — :meth:`CapabilityContext.query`, in production."""


def _sql_string_literal(value: str) -> str:
    """Quote ``value`` as a DuckDB string literal, doubling any embedded quote.

    The rule SQL has had since it had string literals, and the one
    :meth:`crocodile.core.store.catalog.Catalog._read_expr` applies to lake paths. It is
    here rather than at the call site because a symbol arrives from a URL query string on
    REST and from a tool argument on MCP, and a value that can end a literal and continue
    the statement is the defect ``sequencer-latency`` shipped on a public route.
    """
    return "'" + value.replace("'", "''") + "'"


def _usable_levels(levels: list[Level], *, descending: bool) -> list[Level]:
    """Sort one side of a stored book and drop the entries that are not levels.

    A size of ``0.0`` is the canonical *removal* of a price level rather than a level with
    no size — ``crocodile.core.store.rows`` refuses to default a null size for exactly that
    reason — so a venue that signals deletions by republishing a zero leaves entries in a
    snapshot that must not be counted as depth. Negative sizes and non-positive prices are
    not levels under any reading. The sort is the equity slippage fork's contribution to
    :mod:`crocodile.core.analytics.slippage`: its crypto twin trusted the stored order and
    got a wrong best price out of any venue that did not publish one.
    """
    kept = [level for level in levels if level[0] > 0.0 and level[1] > 0.0]
    return sorted(kept, key=lambda level: level[0], reverse=descending)


def depth_from_book_snapshots(
    query: LakeQuery,
    symbol: str,
    *,
    asset_class: AssetClass,
    as_of_ns: int,
    top_n: int,
    max_age_ns: int,
) -> DepthProfile:
    """Return the stored book for ``symbol`` at ``as_of_ns``, shaped as a depth ladder.

    Args:
        query: The lake read, under the calling surface's policy. See :data:`LakeQuery`.
        symbol: Canonical symbol, as the lake spells it (``deribit:BTC-PERPETUAL``).
        asset_class: The market this ladder belongs to, carried onto the record. Passed in
            rather than read off the row for the reason
            :class:`~crocodile.core.capability.CapabilityContext` carries the field at all:
            the surface has already resolved which market the request is in, and a lake
            that spans the migration holds rows whose own marker needs
            ``crocodile.core.store.rows`` to interpret. Nothing about this function is
            crypto-specific, which is deliberate — the day an equity provider streams a
            book, its half of ``depth`` is this function with a different argument here.
        as_of_ns: The instant the ladder claims to describe. The newest snapshot at or
            before it is the one that answers; nothing after it is read.
        top_n: Levels per side. The ladder the caller asked for is ``2 * top_n``, which is
            the denominator of the confidence's fill term.
        max_age_ns: How stale a snapshot may be and still be reported as describing
            ``as_of_ns``. Both an admission bound — an older book is not returned at all —
            and the denominator of the confidence's freshness term.

    Returns:
        A :class:`~crocodile.core.schema.records.DepthProfile` in the same shape the equity
        half returns: bids descending, asks ascending, ``reference_price`` at the midpoint,
        ``depth`` the level count, and the four ``prov_*`` fields built by
        :func:`~crocodile.core.schema.provenance.provenance_fields` from what was measured
        here. ``local_ts`` is ``as_of_ns`` — the instant the record claims — and
        ``source_ts`` is whatever the venue stamped on the snapshot it was cut from, so the
        age the confidence was computed over stays legible on the row.

    Raises:
        ValueError: if ``top_n`` or ``max_age_ns`` is not positive, if no snapshot for
            ``symbol`` falls in ``(as_of_ns - max_age_ns, as_of_ns]``, or if the one that
            does carries no usable level on either side. The last is a refusal rather than a
            zero-confidence record: ``DepthProfile.reference_price`` is required, and an
            empty book offers nothing to derive it from — writing a number there would be
            the fabricated measurement Gate 3b scans for.
    """
    if top_n <= 0:
        raise ValueError(f"top_n must be positive; got {top_n!r}")
    if max_age_ns <= 0:
        raise ValueError(f"max_age_ns must be positive; got {max_age_ns!r}")

    oldest_ns = int(as_of_ns) - int(max_age_ns)
    sql = (
        "SELECT source, symbol, symbol_raw, local_ts, source_ts, bids, asks "
        "FROM book_snapshot "
        f"WHERE symbol = {_sql_string_literal(symbol)} "
        # The lookahead bound, in the WHERE clause rather than in a filter over rows that
        # already arrived: a later snapshot is not something this answer gets to see and
        # then discard, it is something it never reads. See the module docstring.
        f"AND local_ts <= {int(as_of_ns)} AND local_ts >= {oldest_ns} "
        "ORDER BY local_ts DESC LIMIT 1"
    )
    try:
        frame = query(sql)
    except duckdb.CatalogException:
        # No `book_snapshot` view means no book snapshot has ever been written to this lake,
        # which is the same answer as a query returning no rows. Nothing wider is caught:
        # a corrupt part file or an unreadable directory is a broken lake, and reporting one
        # as an empty one is a diagnosis the caller cannot tell from the truth.
        frame = pl.DataFrame()

    if frame.is_empty():
        raise ValueError(
            f"no stored book snapshot for {symbol!r} in the {max_age_ns} ns before "
            f"{as_of_ns}; widen max_age_ns, or collect the book"
        )

    row = frame.to_dicts()[0]
    bids = _usable_levels(_coerce_levels_from_row(row.get("bids")), descending=True)[:top_n]
    asks = _usable_levels(_coerce_levels_from_row(row.get("asks")), descending=False)[:top_n]
    if not bids and not asks:
        raise ValueError(
            f"the stored book snapshot for {symbol!r} at {row['local_ts']} carries no level "
            f"with a positive size on either side; there is no reference price to derive"
        )
    if bids and asks:
        reference_price = (bids[0][0] + asks[0][0]) / 2.0
    else:
        reference_price = bids[0][0] if bids else asks[0][0]

    snapshot_ts = int(row["local_ts"])
    # Non-negative by the WHERE clause above. The formula refuses a negative one anyway,
    # which makes it the tripwire on this bound rather than a second copy of it.
    age_ns = int(as_of_ns) - snapshot_ts
    tail = provenance_fields(
        "book_snapshot_slice",
        {
            "n_levels": len(bids) + len(asks),
            "n_requested": 2 * top_n,
            "age_ns": age_ns,
            "window_ns": int(max_age_ns),
        },
    )
    source_ts = row["source_ts"]
    return DepthProfile(
        source=str(row["source"]),
        symbol=str(row["symbol"]),
        symbol_raw=str(row["symbol_raw"]),
        local_ts=int(as_of_ns),
        asset_class=asset_class,
        source_ts=None if source_ts is None else int(source_ts),
        bids=bids,
        asks=asks,
        reference_price=reference_price,
        depth=len(bids) + len(asks),
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
    )

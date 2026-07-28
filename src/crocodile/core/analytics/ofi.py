"""Order-flow imbalance: one definition, over whichever top of book a market publishes.

The measurement is Cont, Kukanov and Stoikov's order-flow imbalance — the signed size that
entered or left the touch between two consecutive observations of it. Its whole content is
in the conditioning: each side's increment depends on whether that side's *price* moved,
not only on how its size changed. A bid that steps up brings its entire new size as new
demand; a bid that steps down takes the entire old size away as cancelled demand; a bid
that holds its price contributes only the difference. Dropping that conditioning leaves
``curr.bid_sz - prev.bid_sz``, which is a different statistic: on a bid stepping up from
100@2 to 101@4 it reports +2 where OFI reports +4, and on a bid stepping *down* from 101@4
to 100@2 it reports -2 where OFI reports -4. The naive version reads a queue that emptied
and refilled one tick lower as a mild outflow; OFI reads it as the whole queue leaving.

**Why this lives in core rather than in either asset class.** ``crypto/analytics/ofi.py``
had the conditioning right and had it inline, as sixteen lines in the middle of a function
whose other job is reading ``book_snapshot`` rows out of DuckDB. M7's equity half is the
same statistic over an equity L1 quote stream — the same two prices and two sizes, arriving
on the ``quote`` channel instead — so the alternative to this module was a second copy of
those sixteen lines in ``equity/analytics/ofi.py``. Two copies of one definition is what
``crocodile.core.analytics.slippage`` was written to end for the book walk, and the failure
mode there is the one that applies here: the copies do not diverge loudly, they diverge on
the branch nobody's fixture exercises, and both keep answering under one capability name.

What is *not* shared is where the rows come from. A crypto top of book is the first level
of a stored :class:`~crocodile.core.schema.records.BookSnapshot`; an equity one is a whole
:class:`~crocodile.core.schema.records.Quote` record. Those are two reads against two
channels and they stay in their own packages; what crosses is :class:`TopOfBook`, which is
the four numbers both of them reduce to.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from typing import Final, NamedTuple

import polars as pl

__all__ = ["OFI_SCHEMA", "TopOfBook", "bin_ofi", "ofi_increment"]


class TopOfBook(NamedTuple):
    """The best bid and best ask, with their sizes, at one instant.

    A named tuple rather than four positional floats because the two sides differ only by
    which of them wants a *lower* price, and ``ofi_increment(prev, curr)`` transposing a
    price with a size would still typecheck, still run, and still return a number.
    """

    bid_px: float
    bid_sz: float
    ask_px: float
    ask_sz: float


OFI_SCHEMA: Final[Mapping[str, pl.DataType]] = {
    "timestamp": pl.Int64(),
    "best_bid": pl.Float64(),
    "best_ask": pl.Float64(),
    "ofi": pl.Float64(),
}
"""The four columns both asset classes report, stated so an empty result is still a table.

``crypto/analytics/ofi.py`` returned a bare ``pl.DataFrame()`` for a lake with no book —
a frame with no columns, so ``df["ofi"]`` on a quiet symbol raised ``ColumnNotFound``
rather than yielding nothing. Declaring the schema is what makes "no order flow in this
window" the same shape as "some", which is what a caller selecting columns needs.
"""


def ofi_increment(prev: TopOfBook, curr: TopOfBook) -> float:
    """Return the order-flow imbalance contributed by the step from ``prev`` to ``curr``.

    The bid side contributes ``curr.bid_sz`` when the bid price rose, ``-prev.bid_sz`` when
    it fell, and the size difference when it held; the ask side is the mirror image with the
    inequalities reversed, since an ask *improves* by falling. The imbalance is the bid
    contribution minus the ask contribution, so a positive number is net buying pressure at
    the touch.

    Both sides are required to be present. A one-sided or empty observation is not a top of
    book and has no imbalance to report — it is dropped by the caller before it reaches
    here, which is what the two readers in ``crypto`` and ``equity`` both do, because the
    alternative is treating an absent side as a zero-size quote and reporting the whole
    remaining side as flow.
    """
    if curr.bid_px > prev.bid_px:
        bid_flow = curr.bid_sz
    elif curr.bid_px < prev.bid_px:
        bid_flow = -prev.bid_sz
    else:
        bid_flow = curr.bid_sz - prev.bid_sz

    if curr.ask_px < prev.ask_px:
        ask_flow = curr.ask_sz
    elif curr.ask_px > prev.ask_px:
        ask_flow = -prev.ask_sz
    else:
        ask_flow = curr.ask_sz - prev.ask_sz

    return bid_flow - ask_flow


class _Step(NamedTuple):
    """One consecutive-pair increment, before it is summed into a bin."""

    ts: int
    bid_px: float
    ask_px: float
    ofi: float


def bin_ofi(
    tops: Sequence[tuple[int, TopOfBook]], *, start_ns: int, interval_ns: int
) -> pl.DataFrame:
    """Sum the pairwise increments of ``tops`` into bins of ``interval_ns``.

    ``tops`` is ``(local_ts, top)`` pairs. They are sorted here rather than trusted: the
    increment is a function of two *consecutive* observations, so a frame that arrived out
    of order would not produce a wrong-looking answer, it would produce a plausible one
    computed against the wrong predecessor.

    Bins are aligned to ``start_ns`` rather than to the epoch, which is the crypto
    implementation's rule and is the one that makes the first bin start where the caller's
    window starts. Aligning to the epoch would give a caller asking for ``5m`` from 10:02 a
    first bin three minutes wide, reported under a timestamp before their range.

    Each row carries the *last* step's prices in its bin, so ``best_bid``/``best_ask``
    describe the state the bin closed in rather than an average of a state that never held.

    Fewer than two observations yields an empty frame with :data:`OFI_SCHEMA`: one
    observation has no predecessor, and an imbalance is defined only over a step.
    """
    if interval_ns <= 0:
        raise ValueError(f"interval_ns must be positive, got {interval_ns}")
    ordered = sorted(tops, key=lambda pair: pair[0])
    if len(ordered) < 2:
        return pl.DataFrame(schema=OFI_SCHEMA)

    steps: list[_Step] = [
        _Step(ts=curr_ts, bid_px=curr.bid_px, ask_px=curr.ask_px, ofi=ofi_increment(prev, curr))
        for (_, prev), (curr_ts, curr) in itertools.pairwise(ordered)
    ]

    bins: dict[int, list[_Step]] = {}
    for step in steps:
        bin_start = start_ns + ((step.ts - start_ns) // interval_ns) * interval_ns
        bins.setdefault(bin_start, []).append(step)

    rows = [
        {
            "timestamp": bin_start,
            "best_bid": bin_steps[-1].bid_px,
            "best_ask": bin_steps[-1].ask_px,
            "ofi": sum(step.ofi for step in bin_steps),
        }
        for bin_start, bin_steps in sorted(bins.items())
    ]
    return pl.DataFrame(rows, schema=OFI_SCHEMA)

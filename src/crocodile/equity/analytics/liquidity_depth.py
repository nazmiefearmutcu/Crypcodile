"""Band depth over the equity ladder M6 built — ``liquidity-depth``'s equity half.

The crypto half sums a stored :class:`~crocodile.core.schema.records.BookSnapshot`'s levels
inside 1 %, 2 % and 5 % of mid, one row per book sequence. Equities have no such stream, and
that absence is exactly what M6 removed: :func:`~crocodile.equity.depth.select_depth_source`
produces a ladder either way — Alpaca's L1 top of book when the keys are configured, a
volume-at-price profile binned out of Yahoo 1-minute bars when they are not. This module is
the band arithmetic read over that ladder, and the arithmetic itself is
:func:`~crocodile.core.analytics.liquidity_depth.depth_within_bands`, shared with crypto.

**The three columns that differ from the crypto half, and why each has to.**

``block`` becomes ``local_ts``. A block number identifies a chain state; equities have no
chain, and there is no sequence identifier on either branch of the equity ladder. Emitting
a zero or a counter under the name ``block`` would be a fabricated measurement in a column
a caller joins on. What identifies an equity snapshot is when it was taken, so that is what
is reported, under its own name.

``reference_price`` is published rather than left implicit. The crypto half centres its
bands on the mid of the touch and does not report it, because a caller holding the snapshot
can recompute it. Here the centre is the profile's own ``reference_price``, which is the L1
mid on one branch and the last 1-minute close on the other — two different quantities under
one column — so a band sum without the price it was measured around cannot be interpreted.

The four ``prov_*`` columns come along, and this is the point of the whole row rather than a
decoration. ``Impl.prov`` on the declaration is a *ceiling* — what this implementation
produces on its best day, which is the keyed L1 branch — and it is fixed at import time, so
it cannot answer "which branch actually ran for this call". The profile's own tail was
measured when the ladder was built, by ``provenance_fields``: ``alpaca_l1`` scoring how much
of the top of book was quoted, or ``yahoo_1m_vap`` scoring how much of a session the profile
was binned out of. Dropping it would leave the caller with a frame of sizes and no way to
tell resting quotes from traded volume standing in for them — which on the synthetic branch
is the difference between a book and a histogram.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import polars as pl

from crocodile.core.analytics.liquidity_depth import band_columns, depth_within_bands
from crocodile.core.schema.records import DepthProfile

__all__ = ["LIQUIDITY_DEPTH_SCHEMA", "liquidity_depth_from_profile"]

LIQUIDITY_DEPTH_SCHEMA: Final[Mapping[str, pl.DataType]] = {
    "local_ts": pl.Int64(),
    "reference_price": pl.Float64(),
    "depth": pl.Int64(),
    **{name: pl.Float64() for name in band_columns()},
    "prov": pl.String(),
    "prov_basis": pl.String(),
    "prov_confidence": pl.Float64(),
    "prov_inputs": pl.List(pl.String()),
}
"""Declared so a ladder with nothing in it is still a table with the columns above.

An equity snapshot taken outside market hours can legitimately have no level on one side —
Alpaca returns a one-sided latest quote — and a symbol Yahoo has no 1-minute bars for
produces no profile at all. Both are empty answers rather than errors, and a caller
selecting ``bid_depth_1pct`` should get an empty column rather than ``ColumnNotFound``.
"""


def liquidity_depth_from_profile(profile: DepthProfile) -> pl.DataFrame:
    """Sum ``profile``'s levels inside each band of its own reference price.

    One row, because one profile is one observation of the ladder. The crypto half emits
    many because a lake holds many book sequences; an equity ladder is fetched live, so the
    row count is the number of snapshots taken, which is one.

    A profile whose ``reference_price`` is not positive yields the empty frame rather than
    six zero sums. Every band is multiplicative, so a non-positive centre collapses all of
    them onto it and every sum comes back ``0.0`` — a full book reported as having no near
    depth, at whatever confidence the ladder measured for itself. That is the shape of
    fabricated reading this package's gates exist to refuse, and the honest form of it is no
    row at all.
    """
    if profile.reference_price <= 0.0:
        return pl.DataFrame(schema=LIQUIDITY_DEPTH_SCHEMA)
    row: dict[str, Any] = {
        "local_ts": profile.local_ts,
        "reference_price": profile.reference_price,
        "depth": profile.depth,
        **depth_within_bands(profile.bids, profile.asks, profile.reference_price),
        "prov": profile.prov.value,
        "prov_basis": profile.prov_basis,
        "prov_confidence": profile.prov_confidence,
        "prov_inputs": list(profile.prov_inputs),
    }
    return pl.DataFrame([row], schema=LIQUIDITY_DEPTH_SCHEMA)

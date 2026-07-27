"""The provenance tail, in frame form, for every resampler that returns a DataFrame.

A resampler that builds records states a provenance tail on each one. A resampler that
builds a *frame* has to state the same thing in columns, and four of them do:
:func:`crocodile.core.resample.ohlcv.resample_ohlcv`, which aggregates in DuckDB, and
``resample_{trades,quotes,bars}_df`` in :mod:`crocodile.equity.resample.ohlcv`, which
aggregate in Polars. All four used to state nothing, so a caller writing those rows back
to the lake had the header default applied for them — NATIVE at confidence 1.0, the claim
that a venue reported this bar — over prices that had been scraped off a rendered page.

These helpers live in ``core`` rather than beside the Polars paths because the DuckDB
path moved here when the two ``resample_ohlcv`` implementations were merged, and a tail
spelled one way in ``core`` and another in ``equity`` is the same class of divergence
that merge existed to remove.
"""

from __future__ import annotations

import polars as pl

from crocodile.core.schema.provenance import Provenance, trust_rank

__all__ = [
    "DESIRED_COLS",
    "PROV_RANK",
    "TRUST_RANKS",
    "emitted_prov",
    "order_columns",
    "rank_inputs",
]

TRUST_RANKS: dict[str, int] = {level.value: trust_rank(level) for level in Provenance}
LEVEL_BY_RANK: dict[int, str] = {rank: value for value, rank in TRUST_RANKS.items()}
PROV_RANK = "_prov_rank"
"""Name of the working column holding a row's numeric trust rank.

Underscore-prefixed because it is dropped before the frame is returned: it exists only so
the worst level in a bucket can be taken with ``max()``, which over an enum's *spelling*
would be alphabetical rather than an order.
"""

DESIRED_COLS = [
    "bar",
    "symbol",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "buy_volume",
    "sell_volume",
    "vwap",
    "num_trades",
    # The Polars frame paths still emit the equity fork's spelling of the count. The
    # DuckDB path emits ``num_trades``, which is what the canonical ``OHLCV`` record and
    # the lake column are called; both are listed so neither set of callers sees its
    # columns reordered while the frame paths keep their own name.
    "trade_count",
    "prov",
    "prov_basis",
    "prov_confidence",
]
"""Canonical column order for a resampled bar frame."""


def rank_inputs(df: pl.DataFrame, floor: Provenance) -> tuple[pl.DataFrame, bool]:
    """Attach the numeric trust rank of each input row's ``prov``, if it declares one.

    Returns the frame and whether the rank column was added. A frame with no ``prov``
    column makes no provenance claim at all, and the emitted level then rests on the
    basis alone — which is the same reading the record paths give a default-constructed
    input.
    """
    if "prov" not in df.columns:
        return df, False
    return (
        df.with_columns(
            pl.col("prov")
            .replace_strict(TRUST_RANKS, default=trust_rank(floor), return_dtype=pl.Int32)
            .fill_null(trust_rank(floor))
            .alias(PROV_RANK)
        ),
        True,
    )


def emitted_prov(has_rank: bool, floor: Provenance) -> pl.Expr:
    """Return the emitted ``prov`` for a bucket: the basis level, floored by its inputs."""
    if not has_rank:
        return pl.lit(floor.value).alias("prov")
    return (
        pl.max_horizontal(pl.col(PROV_RANK), pl.lit(trust_rank(floor), dtype=pl.Int32))
        .replace_strict(LEVEL_BY_RANK, return_dtype=pl.String)
        .alias("prov")
    )


def order_columns(resampled: pl.DataFrame) -> pl.DataFrame:
    """Put the canonical bar columns first, then any remaining group keys."""
    out_cols = [c for c in DESIRED_COLS if c in resampled.columns] + [
        c for c in resampled.columns if c not in DESIRED_COLS
    ]
    return resampled.select(out_cols)

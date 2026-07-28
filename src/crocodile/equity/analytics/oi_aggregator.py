"""Open interest for listed equities, summed out of the option chain.

A perpetual's venue publishes one open-interest number for the instrument, which is what
the crypto half reads off the ``open_interest`` channel. No equity feed publishes an
underlying's open interest at all — what Yahoo publishes is ``openInterest`` per *option
contract*, so the underlying's figure is the sum over its chain, and that sum is this
module's whole job. It is also what makes the two halves the same capability: same
``OpenInterestParams``, same forward-filled board out of
:func:`crocodile.core.analytics.open_interest.align_open_interest`, same
``local_ts``/per-source/``total_oi`` columns.

Two consequences of counting contracts that are worth stating rather than discovering.

**Calls and puts are summed together**, which is the convention "open interest in AAPL
options" names and is the number a chain page totals. Someone who wants the two sides
apart is asking a different question, and the answer to it is the chain itself — which
``replay`` and ``catalog-scan`` already serve, per contract, with ``opt_type`` on the row.

**A contract with no published open interest contributes nothing rather than a zero.**
Yahoo omits the field on contracts that have never traded, and summing a null as 0.0 and
summing it as absent give the same total — but the *series* is what differs: a poll in
which every contract omitted the field would otherwise write a real 0.0 over the last
known figure and forward-fill that zero forward. Skipping the null leaves the last figure
standing, which is the same rule the crypto half applies to a null sample.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from crocodile.core.analytics.open_interest import SeriesKey, align_open_interest
from crocodile.core.store.catalog import Catalog


def aggregate_option_open_interest(
    catalog: Catalog,
    underlyings: str | Sequence[str] | None = None,
    start_ns: int = 0,
    end_ns: int = 0,
) -> pl.DataFrame:
    """Aggregate the stored option chain's open interest per underlying.

    Args:
        catalog: The lake to read ``options_chain`` from.
        underlyings: Case-insensitive literal substring patterns, OR-ed, matched against
            the row's ``underlying``. A lone string is the one-element case and ``None``
            or an empty sequence means every underlying — which is
            ``OpenInterestParams.symbols``' semantic exactly, because the two halves share
            the struct and a pattern that meant "symbol" here and "underlying" there would
            be one field with two meanings.
        start_ns: Inclusive lower bound on ``local_ts``.
        end_ns: Inclusive upper bound on ``local_ts``.

    Returns:
        ``local_ts``, one column per provider, and ``total_oi``. An empty frame when the
        lake holds no matching chain — the contract every analytics function here keeps.
    """
    try:
        catalog.refresh_views()
        raw_df = catalog.query(
            'SELECT * FROM "options_chain" '
            f"WHERE local_ts >= {start_ns} AND local_ts <= {end_ns}"
        )
    except Exception:
        return pl.DataFrame()

    if raw_df is None or len(raw_df) == 0:
        return pl.DataFrame()

    if underlyings is None:
        patterns: list[str] = []
    elif isinstance(underlyings, str):
        patterns = [underlyings]
    else:
        patterns = [p for p in underlyings if isinstance(p, str)]
    # A blank token would become contains(""), which matches every underlying — the
    # opposite of what an empty filter element reads as.
    patterns = [p for p in patterns if p.strip()]

    if patterns:
        filter_expr = pl.col("underlying").str.to_lowercase().str.contains(
            patterns[0].lower(), literal=True
        )
        for pattern in patterns[1:]:
            filter_expr = filter_expr | pl.col("underlying").str.to_lowercase().str.contains(
                pattern.lower(), literal=True
            )
        raw_df = raw_df.filter(filter_expr)

    if len(raw_df) == 0:
        return pl.DataFrame()

    timestamps = sorted(raw_df["local_ts"].unique().to_list())
    series_keys: list[SeriesKey] = sorted(
        {
            (row["source"], row["underlying"])
            for row in raw_df.select(["source", "underlying"]).unique().iter_rows(named=True)
        }
    )

    # One sample per (provider, underlying) per instant, summed over that instant's
    # contracts. This is the step the crypto half does not need and the only arithmetic
    # this module adds.
    samples: dict[int, dict[SeriesKey, float]] = {}
    for row in raw_df.iter_rows(named=True):
        open_interest = row["open_interest"]
        if open_interest is None:
            continue
        at = samples.setdefault(row["local_ts"], {})
        key: SeriesKey = (row["source"], row["underlying"])
        at[key] = at.get(key, 0.0) + float(open_interest)

    return align_open_interest(timestamps, series_keys, samples)

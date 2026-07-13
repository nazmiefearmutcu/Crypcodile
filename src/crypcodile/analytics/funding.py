"""Funding APR analytics (Task 6.2).

Reads the ``funding`` channel via the DuckDB Catalog and computes per-event
APR, cumulative funding, and summary statistics.

Sign convention
---------------
Positive ``funding_rate`` means **longs pay shorts** (the long position holder
pays the funding to the short holder).  Negative ``funding_rate`` means shorts
pay longs.

Formulas
--------
- ``periods_per_year(interval_hours)`` = ``8760 / interval_hours``
  (8760 = number of hours in a non-leap year; 8h interval → 1095 periods/yr).
- ``apr_from_rate(rate, interval_hours)`` = ``rate * periods_per_year(interval_hours)``
  (simple, non-compounded annualisation; appropriate for the short holding
  periods involved in perpetual funding).
- ``cumulative_funding`` = running sum of ``funding_rate`` over rows ordered by
  ``funding_ts`` ascending (after deduplicating by ``funding_ts`` so live-lake
  re-emits of the same settlement do not inflate the sum).

Missing ``interval_hours``
--------------------------
If a stored row has a NULL ``interval_hours`` (the field is optional in the
canonical schema) the value is defaulted to **8** before computing APR.  This
matches the most common perpetual-contract funding interval.
"""

from __future__ import annotations

import polars as pl

from crypcodile.store.catalog import Catalog

__all__ = [
    "apr_from_rate",
    "funding_apr",
    "funding_summary",
    "periods_per_year",
]

# Default funding interval when the stored field is NULL.
_DEFAULT_INTERVAL_HOURS: int = 8


# ---------------------------------------------------------------------------
# Pure-math helpers
# ---------------------------------------------------------------------------


def periods_per_year(interval_hours: int) -> float:
    """Return the number of funding periods in a calendar year.

    Args:
        interval_hours: Length of one funding interval in hours.
                        Must be a positive integer.

    Returns:
        ``8760.0 / interval_hours``  (e.g. 1095.0 for 8-hour intervals).

    Raises:
        ValueError: If *interval_hours* is zero or negative.
    """
    if interval_hours <= 0:
        raise ValueError(
            f"interval_hours must be a positive integer, got {interval_hours}"
        )
    return 8760.0 / interval_hours


def apr_from_rate(rate: float, interval_hours: int) -> float:
    """Annualise a single funding rate.

    Uses simple (non-compounded) annualisation:
    ``apr = rate * (8760 / interval_hours)``.

    Args:
        rate:           The funding rate for one period (e.g. 0.0001).
        interval_hours: Length of one funding interval in hours.

    Returns:
        Annualised percentage rate as a decimal (e.g. 0.10950 for 10.95 %).
    """
    return rate * periods_per_year(interval_hours)


# ---------------------------------------------------------------------------
# Catalog-backed analytics
# ---------------------------------------------------------------------------


def funding_apr(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
) -> pl.DataFrame:
    """Return per-event funding APR and cumulative funding for a symbol.

    Queries the ``funding`` channel and returns one row per funding event
    within ``[start_ns, end_ns]``, ordered by ``funding_ts`` ascending.

    Live lakes often re-emit the same settlement many times (WS reconnects,
    overlapping collectors).  Rows that share a ``funding_timestamp`` (resolved
    as ``funding_ts``) are collapsed to a single event **before** APR and the
    cumulative sum are computed, so cumulative funding cannot explode.

    Args:
        catalog:  A :class:`~crypcodile.store.catalog.Catalog` instance.
        symbol:   Canonical symbol string (e.g. ``"deribit:BTC-PERPETUAL"``).
        start_ns: Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:   Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

    Returns:
        A Polars DataFrame with columns:

        ==================  ===========  ==============================================
        funding_ts          Int64        Funding event timestamp (nanoseconds UTC).
        funding_rate        Float64      Per-period funding rate (signed).
        interval_hours      Int64        Funding interval in hours (NULL → 8).
        apr                 Float64      Annualised rate = rate x periods_per_year.
        cumulative_funding  Float64      Running sum of ``funding_rate`` (ascending).
        ==================  ===========  ==============================================

        Returns an empty :class:`polars.DataFrame` (0 columns, 0 rows) when no
        data exists for the symbol in the given range — consistent with the
        ``resample_ohlcv`` empty-result contract.
    """
    raw = catalog.scan("funding", symbol, start_ns, end_ns)
    if len(raw) == 0:
        return pl.DataFrame()

    # Resolve columns we need; guard against unexpected schema variations.
    rate_col = raw["funding_rate"]

    # funding_timestamp may be the exchange-provided timestamp; fall back to
    # local_ts when it is NULL (or the column is absent).
    if "funding_timestamp" in raw.columns:
        ts_col = raw["funding_timestamp"].fill_null(raw["local_ts"])
    else:
        ts_col = raw["local_ts"]

    # Resolve interval_hours — default NULL → 8.
    if "interval_hours" in raw.columns:
        ih_col = raw["interval_hours"].fill_null(_DEFAULT_INTERVAL_HOURS).cast(pl.Int64)
    else:
        ih_col = pl.Series("interval_hours", [_DEFAULT_INTERVAL_HOURS] * len(raw), dtype=pl.Int64)

    # Build a working frame ordered by funding_ts, then collapse duplicate
    # settlement timestamps (keep last write).  Must happen before cumulative
    # sum so re-emitted live-lake rows do not inflate total funding.
    work = (
        pl.DataFrame(
            {
                "funding_ts": ts_col.cast(pl.Int64),
                "funding_rate": rate_col.cast(pl.Float64),
                "interval_hours": ih_col,
            }
        )
        .sort("funding_ts")
        .unique(subset=["funding_ts"], keep="last", maintain_order=True)
    )

    # Compute APR per row via the validated helper (pure math, no numpy/scipy).
    # Routing through apr_from_rate -> periods_per_year enforces the interval_hours>0
    # invariant, turning a corrupt 0 (ZeroDivisionError) or negative (silently wrong,
    # negated APR) into a legible ValueError instead.
    apr_values = [
        apr_from_rate(rate, ih)
        for rate, ih in zip(
            work["funding_rate"].to_list(), work["interval_hours"].to_list(), strict=True
        )
    ]

    # Compute cumulative_funding (running sum of funding_rate).
    rates = work["funding_rate"].to_list()
    running: float = 0.0
    cum_values: list[float] = []
    for r in rates:
        running += r
        cum_values.append(running)

    result = work.with_columns(
        [
            pl.Series("apr", apr_values, dtype=pl.Float64),
            pl.Series("cumulative_funding", cum_values, dtype=pl.Float64),
        ]
    )

    return result


def funding_summary(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
) -> pl.DataFrame:
    """Return a single-row summary of funding statistics for a symbol.

    Args:
        catalog:  A :class:`~crypcodile.store.catalog.Catalog` instance.
        symbol:   Canonical symbol string.
        start_ns: Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:   Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

    Returns:
        A single-row Polars DataFrame with columns:

        ===========  =======  ============================================
        n_events     Int64    Number of funding events in the range.
        mean_rate    Float64  Mean per-period funding rate.
        mean_apr     Float64  Mean annualised rate (mean_rate x ppy).
        total_funding Float64 Sum of all per-period funding rates.
        ===========  =======  ============================================

        Returns an empty :class:`polars.DataFrame` when no data exists.
    """
    detail = funding_apr(catalog, symbol, start_ns, end_ns)
    if len(detail) == 0:
        return pl.DataFrame()

    rates = detail["funding_rate"].to_list()
    aprs = detail["apr"].to_list()
    n = len(rates)
    mean_rate = sum(rates) / n
    mean_apr = sum(aprs) / n
    total_funding = sum(rates)

    return pl.DataFrame(
        {
            "n_events": pl.Series([n], dtype=pl.Int64),
            "mean_rate": pl.Series([mean_rate], dtype=pl.Float64),
            "mean_apr": pl.Series([mean_apr], dtype=pl.Float64),
            "total_funding": pl.Series([total_funding], dtype=pl.Float64),
        }
    )

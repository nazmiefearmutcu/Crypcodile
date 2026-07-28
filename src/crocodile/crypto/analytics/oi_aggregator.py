"""Open interest across crypto venues, aligned onto one clock.

Reads the ``open_interest`` channel — which is a value a perpetual's venue publishes about
itself — filters it to the requested symbol patterns, and hands the samples to
:func:`crocodile.core.analytics.open_interest.align_open_interest`, which owns the
forward-fill and the wide frame. The equity half of ``open-interest`` counts a different
kind of series out of a different channel and produces the same table through the same
function; that shared shape is what makes the capability symmetric rather than two
capabilities under one name.
"""

from __future__ import annotations

import polars as pl

from crocodile.core.analytics.open_interest import SeriesKey, align_open_interest
from crocodile.core.store.catalog import Catalog


def aggregate_open_interest(
    catalog: Catalog,
    symbols: str | list[str] | None = None,
    start_ns: int = 0,
    end_ns: int = 0,
) -> pl.DataFrame:
    """Aggregate open interest across different exchanges and symbols.

    Queries the `open_interest` view, filters by symbols (substring match),
    and aligns the open interest data across exchanges with forward-filling.

    OI is tracked per (exchange, symbol) so multiple symbols on the same
    exchange at the same timestamp do not overwrite each other. Exchange
    columns are the sum of that exchange's symbols; ``total_oi`` is the
    sum across all (exchange, symbol) series.
    """
    try:
        catalog.refresh_views()
        raw_df = catalog.query(
            'SELECT * FROM "open_interest" '
            f"WHERE local_ts >= {start_ns} AND local_ts <= {end_ns}"
        )
    except Exception:
        return pl.DataFrame()

    if raw_df is None or len(raw_df) == 0:
        return pl.DataFrame()

    # Filter matching symbols (substring, case-insensitive).
    # Use literal=True so dots/parens in symbols are not regex metacharacters
    # (e.g. "BTC.USDT" must not match "BTCXUSDT").
    if symbols is None:
        symbols_list: list[str] = []
    elif isinstance(symbols, str):
        symbols_list = [symbols]
    else:
        try:
            symbols_list = list(symbols)
        except TypeError:
            symbols_list = []

    # Drop empty / whitespace-only tokens so they do not become contains("")
    # (which matches every symbol under the default regex engine).
    symbols_list = [s for s in symbols_list if isinstance(s, str) and s.strip()]

    if symbols_list:
        filters = [
            pl.col("symbol").str.to_lowercase().str.contains(s.lower(), literal=True)
            for s in symbols_list
        ]
        filter_expr = filters[0]
        for f in filters[1:]:
            filter_expr = filter_expr | f
        raw_df = raw_df.filter(filter_expr)

    if len(raw_df) == 0:
        return pl.DataFrame()

    timestamps = sorted(raw_df["local_ts"].unique().to_list())
    series_keys = sorted(
        {
            (row["source"], row["symbol"])
            for row in raw_df.select(["source", "symbol"]).unique().iter_rows(named=True)
        }
    )

    # Map: ts -> (exchange, symbol) -> open_interest
    # Skip null OI samples so they do not overwrite last-known values with 0.0
    # and zero out forward-fill for that series.
    samples: dict[int, dict[SeriesKey, float]] = {}
    for row in raw_df.iter_rows(named=True):
        oi_val = row["open_interest"]
        if oi_val is None:
            continue
        samples.setdefault(row["local_ts"], {})[(row["source"], row["symbol"])] = float(oi_val)

    return align_open_interest(timestamps, series_keys, samples)

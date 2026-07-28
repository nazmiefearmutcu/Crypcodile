"""Order Flow Imbalance (OFI) analytics (Task R2).

Calculates the OFI index over time-binned intervals using historical book snapshots.
"""

from __future__ import annotations

import polars as pl

from crocodile.core.analytics.ofi import OFI_SCHEMA, TopOfBook, bin_ofi
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.rows import _coerce_levels_from_row

__all__ = ["calculate_ofi", "parse_interval_to_ns"]


def parse_interval_to_ns(interval_str: str) -> int:
    """Parse interval duration string (e.g. '1s', '5m', '1h') to nanoseconds.

    Args:
        interval_str: Interval duration, e.g. "1s", "5m", "1h", "1d".

    Returns:
        Interval duration in nanoseconds.
    """
    interval_str = interval_str.strip().lower()
    if not interval_str:
        raise ValueError("Interval string cannot be empty.")
    
    unit = interval_str[-1]
    value_str = interval_str[:-1]
    if not value_str.isdigit():
        raise ValueError(f"Invalid interval duration value: '{value_str}' in '{interval_str}'")
    
    value = int(value_str)
    if unit == "s":
        factor = 1_000_000_000
    elif unit == "m":
        factor = 60 * 1_000_000_000
    elif unit == "h":
        factor = 3600 * 1_000_000_000
    elif unit == "d":
        factor = 24 * 3600 * 1_000_000_000
    else:
        raise ValueError(
            f"Unknown interval unit '{unit}' in '{interval_str}'. "
            f"Supported units are s, m, h, d."
        )
    return value * factor


def calculate_ofi(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    interval: str,
) -> pl.DataFrame:
    """Calculate the Order Flow Imbalance (OFI) index over time-binned intervals.

    Args:
        catalog: A :class:`~crocodile.core.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.
        start_ns: Inclusive lower bound on local_ts (nanoseconds UTC).
        end_ns: Inclusive upper bound on local_ts (nanoseconds UTC).
        interval: Time-bin interval string, e.g. "1s", "5m", "1h".

    Returns:
        A Polars DataFrame with the columns
        :data:`~crocodile.core.analytics.ofi.OFI_SCHEMA` declares — ``timestamp``,
        ``best_bid``, ``best_ask``, ``ofi`` — and no rows where the window holds fewer
        than two usable snapshots.

    The imbalance arithmetic and the binning are
    :func:`~crocodile.core.analytics.ofi.ofi_increment` and
    :func:`~crocodile.core.analytics.ofi.bin_ofi`, which used to be sixteen lines inline
    here. They moved when M7 gave them a second caller: an equity L1 quote is the same
    two prices and two sizes as this function's top of book, so the alternative was a
    second copy of the conditioning — and a copy that dropped it would still return
    numbers, just a different statistic under the same capability name. What stays here
    is the part that is genuinely crypto's: reading ``book_snapshot`` and taking the
    first level of each side.
    """
    interval_ns = parse_interval_to_ns(interval)

    try:
        catalog.refresh_views()
        df = catalog.scan("book_snapshot", symbol, start_ns, end_ns)
    except Exception:
        df = pl.DataFrame()

    if df.is_empty():
        return pl.DataFrame(schema=OFI_SCHEMA)

    tops: list[tuple[int, TopOfBook]] = []
    for r in df.to_dicts():
        bids = _coerce_levels_from_row(r.get("bids"))
        asks = _coerce_levels_from_row(r.get("asks"))
        # A snapshot missing a side is not a top of book: there is no imbalance between
        # one price and nothing.
        if not bids or not asks:
            continue
        tops.append((
            int(r["local_ts"]),
            TopOfBook(
                bid_px=float(bids[0][0]),
                bid_sz=float(bids[0][1]),
                ask_px=float(asks[0][0]),
                ask_sz=float(asks[0][1]),
            ),
        ))

    return bin_ofi(tops, start_ns=start_ns, interval_ns=interval_ns)

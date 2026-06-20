"""Order Flow Imbalance (OFI) analytics (Task R2).

Calculates the OFI index over time-binned intervals using historical book snapshots.
"""

from __future__ import annotations

import polars as pl

from crypcodile.store.catalog import Catalog
from crypcodile.store.rows import _coerce_levels_from_row

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
        catalog: A :class:`~crypcodile.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.
        start_ns: Inclusive lower bound on local_ts (nanoseconds UTC).
        end_ns: Inclusive upper bound on local_ts (nanoseconds UTC).
        interval: Time-bin interval string, e.g. "1s", "5m", "1h".

    Returns:
        A Polars DataFrame containing columns:
        - timestamp: Bin start timestamp (Int64, nanoseconds UTC)
        - best_bid: Best bid price of the last snapshot in the bin (Float64)
        - best_ask: Best ask price of the last snapshot in the bin (Float64)
        - ofi: Net order flow imbalance in the bin (Float64)
    """
    interval_ns = parse_interval_to_ns(interval)

    try:
        catalog.refresh_views()
        df = catalog.scan("book_snapshot", symbol, start_ns, end_ns)
    except Exception:
        df = pl.DataFrame()

    if df.is_empty():
        return pl.DataFrame()

    # Convert to list of dicts for easy step-by-step processing
    rows = df.to_dicts()
    snapshots = []
    for r in rows:
        ts = int(r["local_ts"])
        bids = _coerce_levels_from_row(r.get("bids"))
        asks = _coerce_levels_from_row(r.get("asks"))
        if not bids or not asks:
            continue
        snapshots.append({
            "ts": ts,
            "bid_px": float(bids[0][0]),
            "bid_sz": float(bids[0][1]),
            "ask_px": float(asks[0][0]),
            "ask_sz": float(asks[0][1]),
        })

    # Sort snapshots by timestamp just to be safe
    snapshots.sort(key=lambda x: x["ts"])

    if len(snapshots) < 2:
        return pl.DataFrame()

    # Calculate step-by-step OFI
    steps = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]

        # Bid flow change
        if curr["bid_px"] > prev["bid_px"]:
            delta_wb = curr["bid_sz"]
        elif curr["bid_px"] < prev["bid_px"]:
            delta_wb = -prev["bid_sz"]
        else:
            delta_wb = curr["bid_sz"] - prev["bid_sz"]

        # Ask flow change
        if curr["ask_px"] < prev["ask_px"]:
            delta_wa = curr["ask_sz"]
        elif curr["ask_px"] > prev["ask_px"]:
            delta_wa = -prev["ask_sz"]
        else:
            delta_wa = curr["ask_sz"] - prev["ask_sz"]

        ofi_val = delta_wb - delta_wa
        steps.append({
            "ts": curr["ts"],
            "bid_px": curr["bid_px"],
            "ask_px": curr["ask_px"],
            "ofi": ofi_val,
        })

    # Bin the step OFIs
    bins: dict[int, list[dict]] = {}
    for step in steps:
        # Align to start_ns
        bin_start_ts = start_ns + ((step["ts"] - start_ns) // interval_ns) * interval_ns
        if bin_start_ts not in bins:
            bins[bin_start_ts] = []
        bins[bin_start_ts].append(step)

    # Build the final binned dataframe
    binned_data = []
    for bin_start_ts, bin_steps in sorted(bins.items()):
        total_ofi = sum(s["ofi"] for s in bin_steps)
        # Last step in this bin dictates the final prices
        last_step = bin_steps[-1]
        binned_data.append({
            "timestamp": bin_start_ts,
            "best_bid": last_step["bid_px"],
            "best_ask": last_step["ask_px"],
            "ofi": total_ofi,
        })

    if not binned_data:
        return pl.DataFrame()

    return pl.DataFrame(
        binned_data,
        schema={
            "timestamp": pl.Int64,
            "best_bid": pl.Float64,
            "best_ask": pl.Float64,
            "ofi": pl.Float64,
        }
    )

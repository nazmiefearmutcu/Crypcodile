import logging
import time

import polars as pl

from crocodile.core.analytics.liquidity_depth import band_columns, depth_within_bands
from crocodile.core.store.catalog import Catalog

log = logging.getLogger(__name__)


def calculate_block_liquidity_depth(catalog: Catalog, symbol: str) -> pl.DataFrame:
    """Order book depth (bid/ask size) within 1%, 2% and 5% of mid, per block.

    Args:
        catalog: A :class:`~crocodile.core.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.

    Returns:
        A Polars DataFrame containing columns:
        - block: Block number (Int64)
        - bid_depth_1pct: Cumulative bid size within -1% from mid-price (Float64)
        - ask_depth_1pct: Cumulative ask size within +1% from mid-price (Float64)
        - bid_depth_2pct: Cumulative bid size within -2% from mid-price (Float64)
        - ask_depth_2pct: Cumulative ask size within +2% from mid-price (Float64)
        - bid_depth_5pct: Cumulative bid size within -5% from mid-price (Float64)
        - ask_depth_5pct: Cumulative ask size within +5% from mid-price (Float64)
        Ordered by block ascending.

    The band sums are :func:`~crocodile.core.analytics.liquidity_depth.depth_within_bands`,
    which is where they moved when M6 gave the same question an equity caller: the ladder
    ``crocodile.equity.depth`` produces is a different book, but "how much size stands
    within *n* percent of the middle" is not a different question. The six literal band
    edges that used to be written out here (``* 0.99``, ``* 1.01``, ``* 0.98`` …) are now
    derived from one fraction per band, so the two sides of a band cannot drift apart.
    """
    try:
        catalog.refresh_views()
        # Scan book_snapshot for the symbol
        df = catalog.scan("book_snapshot", symbol, 0, int(time.time() * 1_000_000_000))
    except Exception:
        df = pl.DataFrame()

    empty_df = pl.DataFrame(
        schema={"block": pl.Int64, **{name: pl.Float64 for name in band_columns()}}
    )

    if df.is_empty():
        return empty_df

    # We need sequence_id for block identification
    if "sequence_id" not in df.columns:
        return empty_df

    df = df.filter(pl.col("sequence_id").is_not_null())
    if df.is_empty():
        return empty_df

    # Sort by local_ts and keep the latest snapshot for each block
    df = df.sort("local_ts").unique(subset=["sequence_id"], keep="last")

    rows: list[dict[str, float]] = []

    for row in df.iter_rows(named=True):
        seq_id = row["sequence_id"]
        raw_bids = row["bids"]
        raw_asks = row["asks"]

        # Parse bids and asks
        bids = []
        if raw_bids:
            for b in raw_bids:
                if isinstance(b, dict):
                    bids.append((float(b["price"]), float(b["amount"])))
                else:
                    bids.append((float(b[0]), float(b[1])))

        asks = []
        if raw_asks:
            for a in raw_asks:
                if isinstance(a, dict):
                    asks.append((float(a["price"]), float(a["amount"])))
                else:
                    asks.append((float(a[0]), float(a[1])))

        if not bids or not asks:
            continue

        mid_price = (bids[0][0] + asks[0][0]) / 2.0
        if mid_price <= 0.0:
            # A non-positive mid collapses every multiplicative band onto it, so the sums
            # would come back zero and read as a book with no near depth. Skipping the
            # snapshot says nothing about it instead of saying something false.
            continue

        rows.append({"block": seq_id, **depth_within_bands(bids, asks, mid_price)})

    if not rows:
        return empty_df

    return pl.DataFrame(rows, schema=empty_df.schema).sort("block")

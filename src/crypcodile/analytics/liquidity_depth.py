import logging
import time
import polars as pl
from crypcodile.store.catalog import Catalog

log = logging.getLogger(__name__)

def calculate_block_liquidity_depth(catalog: Catalog, symbol: str) -> pl.DataFrame:
    """Calculate order book depth (bid/ask sizes) at ±1%, ±2%, ±5% levels per block from historical records.

    Args:
        catalog: A :class:`~crypcodile.store.catalog.Catalog` instance.
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
    """
    try:
        catalog.refresh_views()
        # Scan book_snapshot for the symbol
        df = catalog.scan("book_snapshot", symbol, 0, int(time.time() * 1_000_000_000))
    except Exception:
        df = pl.DataFrame()

    empty_df = pl.DataFrame(
        schema={
            "block": pl.Int64,
            "bid_depth_1pct": pl.Float64,
            "ask_depth_1pct": pl.Float64,
            "bid_depth_2pct": pl.Float64,
            "ask_depth_2pct": pl.Float64,
            "bid_depth_5pct": pl.Float64,
            "ask_depth_5pct": pl.Float64,
        }
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

    blocks = []
    b_1, a_1 = [], []
    b_2, a_2 = [], []
    b_5, a_5 = [], []

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

        bid_1 = sum(b[1] for b in bids if b[0] >= mid_price * 0.99)
        ask_1 = sum(a[1] for a in asks if a[0] <= mid_price * 1.01)

        bid_2 = sum(b[1] for b in bids if b[0] >= mid_price * 0.98)
        ask_2 = sum(a[1] for a in asks if a[0] <= mid_price * 1.02)

        bid_5 = sum(b[1] for b in bids if b[0] >= mid_price * 0.95)
        ask_5 = sum(a[1] for a in asks if a[0] <= mid_price * 1.05)

        blocks.append(seq_id)
        b_1.append(bid_1)
        a_1.append(ask_1)
        b_2.append(bid_2)
        a_2.append(ask_2)
        b_5.append(bid_5)
        a_5.append(ask_5)

    if not blocks:
        return empty_df

    res_df = pl.DataFrame({
        "block": blocks,
        "bid_depth_1pct": b_1,
        "ask_depth_1pct": a_1,
        "bid_depth_2pct": b_2,
        "ask_depth_2pct": a_2,
        "bid_depth_5pct": b_5,
        "ask_depth_5pct": a_5,
    }).sort("block")

    return res_df

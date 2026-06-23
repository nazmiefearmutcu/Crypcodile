import logging
import time
import polars as pl
from crypcodile.store.catalog import Catalog
from crypcodile.schema.records import Record

log = logging.getLogger(__name__)

def calculate_peg_deviation(catalog: Catalog, symbol: str, threshold: float = 0.01) -> pl.DataFrame:
    """Query the catalog to detect peg deviations for stablecoin pairs from $1.00 target.

    Args:
        catalog: A :class:`~crypcodile.store.catalog.Catalog` instance.
        symbol: Canonical symbol string of the stablecoin pair (e.g., 'base_onchain:USDC-USDbC').
        threshold: Deviation percentage threshold (e.g., 0.01 for 1%).

    Returns:
        A Polars DataFrame containing columns:
        - timestamp: Local timestamp (Int64, nanoseconds)
        - symbol: Canonical symbol string (Utf8)
        - price: The mid-price (Float64)
        - deviation_pct: Absolute deviation from 1.0 (Float64)
        - is_alert_triggered: Boolean flag indicating if deviation >= threshold
    """
    try:
        catalog.refresh_views()
        # Scan for book_ticker records
        df = catalog.scan("book_ticker", symbol, 0, int(time.time() * 1_000_000_000))
    except Exception:
        df = pl.DataFrame()

    if df.is_empty():
        # Fallback to book_snapshot if book_ticker is empty
        try:
            df = catalog.scan("book_snapshot", symbol, 0, int(time.time() * 1_000_000_000))
            if not df.is_empty():
                # Extract first level of bids and asks
                df = df.with_columns([
                    pl.col("bids").list.get(0).struct.field("price").alias("bid_px"),
                    pl.col("asks").list.get(0).struct.field("price").alias("ask_px")
                ])
        except Exception:
            df = pl.DataFrame()

    if df.is_empty():
        return pl.DataFrame(
            schema={
                "timestamp": pl.Int64,
                "symbol": pl.Utf8,
                "price": pl.Float64,
                "deviation_pct": pl.Float64,
                "is_alert_triggered": pl.Boolean,
            }
        )

    # Calculate mid-price, deviation, and alert flag
    df = df.with_columns(
        ((pl.col("bid_px") + pl.col("ask_px")) / 2.0).alias("price")
    )
    df = df.with_columns(
        (pl.col("price") - 1.0).abs().alias("deviation_pct")
    )
    df = df.with_columns(
        (pl.col("deviation_pct") >= threshold).alias("is_alert_triggered")
    )

    result = df.select([
        pl.col("local_ts").alias("timestamp"),
        pl.col("symbol"),
        pl.col("price"),
        pl.col("deviation_pct"),
        pl.col("is_alert_triggered")
    ]).sort("timestamp")

    return result

def check_live_peg_deviation(record: Record, threshold: float = 0.01) -> bool:
    """Inspects a record in the live ingest pipeline to log warnings if peg is breached."""
    if not hasattr(record, "symbol"):
        return False
    
    symbol = record.symbol
    # Match symbols containing USDC or USDbC
    if "USDC" in symbol or "USDbC" in symbol:
        price = None
        # Extract mid-price from book_ticker or book_snapshot
        if hasattr(record, "bid_px") and hasattr(record, "ask_px"):
            price = (record.bid_px + record.ask_px) / 2.0
        elif hasattr(record, "bids") and hasattr(record, "asks") and record.bids and record.asks:
            price = (record.bids[0][0] + record.asks[0][0]) / 2.0

        if price is not None:
            deviation = abs(price - 1.0)
            if deviation >= threshold:
                log.warning(
                    f"STABLECOIN PEG DEVIATION ALERT: {symbol} price is {price:.6f} "
                    f"(deviation: {deviation:.4f} >= threshold: {threshold:.4f})"
                )
                return True
    return False

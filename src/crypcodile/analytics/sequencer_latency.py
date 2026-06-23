import logging
import polars as pl
from crypcodile.store.catalog import Catalog

log = logging.getLogger(__name__)

def calculate_sequencer_latency(catalog: Catalog, exchange: str = "base_onchain") -> pl.DataFrame:
    """Measure sequencer performance, block production intervals, and ingestion delay.

    Args:
        catalog: A :class:`~crypcodile.store.catalog.Catalog` instance.
        exchange: The exchange name (e.g., 'base_onchain').

    Returns:
        A Polars DataFrame containing summary statistics:
        - metric: Metric name ('production_interval' or 'ingestion_delay') (Utf8)
        - avg_seconds: Average value in seconds (Float64)
        - max_seconds: Maximum value in seconds (Float64)
        - std_seconds: Standard deviation in seconds (Float64)
    """
    empty_df = pl.DataFrame(
        schema={
            "metric": pl.Utf8,
            "avg_seconds": pl.Float64,
            "max_seconds": pl.Float64,
            "std_seconds": pl.Float64,
        }
    )

    try:
        catalog.refresh_views()
        # Query exchange_ts and local_ts from book_ticker table
        df = catalog.query(
            f"SELECT local_ts, exchange_ts FROM book_ticker "
            f"WHERE exchange = '{exchange}' AND exchange_ts IS NOT NULL ORDER BY exchange_ts"
        )
    except Exception:
        df = pl.DataFrame()

    if df.is_empty() or len(df) < 2:
        try:
            # Fallback to trade table
            df = catalog.query(
                f"SELECT local_ts, exchange_ts FROM trade "
                f"WHERE exchange = '{exchange}' AND exchange_ts IS NOT NULL ORDER BY exchange_ts"
            )
        except Exception:
            df = pl.DataFrame()

    if df.is_empty() or len(df) < 2:
        return empty_df

    # Calculate production interval (diff of exchange_ts) and ingestion delay (local_ts - exchange_ts)
    # Convert nanoseconds to seconds (divide by 1e9)
    df = df.with_columns([
        (pl.col("exchange_ts").diff() / 1e9).alias("prod_int_sec"),
        ((pl.col("local_ts") - pl.col("exchange_ts")) / 1e9).alias("ingest_delay_sec")
    ])

    # Filter out null (first row prod_int_sec is null)
    df_clean = df.filter(pl.col("prod_int_sec").is_not_null())

    if df_clean.is_empty():
        return empty_df

    # Compute stats for production interval
    prod_avg = df_clean["prod_int_sec"].mean()
    prod_max = df_clean["prod_int_sec"].max()
    prod_std = df_clean["prod_int_sec"].std()

    # Compute stats for ingestion delay
    ingest_avg = df_clean["ingest_delay_sec"].mean()
    ingest_max = df_clean["ingest_delay_sec"].max()
    ingest_std = df_clean["ingest_delay_sec"].std()

    # Fallback std to 0.0 if None
    prod_std = prod_std if prod_std is not None else 0.0
    ingest_std = ingest_std if ingest_std is not None else 0.0

    summary_df = pl.DataFrame({
        "metric": ["production_interval", "ingestion_delay"],
        "avg_seconds": [float(prod_avg), float(ingest_avg)],
        "max_seconds": [float(prod_max), float(ingest_max)],
        "std_seconds": [float(prod_std), float(ingest_std)]
    })

    return summary_df

"""Whale & Liquidation Alert Tracker (Task R3).

Queries trade and liquidation tables for a symbol and filters events exceeding a USD threshold.
"""

from __future__ import annotations

import polars as pl

from crypcodile.store.catalog import Catalog

__all__ = ["track_whale_alerts"]


def track_whale_alerts(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    min_usd: float,
) -> pl.DataFrame:
    """Query trade and liquidation events for a symbol exceeding a USD value threshold.

    Args:
        catalog: A :class:`~crypcodile.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.
        start_ns: Inclusive lower bound on local_ts (nanoseconds UTC).
        end_ns: Inclusive upper bound on local_ts (nanoseconds UTC).
        min_usd: Minimum USD value (price * amount) to qualify as a whale alert.

    Returns:
        A Polars DataFrame containing columns:
        - timestamp: Event local timestamp (Int64, nanoseconds UTC)
        - event_type: Event type ("Trade" or "Liquidation") (Utf8)
        - price: Execution price (Float64)
        - amount: Base asset amount (Float64)
        - usd_value: USD value (price * amount) (Float64)
        - side: Execution side ("buy" or "sell") (Utf8)
        Ordered by timestamp ascending.
    """
    if min_usd < 0:
        raise ValueError("min_usd must be non-negative.")

    try:
        catalog.refresh_views()
        trade_df = catalog.scan("trade", symbol, start_ns, end_ns)
    except Exception:
        trade_df = pl.DataFrame()

    try:
        catalog.refresh_views()
        liq_df = catalog.scan("liquidation", symbol, start_ns, end_ns)
    except Exception:
        liq_df = pl.DataFrame()

    trades_selected = pl.DataFrame()
    if not trade_df.is_empty():
        trade_df = trade_df.with_columns(
            (pl.col("price") * pl.col("amount")).alias("usd_value")
        )
        trade_df = trade_df.filter(pl.col("usd_value") >= min_usd)
        if not trade_df.is_empty():
            trades_selected = trade_df.select([
                pl.col("local_ts").alias("timestamp"),
                pl.lit("Trade").alias("event_type"),
                pl.col("price").cast(pl.Float64),
                pl.col("amount").cast(pl.Float64),
                pl.col("usd_value").cast(pl.Float64),
                pl.col("side").cast(pl.Utf8),
            ])

    liqs_selected = pl.DataFrame()
    if not liq_df.is_empty():
        liq_df = liq_df.with_columns(
            (pl.col("price") * pl.col("amount")).alias("usd_value")
        )
        liq_df = liq_df.filter(pl.col("usd_value") >= min_usd)
        if not liq_df.is_empty():
            liqs_selected = liq_df.select([
                pl.col("local_ts").alias("timestamp"),
                pl.lit("Liquidation").alias("event_type"),
                pl.col("price").cast(pl.Float64),
                pl.col("amount").cast(pl.Float64),
                pl.col("usd_value").cast(pl.Float64),
                pl.col("side").cast(pl.Utf8),
            ])

    frames = []
    if not trades_selected.is_empty():
        frames.append(trades_selected)
    if not liqs_selected.is_empty():
        frames.append(liqs_selected)

    if not frames:
        return pl.DataFrame(
            schema={
                "timestamp": pl.Int64,
                "event_type": pl.Utf8,
                "price": pl.Float64,
                "amount": pl.Float64,
                "usd_value": pl.Float64,
                "side": pl.Utf8,
            }
        )

    combined = pl.concat(frames).sort("timestamp")
    return combined

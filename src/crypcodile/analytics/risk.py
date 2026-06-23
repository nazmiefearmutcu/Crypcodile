from __future__ import annotations

import math
import time

import polars as pl

from crypcodile.store.catalog import Catalog


def calculate_chaos_score(
    volatility: float,
    stablecoin_deviation: float,
    orderbook_imbalance: float,
    sequencer_delay: float,
) -> float:
    """Combine volatility, stablecoin deviation, orderbook imbalance,

    and sequencer delay into a normalized [0, 100] Chaos Score.

    Each metric is normalized using a soft-thresholding function to [0, 1]
    before averaging.
    """
    if math.isnan(volatility):
        vol_norm = 0.0
    else:
        vol = abs(volatility)
        vol_norm = vol / (vol + 0.1) if vol > 0 else 0.0

    if math.isnan(stablecoin_deviation):
        dev_norm = 0.0
    else:
        dev = abs(stablecoin_deviation)
        dev_norm = dev / (dev + 0.01) if dev > 0 else 0.0

    if math.isnan(orderbook_imbalance):
        imb_norm = 1.0
    else:
        # Normalize imbalance (orderbook imbalance is already bounded -1 to 1)
        imb = abs(orderbook_imbalance)
        imb_norm = 1.0 if math.isnan(imb) else min(1.0, imb)

    if math.isnan(sequencer_delay):
        delay_norm = 0.0
    else:
        delay = abs(sequencer_delay)
        delay_norm = delay / (delay + 5.0) if delay > 0 else 0.0

    # Average normalized values and scale to [0, 100]
    score = (vol_norm + dev_norm + imb_norm + delay_norm) / 4.0 * 100.0
    return float(score)


def calculate_dynamic_chaos_score(
    catalog: Catalog | None = None,
    symbol: str | None = None,
    volatility: float | None = None,
    stablecoin_deviation: float | None = None,
    orderbook_imbalance: float | None = None,
    sequencer_delay: float | None = None,
) -> float:
    """Calculate the Chaos Score dynamically by either fetching current metrics

    from the Catalog or using the provided parameters.
    """
    # 1. Resolve symbol if catalog is present and symbol is not provided
    if catalog is not None and symbol is None:
        try:
            df_sym = catalog.query("SELECT symbol FROM book_ticker LIMIT 1")
            if not df_sym.is_empty():
                symbol = df_sym["symbol"][0]
            else:
                df_sym = catalog.query("SELECT symbol FROM trade LIMIT 1")
                if not df_sym.is_empty():
                    symbol = df_sym["symbol"][0]
        except Exception:
            pass

    # 2. Fetch/calculate volatility if not provided
    if volatility is None:
        volatility = 0.0
        if catalog is not None and symbol is not None:
            try:
                # Find latest timestamp
                latest_ts_df = catalog.query(
                    f"SELECT MAX(local_ts) FROM book_ticker WHERE symbol = '{symbol}'"
                )
                latest_ts = latest_ts_df.item() if not latest_ts_df.is_empty() else None
                if latest_ts is None:
                    latest_ts = int(time.time() * 1_000_000_000)
                start_ns = latest_ts - 3600 * 1_000_000_000  # 1 hour window
                df_ticker = catalog.scan("book_ticker", symbol, start_ns, latest_ts)
                if not df_ticker.is_empty() and len(df_ticker) >= 2:
                    if "bid_px" in df_ticker.columns and "ask_px" in df_ticker.columns:
                        df_ticker = df_ticker.with_columns(
                            ((pl.col("bid_px") + pl.col("ask_px")) / 2.0).alias("mid_price")
                        )
                        df_ticker = df_ticker.with_columns(
                            (pl.col("mid_price") / pl.col("mid_price").shift(1)).log().alias("log_return")
                        )
                        vol_val = df_ticker.select(pl.col("log_return").std()).item()
                        if vol_val is not None:
                            volatility = float(vol_val)
            except Exception:
                pass

    # 3. Fetch/calculate stablecoin deviation if not provided
    if stablecoin_deviation is None:
        stablecoin_deviation = 0.0
        if catalog is not None:
            try:
                # Resolve stablecoin pair
                stablecoin_sym = None
                if symbol is not None and any(
                    x in symbol.upper() for x in ["USDC", "USDT", "USDBC", "USD"]
                ):
                    stablecoin_sym = symbol
                else:
                    stablecoin_sym = "base_onchain:USDC-USDbC"

                df_ticker = catalog.query(
                    f"SELECT bid_px, ask_px FROM book_ticker "
                    f"WHERE symbol = '{stablecoin_sym}' "
                    f"ORDER BY local_ts DESC LIMIT 1"
                )
                if not df_ticker.is_empty():
                    mid = (df_ticker["bid_px"][0] + df_ticker["ask_px"][0]) / 2.0
                    stablecoin_deviation = abs(mid - 1.0)
            except Exception:
                pass

    # 4. Fetch/calculate orderbook imbalance if not provided
    if orderbook_imbalance is None:
        orderbook_imbalance = 0.0
        if catalog is not None and symbol is not None:
            try:
                df_snap = catalog.query(
                    f"SELECT bids, asks FROM book_snapshot "
                    f"WHERE symbol = '{symbol}' "
                    f"ORDER BY local_ts DESC LIMIT 1"
                )
                if not df_snap.is_empty():
                    from crypcodile.store.rows import _coerce_levels_from_row

                    bids = _coerce_levels_from_row(df_snap["bids"][0])
                    asks = _coerce_levels_from_row(df_snap["asks"][0])
                    if bids and asks:
                        bid_sz = float(bids[0][1])
                        ask_sz = float(asks[0][1])
                        if bid_sz + ask_sz > 0:
                            orderbook_imbalance = (bid_sz - ask_sz) / (bid_sz + ask_sz)
            except Exception:
                pass

    # 5. Fetch/calculate sequencer delay if not provided
    if sequencer_delay is None:
        sequencer_delay = 0.0
        if catalog is not None and symbol is not None:
            try:
                df_delay = catalog.query(
                    f"SELECT local_ts, exchange_ts FROM book_ticker "
                    f"WHERE symbol = '{symbol}' AND exchange_ts IS NOT NULL "
                    f"ORDER BY local_ts DESC LIMIT 1"
                )
                if not df_delay.is_empty():
                    sequencer_delay = (
                        df_delay["local_ts"][0] - df_delay["exchange_ts"][0]
                    ) / 1e9
            except Exception:
                pass

    return calculate_chaos_score(
        volatility=volatility,
        stablecoin_deviation=stablecoin_deviation,
        orderbook_imbalance=orderbook_imbalance,
        sequencer_delay=sequencer_delay,
    )


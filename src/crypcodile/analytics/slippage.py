"""Execution Slippage Estimator (Task R1).

Queries the latest book_snapshot for a symbol and calculates the expected 
execution price (VWAP) and slippage for a given base asset size.
"""

from __future__ import annotations

import polars as pl

from crypcodile.store.catalog import Catalog
from crypcodile.store.rows import _coerce_levels_from_row

__all__ = ["estimate_slippage", "parse_base_quote", "parse_size_input"]


def parse_base_quote(symbol: str) -> tuple[str, str]:
    """Parse a symbol (e.g., 'binance-spot:BTCUSDT' or 'AERO-USDC') into base and quote assets."""
    # Remove exchange prefix if present
    raw = symbol.split(":")[-1] if ":" in symbol else symbol
    
    # Check for explicit separators
    for sep in ("-", "_", "/"):
        if sep in raw:
            parts = raw.split(sep)
            if len(parts) >= 2:
                return parts[0].upper(), parts[1].upper()
                
    # If no separator, try to match common quote assets at the end
    common_quotes = [
        "USDT", "USDC", "USDbC", "USD", "EUR", "TRY", "GBP", "JPY", 
        "BTC", "ETH", "BNB", "DAI", "BUSD", "TUSD", "FDUSD", "PLN", "RUB"
    ]
    raw_upper = raw.upper()
    for quote in common_quotes:
        if raw_upper.endswith(quote) and len(raw_upper) > len(quote):
            base = raw_upper[:-len(quote)]
            return base, quote
            
    # Fallback: if length > 4 split at length - 4, else split in half
    if len(raw_upper) > 4:
        return raw_upper[:-4], raw_upper[-4:]
    else:
        mid = len(raw_upper) // 2
        return raw_upper[:mid], raw_upper[mid:]


def parse_size_input(size_input: str) -> tuple[float, str | None]:
    """Parse string size input like '100 USDT' or '100USDT' or '100'."""
    cleaned = size_input.strip()
    parts = cleaned.split()
    if len(parts) == 1:
        s = parts[0]
        import re
        match = re.match(r"^([0-9\.]+)\s*([a-zA-Z]+)?$", s)
        if match:
            val_str, unit = match.groups()
            return float(val_str), unit.upper() if unit else None
        else:
            return float(s), None
    elif len(parts) >= 2:
        val_str = parts[0]
        unit_str = parts[1]
        return float(val_str), unit_str.upper()
    else:
        raise ValueError(f"Invalid size input: {size_input}")


def estimate_slippage(
    catalog: Catalog,
    symbol: str,
    side: str,
    size: float | str,
    size_unit: str | None = None,
) -> pl.DataFrame:
    """Calculate the expected execution price and slippage for a given size.

    Args:
        catalog: A :class:`~crypcodile.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.
        side: "buy" or "sell".
        size: The execution size (base or quote asset).
        size_unit: The unit/asset of the size (e.g. 'BTC' or 'USDT').

    Returns:
        A Polars DataFrame containing the estimation details.
    """
    # Parse size if it's a string
    if isinstance(size, str):
        val, unit = parse_size_input(size)
        size_val = val
        if unit:
            size_unit = unit
    else:
        size_val = float(size)

    if size_val <= 0:
        raise ValueError("Size must be greater than zero.")

    side_lower = side.lower().strip()
    if side_lower in ("b", "buy"):
        side_lower = "buy"
    elif side_lower in ("s", "sell"):
        side_lower = "sell"
    else:
        raise ValueError(f"Invalid side '{side}'. Must be 'buy' or 'sell'.")

    base_asset, quote_asset = parse_base_quote(symbol)

    is_quote = False
    if size_unit:
        unit_upper = size_unit.upper()
        if unit_upper == quote_asset.upper():
            is_quote = True
        elif unit_upper == base_asset.upper():
            is_quote = False

    # Query the latest book snapshot for the symbol
    try:
        catalog.refresh_views()
        df = catalog.connection.execute(
            "SELECT bids, asks FROM book_snapshot WHERE symbol = ? ORDER BY local_ts DESC LIMIT 1",
            [symbol]
        ).pl()
    except Exception:
        df = pl.DataFrame()

    if df.is_empty():
        raise ValueError(f"No book snapshots found for symbol '{symbol}'.")

    row = df.to_dicts()[0]
    bids = _coerce_levels_from_row(row.get("bids"))
    asks = _coerce_levels_from_row(row.get("asks"))

    levels = bids if side_lower == "sell" else asks

    if not levels:
        raise ValueError(f"Order book for symbol '{symbol}' has no levels on the {side} side.")

    best_price = levels[0][0]

    if is_quote:
        filled_quote = 0.0
        filled_base = 0.0
        for price, amount in levels:
            if filled_quote >= size_val:
                break
            level_quote_avail = amount * price
            to_fill_quote = min(level_quote_avail, size_val - filled_quote)
            base_qty = to_fill_quote / price
            filled_base += base_qty
            filled_quote += to_fill_quote

        if filled_quote < size_val:
            raise ValueError(
                f"Requested size {size_val} {quote_asset} exceeds total order book depth ({filled_quote:.6f} {quote_asset}) "
                f"for symbol '{symbol}' on the {side} side."
            )
        expected_price = size_val / filled_base
        final_size = size_val
        final_unit = quote_asset
    else:
        filled = 0.0
        total_cost = 0.0
        for price, amount in levels:
            if filled >= size_val:
                break
            to_fill = min(amount, size_val - filled)
            total_cost += to_fill * price
            filled += to_fill

        if filled < size_val:
            raise ValueError(
                f"Requested size {size_val} {base_asset} exceeds total order book depth ({filled:.6f} {base_asset}) "
                f"for symbol '{symbol}' on the {side} side."
            )
        expected_price = total_cost / size_val
        final_size = size_val
        final_unit = base_asset

    if side_lower == "buy":
        slippage_usd = expected_price - best_price
    else:
        slippage_usd = best_price - expected_price

    slippage_pct = (slippage_usd / best_price) * 100.0 if best_price > 0 else 0.0

    return pl.DataFrame(
        {
            "symbol": [symbol],
            "side": [side_lower],
            "size": [final_size],
            "size_unit": [final_unit],
            "best_price": [best_price],
            "expected_price": [expected_price],
            "slippage_usd": [slippage_usd],
            "slippage_pct": [slippage_pct],
        }
    )

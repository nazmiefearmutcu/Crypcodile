from __future__ import annotations

import polars as pl


def gas_to_volatility_correlation(
    gas_df: pl.DataFrame,
    vol_df: pl.DataFrame,
) -> dict[str, float]:
    """Calculate Pearson and Spearman correlation between gas costs and volatility.

    Args:
        gas_df: Polars DataFrame containing gas parameters. Must have a
                `local_ts` column and a numeric gas column (e.g. `gas_price`,
                `gas_cost`).
        vol_df: Polars DataFrame containing volatility metrics. Must have a
                `local_ts` column and a numeric volatility column (e.g.
                `volatility`, `vol`).

    Returns:
        A dictionary with keys 'pearson' and 'spearman' mapping to the
        respective correlation coefficients (float). Returns NaN if undefined.
    """
    if gas_df.is_empty() or vol_df.is_empty():
        return {"pearson": float("nan"), "spearman": float("nan")}

    # Detect gas column name
    gas_cols = [c for c in gas_df.columns if "gas" in c.lower() or "cost" in c.lower()]
    gas_col = gas_cols[0] if gas_cols else [c for c in gas_df.columns if c != "local_ts"][0]

    # Detect volatility column name
    vol_cols = [c for c in vol_df.columns if "vol" in c.lower()]
    vol_col = vol_cols[0] if vol_cols else [c for c in vol_df.columns if c != "local_ts"][0]

    # Align dataframes on local_ts (sorted inner join or ASOF join)
    # Using sorted inner join as default
    joined = gas_df.join(vol_df, on="local_ts", how="inner")
    
    # Drop rows with null values in the target columns
    joined = joined.drop_nulls(subset=[gas_col, vol_col])

    if len(joined) < 2:
        return {"pearson": float("nan"), "spearman": float("nan")}

    try:
        # Compute Pearson correlation
        pearson_val = joined.select(pl.corr(gas_col, vol_col, method="pearson")).item()
        # Compute Spearman correlation
        spearman_val = joined.select(pl.corr(gas_col, vol_col, method="spearman")).item()
        
        return {
            "pearson": float(pearson_val) if pearson_val is not None else float("nan"),
            "spearman": float(spearman_val) if spearman_val is not None else float("nan"),
        }
    except Exception:
        return {"pearson": float("nan"), "spearman": float("nan")}

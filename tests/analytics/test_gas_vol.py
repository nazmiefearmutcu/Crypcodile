from __future__ import annotations

import math

import polars as pl

from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation


def test_gas_to_volatility_correlation_perfect() -> None:
    gas_df = pl.DataFrame({
        "local_ts": [1, 2, 3, 4, 5],
        "gas_price": [10.0, 20.0, 30.0, 40.0, 50.0],
    })
    vol_df = pl.DataFrame({
        "local_ts": [1, 2, 3, 4, 5],
        "volatility": [0.1, 0.2, 0.3, 0.4, 0.5],
    })

    res = gas_to_volatility_correlation(gas_df, vol_df)
    assert abs(res["pearson"] - 1.0) < 1e-7
    assert abs(res["spearman"] - 1.0) < 1e-7


def test_gas_to_volatility_correlation_empty() -> None:
    gas_df = pl.DataFrame(schema={"local_ts": pl.Int64, "gas_price": pl.Float64})
    vol_df = pl.DataFrame(schema={"local_ts": pl.Int64, "volatility": pl.Float64})

    res = gas_to_volatility_correlation(gas_df, vol_df)
    assert math.isnan(res["pearson"])
    assert math.isnan(res["spearman"])

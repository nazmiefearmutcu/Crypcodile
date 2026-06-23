from __future__ import annotations

import math
import pytest
import polars as pl

from crypcodile.analytics.risk import calculate_chaos_score
from crypcodile.analytics.lending_stress import lending_stress_test
from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation
from crypcodile.exchanges.gmx_synthetix.position_tracker import PerpPositionTracker
from crypcodile.analytics.funding_prediction import XGBoostFundingPredictor


def test_chaos_score_extreme_inputs() -> None:
    # 1. Test infinite inputs
    # volatility = inf -> vol_norm = inf / (inf + 0.1) = nan
    score_inf = calculate_chaos_score(float("inf"), 0.0, 0.0, 0.0)
    # The current code returns NaN when any input is infinite because inf / (inf + constant) is nan
    assert math.isnan(score_inf), f"Expected nan for infinite volatility, got {score_inf}"

    # 2. Test nan inputs
    # volatility = nan -> vol > 0 is False -> returns 0.0
    score_nan = calculate_chaos_score(float("nan"), 0.0, 0.0, 0.0)
    assert score_nan == 0.0, f"Expected 0.0 for nan volatility due to conditional check, got {score_nan}"

    # 3. Test orderbook imbalance with nan
    # min(1.0, nan) in Python returns 1.0 (due to comparison logic with nan)
    # So the normalized imbalance becomes 1.0, giving an overall score of 25.0
    score_imb_nan = calculate_chaos_score(0.0, 0.0, float("nan"), 0.0)
    assert score_imb_nan == 25.0


def test_lending_stress_haircut_discontinuity() -> None:
    # Test the logic:
    # haircut_fraction = haircut_pct / 100.0 if abs(haircut_pct) > 1.0 else haircut_pct
    #
    # Case 1: haircut_pct = 1.0 (fractional)
    # abs(1.0) > 1.0 is False, so haircut_fraction = 1.0 (100% drop)
    res_1 = lending_stress_test(
        collateral_usd=100.0,
        debt_usd=50.0,
        liquidation_threshold=0.8,
        haircut_pct=1.0,
    )
    # Simulated collateral drops by 100%, so simulated collateral is 0, health factor is 0.0
    assert res_1["simulated_health_factor"] == 0.0

    # Case 2: haircut_pct = 1.01 (percentage divided by 100)
    # abs(1.01) > 1.0 is True, so haircut_fraction = 0.0101 (1.01% drop)
    res_2 = lending_stress_test(
        collateral_usd=100.0,
        debt_usd=50.0,
        liquidation_threshold=0.8,
        haircut_pct=1.01,
    )
    # Simulated collateral drops by 1.01%, so simulated collateral is 98.99, health factor is 1.58384
    assert res_2["simulated_health_factor"] > 1.5


def test_gas_vol_correlation_missing_columns() -> None:
    # gas_df only has local_ts
    gas_df = pl.DataFrame({"local_ts": [1, 2, 3]})
    vol_df = pl.DataFrame({"local_ts": [1, 2, 3], "volatility": [0.1, 0.2, 0.3]})

    # The current code:
    # gas_col = gas_cols[0] if gas_cols else [c for c in gas_df.columns if c != "local_ts"][0]
    # will evaluate [][0] and raise IndexError.
    with pytest.raises(IndexError):
        gas_to_volatility_correlation(gas_df, vol_df)


def test_perp_position_tracker_invalid_strings() -> None:
    tracker = PerpPositionTracker()
    # If the event contains non-numeric strings that cannot be cast to float,
    # it raises ValueError
    with pytest.raises(ValueError):
        tracker.process_event({
            "event": "PositionIncrease",
            "symbol": "BTC-USD",
            "size_delta_usd": "invalid_number",
        })


def test_funding_predictor_malformed_dict_fallback() -> None:
    predictor = XGBoostFundingPredictor(window_size=3)
    # Pass a dict where recent_funding_rates contains unparseable values
    # It should catch the ValueError and fall back to fallback_mean (0.0)
    pred = predictor.predict({
        "recent_funding_rates": ["invalid", None],
    })
    assert pred == 0.0

from __future__ import annotations

import math
import pytest
import polars as pl
from unittest.mock import MagicMock, AsyncMock
from web3 import AsyncWeb3

from crypcodile.exchanges.gmx_synthetix.position_tracker import PerpPositionTracker
from crypcodile.analytics.oi_aggregator import aggregate_open_interest
from crypcodile.analytics.basis import perp_basis, spot_future_basis, spot_perp_basis
from crypcodile.analytics.funding_prediction import XGBoostFundingPredictor
from crypcodile.analytics.risk import calculate_chaos_score
from crypcodile.analytics.whale_transfers import WhaleTransferTracker
from crypcodile.analytics.smart_money import SmartMoneyTracker
from crypcodile.analytics.lending_stress import lending_stress_test
from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation
from crypcodile.store.catalog import Catalog


# ---------------------------------------------------------------------------
# 1. PerpPositionTracker Stress Tests
# ---------------------------------------------------------------------------

def test_perp_position_tracker_none_values() -> None:
    tracker = PerpPositionTracker()
    # If size_delta_usd is None, it should raise TypeError/ValueError or be handled
    with pytest.raises((TypeError, ValueError)):
        tracker.process_event({
            "event": "IncreasePosition",
            "symbol": "BTC",
            "size_delta_usd": None,
            "collateral_delta_usd": 100.0,
            "price": 50000.0,
        })

def test_perp_position_tracker_invalid_strings() -> None:
    tracker = PerpPositionTracker()
    with pytest.raises((TypeError, ValueError)):
        tracker.process_event({
            "event": "IncreasePosition",
            "symbol": "BTC",
            "size_delta_usd": "invalid_number",
            "collateral_delta_usd": 100.0,
            "price": 50000.0,
        })

def test_perp_position_tracker_negative_leverage_and_margin() -> None:
    tracker = PerpPositionTracker()
    # Negative margin provided in event
    tracker.process_event({
        "event": "IncreasePosition",
        "symbol": "BTC",
        "size_delta_usd": 1000.0,
        "collateral_delta_usd": -100.0,
        "price": 50000.0,
        "margin": -50.0,
    })
    pos = tracker.get_position("BTC")
    assert pos is not None
    # If margin is negative, tracker sets leverage to 0.0
    assert pos["leverage"] == 0.0
    assert pos["margin"] == -50.0

def test_perp_position_tracker_overflow_leverage() -> None:
    tracker = PerpPositionTracker()
    # Extremely small positive margin causes leverage to overflow / become huge
    tracker.process_event({
        "event": "IncreasePosition",
        "symbol": "BTC",
        "size_delta_usd": 1e300,
        "collateral_delta_usd": 1e-300,
        "price": 50000.0,
    })
    pos = tracker.get_position("BTC")
    assert pos is not None
    # Leverage overflows to infinity because of division by extremely small float
    assert pos["leverage"] == float("inf")


# ---------------------------------------------------------------------------
# 2. Open Interest Aggregator Stress Tests
# ---------------------------------------------------------------------------

def test_oi_aggregator_empty_symbols_list() -> None:
    catalog = MagicMock(spec=Catalog)
    # Return a dummy dataframe with correct columns but empty symbols
    mock_df = pl.DataFrame({
        "local_ts": [1700000000],
        "exchange": ["binance"],
        "symbol": ["binance:BTCUSDT"],
        "open_interest": [100.0],
    })
    catalog.query.return_value = mock_df
    
    # Empty symbols list []
    res = aggregate_open_interest(catalog, [], 1700000000, 1700000000)
    # When symbols list is empty, it doesn't filter, so it should aggregate everything
    assert len(res) == 1
    assert res.row(0, named=True)["total_oi"] == 100.0

def test_oi_aggregator_missing_columns() -> None:
    catalog = MagicMock(spec=Catalog)
    # Missing local_ts / exchange columns
    mock_df = pl.DataFrame({
        "symbol": ["binance:BTCUSDT"],
        "open_interest": [100.0],
    })
    catalog.query.return_value = mock_df
    # Should fail or raise ColumnNotFoundError when trying to access missing columns
    import polars.exceptions as ple
    with pytest.raises(ple.ColumnNotFoundError):
        aggregate_open_interest(catalog, ["BTC"], 1700000000, 1700000000)

def test_oi_aggregator_null_timestamps_sorting_crash() -> None:
    catalog = MagicMock(spec=Catalog)
    # Contains None in local_ts -> sorting raw_df["local_ts"].unique() raises TypeError
    mock_df = pl.DataFrame({
        "local_ts": [1700000000, None],
        "exchange": ["binance", "okx"],
        "symbol": ["binance:BTCUSDT", "okx:BTC-USDT"],
        "open_interest": [100.0, 50.0],
    })
    catalog.query.return_value = mock_df
    with pytest.raises(TypeError):
        aggregate_open_interest(catalog, ["BTC"], 1700000000, 1700000000)


# ---------------------------------------------------------------------------
# 3. Basis Analytics Stress Tests
# ---------------------------------------------------------------------------

def test_perp_basis_nan_and_inf_prices() -> None:
    catalog = MagicMock(spec=Catalog)
    # mark_price is NaN or inf, index_price is valid
    mock_df = pl.DataFrame({
        "local_ts": [1700000000, 1700000001],
        "mark_price": [float("nan"), float("inf")],
        "index_price": [100.0, 100.0],
    })
    catalog.scan.return_value = mock_df
    res = perp_basis(catalog, "deribit:BTC-PERPETUAL", 1700000000, 1700000001)
    # If NaN / inf are not filtered, they will pass the filters since NaN > 0.0 (sometimes) and inf > 0.0
    # Let's see what the returned values are
    assert len(res) == 2
    row0 = res.row(0, named=True)
    row1 = res.row(1, named=True)
    assert math.isnan(row0["basis"])
    assert math.isinf(row1["basis"])

def test_spot_future_basis_near_zero_expiry() -> None:
    catalog = MagicMock(spec=Catalog)
    future_df = pl.DataFrame({"local_ts": [1700000000], "price": [101.0]})
    spot_df = pl.DataFrame({"local_ts": [1700000000], "price": [100.0]})
    
    # We mock catalog.scan to return future_df on first call, spot_df on second call
    catalog.scan.side_effect = [future_df, spot_df]
    
    # Mock DuckDB connection executing the ASOF query
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.pl.return_value = pl.DataFrame({
        "local_ts": [1700000000],
        "future_price": [101.0],
        "spot_price": [100.0],
        "basis": [1.0],
        "basis_pct": [0.01],
    })
    mock_conn.execute.return_value = mock_result
    catalog.connection = mock_conn

    # Expiry is extremely close to local_ts: days_to_expiry is positive but very small
    expiry_ns = 1700000000 + 1  # 1 nanosecond later
    res = spot_future_basis(catalog, "BTC-FUTURE", "BTC-SPOT", 1700000000, 1700000000, expiry_ns=expiry_ns)
    # days_to_expiry = 1 / 86_400e9 = 1.157e-14
    # annualized_pct = 0.01 * 365 / days_to_expiry = 3.15e14 %
    assert len(res) == 1
    assert res.row(0, named=True)["annualized_pct"] > 1e12


# ---------------------------------------------------------------------------
# 4. XGBoostFundingPredictor Stress Tests
# ---------------------------------------------------------------------------

def test_xgboost_predictor_non_numeric_target_df() -> None:
    predictor = XGBoostFundingPredictor()
    # A DataFrame with non-numeric (string) target column
    current_features = pl.DataFrame({
        "funding_rate": ["invalid", "numbers"],
    })
    # Polars .rolling_mean() on a string column silently returns [null, null] of Float64 type.
    # The predictor then fills these nulls with _fallback_mean (0.0).
    res = predictor.predict(current_features)
    assert isinstance(res, pl.Series)
    assert res.to_list() == [0.0, 0.0]




# ---------------------------------------------------------------------------
# 5. Risk / Chaos Score Stress Tests
# ---------------------------------------------------------------------------

def test_chaos_score_inf_inputs_nan() -> None:
    # Volatility as inf produces NaN score because inf / (inf + 0.1) is NaN in float math
    score = calculate_chaos_score(float("inf"), 0.0, 0.0, 0.0)
    assert math.isnan(score)

def test_chaos_score_string_inputs_crash() -> None:
    with pytest.raises(TypeError):
        calculate_chaos_score("invalid", 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 6. WhaleTransferTracker Stress Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whale_transfer_tracker_decimals_overflow() -> None:
    mock_w3 = MagicMock(spec=AsyncWeb3)
    mock_w3.to_checksum_address = lambda x: AsyncWeb3.to_checksum_address(x)
    
    # 1000 decimals causes 10**1000 to raise OverflowError when converted to float/used
    tracker = WhaleTransferTracker(
        w3=mock_w3,
        token_address="0xC02aaA39b223FE8D0A0e5C4F27ead9083C756Cc2",
        token_price_usd=3000.0,
        usd_threshold=100000.0,
        decimals=1000,
    )
    
    # Mock return logs
    mock_logs = [{
        "topics": [
            bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
            b"\x00" * 32,
            b"\x00" * 32,
        ],
        "data": (100).to_bytes(32, "big"),
        "transactionHash": b"\x01" * 32,
        "blockNumber": 12345,
    }]
    mock_w3.eth = MagicMock()
    mock_w3.eth.get_logs = AsyncMock(return_value=mock_logs)
    
    res = await tracker.get_whale_transfers(12000, 13000)
    # Because of OverflowError in power calculation, the log is skipped (returns empty list)
    assert res == []


# ---------------------------------------------------------------------------
# 7. SmartMoneyTracker Stress Tests
# ---------------------------------------------------------------------------

def test_smart_money_tracker_non_string_addresses() -> None:
    # Passing an integer as address raises AttributeError on lower()
    with pytest.raises(AttributeError):
        SmartMoneyTracker([12345])

def test_smart_money_tracker_none_values_process() -> None:
    tracker = SmartMoneyTracker(["0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"])
    # Ifusd_value or timestamp is None, it should raise TypeError
    with pytest.raises(TypeError):
        tracker.process_transfer({
            "from": "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B",
            "to": "0xNormalUser",
            "usd_value": None,
            "timestamp": 1000,
        })


# ---------------------------------------------------------------------------
# 8. Lending Stress Test Heuristic Inconsistency
# ---------------------------------------------------------------------------

def test_lending_stress_test_inconsistent_haircut() -> None:
    # 1. Haircut of 1.0 (intended as 1%):
    res_1 = lending_stress_test(
        collateral_usd=10000.0,
        debt_usd=5000.0,
        liquidation_threshold=0.8,
        haircut_pct=1.0,
    )
    # Because abs(1.0) > 1.0 is False, haircut_pct is interpreted directly as fraction (1.0 = 100%)
    # So simulated health factor becomes 0.0!
    assert res_1["simulated_health_factor"] == 0.0
    
    # 2. Haircut of 1.5 (intended as 1.5%):
    res_2 = lending_stress_test(
        collateral_usd=10000.0,
        debt_usd=5000.0,
        liquidation_threshold=0.8,
        haircut_pct=1.5,
    )
    # Because abs(1.5) > 1.0 is True, haircut_pct is divided by 100 (0.015 = 1.5%)
    # So simulated health factor is (10000 * 0.985 * 0.8) / 5000 = 1.576
    assert abs(res_2["simulated_health_factor"] - 1.576) < 1e-9


# ---------------------------------------------------------------------------
# 9. Gas to Volatility Correlation Crash Cases
# ---------------------------------------------------------------------------

def test_gas_vol_correlation_missing_ts() -> None:
    # Missing local_ts column entirely
    gas_df = pl.DataFrame({"gas_price": [10.0, 20.0]})
    vol_df = pl.DataFrame({"local_ts": [1, 2], "volatility": [0.1, 0.2]})
    with pytest.raises(Exception):
        gas_to_volatility_correlation(gas_df, vol_df)

def test_gas_vol_correlation_only_ts() -> None:
    # Only local_ts column, no other columns -> raises IndexError
    gas_df = pl.DataFrame({"local_ts": [1, 2]})
    vol_df = pl.DataFrame({"local_ts": [1, 2], "volatility": [0.1, 0.2]})
    with pytest.raises(IndexError):
        gas_to_volatility_correlation(gas_df, vol_df)

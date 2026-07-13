"""Unit tests for MCP analytics tool handlers (slippage, OFI, whale, IV, skew, basis).

Exercises pure handlers with mocked clients; no stdio / live lake required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import polars as pl

import pytest

from crypcodile.mcp_server import (
    TOOLS,
    handle_calculate_ofi,
    handle_detect_mev_sandwiches,
    handle_estimate_slippage,
    handle_get_chaos_score,
    handle_get_funding_prediction,
    handle_get_indicators,
    handle_get_iv_surface,
    handle_get_lending_stress,
    handle_get_liquidity_depth,
    handle_get_open_interest,
    handle_get_peg_deviation,
    handle_get_perp_basis,
    handle_get_risk_reversal,
    handle_get_sequencer_latency,
    handle_get_spot_future_basis,
    handle_get_spot_perp_basis,
    handle_get_term_structure,
    handle_get_vol_skew,
    handle_label_transfers,
    handle_smart_money_summary,
    handle_track_whale_alerts,
)

_ANALYTICS_TOOLS = {
    "estimate_slippage",
    "calculate_ofi",
    "track_whale_alerts",
    "get_iv_surface",
    "get_term_structure",
    "get_vol_skew",
    "get_risk_reversal",
    "get_perp_basis",
    "get_spot_perp_basis",
    "get_spot_future_basis",
    "get_indicators",
    "get_liquidity_depth",
    "get_sequencer_latency",
    "get_open_interest",
    "get_funding_prediction",
    "get_chaos_score",
    "get_lending_stress",
    "get_peg_deviation",
    "detect_mev_sandwiches",
    "smart_money_summary",
    "label_transfers",
}

_SMART_ADDR = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_OTHER_ADDR = "0x1111111111111111111111111111111111111111"
_SMART_TRANSFERS = [
    {
        "from": _SMART_ADDR,
        "to": _OTHER_ADDR,
        "usd_value": 100.0,
        "timestamp": 1,
    },
    {
        "from": _OTHER_ADDR,
        "to": _SMART_ADDR,
        "usd_value": 40.0,
        "timestamp": 2,
    },
]

_MEV_SANDWICH_TRADES = [
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 10,
        "sender": "0xattacker",
        "is_buy": True,
    },
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 11,
        "sender": "0xvictim",
        "is_buy": True,
    },
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 12,
        "sender": "0xattacker",
        "is_buy": False,
    },
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 13,
        "sender": "0xnormal",
        "is_buy": True,
    },
]


def test_tools_contains_analytics_names() -> None:
    names = {t["name"] for t in TOOLS}
    assert _ANALYTICS_TOOLS.issubset(names)


def test_handle_estimate_slippage_returns_dicts() -> None:
    client = MagicMock()
    client.estimate_slippage.return_value = pl.DataFrame(
        {"mid": [100.0], "avg_price": [100.5], "slippage_bps": [5.0]}
    )
    rows = handle_estimate_slippage(client, "deribit:BTC-PERPETUAL", "buy", 1.0)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["mid"] == 100.0
    client.estimate_slippage.assert_called_once_with(
        "deribit:BTC-PERPETUAL", "buy", 1.0
    )


def test_handle_estimate_slippage_empty() -> None:
    client = MagicMock()
    client.estimate_slippage.return_value = pl.DataFrame()
    assert handle_estimate_slippage(client, "x", "buy", 1.0) == []


def test_handle_calculate_ofi_returns_dicts() -> None:
    client = MagicMock()
    client.calculate_ofi.return_value = pl.DataFrame(
        {"bin_start": [1], "ofi": [0.25]}
    )
    rows = handle_calculate_ofi(client, "deribit:BTC-PERPETUAL", 0, 100, "1m")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["ofi"] == 0.25
    client.calculate_ofi.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100, "1m"
    )


def test_handle_calculate_ofi_empty() -> None:
    client = MagicMock()
    client.calculate_ofi.return_value = pl.DataFrame()
    assert handle_calculate_ofi(client, "x", 0, 1, "1m") == []


def test_handle_track_whale_alerts_returns_dicts() -> None:
    client = MagicMock()
    client.track_whale_alerts.return_value = pl.DataFrame(
        {"price": [50000.0], "usd_notional": [1_000_000.0]}
    )
    rows = handle_track_whale_alerts(
        client, "deribit:BTC-PERPETUAL", 0, 100, 500_000.0
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["usd_notional"] == 1_000_000.0
    client.track_whale_alerts.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100, 500_000.0
    )


def test_handle_track_whale_alerts_empty() -> None:
    client = MagicMock()
    client.track_whale_alerts.return_value = pl.DataFrame()
    assert handle_track_whale_alerts(client, "x", 0, 1, 100.0) == []


def test_handle_get_iv_surface_returns_dicts() -> None:
    client = MagicMock()
    client.iv_surface.return_value = pl.DataFrame(
        {"strike": [100.0], "iv": [0.5], "opt_type": ["C"]}
    )
    rows = handle_get_iv_surface(client, "BTC", 1_700_000_000_000_000_000, rate=0.01)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["iv"] == 0.5
    client.iv_surface.assert_called_once_with(
        "BTC", 1_700_000_000_000_000_000, rate=0.01
    )


def test_handle_get_iv_surface_empty() -> None:
    client = MagicMock()
    client.iv_surface.return_value = pl.DataFrame()
    assert handle_get_iv_surface(client, "BTC", 0) == []


def test_handle_get_term_structure_returns_dicts() -> None:
    client = MagicMock()
    client.term_structure.return_value = pl.DataFrame(
        {"expiry": [1], "atm_iv": [0.4], "days_to_expiry": [30.0]}
    )
    rows = handle_get_term_structure(client, "ETH", 1_700_000_000_000_000_000)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["atm_iv"] == 0.4
    client.term_structure.assert_called_once_with(
        "ETH", 1_700_000_000_000_000_000, rate=0.0
    )


def test_handle_get_term_structure_empty() -> None:
    client = MagicMock()
    client.term_structure.return_value = pl.DataFrame()
    assert handle_get_term_structure(client, "ETH", 0) == []


def test_handle_get_vol_skew_returns_dicts() -> None:
    client = MagicMock()
    client.vol_skew.return_value = pl.DataFrame(
        {"strike": [100.0], "iv": [0.55], "delta": [0.5]}
    )
    rows = handle_get_vol_skew(
        client, "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.01
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["iv"] == 0.55
    client.vol_skew.assert_called_once_with(
        "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.01
    )


def test_handle_get_vol_skew_empty() -> None:
    client = MagicMock()
    client.vol_skew.return_value = pl.DataFrame()
    assert handle_get_vol_skew(client, "BTC", 1, 0) == []


def test_handle_get_risk_reversal_returns_dict() -> None:
    client = MagicMock()
    skew = pl.DataFrame({"strike": [100.0], "iv": [0.5], "delta": [0.5]})
    client.vol_skew.return_value = skew
    client.risk_reversal_butterfly.return_value = (0.02, 0.01)
    result = handle_get_risk_reversal(
        client, "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.0
    )
    assert result == {"risk_reversal": 0.02, "butterfly": 0.01}
    client.vol_skew.assert_called_once_with(
        "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.0
    )
    client.risk_reversal_butterfly.assert_called_once_with(skew, target_delta=0.25)


def test_handle_get_risk_reversal_empty_skew() -> None:
    client = MagicMock()
    client.vol_skew.return_value = pl.DataFrame()
    assert handle_get_risk_reversal(client, "BTC", 1, 0) == {
        "risk_reversal": None,
        "butterfly": None,
    }
    client.risk_reversal_butterfly.assert_not_called()


def test_handle_get_perp_basis_returns_dicts() -> None:
    client = MagicMock()
    client.perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1],
            "mark_price": [100.5],
            "index_price": [100.0],
            "basis": [0.5],
            "basis_pct": [0.005],
        }
    )
    rows = handle_get_perp_basis(client, "deribit:BTC-PERPETUAL", 0, 100)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["basis"] == 0.5
    client.perp_basis.assert_called_once_with("deribit:BTC-PERPETUAL", 0, 100)


def test_handle_get_perp_basis_empty() -> None:
    client = MagicMock()
    client.perp_basis.return_value = pl.DataFrame()
    assert handle_get_perp_basis(client, "x", 0, 1) == []


def test_handle_get_spot_perp_basis_returns_dicts() -> None:
    client = MagicMock()
    client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1],
            "spot_price": [100.0],
            "perp_price": [100.5],
            "basis": [0.5],
            "basis_pct": [0.005],
        }
    )
    rows = handle_get_spot_perp_basis(
        client, "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 0, 100
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["basis"] == 0.5
    client.spot_perp_basis.assert_called_once_with(
        "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 0, 100
    )


def test_handle_get_spot_perp_basis_empty() -> None:
    client = MagicMock()
    client.spot_perp_basis.return_value = pl.DataFrame()
    assert handle_get_spot_perp_basis(client, "s", "p", 0, 1) == []


def test_handle_get_spot_future_basis_returns_dicts() -> None:
    client = MagicMock()
    client.spot_future_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1],
            "future_price": [101.0],
            "spot_price": [100.0],
            "basis": [1.0],
            "basis_pct": [0.01],
        }
    )
    rows = handle_get_spot_future_basis(
        client, "deribit:BTC-27JUN25", "deribit:BTC-SPOT", 0, 100
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["basis"] == 1.0
    client.spot_future_basis.assert_called_once_with(
        "deribit:BTC-27JUN25", "deribit:BTC-SPOT", 0, 100, expiry_ns=None
    )


def test_handle_get_spot_future_basis_with_expiry() -> None:
    client = MagicMock()
    client.spot_future_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1],
            "future_price": [101.0],
            "spot_price": [100.0],
            "basis": [1.0],
            "basis_pct": [0.01],
            "annualized_pct": [0.05],
        }
    )
    rows = handle_get_spot_future_basis(
        client,
        "deribit:BTC-27JUN25",
        "deribit:BTC-SPOT",
        0,
        100,
        expiry_ns=1_000_000_000,
    )
    assert len(rows) == 1
    assert rows[0]["annualized_pct"] == 0.05
    client.spot_future_basis.assert_called_once_with(
        "deribit:BTC-27JUN25",
        "deribit:BTC-SPOT",
        0,
        100,
        expiry_ns=1_000_000_000,
    )


def test_handle_get_spot_future_basis_empty() -> None:
    client = MagicMock()
    client.spot_future_basis.return_value = pl.DataFrame()
    assert handle_get_spot_future_basis(client, "f", "s", 0, 1) == []


def test_handle_get_indicators_returns_dicts() -> None:
    client = MagicMock()
    client.get_indicators.return_value = pl.DataFrame(
        {"bar": [1], "close": [100.0], "sma": [99.5]}
    )
    rows = handle_get_indicators(
        client,
        "deribit:BTC-PERPETUAL",
        0,
        100,
        interval="1h",
        indicator="sma",
        period=14,
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["sma"] == 99.5
    client.get_indicators.assert_called_once_with(
        "deribit:BTC-PERPETUAL",
        0,
        100,
        interval="1h",
        indicator="sma",
        period=14,
    )


def test_handle_get_indicators_empty() -> None:
    client = MagicMock()
    client.get_indicators.return_value = pl.DataFrame()
    assert handle_get_indicators(client, "x", 0, 1) == []


def test_handle_get_liquidity_depth_returns_dicts() -> None:
    client = MagicMock()
    client.calculate_block_liquidity_depth.return_value = pl.DataFrame(
        {
            "block": [1],
            "bid_depth_1pct": [10.0],
            "ask_depth_1pct": [12.0],
            "bid_depth_2pct": [20.0],
            "ask_depth_2pct": [22.0],
            "bid_depth_5pct": [50.0],
            "ask_depth_5pct": [55.0],
        }
    )
    rows = handle_get_liquidity_depth(client, "base_onchain:DEGEN-WETH")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["bid_depth_1pct"] == 10.0
    assert rows[0]["block"] == 1
    client.calculate_block_liquidity_depth.assert_called_once_with(
        "base_onchain:DEGEN-WETH"
    )


def test_handle_get_liquidity_depth_empty() -> None:
    client = MagicMock()
    client.calculate_block_liquidity_depth.return_value = pl.DataFrame()
    assert handle_get_liquidity_depth(client, "x") == []


def test_handle_get_sequencer_latency_returns_dicts() -> None:
    client = MagicMock()
    client.calculate_sequencer_latency.return_value = pl.DataFrame(
        {
            "metric": ["production_interval", "ingestion_delay"],
            "avg_seconds": [2.0, 0.1],
            "max_seconds": [4.0, 0.5],
            "std_seconds": [0.5, 0.05],
        }
    )
    rows = handle_get_sequencer_latency(client, "base_onchain")
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0]["metric"] == "production_interval"
    assert rows[0]["avg_seconds"] == 2.0
    client.calculate_sequencer_latency.assert_called_once_with("base_onchain")


def test_handle_get_sequencer_latency_default_exchange() -> None:
    client = MagicMock()
    client.calculate_sequencer_latency.return_value = pl.DataFrame(
        {
            "metric": ["production_interval"],
            "avg_seconds": [2.0],
            "max_seconds": [2.0],
            "std_seconds": [0.0],
        }
    )
    rows = handle_get_sequencer_latency(client)
    assert len(rows) == 1
    client.calculate_sequencer_latency.assert_called_once_with("base_onchain")


def test_handle_get_sequencer_latency_empty() -> None:
    client = MagicMock()
    client.calculate_sequencer_latency.return_value = pl.DataFrame()
    assert handle_get_sequencer_latency(client, "base_onchain") == []


def test_handle_get_open_interest_returns_dicts() -> None:
    client = MagicMock()
    client.aggregate_open_interest.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2],
            "binance": [100.0, 110.0],
            "bybit": [50.0, 55.0],
            "total_oi": [150.0, 165.0],
        }
    )
    rows = handle_get_open_interest(client, "BTC", 0, 100)
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0]["total_oi"] == 150.0
    assert rows[1]["binance"] == 110.0
    client.aggregate_open_interest.assert_called_once_with("BTC", 0, 100)


def test_handle_get_open_interest_all_symbols() -> None:
    client = MagicMock()
    client.aggregate_open_interest.return_value = pl.DataFrame(
        {"local_ts": [1], "total_oi": [200.0]}
    )
    rows = handle_get_open_interest(client, None, 0, 100)
    assert len(rows) == 1
    client.aggregate_open_interest.assert_called_once_with(None, 0, 100)


def test_handle_get_open_interest_empty() -> None:
    client = MagicMock()
    client.aggregate_open_interest.return_value = pl.DataFrame()
    assert handle_get_open_interest(client, "BTC", 0, 1) == []


def test_handle_get_funding_prediction_returns_dict() -> None:
    result = handle_get_funding_prediction([0.01, 0.02, 0.03], window_size=3)
    assert isinstance(result, dict)
    assert "predicted_funding_rate" in result
    assert isinstance(result["predicted_funding_rate"], float)
    assert result["method"] in ("rolling_mean", "xgboost")
    assert result["n_history"] == 3
    assert result["window_size"] == 3
    assert "xgboost_available" in result
    if result["method"] == "rolling_mean":
        assert abs(result["predicted_funding_rate"] - 0.02) < 1e-9


def test_handle_get_funding_prediction_default_window() -> None:
    result = handle_get_funding_prediction([0.01, 0.02, 0.03, 0.04, 0.05])
    assert result["window_size"] == 5
    assert result["n_history"] == 5
    if result["method"] == "rolling_mean":
        assert abs(result["predicted_funding_rate"] - 0.03) < 1e-9


def test_handle_get_funding_prediction_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        handle_get_funding_prediction([])


def test_handle_get_funding_prediction_invalid_window_raises() -> None:
    with pytest.raises(ValueError, match="window_size"):
        handle_get_funding_prediction([0.01], window_size=0)


def test_handle_get_chaos_score_zeros() -> None:
    result = handle_get_chaos_score(0.0, 0.0, 0.0, 0.0)
    assert isinstance(result, dict)
    assert result["volatility"] == 0.0
    assert result["stablecoin_deviation"] == 0.0
    assert result["orderbook_imbalance"] == 0.0
    assert result["sequencer_delay"] == 0.0
    assert result["chaos_score"] == 0.0


def test_handle_get_chaos_score_matches_analytics() -> None:
    from crypcodile.analytics.risk import calculate_chaos_score

    vol, dev, imb, delay = 0.02, 0.001, -0.2, 2.0
    result = handle_get_chaos_score(vol, dev, imb, delay)
    expected = calculate_chaos_score(vol, dev, imb, delay)
    assert result["chaos_score"] == expected
    assert result["volatility"] == vol
    assert result["stablecoin_deviation"] == dev
    assert result["orderbook_imbalance"] == imb
    assert result["sequencer_delay"] == delay
    assert 0.0 <= result["chaos_score"] <= 100.0


def test_handle_get_chaos_score_high_risk_bounded() -> None:
    result = handle_get_chaos_score(1000.0, 1000.0, 1.0, 1000.0)
    assert 0.0 <= result["chaos_score"] <= 100.0
    assert result["chaos_score"] > 50.0


def test_handle_get_peg_deviation_alert() -> None:
    result = handle_get_peg_deviation(0.98, threshold=0.01)
    assert isinstance(result, dict)
    assert result["price"] == pytest.approx(0.98)
    assert result["deviation_pct"] == pytest.approx(0.02)
    assert result["is_alert_triggered"] is True
    assert result["threshold"] == pytest.approx(0.01)


def test_handle_get_peg_deviation_ok() -> None:
    result = handle_get_peg_deviation(1.0)
    assert result["price"] == pytest.approx(1.0)
    assert result["deviation_pct"] == pytest.approx(0.0)
    assert result["is_alert_triggered"] is False
    assert result["threshold"] == pytest.approx(0.01)


def test_handle_get_peg_deviation_custom_target() -> None:
    result = handle_get_peg_deviation(1.05, threshold=0.02, target=1.0)
    assert result["deviation_pct"] == pytest.approx(0.05)
    assert result["is_alert_triggered"] is True
    result_ok = handle_get_peg_deviation(2.0, threshold=0.05, target=2.0)
    assert result_ok["deviation_pct"] == pytest.approx(0.0)
    assert result_ok["is_alert_triggered"] is False


def test_handle_get_peg_deviation_matches_analytics() -> None:
    from crypcodile.analytics.peg_deviation import peg_deviation_from_price

    expected = peg_deviation_from_price(0.975, threshold=0.01, target=1.0)
    result = handle_get_peg_deviation(0.975, threshold=0.01, target=1.0)
    assert result == expected


def test_handle_get_lending_stress_healthy() -> None:
    result = handle_get_lending_stress(
        collateral_usd=10_000.0,
        debt_usd=4_000.0,
        liquidation_threshold=0.8,
        haircut_pct=0.10,
    )
    assert isinstance(result, dict)
    assert result["collateral_usd"] == 10_000.0
    assert result["debt_usd"] == 4_000.0
    assert result["liquidation_threshold"] == 0.8
    assert result["haircut_pct"] == 0.10
    assert result["current_health_factor"] == pytest.approx(2.0)
    assert result["simulated_health_factor"] == pytest.approx(1.8)
    assert result["is_liquidatable"] is False
    assert result["simulated_is_liquidatable"] is False


def test_handle_get_lending_stress_liquidation() -> None:
    # Current HF = (10000 * 0.8) / 9000 ≈ 0.889 → liquidatable now
    result = handle_get_lending_stress(
        collateral_usd=10_000.0,
        debt_usd=9_000.0,
        liquidation_threshold=0.8,
        haircut_pct=10.0,
    )
    assert result["current_health_factor"] < 1.0
    assert result["is_liquidatable"] is True
    assert result["simulated_is_liquidatable"] is True


def test_handle_get_lending_stress_zero_debt_inf() -> None:
    result = handle_get_lending_stress(
        collateral_usd=5_000.0,
        debt_usd=0.0,
        liquidation_threshold=0.8,
        haircut_pct=0.20,
    )
    assert result["current_health_factor"] == float("inf")
    assert result["simulated_health_factor"] == float("inf")
    assert result["is_liquidatable"] is False
    assert result["simulated_is_liquidatable"] is False


def test_handle_get_lending_stress_matches_analytics() -> None:
    from crypcodile.analytics.lending_stress import lending_stress_test

    kwargs = {
        "collateral_usd": 12_500.0,
        "debt_usd": 3_000.0,
        "liquidation_threshold": 0.75,
        "haircut_pct": 20.0,
    }
    result = handle_get_lending_stress(**kwargs)
    expected = lending_stress_test(**kwargs)
    assert result["current_health_factor"] == expected["current_health_factor"]
    assert result["simulated_health_factor"] == expected["simulated_health_factor"]
    assert result["is_liquidatable"] == expected["is_liquidatable"]
    assert result["simulated_is_liquidatable"] == expected["simulated_is_liquidatable"]


def test_handle_get_lending_stress_percent_vs_fraction_haircut() -> None:
    """Haircut 20 and 0.20 must yield the same stress metrics."""
    a = handle_get_lending_stress(10_000.0, 5_000.0, 0.8, 20.0)
    b = handle_get_lending_stress(10_000.0, 5_000.0, 0.8, 0.20)
    assert a["simulated_health_factor"] == b["simulated_health_factor"]
    assert a["current_health_factor"] == b["current_health_factor"]


def test_handle_detect_mev_sandwiches_positive() -> None:
    rows = handle_detect_mev_sandwiches(_MEV_SANDWICH_TRADES)
    assert isinstance(rows, list)
    assert len(rows) == 4
    flags = [r["is_sandwich"] for r in rows]
    assert flags == [True, True, True, False]
    assert rows[0]["sender"] == "0xattacker"
    assert rows[1]["sender"] == "0xvictim"
    assert rows[3]["sender"] == "0xnormal"


def test_handle_detect_mev_sandwiches_negative_across_blocks() -> None:
    trades = [
        {
            "block": 100,
            "pool": "AERO-USDC",
            "log_index": 10,
            "sender": "0xattacker",
            "is_buy": True,
        },
        {
            "block": 101,
            "pool": "AERO-USDC",
            "log_index": 11,
            "sender": "0xvictim",
            "is_buy": True,
        },
        {
            "block": 102,
            "pool": "AERO-USDC",
            "log_index": 12,
            "sender": "0xattacker",
            "is_buy": False,
        },
    ]
    rows = handle_detect_mev_sandwiches(trades)
    assert len(rows) == 3
    assert not any(r["is_sandwich"] for r in rows)


def test_handle_detect_mev_sandwiches_empty() -> None:
    assert handle_detect_mev_sandwiches([]) == []


def test_handle_detect_mev_sandwiches_missing_cols_raises() -> None:
    with pytest.raises(ValueError, match="missing required columns"):
        handle_detect_mev_sandwiches([{"block": 1}])


def test_handle_detect_mev_sandwiches_not_list_raises() -> None:
    with pytest.raises(TypeError, match="list of dicts"):
        handle_detect_mev_sandwiches({"block": 1})  # type: ignore[arg-type]


def test_handle_detect_mev_sandwiches_matches_analytics() -> None:
    from crypcodile.analytics.mev_sandwich import detect_sandwiches

    rows = handle_detect_mev_sandwiches(_MEV_SANDWICH_TRADES)
    expected = detect_sandwiches(pl.DataFrame(_MEV_SANDWICH_TRADES)).to_dicts()
    assert rows == expected


def test_handle_smart_money_summary_with_labels() -> None:
    rows = handle_smart_money_summary(
        _SMART_TRANSFERS,
        {_SMART_ADDR: "vitalik"},
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == -60.0
    assert rows[0]["total_volume_usd"] == 140.0
    assert rows[0]["tx_count"] == 2
    assert rows[0]["label"] == "vitalik"
    assert rows[0]["last_active_ts"] == 2


def test_handle_smart_money_summary_list_watchlist() -> None:
    rows = handle_smart_money_summary(_SMART_TRANSFERS, [_SMART_ADDR])
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == -60.0
    # List watchlist uses address as label
    assert rows[0]["label"] == _SMART_ADDR


def test_handle_smart_money_summary_nested_watchlist() -> None:
    rows = handle_smart_money_summary(
        _SMART_TRANSFERS,
        {"watchlist": {_SMART_ADDR: "mev-bot"}},
    )
    assert len(rows) == 1
    assert rows[0]["label"] == "mev-bot"


def test_handle_smart_money_summary_empty_transfers() -> None:
    assert handle_smart_money_summary([], {_SMART_ADDR: "x"}) == []


def test_handle_smart_money_summary_empty_watchlist() -> None:
    assert handle_smart_money_summary(_SMART_TRANSFERS, {}) == []
    assert handle_smart_money_summary(_SMART_TRANSFERS, []) == []


def test_handle_smart_money_summary_no_matching_activity() -> None:
    rows = handle_smart_money_summary(
        _SMART_TRANSFERS,
        {"0xdeaddeaddeaddeaddeaddeaddeaddeaddeaddead": "ghost"},
    )
    assert rows == []


def test_handle_smart_money_summary_aliases() -> None:
    transfers = [
        {
            "from_address": _SMART_ADDR,
            "to_address": _OTHER_ADDR,
            "amount": "50",
            "local_ts": "9",
        }
    ]
    rows = handle_smart_money_summary(transfers, {_SMART_ADDR: "smart"})
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == -50.0
    assert rows[0]["total_volume_usd"] == 50.0
    assert rows[0]["last_active_ts"] == 9
    assert rows[0]["label"] == "smart"


def test_handle_smart_money_summary_not_list_raises() -> None:
    with pytest.raises(TypeError, match="list of dicts"):
        handle_smart_money_summary(
            {"from": _SMART_ADDR},  # type: ignore[arg-type]
            {_SMART_ADDR: "x"},
        )


def test_handle_label_transfers_basic() -> None:
    rows = handle_label_transfers(
        _SMART_TRANSFERS,
        {_SMART_ADDR: "vitalik"},
    )
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0]["from_label"] == "vitalik"
    assert rows[0]["to_label"] == ""
    assert rows[0]["is_known"] is True
    assert rows[1]["from_label"] == ""
    assert rows[1]["to_label"] == "vitalik"
    assert rows[1]["is_known"] is True


def test_handle_label_transfers_known_only() -> None:
    rows = handle_label_transfers(
        [
            {"from": _SMART_ADDR, "to": _OTHER_ADDR, "usd_value": 1},
            {"from": _OTHER_ADDR, "to": _OTHER_ADDR, "usd_value": 2},
        ],
        {_SMART_ADDR: "vitalik"},
        known_only=True,
    )
    assert len(rows) == 1
    assert rows[0]["from_label"] == "vitalik"
    assert rows[0]["is_known"] is True


def test_handle_label_transfers_min_usd() -> None:
    rows = handle_label_transfers(
        _SMART_TRANSFERS,
        {_SMART_ADDR: "vitalik"},
        min_usd=50.0,
    )
    assert len(rows) == 1
    assert rows[0]["usd_value"] == 100.0
    assert rows[0]["from_label"] == "vitalik"


def test_handle_label_transfers_list_watchlist() -> None:
    rows = handle_label_transfers(_SMART_TRANSFERS, [_SMART_ADDR])
    # List watchlist uses the address string itself as the label.
    assert rows[0]["from_label"] == _SMART_ADDR
    assert rows[0]["is_known"] is True


def test_handle_label_transfers_nested_watchlist() -> None:
    rows = handle_label_transfers(
        _SMART_TRANSFERS,
        {"watchlist": {_SMART_ADDR: "mev-bot"}},
    )
    assert rows[0]["from_label"] == "mev-bot"


def test_handle_label_transfers_empty_transfers() -> None:
    assert handle_label_transfers([], {_SMART_ADDR: "x"}) == []


def test_handle_label_transfers_empty_watchlist_still_labels() -> None:
    rows = handle_label_transfers(_SMART_TRANSFERS, {})
    assert len(rows) == 2
    assert all(r["from_label"] == "" for r in rows)
    assert all(r["to_label"] == "" for r in rows)
    assert all(r["is_known"] is False for r in rows)


def test_handle_label_transfers_aliases() -> None:
    transfers = [
        {
            "from_address": _SMART_ADDR,
            "to_address": _OTHER_ADDR,
            "amount": 10,
        }
    ]
    rows = handle_label_transfers(transfers, {_SMART_ADDR: "smart"})
    assert len(rows) == 1
    assert rows[0]["from_label"] == "smart"
    assert rows[0]["is_known"] is True


def test_handle_label_transfers_matches_analytics() -> None:
    from crypcodile.analytics.whale_transfers import label_transfer_addresses

    watch = {_SMART_ADDR.lower(): "vitalik"}
    expected = label_transfer_addresses(_SMART_TRANSFERS, watch)
    result = handle_label_transfers(_SMART_TRANSFERS, {_SMART_ADDR: "vitalik"})
    assert result == expected


def test_handle_label_transfers_not_list_raises() -> None:
    with pytest.raises(TypeError, match="list of dicts"):
        handle_label_transfers(
            {"from": _SMART_ADDR},  # type: ignore[arg-type]
            {_SMART_ADDR: "x"},
        )


def test_handle_label_transfers_bad_row_raises() -> None:
    with pytest.raises(TypeError, match="transfers\\[0\\] must be a dict"):
        handle_label_transfers(
            ["not-a-dict"],  # type: ignore[list-item]
            {_SMART_ADDR: "x"},
        )


def test_handle_label_transfers_bad_watchlist_raises() -> None:
    with pytest.raises(TypeError, match="watchlist must be a dict or list"):
        handle_label_transfers(_SMART_TRANSFERS, "0xabc")  # type: ignore[arg-type]


def test_handle_smart_money_summary_bad_row_raises() -> None:
    with pytest.raises(TypeError, match="transfers\\[0\\]"):
        handle_smart_money_summary(
            ["not-a-dict"],  # type: ignore[list-item]
            {_SMART_ADDR: "x"},
        )


def test_handle_smart_money_summary_bad_watchlist_raises() -> None:
    with pytest.raises(TypeError, match="watchlist"):
        handle_smart_money_summary(_SMART_TRANSFERS, "0xabc")  # type: ignore[arg-type]


def test_handle_smart_money_summary_matches_analytics() -> None:
    from crypcodile.analytics.smart_money import summarize_smart_money

    watchlist = {_SMART_ADDR: "vitalik"}
    rows = handle_smart_money_summary(_SMART_TRANSFERS, watchlist)
    expected = summarize_smart_money(_SMART_TRANSFERS, watchlist)
    assert rows == expected


def test_analytics_tool_schemas_have_required_fields() -> None:
    by_name: dict[str, Any] = {t["name"]: t for t in TOOLS}
    assert set(by_name["estimate_slippage"]["inputSchema"]["required"]) == {
        "symbol",
        "side",
        "size",
    }
    assert set(by_name["calculate_ofi"]["inputSchema"]["required"]) == {
        "symbol",
        "start",
        "end",
        "interval",
    }
    assert set(by_name["track_whale_alerts"]["inputSchema"]["required"]) == {
        "symbol",
        "start",
        "end",
        "min_usd",
    }
    assert set(by_name["get_iv_surface"]["inputSchema"]["required"]) == {
        "underlying",
        "at",
    }
    assert set(by_name["get_term_structure"]["inputSchema"]["required"]) == {
        "underlying",
        "at",
    }
    assert set(by_name["get_vol_skew"]["inputSchema"]["required"]) == {
        "underlying",
        "expiry_ns",
        "at",
    }
    assert set(by_name["get_risk_reversal"]["inputSchema"]["required"]) == {
        "underlying",
        "expiry_ns",
        "at",
    }
    assert set(by_name["get_perp_basis"]["inputSchema"]["required"]) == {
        "perp_symbol",
        "start",
        "end",
    }
    assert set(by_name["get_spot_perp_basis"]["inputSchema"]["required"]) == {
        "spot_symbol",
        "perp_symbol",
        "start",
        "end",
    }
    assert set(by_name["get_spot_future_basis"]["inputSchema"]["required"]) == {
        "future_symbol",
        "spot_symbol",
        "start",
        "end",
    }
    assert "expiry_ns" in by_name["get_spot_future_basis"]["inputSchema"]["properties"]
    assert set(by_name["get_indicators"]["inputSchema"]["required"]) == {
        "symbol",
        "start",
        "end",
    }
    assert set(by_name["get_liquidity_depth"]["inputSchema"]["required"]) == {
        "symbol",
    }
    assert by_name["get_sequencer_latency"]["inputSchema"]["required"] == []
    assert "exchange" in by_name["get_sequencer_latency"]["inputSchema"]["properties"]
    assert set(by_name["get_open_interest"]["inputSchema"]["required"]) == {
        "start",
        "end",
    }
    assert "symbols" in by_name["get_open_interest"]["inputSchema"]["properties"]
    assert set(by_name["get_funding_prediction"]["inputSchema"]["required"]) == {
        "rates",
    }
    rates_schema = by_name["get_funding_prediction"]["inputSchema"]["properties"][
        "rates"
    ]
    assert rates_schema["type"] == "array"
    assert rates_schema["items"]["type"] == "number"
    assert "window_size" in by_name["get_funding_prediction"]["inputSchema"][
        "properties"
    ]
    assert set(by_name["get_chaos_score"]["inputSchema"]["required"]) == {
        "volatility",
        "stablecoin_deviation",
        "orderbook_imbalance",
        "sequencer_delay",
    }
    chaos_props = by_name["get_chaos_score"]["inputSchema"]["properties"]
    for key in (
        "volatility",
        "stablecoin_deviation",
        "orderbook_imbalance",
        "sequencer_delay",
    ):
        assert chaos_props[key]["type"] == "number"
    assert set(by_name["get_lending_stress"]["inputSchema"]["required"]) == {
        "collateral_usd",
        "debt_usd",
        "liquidation_threshold",
        "haircut_pct",
    }
    lend_props = by_name["get_lending_stress"]["inputSchema"]["properties"]
    for key in (
        "collateral_usd",
        "debt_usd",
        "liquidation_threshold",
        "haircut_pct",
    ):
        assert lend_props[key]["type"] == "number"
    assert set(by_name["get_peg_deviation"]["inputSchema"]["required"]) == {
        "price",
    }
    peg_props = by_name["get_peg_deviation"]["inputSchema"]["properties"]
    assert peg_props["price"]["type"] == "number"
    assert peg_props["threshold"]["type"] == "number"
    assert peg_props["target"]["type"] == "number"
    assert set(by_name["detect_mev_sandwiches"]["inputSchema"]["required"]) == {
        "trades",
    }
    trades_schema = by_name["detect_mev_sandwiches"]["inputSchema"]["properties"][
        "trades"
    ]
    assert trades_schema["type"] == "array"
    assert trades_schema["items"]["type"] == "object"
    assert set(trades_schema["items"]["required"]) == {
        "block",
        "pool",
        "log_index",
        "sender",
        "is_buy",
    }
    assert set(by_name["smart_money_summary"]["inputSchema"]["required"]) == {
        "transfers",
        "watchlist",
    }
    sm_props = by_name["smart_money_summary"]["inputSchema"]["properties"]
    assert sm_props["transfers"]["type"] == "array"
    assert sm_props["transfers"]["items"]["type"] == "object"
    assert "watchlist" in sm_props
    assert set(by_name["label_transfers"]["inputSchema"]["required"]) == {
        "transfers",
        "watchlist",
    }
    lt_props = by_name["label_transfers"]["inputSchema"]["properties"]
    assert lt_props["transfers"]["type"] == "array"
    assert lt_props["transfers"]["items"]["type"] == "object"
    assert "watchlist" in lt_props
    assert lt_props["known_only"]["type"] == "boolean"
    assert lt_props["min_usd"]["type"] == "number"

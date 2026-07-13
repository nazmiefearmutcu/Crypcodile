from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from web3 import AsyncWeb3

from crypcodile.analytics.lending_stress import (
    LendingStressTester,
    lending_stress_test,
    simulate_stress,
)


def test_lending_stress_test_healthy() -> None:
    # Collateral: $10,000, Debt: $5,000, Liquidation Threshold: 80% (0.8)
    # Current HF = (10000 * 0.8) / 5000 = 8000 / 5000 = 1.6
    # Haircut: 20% -> simulated collateral = $8,000
    # Simulated HF = (8000 * 0.8) / 5000 = 6400 / 5000 = 1.28
    res = lending_stress_test(
        collateral_usd=10000.0,
        debt_usd=5000.0,
        liquidation_threshold=0.8,
        haircut_pct=0.20,
    )
    assert abs(res["current_health_factor"] - 1.6) < 1e-9
    assert abs(res["simulated_health_factor"] - 1.28) < 1e-9
    assert res["is_liquidatable"] is False
    assert res["simulated_is_liquidatable"] is False


def test_lending_stress_test_liquidation() -> None:
    # Collateral: $10,000, Debt: $9,000, Liquidation Threshold: 80% (0.8)
    # Current HF = (10000 * 0.8) / 9000 = 0.8888 -> liquidatable
    res = lending_stress_test(
        collateral_usd=10000.0,
        debt_usd=9000.0,
        liquidation_threshold=0.8,
        haircut_pct=10.0,  # 10% percentage notation
    )
    assert res["current_health_factor"] < 1.0
    assert res["is_liquidatable"] is True
    assert res["simulated_is_liquidatable"] is True


def test_lending_stress_test_zero_debt() -> None:
    res = lending_stress_test(
        collateral_usd=10000.0,
        debt_usd=0.0,
        liquidation_threshold=0.8,
        haircut_pct=20.0,
    )
    assert res["current_health_factor"] == float("inf")
    assert res["simulated_health_factor"] == float("inf")
    assert res["is_liquidatable"] is False
    assert res["simulated_is_liquidatable"] is False


@pytest.mark.asyncio
async def test_lending_stress_tester_class() -> None:
    mock_w3 = MagicMock()
    mock_w3.to_checksum_address = lambda x: AsyncWeb3.to_checksum_address(x)
    mock_w3.eth = MagicMock()

    # Mock contract calls
    # getUserAccountData output format:
    # (totalCollateralBase, totalDebtBase, availableBorrowsBase,
    #  currentLiquidationThreshold, ltv, healthFactor)
    mock_user_data = [
        int(10000.0 * 1e8),
        int(5000.0 * 1e8),
        int(3000.0 * 1e8),
        8000,
        7500,
        int(1.6 * 1e18),
    ]

    mock_call = AsyncMock(return_value=mock_user_data)
    mock_contract = MagicMock()
    mock_contract.functions.getUserAccountData.return_value.call = mock_call
    mock_w3.eth.contract.return_value = mock_contract

    tester = LendingStressTester(mock_w3, "0xA238Dd80C259b7051470e3E0f9d921008C39F93b")

    pos = await tester.get_user_positions("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    assert abs(pos["collateral_usd"] - 10000.0) < 1e-9
    assert abs(pos["debt_usd"] - 5000.0) < 1e-9
    assert abs(pos["liquidation_threshold"] - 0.8) < 1e-9
    assert abs(pos["ltv"] - 0.75) < 1e-9
    assert abs(pos["health_factor"] - 1.6) < 1e-9

    res = await tester.simulate_stress("0x70997970C51812dc3A010C7d01b50e0d17dc79C8", 0.20)
    assert abs(res["current_collateral_usd"] - 10000.0) < 1e-9
    assert abs(res["current_debt_usd"] - 5000.0) < 1e-9
    assert abs(res["stressed_collateral_usd"] - 8000.0) < 1e-9
    assert abs(res["stressed_health_factor"] - 1.28) < 1e-9
    assert res["is_liquidatable"] is False
    assert res["simulated_is_liquidatable"] is False

    # Also verify standalone helper
    standalone_res = await simulate_stress(
        user_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        price_haircut=20.0,
        w3=mock_w3,
        pool_address="0xA238Dd80C259b7051470e3E0f9d921008C39F93b"
    )
    assert abs(standalone_res["stressed_health_factor"] - 1.28) < 1e-9


def _mock_pool_w3(mock_user_data: list) -> MagicMock:
    """Build a MagicMock AsyncWeb3 whose pool contract returns mock_user_data."""
    mock_w3 = MagicMock()
    mock_w3.to_checksum_address = lambda x: AsyncWeb3.to_checksum_address(x)
    mock_w3.eth = MagicMock()
    mock_call = AsyncMock(return_value=mock_user_data)
    mock_contract = MagicMock()
    mock_contract.functions.getUserAccountData.return_value.call = mock_call
    mock_w3.eth.contract.return_value = mock_contract
    return mock_w3


@pytest.mark.asyncio
async def test_get_user_positions_hf_zero_is_zero_not_inf() -> None:
    """Aave HF raw 0 is a real zero health factor, not 'no debt'."""
    mock_user_data = [
        int(10000.0 * 1e8),
        int(5000.0 * 1e8),
        0,
        8000,
        7500,
        0,  # healthFactor raw == 0
    ]
    tester = LendingStressTester(
        _mock_pool_w3(mock_user_data),
        "0xA238Dd80C259b7051470e3E0f9d921008C39F93b",
    )
    pos = await tester.get_user_positions("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    assert pos["health_factor"] == 0.0
    assert pos["health_factor"] != float("inf")


@pytest.mark.asyncio
async def test_get_user_positions_hf_max_uint256_is_inf() -> None:
    """Only max uint256 (no debt) maps to infinity."""
    mock_user_data = [
        int(10000.0 * 1e8),
        0,
        0,
        8000,
        7500,
        2**256 - 1,  # Aave sentinel for no debt
    ]
    tester = LendingStressTester(
        _mock_pool_w3(mock_user_data),
        "0xA238Dd80C259b7051470e3E0f9d921008C39F93b",
    )
    pos = await tester.get_user_positions("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
    assert pos["health_factor"] == float("inf")

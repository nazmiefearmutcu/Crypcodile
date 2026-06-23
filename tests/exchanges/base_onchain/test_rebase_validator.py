import pytest
from unittest.mock import AsyncMock, MagicMock
from crypcodile.exchanges.base_onchain.rebase_validator import RebaseValidator

@pytest.mark.asyncio
async def test_rebase_validator_drift():
    w3 = MagicMock()
    token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"
    holder = "0x1111111111111111111111111111111111111111"
    
    mock_contract = MagicMock()
    w3.eth.contract.return_value = mock_contract
    
    mock_contract.functions.decimals().call = AsyncMock(return_value=6)
    mock_contract.functions.balanceOf().call = AsyncMock(return_value=100050000)
    
    validator = RebaseValidator(w3, token, [holder])
    
    local_balances = {holder.lower(): 100.0}
    corrections = await validator.validate_balances(local_balances, 1000000, 1700000000)
    assert len(corrections) == 1
    assert pytest.approx(corrections[0].correction_amount) == 0.05
    assert corrections[0].onchain_balance == 100.05
    assert corrections[0].local_balance == 100.0
    
    local_balances_ok = {holder.lower(): 100.05}
    corrections_ok = await validator.validate_balances(local_balances_ok, 1000000, 1700000000)
    assert len(corrections_ok) == 0

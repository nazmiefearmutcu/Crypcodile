import pytest
from unittest.mock import AsyncMock, MagicMock
from crypcodile.exchanges.base_onchain.por_syncer import ProofOfReserveSyncer

@pytest.mark.asyncio
async def test_por_syncer_backed():
    w3 = MagicMock()
    feed = "0x7777777777777777777777777777777777777777"
    token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"
    
    mock_contract = MagicMock()
    w3.eth.contract.return_value = mock_contract
    
    mock_contract.functions.decimals().call = AsyncMock(side_effect=[8, 6])
    mock_contract.functions.latestRoundData().call = AsyncMock(
        return_value=(1, 10000000000, 1600000000, 1600000000, 1)
    )
    mock_contract.functions.totalSupply().call = AsyncMock(return_value=99500000)
    
    syncer = ProofOfReserveSyncer(w3, feed, token)
    update = await syncer.sync_por(1000000, 1700000000)
    
    assert update.reserves == 100.0
    assert update.total_supply == 99.5
    assert update.backing_ratio == 100.0 / 99.5
    assert update.is_backed is True

@pytest.mark.asyncio
async def test_por_syncer_under_collateralized():
    w3 = MagicMock()
    feed = "0x7777777777777777777777777777777777777777"
    token = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"
    
    mock_contract = MagicMock()
    w3.eth.contract.return_value = mock_contract
    
    mock_contract.functions.decimals().call = AsyncMock(side_effect=[8, 6])
    mock_contract.functions.latestRoundData().call = AsyncMock(
        return_value=(1, 9500000000, 1600000000, 1600000000, 1)
    )
    mock_contract.functions.totalSupply().call = AsyncMock(return_value=100000000)
    
    syncer = ProofOfReserveSyncer(w3, feed, token)
    update = await syncer.sync_por(1000000, 1700000000)
    
    assert update.reserves == 95.0
    assert update.total_supply == 100.0
    assert update.backing_ratio == 0.95
    assert update.is_backed is False

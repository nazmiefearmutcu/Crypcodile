import pytest
from unittest.mock import AsyncMock, patch
from crypcodile.exchanges.base_onchain.asset_registry import AssetRegistry

@pytest.mark.asyncio
async def test_asset_registry_static_resolution():
    registry = AssetRegistry()
    
    # Verify we can resolve AERO, DEGEN, and BRETT case-insensitively
    aero = registry.resolve_token("AERO")
    assert aero is not None
    assert aero["symbol"] == "AERO"
    assert aero["address"] == "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
    
    degen = registry.resolve_token("degen")
    assert degen is not None
    assert degen["symbol"] == "DEGEN"
    
    brett = registry.resolve_token("Brett")
    assert brett is not None
    assert brett["symbol"] == "BRETT"

    # Verify at least 100 tokens exist in cache
    assert len(registry.cache) >= 100

@pytest.mark.asyncio
async def test_asset_registry_dynamic_fetch_fallback():
    registry = AssetRegistry()
    
    # Mocking standard HTTP request to fail (simulate offline)
    with patch("aiohttp.ClientSession.get", side_effect=Exception("Network disconnected")):
        await registry.fetch_dynamic_registry()
        # Verify cache remains fully populated after silent failure
        assert len(registry.cache) >= 100
        assert registry.resolve_token("AERO") is not None

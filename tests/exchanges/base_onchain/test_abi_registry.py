import os
import pytest
from unittest.mock import AsyncMock, patch
from crypcodile.exchanges.base_onchain.abi_registry import ABIRegistry

@pytest.mark.asyncio
async def test_abi_registry_cache_and_fetch(tmp_path):
    cache_dir = str(tmp_path / "abi_cache")
    registry = ABIRegistry(cache_dir=cache_dir)
    address = "0x2b5c70255c65f5700885e76f90643097d1234567"
    mock_abi = [{"inputs": [], "name": "test", "type": "function"}]

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"status": "1", "result": mock_abi})
        
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_ctx
        
        abi = await registry.get_abi(address)
        assert abi == mock_abi
        
    assert os.path.exists(os.path.join(cache_dir, f"{address.lower()}.json"))

    with patch("aiohttp.ClientSession.get") as mock_get:
        abi = await registry.get_abi(address)
        assert abi == mock_abi
        mock_get.assert_not_called()

@pytest.mark.asyncio
async def test_abi_registry_sourcify_fallback(tmp_path):
    cache_dir = str(tmp_path / "abi_cache")
    registry = ABIRegistry(cache_dir=cache_dir)
    address = "0x3b5c70255c65f5700885e76f90643097d1234568"
    mock_abi = [{"inputs": [], "name": "sourcify", "type": "function"}]

    with patch("aiohttp.ClientSession.get") as mock_get:
        mock_resp_basescan = AsyncMock()
        mock_resp_basescan.status = 200
        mock_resp_basescan.json = AsyncMock(return_value={"status": "0", "result": "Contract source code not verified"})
        
        mock_resp_sourcify = AsyncMock()
        mock_resp_sourcify.status = 200
        mock_resp_sourcify.json = AsyncMock(return_value={"output": {"abi": mock_abi}})
        
        mock_ctx_basescan = AsyncMock()
        mock_ctx_basescan.__aenter__.return_value = mock_resp_basescan
        
        mock_ctx_sourcify = AsyncMock()
        mock_ctx_sourcify.__aenter__.return_value = mock_resp_sourcify
        
        mock_get.side_effect = [mock_ctx_basescan, mock_ctx_sourcify]
        
        abi = await registry.get_abi(address)
        assert abi == mock_abi

import pytest
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock
from crypcodile.exchanges.base_onchain.smart_wallet import CoinbaseSmartWalletDetector

@pytest.mark.asyncio
async def test_smart_wallet_detector_cache():
    # Use a temporary file for the cache
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name

    try:
        # 1. Initialize detector with new cache
        detector = CoinbaseSmartWalletDetector(cache_path=cache_path)
        
        # Mock Web3
        w3 = AsyncMock()
        
        # Address is not in cache, let's mock RPC to say it's a smart wallet via proxy bytecode check
        # "363d3d37" is the proxy bytecode signature Coinbase Smart Wallet uses
        w3.eth.get_code.return_value = bytes.fromhex("363d3d3700000000")
        w3.eth.get_logs.return_value = [] # no logs

        # First check (queries RPC)
        from web3 import Web3
        wallet_address = Web3.to_checksum_address("0x00000000003b26925905180037a35368a55e206b")
        res = await detector.is_smart_wallet(w3, wallet_address)
        assert res is True
        assert w3.eth.get_code.call_count == 1

        # Second check (should hit cache, no RPC call)
        res_cached = await detector.is_smart_wallet(w3, wallet_address)
        assert res_cached is True
        assert w3.eth.get_code.call_count == 1  # call count remains 1

        # 2. Re-instantiate detector to test loading from disk cache
        new_detector = CoinbaseSmartWalletDetector(cache_path=cache_path)
        assert wallet_address in new_detector.cache
        assert new_detector.cache[wallet_address] is True

    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)

import pytest
import os
import tempfile
from unittest.mock import AsyncMock
from crypcodile.exchanges.base_onchain.bns_resolver import BNSResolver

@pytest.mark.asyncio
async def test_bns_resolver_seed_mocks():
    # Test that resolver has seeded defaults
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name
    try:
        resolver = BNSResolver(cache_path=cache_path)
        
        # Test seeded resolve_name
        coinbase_addr = await resolver.resolve_name(w3=None, name="coinbase.base")
        assert coinbase_addr == "0x5030000000000000000000000000000000000000"
        
        # Test seeded reverse resolve_address
        coinbase_name = await resolver.resolve_address(w3=None, address="0x5030000000000000000000000000000000000000")
        assert coinbase_name == "coinbase.base"
    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)

def test_bns_resolver_namehash():
    resolver = BNSResolver(cache_path=".tmp_bns.json")
    # Verify namehash returns bytes32
    nh = resolver._namehash("coinbase.base")
    assert isinstance(nh, bytes)
    assert len(nh) == 32
    
    # namehash of empty name is zero bytes
    assert resolver._namehash("") == b'\x00' * 32
    if os.path.exists(".tmp_bns.json"):
        os.remove(".tmp_bns.json")

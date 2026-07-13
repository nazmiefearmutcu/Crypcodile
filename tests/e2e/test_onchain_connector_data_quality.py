import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport
from crypcodile.ingest.sync_recovery import SyncRecovery


def test_sync_recovery_seen_logs(tmp_path):
    state_file = tmp_path / "recovery.json"
    recovery = SyncRecovery(str(state_file))
    
    # Save some seen logs
    logs = {
        ("0xabc", 1): True,
        ("0xdef", 2): True,
    }
    recovery.save_seen_logs(logs)
    
    # Create new recovery instance to load
    new_recovery = SyncRecovery(str(state_file))
    loaded = new_recovery.get_seen_logs()
    
    assert loaded == {("0xabc", 1): True, ("0xdef", 2): True}


def test_fifo_eviction(tmp_path):
    state_file = tmp_path / "recovery.json"
    # Create transport
    transport = BaseOnchainTransport("http://mock-rpc", ["cbBTC-USDC"])
    transport.sync_recovery = SyncRecovery(str(state_file))
    transport._seen_logs = {}

    # Add 5001 items to self._seen_logs
    for i in range(5001):
        transport._add_seen_log((f"0x{i:03x}", i))
        
    # The length should be 5001 - 2500 = 2501
    assert len(transport._seen_logs) == 2501
    
    # The first 2500 items (0 to 2499) should be evicted
    assert ("0x000", 0) not in transport._seen_logs
    assert ("0x9c3", 2499) not in transport._seen_logs
    # The 2500th item and beyond should be present
    assert ("0x9c4", 2500) in transport._seen_logs
    assert ("0x1388", 5000) in transport._seen_logs


@pytest.mark.asyncio
async def test_lending_and_limit_order_skipping_on_failure(tmp_path):
    state_file = tmp_path / "recovery.json"
    transport = BaseOnchainTransport("http://mock-rpc", ["cbBTC-USDC"])
    transport.sync_recovery = SyncRecovery(str(state_file))
    transport._seen_logs = {}
    transport.poll_interval = 0.01
    
    # Initialize block counters
    transport._last_lending_block = 80
    transport._last_limit_order_block = 80
    
    raise_lending_error = False
    raise_limit_error = False
    
    # We also need block timestamp mock
    transport._block_cache[100] = 1700000000
    transport._block_cache[90] = 1700000000
    transport._block_cache[80] = 1700000000
    
    async def mock_call_with_retry(func, *args, **kwargs):
        # Extract function representation
        func_str = str(func)
        func_name = getattr(func, "__name__", "")
        
        if "get_block" in func_str or func_name == "get_block":
            return {"hash": b"abc", "parentHash": b"def", "timestamp": 1700000000}
        elif "get_logs" in func_str or func_name == "get_logs":
            query = args[0] if args else {}
            addr = query.get("address", "").lower()
            if "a238dd" in addr or "8fa4c9" in addr: # Lending addresses
                if raise_lending_error:
                    raise Exception("Lending RPC Error")
                return []
            elif "111111" in addr or "def1c0" in addr: # Limit order addresses
                if raise_limit_error:
                    raise Exception("Limit order RPC Error")
                return []
            return []
        elif "getPool" in func_str or "slot0" in func_str or "liquidity" in func_str or "getReserves" in func_str:
            return "0x0000000000000000000000000000000000000000"
        elif "block_number" in func_str or "blockNumber" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return 100
        return None

    transport._call_with_retry = mock_call_with_retry
    transport._get_block_number = AsyncMock(return_value=100)
    
    # 1. Success case: both should advance to 85 (current_block = 100 - 15)
    transport._connected = True
    poll_task = asyncio.create_task(transport._poll_loop())
    await asyncio.sleep(0.05)
    transport._connected = False
    try:
        await poll_task
    except Exception:
        pass
        
    assert transport._last_lending_block == 85
    assert transport._last_limit_order_block == 85
    
    # 2. Failure case: lending fails, limit succeeds.
    # current_block = 105 - 15 = 90.
    transport._get_block_number = AsyncMock(return_value=105)
    raise_lending_error = True
    raise_limit_error = False
    
    transport._connected = True
    poll_task = asyncio.create_task(transport._poll_loop())
    await asyncio.sleep(0.05)
    transport._connected = False
    try:
        await poll_task
    except Exception:
        pass
        
    # Lending should stay at 85, limit order should advance to 90
    assert transport._last_lending_block == 85
    assert transport._last_limit_order_block == 90
    
    # 3. Failure case: limit fails, lending succeeds.
    # current_block = 110 - 15 = 95.
    transport._get_block_number = AsyncMock(return_value=110)
    raise_lending_error = False
    raise_limit_error = True
    
    transport._connected = True
    poll_task = asyncio.create_task(transport._poll_loop())
    await asyncio.sleep(0.05)
    transport._connected = False
    try:
        await poll_task
    except Exception:
        pass
        
    # Lending should advance to 95, limit order should stay at 90
    assert transport._last_lending_block == 95
    assert transport._last_limit_order_block == 90

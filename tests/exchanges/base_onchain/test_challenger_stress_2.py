import asyncio
from unittest.mock import patch

import pytest

from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport
from crypcodile.exchanges.base_onchain.normalize import normalize_onchain_update


# Thread-safe thread sleeping mock to simulate slow RPC nodes
class SleepyMockWeb3:
    def __init__(self, get_block_delay=0.0, get_logs_delay=0.0, fail_pool_b=False):
        self.get_block_delay = get_block_delay
        self.get_logs_delay = get_logs_delay
        self.fail_pool_b = fail_pool_b
        self.eth = SleepyMockEth(self)
        
    @staticmethod
    def to_checksum_address(addr):
        return addr

class SleepyMockEth:
    def __init__(self, parent):
        self.parent = parent
        self._block_number = 1000
        
    @property
    async def block_number(self):
        val = self._block_number
        if self._block_number == 1000:
            self._block_number = 1001
        return val
        
    def contract(self, address, abi):
        return SleepyMockContract(address, self.parent)
        
    async def get_block(self, block_num):
        if self.parent.get_block_delay > 0:
            await asyncio.sleep(self.parent.get_block_delay)
        return {"timestamp": 1600000000 + block_num}
        
    async def get_logs(self, filter_params):
        if self.parent.get_logs_delay > 0:
            await asyncio.sleep(self.parent.get_logs_delay)
        # Return empty list or some dummy logs
        return []

class SleepyMockContract:
    def __init__(self, address, parent):
        self.address = address
        self.parent = parent
        self.functions = SleepyMockContractFunctions(address, parent)

class SleepyMockContractFunctions:
    def __init__(self, address, parent):
        self.address = address
        self.parent = parent

    def getPool(self, *args, **kwargs):
        class Call:
            def __init__(self, *a, **kw):
                pass
            async def call(self):
                # Return standard mock pool address
                return "0xMockPoolAddress"
        return Call(self.address)

    def slot0(self):
        class Call:
            def __init__(self, parent):
                self.parent = parent
            async def call(self):
                if self.parent.fail_pool_b and self.parent.address == "0xMockPoolAddress":
                    # We will fail this one if we want to simulate a pool failure
                    raise Exception("Pool B state query failed")
                # Return normal slot0 tuple
                return [2**96, 0, 0, 0, 0, 0, True]
        return Call(self.parent)

    def liquidity(self):
        class Call:
            async def call(self):
                return 1000000
        return Call()

    def getReserves(self):
        class Call:
            async def call(self):
                return [10000, 10000, 12345]
        return Call()


@pytest.mark.asyncio
async def test_non_blocking_event_loop() -> None:
    """Verify that slow RPC calls run in thread pools and do not block the event loop."""
    mock_w3 = SleepyMockWeb3(get_block_delay=0.1, get_logs_delay=0.1)
    
    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.01)
        
        # We start a concurrent background task in the event loop that ticks
        ticks = 0
        async def event_loop_ticker():
            nonlocal ticks
            while transport._connected:
                ticks += 1
                await asyncio.sleep(0.01)
                
        # Start transport
        await transport.connect()
        ticker_task = asyncio.create_task(event_loop_ticker())
        
        # Allow the transport to run for a brief moment
        await asyncio.sleep(0.3)
        await transport.close()
        await ticker_task
        
        # If the event loop were blocked, ticks would be very low (e.g. 0 or 1)
        # because the 100ms thread sleeps would block the main thread.
        # Since it is non-blocking, ticks should be close to 20-30.
        assert ticks >= 5, f"Event loop was blocked! Only got {ticks} ticks."


@pytest.mark.asyncio
async def test_pool_resolution_retry() -> None:
    """Verify that pool resolution is retried if it initially fails."""
    attempts = 0
    
    class FailingMockWeb3(SleepyMockWeb3):
        def __init__(self):
            super().__init__()
            
    class FailingMockContractFunctions(SleepyMockContractFunctions):
        def getPool(self, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            class Call:
                def __init__(self, *a, **kw):
                    pass
                async def call(self):
                    if attempts == 1:
                        # Initially fail resolution by returning zero address
                        return "0x0000000000000000000000000000000000000000"
                    # Succeed on subsequent attempts
                    return "0xMockPoolAddress"
            return Call(self.address)

    mock_w3 = FailingMockWeb3()
    # Override contract creation to use our failing functions
    def contract_override(address, abi):
        c = SleepyMockContract(address, mock_w3)
        c.functions = FailingMockContractFunctions(address, mock_w3)
        return c
    mock_w3.eth.contract = contract_override

    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.01)
        
        # Connect and run two loops
        await transport.connect()
        # Sleep to let it run at least 2 iterations
        await asyncio.sleep(0.05)
        await transport.close()
        
        # It should have called getPool at least twice (one initial failure, one retry)
        assert attempts >= 2
        
        # Retrieve non-None items
        results = []
        while not transport._queue.empty():
            val = transport._queue.get_nowait()
            if val is not None:
                results.append(val)
                
        # And after the retry succeeded, the pool should be resolved and update queued
        assert len(results) > 0
        assert "cbBTC-USDC" in results[0].decode()


@pytest.mark.asyncio
async def test_cursor_behavior_on_exceptions() -> None:
    """Demonstrate the vulnerability/risk of cursor behavior when a pool fails.
    
    If pool state query fails, self._last_block is not updated, causing log queries
    to fetch duplicates of successful pools in subsequent loops.
    """
    mock_w3 = SleepyMockWeb3()
    # We will simulate 2 resolved pools: one succeeds (A), one fails (B).
    # Since symbols in POOL_SPECS are predetermined, we use "cbBTC-USDC" (Uniswap V3)
    # and "WELL-WETH" (Aerodrome V2).
    # We will make theWELL-WETH state query fail.
    
    class CustomContractFunctions(SleepyMockContractFunctions):
        def slot0(self):
            class Call:
                async def call(self):
                    # Pool A (cbBTC-USDC) slot0 succeeds
                    return [2**96, 0, 0, 0, 0, 0, True]
            return Call()
            
        def getReserves(self):
            class Call:
                async def call(self):
                    # Pool B (WELL-WETH) getReserves fails
                    raise Exception("Persistent RPC read failure for WELL-WETH")
            return Call()

    def contract_override(address, abi):
        c = SleepyMockContract(address, mock_w3)
        c.functions = CustomContractFunctions(address, mock_w3)
        return c
    mock_w3.eth.contract = contract_override
    
    # We also mock get_logs to return a swap for Pool A
    swap_log = {
        "data": ((1 * 10**8).to_bytes(32, byteorder='big', signed=True) +
                 (40000 * 10**6).to_bytes(32, byteorder='big', signed=True)),
        "transactionHash": type('Hex', (object,), {'hex': lambda self: "0xhash"})(),
        "logIndex": 1,
        "blockNumber": 1000
    }
    
    log_calls = []
    async def get_logs_override(filter_params):
        log_calls.append(filter_params)
        return [swap_log] if filter_params["address"] == "0xMockPoolAddress" else []
        
    mock_w3.eth.get_logs = get_logs_override

    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        # We poll both symbols
        transport = BaseOnchainTransport(
            "mock_rpc", ["cbBTC-USDC", "WELL-WETH"], poll_interval=0.01
        )
        
        # Let's run the transport for 3 loops
        await transport.connect()
        for _ in range(200):
            if transport._last_blocks.get("cbBTC-USDC") == 1001:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        await transport.close()
        
        # 1. Verify that WELL-WETH did not advance because it failed, but cbBTC-USDC did advance
        assert transport._last_blocks["cbBTC-USDC"] == 1001
        assert transport._last_blocks["WELL-WETH"] == 980
        
        # 2. Verify that log calls did not query duplicates for cbBTC-USDC,
        # but kept querying from 981 for WELL-WETH.
        assert any(call["fromBlock"] == 996 for call in log_calls)
        assert any(call["fromBlock"] == 976 for call in log_calls)


def test_normalizer_robustness_invalid_types() -> None:
    """Test that the normalizer throws expected errors on corrupted types

    which the connector catches.
    """
    # Price is a string instead of a float/int
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": "forty thousand", # String type
            "reserve0": 10.0,
            "reserve1": 400000.0,
        },
        "swaps": []
    }
    
    with pytest.raises(TypeError):
        list(normalize_onchain_update(msg, local_ts=9999))

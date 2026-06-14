import asyncio
import json
import random
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport

class AwaitableValue:
    def __init__(self, val):
        self.val = val
    def __await__(self):
        async def _async_val():
            if isinstance(self.val, Exception):
                raise self.val
            return self.val
        return _async_val().__await__()


@pytest.mark.asyncio
async def test_pagination_extremely_large_range():
    """Verify block-range pagination under extremely large block ranges."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # Range of 100,000 blocks. Chunk size is 500, so we expect 200 chunk calls.
    start_block = 1000
    end_block = 101000
    transport._last_blocks["cbBTC-USDC"] = start_block
    
    captured_logs_calls = []
    
    async def mock_call_with_retry(func, *args, **kwargs):
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            return []
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return end_block
        if "slot0" in func_str or func_name == "slot0":
            return [2**96, 0, 0, 0, 0, 0, True]
        if "liquidity" in func_str or func_name == "liquidity":
            return 100
        if "tickSpacing" in func_str or func_name == "tickSpacing":
            return 10
        if "get_block" in func_str or func_name == "get_block":
            return {"timestamp": 1234567}
        if "getPool" in func_str or func_name == "getPool":
            return "0xPoolAddress"
        return None

    transport._call_with_retry = mock_call_with_retry
    
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x
        
        # Stop loop after 1 iteration
        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            transport._connected = False
            await original_sleep(0)
            
        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            assert transport._poll_task is not None
            await transport._poll_task
            
    assert len(captured_logs_calls) == 201
    # Verify bounds of the first and last chunk
    assert captured_logs_calls[0]["fromBlock"] == 996
    assert captured_logs_calls[0]["toBlock"] == 1495
    assert captured_logs_calls[-1]["fromBlock"] == 100996
    assert captured_logs_calls[-1]["toBlock"] == 101000


@pytest.mark.asyncio
async def test_pagination_empty_range():
    """Verify block-range pagination when start_block > end_block (empty range)."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # When start_block (last_block + 1) > end_block, no get_logs should be called.
    transport._last_blocks["cbBTC-USDC"] = 1000
    end_block = 1000
    
    captured_logs_calls = []
    
    async def mock_call_with_retry(func, *args, **kwargs):
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            return []
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return end_block
        if "slot0" in func_str or func_name == "slot0":
            return [2**96, 0, 0, 0, 0, 0, True]
        if "liquidity" in func_str or func_name == "liquidity":
            return 100
        if "tickSpacing" in func_str or func_name == "tickSpacing":
            return 10
        if "get_block" in func_str or func_name == "get_block":
            return {"timestamp": 1234567}
        if "getPool" in func_str or func_name == "getPool":
            return "0xPoolAddress"
        return None

    transport._call_with_retry = mock_call_with_retry
    
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            transport._connected = False
            await original_sleep(0)
            
        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task
            
    assert len(captured_logs_calls) == 1
    assert captured_logs_calls[0]["fromBlock"] == 996
    assert captured_logs_calls[0]["toBlock"] == 1000


@pytest.mark.asyncio
async def test_pagination_invalid_range():
    """Verify that error behavior is handled and propagates when block fetching fails or returns invalid value."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    with pytest.raises(ValueError, match="Invalid block height representation"):
        await transport._get_block_number(MagicMock(eth=MagicMock(block_number=AwaitableValue(ValueError("Invalid block height representation")))))


@pytest.mark.asyncio
async def test_backoff_retry_jitter_limits():
    """Verify exponential backoff progression, jitter scaling limits, and maximum delay capping."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=1.0)
    
    sleep_calls = []
    original_sleep = asyncio.sleep
    
    async def mock_sleep(delay):
        sleep_calls.append(delay)
        await original_sleep(0)
        
    call_count = 0
    async def mock_failing_func():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("Intermittent node failure")
        
    base_delay = 1.0
    with patch("asyncio.sleep", mock_sleep):
        with pytest.raises(ConnectionError):
            await transport._call_with_retry(mock_failing_func, base_delay=base_delay)
            
    assert call_count == 5  # Max attempts is 5
    assert len(sleep_calls) == 4  # Sleep is called 4 times before raising on 5th failure
    
    # Verify exponential delay progression and jitter bounds
    for i, delay in enumerate(sleep_calls):
        attempt = i + 1
        expected_raw_delay = min(10.0, base_delay * (2 ** (attempt - 1)))
        # Jitter: delay * random.uniform(0.5, 1.0)
        min_expected = expected_raw_delay * 0.5
        max_expected = expected_raw_delay * 1.0
        assert min_expected <= delay <= max_expected, f"Attempt {attempt} delay {delay} out of range [{min_expected}, {max_expected}]"
        
    # Test capping at max_delay = 10.0
    sleep_calls_capped = []
    async def mock_sleep_capped(delay):
        sleep_calls_capped.append(delay)
        await original_sleep(0)
        
    call_count = 0
    with patch("asyncio.sleep", mock_sleep_capped):
        with pytest.raises(ConnectionError):
            await transport._call_with_retry(mock_failing_func, base_delay=20.0)
            
    # base_delay = 20.0. Exponential delay before min(max_delay, ...) is 20.0, 40.0, etc.
    # Capped at 10.0, so expected_raw_delay must always be 10.0.
    # With jitter: [5.0, 10.0]
    for i, delay in enumerate(sleep_calls_capped):
        assert 5.0 <= delay <= 10.0, f"Capped delay {delay} out of expected range [5.0, 10.0]"


@pytest.mark.asyncio
async def test_retry_thundering_herd_jitter_distribution():
    """Verify that multiple concurrent tasks fail and retry with sufficiently distinct jittered times (no synchronization)."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=1.0)
    
    # We want to measure the sleep times of multiple concurrent calls
    task_sleeps = {}
    original_sleep = asyncio.sleep
    
    async def mock_sleep_trace(delay):
        current_task = asyncio.current_task()
        if current_task not in task_sleeps:
            task_sleeps[current_task] = []
        task_sleeps[current_task].append(delay)
        await original_sleep(0)
        
    async def failing_call_task():
        async def failing_func():
            raise ConnectionError("Intermittent error")
        try:
            await transport._call_with_retry(failing_func, base_delay=1.0)
        except ConnectionError:
            pass

    # Patch asyncio.sleep globally during the execution of gather
    with patch("asyncio.sleep", mock_sleep_trace):
        # Spawn 20 concurrent tasks all starting at the exact same time
        tasks = [failing_call_task() for _ in range(20)]
        await asyncio.gather(*tasks)
    
    assert len(task_sleeps) == 20
    
    # For each retry attempt (0 to 3), check the spread of delay values
    for attempt_idx in range(4):
        delays_at_attempt = [delays[attempt_idx] for delays in task_sleeps.values()]
        # Assert they are not all identical (no synchronization / thundering herd broken)
        unique_delays = set(delays_at_attempt)
        # 20 independent random.uniform(0.5, 1.0) draws are extremely unlikely to result in duplicates.
        assert len(unique_delays) > 1, f"Attempt {attempt_idx+1} delays synchronized!"
        # Verify min/max spread
        min_val = min(delays_at_attempt)
        max_val = max(delays_at_attempt)
        spread = max_val - min_val
        assert spread > 0.1, f"Attempt {attempt_idx+1} spread too narrow: {spread}"

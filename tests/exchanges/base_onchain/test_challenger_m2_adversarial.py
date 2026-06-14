import asyncio
import json
import logging
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport

# Setup logger
logger = logging.getLogger(__name__)


class AwaitableValue:
    def __init__(self, val):
        self.val = val
    def __await__(self):
        async def _async_val():
            if isinstance(self.val, Exception):
                raise self.val
            return self.val
        return _async_val().__await__()


# ---------------------------------------------------------
# Block-range Pagination Tests
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_extremely_large_range_chunking() -> None:
    """Verify that an extremely large block range is chunked correctly in units of 500 blocks."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # 1000 to 2600 is 1600 blocks range
    transport._last_blocks["cbBTC-USDC"] = 1000
    
    captured_logs_calls = []
    async def mock_call_with_retry(func, *args, **kwargs):
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            return []
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return 2600
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
            
    # Verify that the chunks are exactly 500 blocks, and the last is 105 blocks (due to 5 block overlap starting at 996)
    assert len(captured_logs_calls) == 4
    assert captured_logs_calls[0]["fromBlock"] == 996
    assert captured_logs_calls[0]["toBlock"] == 1495
    assert captured_logs_calls[1]["fromBlock"] == 1496
    assert captured_logs_calls[1]["toBlock"] == 1995
    assert captured_logs_calls[2]["fromBlock"] == 1996
    assert captured_logs_calls[2]["toBlock"] == 2495
    assert captured_logs_calls[3]["fromBlock"] == 2496
    assert captured_logs_calls[3]["toBlock"] == 2600


@pytest.mark.asyncio
async def test_pagination_error_loses_all_progress() -> None:
    """Verify the vulnerability where an error in the middle of chunk query 

    aborts the entire run, discarding all successfully retrieved logs 
    and resetting the cursor.
    """
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    transport._last_blocks["cbBTC-USDC"] = 1000
    
    captured_logs_calls = []
    call_idx = 0
    
    async def mock_call_with_retry(func, *args, **kwargs):
        nonlocal call_idx
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            call_idx += 1
            if call_idx == 3:  # Chunk 3 fails
                raise ValueError("RPC Error: rate limit")
            return [{"logIndex": call_idx, "data": b"\x00"*64, "transactionHash": MagicMock(hex=lambda: "0xhash"), "blockNumber": 1000}]
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return 2600
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
            
    # We expect 3 calls (Chunk 1 and 2 succeeded, Chunk 3 failed and raised error)
    assert len(captured_logs_calls) == 3
    # Check that _last_blocks was updated incrementally after successful pagination chunks (Chunk 2 went up to 1995)
    assert transport._last_blocks["cbBTC-USDC"] == 1995


@pytest.mark.asyncio
async def test_pagination_empty_range() -> None:
    """Verify that when start_block > end_block (due to reorg or no new blocks),

    no get_logs RPC calls are made.
    """
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # Last block is 1000, current block is 995 (reorg drop)
    transport._last_blocks["cbBTC-USDC"] = 1000
    
    captured_logs_calls = []
    async def mock_call_with_retry(func, *args, **kwargs):
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            return []
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return 995
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
            
    # Verify no log queries are executed
    assert len(captured_logs_calls) == 0
    # Verify that self._last_blocks["cbBTC-USDC"] remains 1000 (since max(1000, 995) == 1000)
    assert transport._last_blocks["cbBTC-USDC"] == 1000


@pytest.mark.asyncio
async def test_pagination_invalid_range_negative() -> None:
    """Verify handling when the range values are negative or invalid."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # Let last blocks be negative (manually corrupted state)
    transport._last_blocks["cbBTC-USDC"] = -10
    
    captured_logs_calls = []
    async def mock_call_with_retry(func, *args, **kwargs):
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            return []
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return 20
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
            
    # It queries starting from 0 to 20 (robust boundary max(0, ...))
    assert len(captured_logs_calls) == 1
    assert captured_logs_calls[0]["fromBlock"] == 0
    assert captured_logs_calls[0]["toBlock"] == 20


# ---------------------------------------------------------
# Backoff Retry Logic & Jitter Tests
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_logic_jitter_bounds_and_distribution() -> None:
    """Verify that backoff retry logic applies the expected exponential backoff 

    and that jitter is correctly bound between 0.5 and 1.0 of the delay.
    """
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    call_count = 0
    async def mock_fail():
        nonlocal call_count
        call_count += 1
        raise ValueError("Simulated network failure")
        
    captured_sleeps = []
    async def mock_asyncio_sleep(delay):
        captured_sleeps.append(delay)
        return
        
    # We patch asyncio.sleep to record and skip actual sleep delays
    with patch("asyncio.sleep", mock_asyncio_sleep):
        with pytest.raises(ValueError, match="Simulated network failure"):
            # Set base_delay=1.0, max_attempts=5
            await transport._call_with_retry(mock_fail, base_delay=1.0)
            
    assert call_count == 5
    assert len(captured_sleeps) == 4  # Sleeps before retries: attempts 1, 2, 3, 4
    
    # Expected delays without jitter:
    # Attempt 1: min(10.0, 1.0 * 2^0) = 1.0. Jitter: [0.5, 1.0]
    # Attempt 2: min(10.0, 1.0 * 2^1) = 2.0. Jitter: [1.0, 2.0]
    # Attempt 3: min(10.0, 1.0 * 2^2) = 4.0. Jitter: [2.0, 4.0]
    # Attempt 4: min(10.0, 1.0 * 2^3) = 8.0. Jitter: [4.0, 8.0]
    
    expected_ranges = [
        (0.5, 1.0),
        (1.0, 2.0),
        (2.0, 4.0),
        (4.0, 8.0),
    ]
    
    for i, sleep_val in enumerate(captured_sleeps):
        low, high = expected_ranges[i]
        assert low <= sleep_val <= high, f"Sleep {i} val {sleep_val} out of bounds [{low}, {high}]"


@pytest.mark.asyncio
async def test_retry_logic_indefinite_hang_vulnerability() -> None:
    """Demonstrate the critical vulnerability that the retry wrapper lacks a timeout,

    which allows a hanging RPC call to block the entire polling loop indefinitely.
    """
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # Create an event that will never be set, representing a hanging connection/RPC call
    never_set_event = asyncio.Event()
    
    async def mock_hanging_rpc():
        await never_set_event.wait()
        return "success"
        
    # We run call_with_retry in a task. It should hang.
    task = asyncio.create_task(transport._call_with_retry(mock_hanging_rpc))
    
    # Wait for a short duration with asyncio.wait_for.
    # We expect this to raise TimeoutError, confirming that the function hangs indefinitely
    # and doesn't time out internally.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=0.2)
        
    # Cancel the hanging task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_thundering_herd_concurrency() -> None:
    """Simulate a thundering herd scenario where multiple connections fail 

    simultaneously. Verify that the restricted jitter [0.5, 1.0] keeps retry times 
    highly correlated, failing to distribute the load as widely as full jitter.
    """
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # Simulate 50 concurrent tasks failing on their first attempt
    # Delay for first retry is min(10.0, 1.0 * 2^0) = 1.0
    # Jittered delay is 1.0 * random.uniform(0.5, 1.0), so all retries fall in [0.5, 1.0].
    # This means the maximum separation between any two retries is only 0.5s.
    
    num_tasks = 50
    delays = []
    
    async def mock_sleep_record(delay):
        delays.append(delay)
        return
        
    async def run_failing_task():
        attempt = 0
        async def fail_call():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise ValueError("Failure")
            return "success"
        with patch("asyncio.sleep", mock_sleep_record):
            await transport._call_with_retry(fail_call, base_delay=1.0)


    # Run the tasks concurrently
    await asyncio.gather(*(run_failing_task() for _ in range(num_tasks)))
    
    # We only care about the first attempt retry sleeps
    first_retry_sleeps = delays[:num_tasks]
    assert len(first_retry_sleeps) == num_tasks
    
    # Assert all delays are within [0.5, 1.0]
    for d in first_retry_sleeps:
        assert 0.5 <= d <= 1.0
        
    # Measure the standard deviation of retry delays
    mean = sum(first_retry_sleeps) / num_tasks
    variance = sum((x - mean) ** 2 for x in first_retry_sleeps) / num_tasks
    std_dev = variance ** 0.5
    
    # Standard deviation of uniform distribution on [0.5, 1.0] is (1.0 - 0.5) / sqrt(12) = 0.5 / 3.464 = 0.144.
    # Contrast this with full jitter [0.0, 1.0] whose standard deviation is 1.0 / sqrt(12) = 0.288.
    # The lower standard deviation and higher minimum boundary (0.5s vs 0.0s) means the retries are clustered 
    # much closer together, exposing the server to higher peak load (thundering herd).
    logger.info(f"Thundering herd simulation: retry delays mean={mean:.4f}s, std_dev={std_dev:.4f}s")
    assert std_dev < 0.2

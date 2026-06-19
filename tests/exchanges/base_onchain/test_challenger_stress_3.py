import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport
from crypcodile.exchanges.base_onchain.normalize import normalize_onchain_update


class LaggingMockWeb3:
    def __init__(self, block_sequence: list[int]):
        self.block_sequence = block_sequence
        self.call_count = 0
        self.eth = LaggingMockEth(self)
        
    @staticmethod
    def to_checksum_address(addr):
        return addr

class LaggingMockEth:
    def __init__(self, parent: LaggingMockWeb3):
        self.parent = parent
        
    @property
    async def block_number(self) -> int:
        seq = self.parent.block_sequence
        idx = min(self.parent.call_count, len(seq) - 1)
        self.parent.call_count += 1
        return seq[idx]
        
    def contract(self, address, abi):
        return DummyMockContract(address)
        
    async def get_block(self, block_num):
        return {"timestamp": 1600000000}
        
    async def get_logs(self, filter_params):
        if filter_params["fromBlock"] > filter_params["toBlock"]:
            raise ValueError("fromBlock cannot be greater than toBlock")
        return []

class DummyMockContract:
    def __init__(self, address):
        self.address = address
        self.functions = DummyMockContractFunctions(address)

class DummyMockContractFunctions:
    def __init__(self, address):
        self.address = address

    def getPool(self, *args, **kwargs):
        class Call:
            async def call(self):
                return "0xMockPoolAddress"
        return Call()

    def slot0(self):
        class Call:
            async def call(self):
                return [2**96, 0, 0, 0, 0, 0, True]
        return Call()

    def liquidity(self):
        class Call:
            async def call(self):
                return 1000000
        return Call()


@pytest.mark.asyncio
async def test_cursor_behavior_on_block_lag() -> None:
    """Test cursor behavior when RPC node reports a block number lower than last block

    (block lag/reorg).
    """
    # Block sequence:
    # 1. 1000 (initial, will initialize self._last_block = 1000 - 20 = 980.
    #    Then runs loop, succeeds, sets self._last_block = 1000)
    # 2. 990 (lagging block, 1001 > 990 leads to ValueError in get_logs.
    #    Loop fails, success = False, self._last_block stays 1000)
    # 3. 1010 (recovering, queries 1001 to 1010, succeeds, sets self._last_block = 1010)
    mock_w3 = LaggingMockWeb3(block_sequence=[1000, 990, 1010])

    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.01)

        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            if mock_w3.call_count >= 3:
                transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task

        # Verify that the transport handled the ValueError from get_logs (due to 1001 > 990)
        # and on the third block (1010), it successfully queried 1001 to 1010
        # because the cursor was still 1000.
        assert transport._last_blocks["cbBTC-USDC"] == 995


@pytest.mark.asyncio
async def test_block_cache_memory_efficiency() -> None:
    """Verify that _block_cache size limit is respected and does not grow indefinitely

    (memory leak verification).
    """
    mock_w3 = MagicMock()
    mock_w3.eth.get_block = AsyncMock(
        side_effect=lambda block_num: {"timestamp": 1600000000 + block_num}
    )

    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"])
    
    # Fill cache up to the 1000 threshold
    for i in range(1001):
        ts = await transport._get_block_timestamp(mock_w3, i)
        assert ts == 1600000000 + i
    
    # Assert cache size is 1001
    assert len(transport._block_cache) == 1001

    # Retrieve one more block, triggering clear logic
    ts = await transport._get_block_timestamp(mock_w3, 1001)
    assert ts == 1600000000 + 1001
    
    # Cache should have cleared and now only contains the 1001st block
    assert len(transport._block_cache) == 1
    assert 1001 in transport._block_cache


def test_normalize_robustness_null_and_missing_fields() -> None:
    """Verify normalizer behavior with None or missing values for fields."""
    
    # Test case 1: Price is None (should raise TypeError when comparing)
    msg_null_price = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": None,
            "reserve0": 10.0,
            "reserve1": 400000.0,
        },
        "swaps": []
    }
    with pytest.raises(TypeError):
        list(normalize_onchain_update(msg_null_price, local_ts=9999))

    # Test case 2: Reserves are None
    msg_null_reserves = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "reserve0": None,
            "reserve1": None,
        },
        "swaps": []
    }
    with pytest.raises(TypeError):
        list(normalize_onchain_update(msg_null_reserves, local_ts=9999))

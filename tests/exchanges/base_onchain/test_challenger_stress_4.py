import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport, BaseOnchainConnector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.sink.memory import MemorySink

class CustomMockWeb3:
    def __init__(self, fail_reserves=False, fail_slot0=False):
        self.fail_reserves = fail_reserves
        self.fail_slot0 = fail_slot0
        self.eth = CustomMockEth(self)
        
    @staticmethod
    def to_checksum_address(addr):
        return addr

class CustomMockEth:
    def __init__(self, parent):
        self.parent = parent
        self._block_number = 1000
        
    @property
    async def block_number(self):
        return self._block_number
        
    def contract(self, address, abi):
        return CustomMockContract(address, self.parent)
        
    async def get_block(self, block_num):
        return {"timestamp": 1600000000}
        
    async def get_logs(self, filter_params):
        return []

class CustomMockContract:
    def __init__(self, address, parent):
        self.address = address
        self.parent = parent
        self.functions = CustomMockContractFunctions(address, parent)

class CustomMockContractFunctions:
    def __init__(self, address, parent):
        self.address = address
        self.parent = parent

    def getPool(self, *args, **kwargs):
        class Call:
            async def call(self):
                return "0xMockPoolAddress"
        return Call()

    def slot0(self):
        class Call:
            def __init__(self, parent):
                self.parent = parent
            async def call(self):
                if self.parent.fail_slot0:
                    raise Exception("Slot0 query failed")
                return [2**96, 0, 0, 0, 0, 0, True]
        return Call(self.parent)

    def liquidity(self):
        class Call:
            async def call(self):
                return 1000000
        return Call()

    def getReserves(self):
        class Call:
            def __init__(self, parent):
                self.parent = parent
            async def call(self):
                if self.parent.fail_reserves:
                    raise Exception("Reserves query failed")
                return [10000, 10000, 12345]
        return Call(self.parent)


@pytest.mark.asyncio
async def test_unbound_local_error_regression_aerodrome(caplog: pytest.LogCaptureFixture) -> None:
    """Verify that a failure in getReserves (Aerodrome V2) triggers UnboundLocalError

    due to 'swaps' being accessed in step C before initialization.
    """
    mock_w3 = CustomMockWeb3(fail_reserves=True)
    
    with caplog.at_level(logging.ERROR), \
         patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        # WELL-WETH is aerodrome_v2
        transport = BaseOnchainTransport("mock_rpc", ["WELL-WETH"], poll_interval=0.01)
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            transport._connected = False
            await original_sleep(0)
            
        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task
            
        # Verify that UnboundLocalError / local variable 'swaps' error is NOT in the logs
        assert not any(
            "UnboundLocalError" in record.message or "cannot access local variable" in record.message
            for record in caplog.records
        )
        assert any(
            "Reserves query failed" in record.message
            for record in caplog.records
        )


@pytest.mark.asyncio
async def test_unbound_local_error_regression_uniswap(caplog: pytest.LogCaptureFixture) -> None:
    """Verify that a failure in slot0 (Uniswap V3) triggers UnboundLocalError

    due to 'swaps' being accessed in step C before initialization.
    """
    mock_w3 = CustomMockWeb3(fail_slot0=True)
    
    with caplog.at_level(logging.ERROR), \
         patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        # cbBTC-USDC is uniswap_v3
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.01)
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            transport._connected = False
            await original_sleep(0)
            
        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task
            
        assert not any(
            "UnboundLocalError" in record.message or "cannot access local variable" in record.message
            for record in caplog.records
        )
        assert any(
            "Slot0 query failed" in record.message
            for record in caplog.records
        )

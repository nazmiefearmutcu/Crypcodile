import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector, BaseOnchainTransport
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, BookTicker, Trade
from crypcodile.sink.memory import MemorySink

@pytest.fixture(autouse=True)
def mock_load_ipc_sync():
    with patch("crypcodile.exchanges.base_onchain.connector._load_ipc_sync", return_value=None):
        yield

class AwaitableValue:
    def __init__(self, val: Any) -> None:
        self.val = val
    def __await__(self) -> Any:
        async def _async_val() -> Any:
            if isinstance(self.val, Exception):
                raise self.val
            return self.val
        return _async_val().__await__()

TEST_POOL_SPECS = {
    "AERO-USDC": {
        "type": "aerodrome_v2",
        "token0": "AERO",
        "token1": "USDbC",
        "stable": False,
        "decimals0": 18,
        "decimals1": 6,
    },
    "cbBTC-USDC": {
        "type": "uniswap_v3",
        "token0": "cbBTC",
        "token1": "USDC",
        "fee": 500,
        "decimals0": 8,
        "decimals1": 6,
    },
    "DEGEN-WETH": {
        "type": "uniswap_v3",
        "token0": "DEGEN",
        "token1": "WETH",
        "fee": 3000,
        "decimals0": 18,
        "decimals1": 18,
    },
    "WELL-WETH": {
        "type": "aerodrome_v2",
        "token0": "WELL",
        "token1": "WETH",
        "stable": False,
        "decimals0": 18,
        "decimals1": 18,
    }
}

TEST_TOKENS = {
    "AERO": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    "cbBTC": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
    "DEGEN": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",
    "WELL": "0xA88594D404727625A9437C3f886C7643872296AE",
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
    "USDbC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
    "WETH": "0x4200000000000000000000000000000000000006",
}


@pytest.mark.asyncio
async def test_uniswap_v3_standard_pool() -> None:
    # Simulating standard Uniswap V3 pool where token0 address < token1 address.
    mocked_tokens = TEST_TOKENS.copy()
    mocked_tokens["cbBTC"] = "0x1111111111111111111111111111111111111111"
    mocked_tokens["USDC"] = "0x2222222222222222222222222222222222222222"

    with patch.dict("crypcodile.exchanges.base_onchain.connector.POOL_SPECS", TEST_POOL_SPECS), \
         patch.dict("crypcodile.exchanges.base_onchain.connector.TOKENS", mocked_tokens), \
         patch("web3.AsyncWeb3") as mock_web3_class:

        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(1000)
        
        # Factory contract mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(return_value=(
            "0xMockV3StandardPoolAddress"
        ))
        
        # Pool contract mock
        mock_pool = MagicMock()
        # sqrtPriceX96 = 2**96 * 2 -> price ratio = 4.0.
        # Since not flipped: price = price_ratio * 10**(decimals0 - decimals1)
        # = 4.0 * 10**(8-6) = 400.0
        mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[
            (2**96 * 2), 0, 0, 0, 0, 0, True
        ])
        mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=100 * 10**8)
        
        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect
        
        # Get logs mock: return 1 swap log
        # amount0 = -5 * 10**8 (negative, base bought)
        # amount1 = 2000 * 10**6 (quote deposited)
        mock_log = {
            "data": ((-5 * 10**8).to_bytes(32, byteorder='big', signed=True) +
                     (2000 * 10**6).to_bytes(32, byteorder='big', signed=True)),
            "transactionHash": MagicMock(hex=lambda: "0xhash"),
            "logIndex": 1,
            "blockNumber": 1000
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
        
        # Block timestamp mock
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})

        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
        
        # Mock asyncio.sleep to exit polling loop after 1 run
        original_sleep = asyncio.sleep
        async def mock_sleep(delay: Any) -> None:
            transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            assert transport._poll_task is not None
            await transport._poll_task

        # Retrieve update message from the queue
        assert transport._queue.qsize() == 1
        msg_bytes = await transport._queue.get()
        assert msg_bytes is not None
        msg = json.loads(msg_bytes.decode())
        
        assert msg["type"] == "onchain_update"
        assert msg["pool"] == "cbBTC-USDC"
        assert msg["state"]["price"] == 400.0
        # reserve0 = x_virtual / 10**8 -> x_virtual = liquidity / sqrtP = 50 * 10**8
        assert msg["state"]["reserve0"] == 50.0
        # reserve1 = y_virtual / 10**6 -> y_virtual = liquidity * sqrtP = 200 * 10**8
        assert msg["state"]["reserve1"] == 20000.0
        
        assert len(msg["swaps"]) == 1
        swap = msg["swaps"][0]
        assert swap["price"] == 400.0
        assert swap["amount"] == 5.0
        assert swap["is_buy"] is True


@pytest.mark.asyncio
async def test_uniswap_v3_flipped_pool() -> None:
    # cbBTC-USDC is flipped: USDC (0x8335...) < cbBTC (0xcbb7...).
    with patch.dict("crypcodile.exchanges.base_onchain.connector.POOL_SPECS", TEST_POOL_SPECS), \
         patch.dict("crypcodile.exchanges.base_onchain.connector.TOKENS", TEST_TOKENS), \
         patch("web3.AsyncWeb3") as mock_web3_class:

        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(1000)
        
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(return_value=(
            "0xMockV3FlippedPoolAddress"
        ))
        
        mock_pool = MagicMock()
        # sqrtPriceX96 = 2**96 / 2 -> price ratio = 0.25
        # Since flipped: price = (1.0 / price_ratio) * 10**(decimals0 - decimals1)
        mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[
            int(2**96 / 2), 0, 0, 0, 0, 0, True
        ])
        mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=100 * 10**8)
        
        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect
        
        # Get logs mock: return 1 swap log
        # amount1 (base) = -5 * 10**8, amount0 (quote) = 2000 * 10**6
        mock_log = {
            "data": ((2000 * 10**6).to_bytes(32, byteorder='big', signed=True) +
                     (-5 * 10**8).to_bytes(32, byteorder='big', signed=True)),
            "transactionHash": MagicMock(hex=lambda: "0xhash"),
            "logIndex": 1,
            "blockNumber": 1000
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})

        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay: Any) -> None:
            transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            assert transport._poll_task is not None
            await transport._poll_task

        assert transport._queue.qsize() == 1
        msg_bytes = await transport._queue.get()
        assert msg_bytes is not None
        msg = json.loads(msg_bytes.decode())
        
        assert msg["state"]["price"] == 400.0
        # reserve0 = y_virtual / 10**8 -> y_virtual = liquidity * sqrtP = 50 * 10**8
        assert msg["state"]["reserve0"] == 50.0
        # reserve1 = x_virtual / 10**6 -> x_virtual = liquidity / sqrtP = 200 * 10**8
        assert msg["state"]["reserve1"] == 20000.0
        
        assert len(msg["swaps"]) == 1
        swap = msg["swaps"][0]
        assert swap["price"] == 400.0
        assert swap["amount"] == 5.0
        assert swap["is_buy"] is True


@pytest.mark.asyncio
async def test_aerodrome_v2_standard_pool() -> None:
    # AERO-USDC is standard: AERO (0x9401...) < USDbC (0xd9aA...).
    with patch.dict("crypcodile.exchanges.base_onchain.connector.POOL_SPECS", TEST_POOL_SPECS), \
         patch.dict("crypcodile.exchanges.base_onchain.connector.TOKENS", TEST_TOKENS), \
         patch("web3.AsyncWeb3") as mock_web3_class:

        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(1000)
        
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(return_value=(
            "0xMockAeroStandardPoolAddress"
        ))
        
        mock_pool = MagicMock()
        # reserve0 = 1000 * 10**18, reserve1 = 2000 * 10**6.
        # Since standard: reserve0 = 1000.0, reserve1 = 2000.0. price = 2.0
        mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=[
            (1000 * 10**18), (2000 * 10**6), 1234567
        ])
        
        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x420DD381b31aEf6683db6B902084cB0FFECe40Da":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect
        
        # Swap: amt0_in = 0, amt1_in = 10 * 10**6, amt0_out = 5 * 10**18, amt1_out = 0
        mock_log = {
            "data": ((0).to_bytes(32, byteorder='big') +
                     (10 * 10**6).to_bytes(32, byteorder='big') +
                     (5 * 10**18).to_bytes(32, byteorder='big') +
                     (0).to_bytes(32, byteorder='big')),
            "transactionHash": MagicMock(hex=lambda: "0xhash"),
            "logIndex": 1,
            "blockNumber": 1000
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})

        transport = BaseOnchainTransport("mock_rpc", ["AERO-USDC"], poll_interval=0.1)
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay: Any) -> None:
            transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            assert transport._poll_task is not None
            await transport._poll_task

        assert transport._queue.qsize() == 1
        msg_bytes = await transport._queue.get()
        assert msg_bytes is not None
        msg = json.loads(msg_bytes.decode())
        
        assert msg["state"]["price"] == 2.0
        assert msg["state"]["reserve0"] == 1000.0
        assert msg["state"]["reserve1"] == 2000.0
        
        assert len(msg["swaps"]) == 1
        swap = msg["swaps"][0]
        assert swap["price"] == 2.0
        assert swap["amount"] == 5.0
        assert swap["is_buy"] is True


@pytest.mark.asyncio
async def test_aerodrome_v2_flipped_pool() -> None:
    # WELL-WETH is flipped: WETH (0x4200...) < WELL (0xA885...).
    with patch.dict("crypcodile.exchanges.base_onchain.connector.POOL_SPECS", TEST_POOL_SPECS), \
         patch.dict("crypcodile.exchanges.base_onchain.connector.TOKENS", TEST_TOKENS), \
         patch("web3.AsyncWeb3") as mock_web3_class:

        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(1000)
        
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(return_value=(
            "0xMockAeroFlippedPoolAddress"
        ))
        
        mock_pool = MagicMock()
        # res[0] = 50 * 10**18 (WETH, quote), res[1] = 100 * 10**18 (WELL, base).
        mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=[
            (50 * 10**18), (100 * 10**18), 1234567
        ])
        
        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x420DD381b31aEf6683db6B902084cB0FFECe40Da":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect
        
        # Swap: amt0_in = 2*10**18, amt1_in = 0, amt0_out = 0, amt1_out = 4*10**18
        mock_log = {
            "data": ((2 * 10**18).to_bytes(32, byteorder='big') +
                     (0).to_bytes(32, byteorder='big') +
                     (0).to_bytes(32, byteorder='big') +
                     (4 * 10**18).to_bytes(32, byteorder='big')),
            "transactionHash": MagicMock(hex=lambda: "0xhash"),
            "logIndex": 1,
            "blockNumber": 1000
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})

        transport = BaseOnchainTransport("mock_rpc", ["WELL-WETH"], poll_interval=0.1)
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay: Any) -> None:
            transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            assert transport._poll_task is not None
            await transport._poll_task

        assert transport._queue.qsize() == 1
        msg_bytes = await transport._queue.get()
        assert msg_bytes is not None
        msg = json.loads(msg_bytes.decode())
        
        assert msg["state"]["price"] == 0.5
        assert msg["state"]["reserve0"] == 100.0
        assert msg["state"]["reserve1"] == 50.0
        
        assert len(msg["swaps"]) == 1
        swap = msg["swaps"][0]
        assert swap["price"] == 0.5
        assert swap["amount"] == 4.0
        assert swap["is_buy"] is True


def test_connector_normalization_and_records() -> None:
    sink = MemorySink()
    registry = InstrumentRegistry()
    connector = BaseOnchainConnector(
        symbols=["cbBTC-USDC"],
        channels=["trade", "book_delta"],
        out=sink,
        registry=registry,
    )
    
    update_msg = {
        "type": "onchain_update",
        "block": 1000,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1234567890,
        "state": {
            "price": 40000.0,
            "reserve0": 10.0,
            "reserve1": 400000.0,
        },
        "swaps": [
            {
                "tx_hash": "0xabc",
                "log_index": 5,
                "timestamp": 1234567891,
                "price": 40100.0,
                "amount": 0.5,
                "is_buy": True,
            }
        ]
    }
    
    records = list(connector.normalize(update_msg, local_ts=9999))
    
    assert len(records) == 3
    
    trades = [r for r in records if isinstance(r, Trade)]
    tickers = [r for r in records if isinstance(r, BookTicker)]
    snapshots = [r for r in records if isinstance(r, BookSnapshot)]
    
    assert len(trades) == 1
    assert trades[0].symbol == "base_onchain:cbBTC-USDC"
    assert trades[0].price == 40100.0
    assert trades[0].amount == 0.5
    assert trades[0].side == Side.BUY
    
    assert len(tickers) == 1
    assert tickers[0].bid_px == 40000.0 * 0.9995
    assert tickers[0].ask_px == 40000.0 * 1.0005
    assert tickers[0].update_id == 1000
    
    assert len(snapshots) == 1
    assert snapshots[0].bids[0][0] == 40000.0 * 0.9995
    assert snapshots[0].asks[0][0] == 40000.0 * 1.0005


@pytest.mark.asyncio
async def test_liveness_queue_sentinel() -> None:
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=10.0)
        await transport.connect()
        await transport.close()
        
        generator = transport.__aiter__()
        results = []
        async for val in generator:
            results.append(val)
        
        assert len(results) == 0
        assert transport._connected is False


@pytest.mark.asyncio
async def test_rpc_retries_and_call_with_retry() -> None:
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    # 1. Test success after 3 failures
    call_count = 0
    async def mock_rpc_call():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("RPC Error")
        return "success"
        
    res = await transport._call_with_retry(mock_rpc_call, base_delay=0.0001)
    assert res == "success"
    assert call_count == 3
    
    # 2. Test failure after 5 attempts
    call_count_fail = 0
    async def mock_rpc_fail():
        nonlocal call_count_fail
        call_count_fail += 1
        raise ValueError("Persistent RPC Error")
        
    with pytest.raises(ValueError, match="Persistent RPC Error"):
        await transport._call_with_retry(mock_rpc_fail, base_delay=0.0001)
    assert call_count_fail == 5


@pytest.mark.asyncio
async def test_log_pagination_chunking() -> None:
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    captured_logs_calls = []
    async def mock_call_with_retry(func, *args, **kwargs):
        func_name = getattr(func, "__name__", None) or ""
        func_str = str(func)
        if "get_logs" in func_str or func_name == "get_logs":
            captured_logs_calls.append(args[0])
            return []
        if "block_number" in func_str or func_name == "get_bn" or "get_bn" in func_str:
            return 2100
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
    transport._last_blocks["cbBTC-USDC"] = 1000
    
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay: Any) -> None:
            transport._connected = False
            await original_sleep(0)
            
        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task
            
    assert len(captured_logs_calls) == 3
    assert captured_logs_calls[0]["fromBlock"] == 996
    assert captured_logs_calls[0]["toBlock"] == 1495
    assert captured_logs_calls[1]["fromBlock"] == 1496
    assert captured_logs_calls[1]["toBlock"] == 1995
    assert captured_logs_calls[2]["fromBlock"] == 1996
    assert captured_logs_calls[2]["toBlock"] == 2085


def test_realistic_multilevel_orderbook_normalization() -> None:
    from crypcodile.exchanges.base_onchain.normalize import normalize_onchain_update
    
    # 1. Uniswap V3 Pool with Liquidity (not flipped)
    v3_msg = {
        "type": "onchain_update",
        "block": 1000,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1234567890,
        "state": {
            "price": 50000.0,
            "reserve0": 10.0,
            "reserve1": 500000.0,
            "tick": 100,
            "liquidity": 10000 * 10**8,
            "tickSpacing": 10,
            "decimals0": 8,
            "decimals1": 6,
            "is_flipped": False
        },
        "swaps": []
    }
    
    records = list(normalize_onchain_update(v3_msg, local_ts=9999))
    snapshot = next(r for r in records if isinstance(r, BookSnapshot))
    
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    
    for i in range(4):
        assert snapshot.bids[i][0] > snapshot.bids[i+1][0]
        assert snapshot.asks[i][0] < snapshot.asks[i+1][0]
        assert snapshot.bids[i][1] < snapshot.bids[i+1][1]
        assert snapshot.asks[i][1] > snapshot.asks[i+1][1]

    # Check flipped pool calculations
    v3_flipped_msg = {
        "type": "onchain_update",
        "block": 1000,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1234567890,
        "state": {
            "price": 50000.0,
            "reserve0": 10.0,
            "reserve1": 500000.0,
            "tick": 100,
            "liquidity": 10000 * 10**8,
            "tickSpacing": 10,
            "decimals0": 8,
            "decimals1": 6,
            "is_flipped": True
        },
        "swaps": []
    }
    
    records_flipped = list(normalize_onchain_update(v3_flipped_msg, local_ts=9999))
    snapshot_flipped = next(r for r in records_flipped if isinstance(r, BookSnapshot))
    
    assert len(snapshot_flipped.bids) == 5
    assert len(snapshot_flipped.asks) == 5
    for i in range(4):
        assert snapshot_flipped.bids[i][0] > snapshot_flipped.bids[i+1][0]
        assert snapshot_flipped.asks[i][0] < snapshot_flipped.asks[i+1][0]

    # 2. Aerodrome V2 Pool (5 levels reserve based)
    aero_msg = {
        "type": "onchain_update",
        "block": 1000,
        "pool": "AERO-USDC",
        "pool_type": "aerodrome_v2",
        "timestamp": 1234567890,
        "state": {
            "price": 2.0,
            "reserve0": 1000.0,
            "reserve1": 2000.0,
            "decimals0": 18,
            "decimals1": 6,
        },
        "swaps": []
    }
    
    records_aero = list(normalize_onchain_update(aero_msg, local_ts=9999))
    snapshot_aero = next(r for r in records_aero if isinstance(r, BookSnapshot))
    
    assert len(snapshot_aero.bids) == 5
    assert len(snapshot_aero.asks) == 5
    assert snapshot_aero.bids[0][0] == pytest.approx(2.0 * 0.9995)
    assert snapshot_aero.bids[1][0] == pytest.approx(2.0 * 0.9990)
    
    for i in range(4):
        assert snapshot_aero.bids[i][0] > snapshot_aero.bids[i+1][0]
        assert snapshot_aero.asks[i][0] < snapshot_aero.asks[i+1][0]
        assert snapshot_aero.bids[i][1] < snapshot_aero.bids[i+1][1]
        assert snapshot_aero.asks[i][1] > snapshot_aero.asks[i+1][1]


@pytest.mark.asyncio
async def test_custom_pool_configuration_and_dynamic_listing() -> None:
    from crypcodile.exchanges.base_onchain.connector import POOL_SPECS, TOKENS
    
    custom_pools = {
        "TESTCUSTOM-WETH": {
            "type": "uniswap_v3",
            "token0": "TESTCUSTOM",
            "token0_address": "0xCustomTokenAddress00000000000000000000000",
            "token1": "WETH",
            "token1_address": "0x4200000000000000000000000000000000000006",
            "fee": 500,
            "decimals0": 18,
            "decimals1": 18,
        }
    }
    
    sink = MemorySink()
    registry = InstrumentRegistry()
    
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        
        connector = BaseOnchainConnector(
            symbols=["TESTCUSTOM-WETH"],
            channels=["book_delta"],
            out=sink,
            registry=registry,
            custom_pools=custom_pools
        )
        
        assert "TESTCUSTOM-WETH" in POOL_SPECS
        assert "TESTCUSTOM" in TOKENS
        assert TOKENS["TESTCUSTOM"] == "0xCustomTokenAddress00000000000000000000000"
        
        instruments = await connector.list_instruments()
        assert len(instruments) == 1
        inst = instruments[0]
        assert inst.symbol_raw == "TESTCUSTOM-WETH"
        assert inst.base == "TESTCUSTOM"
        assert inst.quote == "WETH"
        assert inst.tick_size == 1e-18


def test_custom_pool_parameter_validation() -> None:
    from crypcodile.exchanges.base_onchain.connector import _register_custom_pools
    
    # 1. Unsupported type
    with pytest.raises(ValueError, match="Unsupported pool type"):
        _register_custom_pools({
            "BAD-POOL": {
                "type": "balancer",
                "token0": "AERO",
                "token0_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
            }
        })
        
    # 2. Malformed EVM address
    with pytest.raises(ValueError, match="Malformed EVM address"):
        _register_custom_pools({
            "BAD-ADDR": {
                "type": "uniswap_v3",
                "token0": "AERO",
                "token0_address": "0xinvalidaddress",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                "fee": 500,
            }
        })

    # 3. Invalid decimals
    with pytest.raises(ValueError, match="decimals0 must be an integer between 0 and 36"):
        _register_custom_pools({
            "BAD-DEC": {
                "type": "uniswap_v3",
                "token0": "AERO",
                "token0_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                "fee": 500,
                "decimals0": 37,
            }
        })
        
    with pytest.raises(ValueError, match="decimals1 must be an integer between 0 and 36"):
        _register_custom_pools({
            "BAD-DEC2": {
                "type": "uniswap_v3",
                "token0": "AERO",
                "token0_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                "fee": 500,
                "decimals1": -1,
            }
        })

    with pytest.raises(ValueError, match="decimals0 must be an integer between 0 and 36"):
        _register_custom_pools({
            "BAD-DEC3": {
                "type": "uniswap_v3",
                "token0": "AERO",
                "token0_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                "fee": 500,
                "decimals0": "18",
            }
        })

    # 4. Uniswap V3 missing fee when no address is specified
    msg = "fee is required for uniswap_v3 when address is not specified"
    with pytest.raises(ValueError, match=msg):
        _register_custom_pools({
            "MISSING-FEE": {
                "type": "uniswap_v3",
                "token0": "AERO",
                "token0_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
            }
        })

    # 5. Aerodrome V2 missing stable when no address is specified
    msg = "stable is required for aerodrome_v2 when address is not specified"
    with pytest.raises(ValueError, match=msg):
        _register_custom_pools({
            "MISSING-STABLE": {
                "type": "aerodrome_v2",
                "token0": "AERO",
                "token0_address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
                "token1": "USDC",
                "token1_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
            }
        })


def test_ipc_dict_reload_on_modification(tmp_path) -> None:
    import time

    from crypcodile.exchanges.base_onchain.connector import IPCDict
    
    ipc_file = tmp_path / "test_ipc.json"
    
    # Write initial state
    initial_data = {
        "MY_DICT": {
            "key1": "val1"
        }
    }
    with open(ipc_file, "w") as f:
        json.dump(initial_data, f)
        
    ipc_file_patch = "crypcodile.exchanges.base_onchain.connector._get_ipc_file"
    with patch(ipc_file_patch, return_value=str(ipc_file)):
        my_dict = IPCDict("MY_DICT", {"default_key": "default_val"})
        
        # Initial sync
        assert my_dict["key1"] == "val1"
        assert my_dict.get("default_key") == "default_val"
        
        # Modify the file, update st_mtime and size
        updated_data = {
            "MY_DICT": {
                "key1": "val_updated",
                "key2": "val2"
            }
        }
        
        # Sleep briefly to ensure mtime changes
        time.sleep(0.01)
        with open(ipc_file, "w") as f:
            json.dump(updated_data, f)
            
        # The dictionary should detect mtime change and reload
        assert my_dict["key1"] == "val_updated"
        assert my_dict["key2"] == "val2"


@pytest.mark.asyncio
async def test_flipped_pool_tick_size_and_custom_tick_size() -> None:
    from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector
    from crypcodile.instruments.registry import InstrumentRegistry
    from crypcodile.sink.memory import MemorySink

    sink = MemorySink()
    registry = InstrumentRegistry()

    # Clear POOL_SPECS to avoid conflicts in this test
    with patch.dict("crypcodile.exchanges.base_onchain.connector.POOL_SPECS", {}, clear=True):
        custom_pools = {
            # Standard pool: token0_address (0x1111...) < token1_address (0x2222...)
            "STANDARD-POOL": {
                "type": "uniswap_v3",
                "token0": "T0",
                "token0_address": "0x1111111111111111111111111111111111111111",
                "token1": "T1",
                "token1_address": "0x2222222222222222222222222222222222222222",
                "fee": 500,
                "decimals0": 18,
                "decimals1": 6,
            },
            # Flipped pool: token1_address (0x1111...) < token0_address (0x2222...)
            "FLIPPED-POOL": {
                "type": "uniswap_v3",
                "token0": "T0_FLIP",
                "token0_address": "0x2222222222222222222222222222222222222222",
                "token1": "T1_FLIP",
                "token1_address": "0x1111111111111111111111111111111111111111",
                "fee": 500,
                "decimals0": 8,
                "decimals1": 18,
            },
            # Custom tick size pool
            "CUSTOM-TICK-POOL": {
                "type": "uniswap_v3",
                "token0": "T0_CUST",
                "token0_address": "0x1111111111111111111111111111111111111111",
                "token1": "T1_CUST",
                "token1_address": "0x2222222222222222222222222222222222222222",
                "fee": 500,
                "decimals0": 18,
                "decimals1": 18,
                "tick_size": 0.05,
            }
        }

        connector = BaseOnchainConnector(
            symbols=["STANDARD-POOL", "FLIPPED-POOL", "CUSTOM-TICK-POOL"],
            channels=["book_delta"],
            out=sink,
            registry=registry,
            custom_pools=custom_pools
        )

        instruments = {inst.symbol_raw: inst for inst in await connector.list_instruments()}
        
        # 1. Standard pool: uses decimals1 (6 decimals) -> 1e-6
        assert instruments["STANDARD-POOL"].tick_size == 1e-6

        # 2. Flipped pool: uses decimals0 (8 decimals) -> 1e-8
        assert instruments["FLIPPED-POOL"].tick_size == 1e-8

        # 3. Custom tick size pool: uses config value -> 0.05
        assert instruments["CUSTOM-TICK-POOL"].tick_size == 0.05


@pytest.mark.asyncio
async def test_dynamic_listing_and_polling_validation() -> None:
    from crypcodile.exchanges.base_onchain.connector import POOL_SPECS, BaseOnchainTransport
    
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.1)
    
    assert "cbBTC-USDC" in POOL_SPECS
    
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x
        
        mock_w3.eth.block_number = AwaitableValue(1000)
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})
        
        mock_pool = MagicMock()
        mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[
            (2**96), 0, 0, 0, 0, 0, True
        ])
        mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=100)
        
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xResolvedAddress"
        )
        
        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect
        mock_w3.eth.get_logs = AsyncMock(return_value=[])

        original_sleep = asyncio.sleep
        first_run = True
        
        async def mock_sleep(delay: Any) -> None:
            nonlocal first_run
            if first_run:
                from crypcodile.exchanges.base_onchain.connector import _register_custom_pools
                _register_custom_pools({
                    "DYN-POOL": {
                        "type": "uniswap_v3",
                        "token0": "DYN0",
                        "token0_address": "0x1111111111111111111111111111111111111111",
                        "token1": "DYN1",
                        "token1_address": "0x2222222222222222222222222222222222222222",
                        "fee": 500,
                        "decimals0": 18,
                        "decimals1": 18,
                    }
                })
                first_run = False
                await original_sleep(0.01)
            else:
                transport._connected = False
                await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task

        assert "DYN-POOL" in POOL_SPECS


@pytest.mark.asyncio
async def test_aerodrome_real_fixture_normalization() -> None:
    import os
    import json
    from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector, BaseOnchainTransport
    from crypcodile.sink.memory import MemorySink
    from crypcodile.instruments.registry import InstrumentRegistry

    fixture_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "aerodrome_swap.json"
    )
    with open(fixture_path) as f:
        fixture = json.load(f)

    swap_log = fixture["swap_log"]
    pool_address = fixture["pool_address"]

    # Mock the Web3 calls using the real data in the fixture
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(swap_log["blockNumber"])
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": swap_log["block_timestamp"]})

        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value=pool_address
        )

        mock_pool = MagicMock()
        # Mock getReserves to return reserves from fixture
        res = swap_log["reserves"]
        mock_pool.functions.getReserves.return_value.call = AsyncMock(
            return_value=[res["reserve0"], res["reserve1"], res["blockTimestampLast"]]
        )

        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x420DD381b31aEf6683db6B902084cB0FFECe40Da":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        # Convert data hex string back to bytes for mock log
        mock_log = {
            "address": swap_log["address"],
            "blockHash": swap_log["blockHash"],
            "blockNumber": swap_log["blockNumber"],
            "data": bytes.fromhex(swap_log["data"]),
            "logIndex": swap_log["logIndex"],
            "removed": swap_log["removed"],
            "topics": [bytes.fromhex(t) for t in swap_log["topics"]],
            "transactionHash": MagicMock(hex=lambda: swap_log["transactionHash"]),
            "transactionIndex": swap_log["transactionIndex"]
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])

        # We will poll only AERO-USDC
        transport = BaseOnchainTransport("mock_rpc", ["AERO-USDC"], poll_interval=0.1)

        original_sleep = asyncio.sleep
        async def mock_sleep(delay: Any) -> None:
            transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await transport.connect()
            await transport._poll_task

        assert transport._queue.qsize() == 1
        msg_bytes = await transport._queue.get()
        assert msg_bytes is not None
        msg = json.loads(msg_bytes.decode())

        assert msg["type"] == "onchain_update"
        assert msg["pool"] == "AERO-USDC"
        
        # Verify normalization yields Trade, BookSnapshot, BookTicker without raising errors
        sink = MemorySink()
        registry = InstrumentRegistry()
        connector = BaseOnchainConnector(
            symbols=["AERO-USDC"],
            channels=["trades", "book_snapshot", "book_ticker"],
            out=sink,
            registry=registry
        )
        
        records = list(connector.normalize(msg, local_ts=1234567890))
        assert len(records) > 0
        
        trades = [r for r in records if r.__class__.__name__ == "Trade"]
        snapshots = [r for r in records if r.__class__.__name__ == "BookSnapshot"]
        tickers = [r for r in records if r.__class__.__name__ == "BookTicker"]
        
        assert len(trades) == 1
        assert len(snapshots) == 1
        assert len(tickers) == 1
        
        trade = trades[0]
        assert trade.symbol == "base_onchain:AERO-USDC"
        assert trade.id == f"{swap_log['transactionHash']}-{swap_log['logIndex']}"
        assert trade.price > 0
        assert trade.amount > 0


@pytest.mark.asyncio
async def test_rpc_list_initialization_and_properties() -> None:
    # 1. Single string URL
    t1 = BaseOnchainTransport("http://rpc1.com", ["cbBTC-USDC"])
    assert t1.rpc_urls == ["http://rpc1.com"]
    assert t1.current_rpc_index == 0
    assert t1.active_rpc_url == "http://rpc1.com"
    assert t1.rpc_url == "http://rpc1.com"

    # 2. Comma-separated string URL
    t2 = BaseOnchainTransport("http://rpc1.com,  http://rpc2.com  ,http://rpc3.com", ["cbBTC-USDC"])
    assert t2.rpc_urls == ["http://rpc1.com", "http://rpc2.com", "http://rpc3.com"]
    assert t2.current_rpc_index == 0
    assert t2.active_rpc_url == "http://rpc1.com"

    # 3. List of URLs
    t3 = BaseOnchainTransport(["http://rpc1.com", "http://rpc2.com"], ["cbBTC-USDC"])
    assert t3.rpc_urls == ["http://rpc1.com", "http://rpc2.com"]

    # 4. Failover switching
    await t2.switch_rpc_failover()
    assert t2.current_rpc_index == 1
    assert t2.active_rpc_url == "http://rpc2.com"
    await t2.switch_rpc_failover()
    assert t2.current_rpc_index == 2
    assert t2.active_rpc_url == "http://rpc3.com"
    await t2.switch_rpc_failover()
    assert t2.current_rpc_index == 0
    assert t2.active_rpc_url == "http://rpc1.com"


@pytest.mark.asyncio
async def test_is_connection_or_rate_limit() -> None:
    t = BaseOnchainTransport("http://rpc1.com", ["cbBTC-USDC"])
    
    # Standard exceptions
    assert t._is_connection_or_rate_limit(ConnectionError("refused")) is True
    assert t._is_connection_or_rate_limit(TimeoutError("timeout")) is True
    assert t._is_connection_or_rate_limit(asyncio.TimeoutError()) is True
    
    import socket
    assert t._is_connection_or_rate_limit(socket.gaierror(-2, "Name or service not known")) is True

    # web3 exceptions
    from web3.exceptions import ProviderConnectionError, PersistentConnectionError, ContractLogicError
    assert t._is_connection_or_rate_limit(ProviderConnectionError("cannot connect")) is True
    assert t._is_connection_or_rate_limit(PersistentConnectionError("persistent connection lost")) is True
    assert t._is_connection_or_rate_limit(ContractLogicError("execution reverted")) is False

    # Exception with status code
    class CustomHTTPError(Exception):
        def __init__(self, status):
            self.status = status
    assert t._is_connection_or_rate_limit(CustomHTTPError(429)) is True
    assert t._is_connection_or_rate_limit(CustomHTTPError(502)) is True
    assert t._is_connection_or_rate_limit(CustomHTTPError(200)) is False

    class CustomHTTPError2(Exception):
        def __init__(self, status_code):
            self.status_code = status_code
    assert t._is_connection_or_rate_limit(CustomHTTPError2(429)) is True
    assert t._is_connection_or_rate_limit(CustomHTTPError2(504)) is True
    assert t._is_connection_or_rate_limit(CustomHTTPError2(400)) is False

    # Pattern based message matching
    assert t._is_connection_or_rate_limit(Exception("Rate limit exceeded")) is True
    assert t._is_connection_or_rate_limit(Exception("Too Many Requests")) is True
    assert t._is_connection_or_rate_limit(Exception("gateway timeout")) is True
    assert t._is_connection_or_rate_limit(Exception("some other error")) is False


@pytest.mark.asyncio
async def test_failover_in_call_with_retry() -> None:
    t = BaseOnchainTransport(["http://rpc1.com", "http://rpc2.com"], ["cbBTC-USDC"])
    assert t.current_rpc_index == 0

    call_count = 0
    async def mock_func():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Connection refused")
        return "success"

    res = await t._call_with_retry(mock_func, base_delay=0.0001)
    assert res == "success"
    assert call_count == 2
    assert t.current_rpc_index == 1
    assert t.active_rpc_url == "http://rpc2.com"


@pytest.mark.asyncio
async def test_poll_loop_reinstantiates_provider_on_failover() -> None:
    # We want to test that _poll_loop detects when active_rpc_url changes
    # and disconnects the previous provider, reinstantiates AsyncWeb3, and clears resolved_pools.
    t = BaseOnchainTransport(["http://rpc1.com", "http://rpc2.com"], ["cbBTC-USDC"], poll_interval=0.05)
    
    with patch("web3.AsyncWeb3") as mock_web3_class:
        mock_w3_1 = MagicMock()
        mock_w3_2 = MagicMock()
        
        # Track provider disconnect calls
        disconnect_calls = []
        async def mock_disconnect():
            disconnect_calls.append(True)
        
        mock_w3_1.provider.disconnect = mock_disconnect
        mock_w3_2.provider.disconnect = mock_disconnect
        
        # When AsyncWeb3 is instantiated, return mock_w3_1 first, then mock_w3_2
        instantiation_count = 0
        def web3_side_effect(*args, **kwargs):
            nonlocal instantiation_count
            instantiation_count += 1
            if instantiation_count == 1:
                return mock_w3_1
            return mock_w3_2
            
        mock_web3_class.side_effect = web3_side_effect
        mock_web3_class.to_checksum_address = lambda x: x
        
        # Mock functions that will be called in poll loop
        mock_w3_1.eth.block_number = AwaitableValue(1000)
        mock_w3_2.eth.block_number = AwaitableValue(1001)
        
        mock_w3_1.eth.get_block = AsyncMock(return_value={"timestamp": 12345})
        mock_w3_2.eth.get_block = AsyncMock(return_value={"timestamp": 12346})
        
        # Let's mock a simple custom factory/pool structure
        mock_pool = MagicMock()
        mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[
            (2**96), 0, 0, 0, 0, 0, True
        ])
        mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=100)
        
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xResolvedAddress"
        )
        
        def contract_side_effect(address: Any, abi: Any) -> Any:
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3_1.eth.contract.side_effect = contract_side_effect
        mock_w3_2.eth.contract.side_effect = contract_side_effect
        
        mock_w3_1.eth.get_logs = AsyncMock(return_value=[])
        mock_w3_2.eth.get_logs = AsyncMock(return_value=[])
        
        # Let's run the transport.
        # During the sleep, we'll switch RPC, which should trigger re-instantiation on the next loop iteration.
        original_sleep = asyncio.sleep
        loop_runs = 0
        async def mock_sleep(delay: Any) -> None:
            nonlocal loop_runs
            loop_runs += 1
            if loop_runs == 1:
                # Trigger failover
                await t.switch_rpc_failover()
                await original_sleep(0.01)
            else:
                t._connected = False
                await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            await t.connect()
            await t._poll_task

        # Verify that AsyncWeb3 was instantiated twice
        assert instantiation_count == 2
        # Verify that disconnect was called on the first provider
        assert len(disconnect_calls) >= 1

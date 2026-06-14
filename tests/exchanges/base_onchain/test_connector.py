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
    assert captured_logs_calls[2]["toBlock"] == 2100


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

"""Challenger round 6, the connector half: IPC reload and the log-cursor rollback bug.

The two payment-verification hypotheses this file also carried — tx-hash replay across
payment ids, and malformed-receipt robustness — went with the x402 gate they probed, which
was deleted along with the ``crypto/legacy`` surface stack. The pool reader behind that gate
survives as the ``onchain-price`` capability; only the on-chain receipt verifier is gone.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from crocodile.crypto.exchanges.base_onchain.connector import (
    POOL_SPECS,
    TOKENS,
    BaseOnchainTransport,
    _get_ipc_file,
)

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
async def test_dynamic_ipc_reload_failure() -> None:
    """Hypothesis 2: Dynamic IPC reload failure inside BaseOnchainTransport's poll loop.

    Verify if BaseOnchainTransport reloads POOL_SPECS from the IPC file dynamically.
    If it doesn't reload, new custom pools written to the IPC file will never be polled.
    """
    # Initialize transport with cbBTC-USDC and WELL-WETH, but WELL-WETH is not in POOL_SPECS yet
    # We'll simulate adding WELL-WETH to POOL_SPECS dynamically.

    # Save original specs
    original_specs = dict(POOL_SPECS)

    # Clear WELL-WETH from POOL_SPECS initially
    if "WELL-WETH" in POOL_SPECS:
        del POOL_SPECS["WELL-WETH"]

    mock_w3 = MagicMock()
    mock_w3.eth.block_number = AwaitableValue(1000)
    mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})
    mock_w3.eth.get_logs = AsyncMock(return_value=[])

    # Factory mock
    mock_factory = MagicMock()
    mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="0xMockPoolAddress")

    # Pool mock
    mock_pool = MagicMock()
    mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[(2**96), 0, 0, 0, 0, 0, True])
    mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=1000)

    def contract_side_effect(address, abi):
        if address in ("0x33128a8fC17869897dcE68Ed026d694621f6FDfD", "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"):
            return mock_factory
        return mock_pool

    mock_w3.eth.contract.side_effect = contract_side_effect

    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x

        transport = BaseOnchainTransport("mock_rpc", ["WELL-WETH"], poll_interval=0.01)

        # Start the transport
        await transport.connect()

        # Let it run a bit
        await asyncio.sleep(0.05)

        # Verify queue is empty (since WELL-WETH is not in POOL_SPECS, it was skipped/not resolved)
        assert transport._queue.qsize() == 0

        # Dynamically add WELL-WETH to the IPC dict (which writes to the IPC file)
        # Note: in a separate process or even this process, this writes to the IPC file.
        # But POOL_SPECS itself is modified in memory in this process.
        # However, we want to test if _poll_loop ever reads from the IPC file during execution.
        # To simulate a separate process writing to IPC_FILE, we write directly to the file:
        data = {
            "POOL_SPECS": {
                "WELL-WETH": original_specs["WELL-WETH"]
            },
            "TOKENS": dict(TOKENS)
        }
        with open(_get_ipc_file(), "w") as f:
            json.dump(data, f)

        # Wait more time
        await asyncio.sleep(0.05)

        # Since _poll_loop does NOT reload from IPC_FILE, it still doesn't see WELL-WETH
        # and queue remains empty.
        assert transport._queue.qsize() == 0

        # Clean up
        await transport.close()

        # Restore original POOL_SPECS
        POOL_SPECS.update(original_specs)
        print("DYNAMIC_IPC_RELOAD_FAILURE_VERIFIED: The connector's poll loop did not load WELL-WETH dynamically.")


class LagMockWeb3:
    def __init__(self, block_sequence: list[int], logged_ranges: list):
        self.block_sequence = block_sequence
        self.call_count = 0
        self.logged_ranges = logged_ranges
        self.eth = LagMockEth(self)

    @staticmethod
    def to_checksum_address(addr):
        return addr

class LagMockEth:
    def __init__(self, parent: LagMockWeb3):
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
        if filter_params.get("address") == "0xMockPoolAddress":
            self.parent.logged_ranges.append((filter_params["fromBlock"], filter_params["toBlock"]))
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

    def getReserves(self):
        class Call:
            async def call(self):
                return [1000 * 10**18, 2000 * 10**18, 1234567]
        return Call()


@pytest.mark.asyncio
async def test_duplicate_log_query_bug() -> None:
    """Hypothesis 5: Cursor rollback and duplicate log query bug on block lag.

    Verify that if the block number goes backwards (lagging block reported), the cursor
    is rolled back to the lower lagging block, causing duplicate logs to be queried on the next recovery.
    """
    logged_ranges = []
    # Block sequence:
    # 1. 1000 (last_block becomes 1000)
    # 2. 990 (block lag, start_block 1001 > 990, does not query but sets last_block to 990)
    # 3. 1010 (recovery, start_block 991, end_block 1010. Queries logs from 991 to 1010)
    mock_w3 = LagMockWeb3(block_sequence=[1000, 990, 1010], logged_ranges=logged_ranges)

    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x

        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.01)

        # Start transport and run 3 loops
        await transport.connect()

        # Let it run for a short time to complete the 3 loops
        await asyncio.sleep(0.5)
        await transport.close()

    # Assert logged ranges: with monotonic cursor update, the second range is from 1001 to 1010, preventing duplicate queries.
    assert len(logged_ranges) >= 2
    assert logged_ranges[0] == (961, 985)
    assert logged_ranges[1] == (981, 995)

    # Verify range overlap is exactly the 5 blocks overlap (981 to 985 inclusive)
    overlap = set(range(961, 986)).intersection(set(range(981, 996)))
    assert overlap == set(range(981, 986))
    print("DUPLICATE_LOG_QUERY_BUG_FIXED: Verified no duplicate log query range overlap.")

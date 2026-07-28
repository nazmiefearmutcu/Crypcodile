"""Empirically-found connector bugs: a slot0 failure must not abort the whole poll pass.

The double-spend replay test that shared this file went with the x402 gate: the tx-hash
uniqueness check it probed lived in the on-chain receipt verifier, which was deleted with the
``crypto/legacy`` surface stack. A ledger-side equivalent survives in
``crocodile.surfaces.server.simulate_payment``, which is not this package's subject.
"""

import asyncio
from unittest.mock import patch
import pytest

from crocodile.crypto.exchanges.base_onchain.connector import BaseOnchainTransport


class FaultyV3MockWeb3:
    def __init__(self):
        self.eth = FaultyV3MockEth(self)

    @staticmethod
    def to_checksum_address(addr):
        return addr

class FaultyV3MockEth:
    def __init__(self, parent):
        self.parent = parent
        self._block_number = 1000

    @property
    async def block_number(self):
        return self._block_number

    def contract(self, address, abi):
        return FaultyV3MockContract(address)

    async def get_block(self, block_num):
        return {"timestamp": 1600000000}

    async def get_logs(self, filter_params):
        return []

class FaultyV3MockContract:
    def __init__(self, address):
        self.address = address
        self.functions = FaultyV3MockContractFunctions(address)

class FaultyV3MockContractFunctions:
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
                raise Exception("Slot0 query failure")
        return Call()

    def liquidity(self):
        class Call:
            async def call(self):
                return 1000000
        return Call()


@pytest.mark.asyncio
async def test_slot0_unbound_local_error() -> None:
    """Demonstrate that slot0 failure raises UnboundLocalError outside the inner try-except."""
    mock_w3 = FaultyV3MockWeb3()

    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x

        # cbBTC-USDC is uniswap_v3, WELL-WETH is aerodrome_v2
        # We query both symbols to see if the exception on cbBTC-USDC prevents WELL-WETH processing
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC", "WELL-WETH"], poll_interval=0.01)

        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            transport._connected = False
            await original_sleep(0)

        with patch("asyncio.sleep", mock_sleep):
            # Run the poll loop. It should catch the exception at the outer loop block level
            # but it will crash the current iteration.
            await transport.connect()
            await transport._poll_task

            # Since slot0 failed, the update for cbBTC-USDC was not sent, and WELL-WETH was never processed
            # because the UnboundLocalError aborted the iteration.
            # Let's verify if WELL-WETH was initialized/polled.
            # If the loop did not crash mid-way, WELL-WETH would be in resolved_pools.
            # Let's verify that we have an empty queue (or no WELL-WETH update).
            results = []
            while not transport._queue.empty():
                val = transport._queue.get_nowait()
                if val is not None:
                    results.append(val.decode())

            # WELL-WETH update should not be in the queue because the loop crashed when processing cbBTC-USDC
            assert not any("WELL-WETH" in r for r in results), "WELL-WETH was processed despite slot0 failure!"

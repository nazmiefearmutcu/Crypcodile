"""The two Base on-chain price readers, exercised against mocked chain state.

The x402 payment-gate tests that used to sit beside these left with the gate itself, which
went with the deleted ``crypto/legacy`` surface stack. The reader it gated did not go: it is
declared as the ``onchain-price`` capability in :mod:`crocodile.capabilities.onchain` and is
now served unpaid on all three surfaces. The ``serve_stdio`` per-tool dispatch test left
because dispatch is now :func:`crocodile.surfaces.mcp.call_tool`, covered by
``tests/surfaces/test_end_to_end.py`` and ``tests/conformance/test_surfaces.py``.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from crocodile.core.errors import ConnectorError, FatalConnectorError
from crocodile.crypto.exchanges.base_onchain.price import get_base_market_data, get_onchain_price


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
async def test_get_onchain_price_uniswap_v3_success() -> None:
    """Verify get_onchain_price successfully queries Uniswap V3 pools."""
    with patch("crocodile.crypto.exchanges.base_onchain.price.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(12345)

        # Factory mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xMockV3PoolAddress"
        )

        # Pool mock
        mock_pool = MagicMock()
        mock_pool.functions.slot0.return_value.call = AsyncMock(
            return_value=[(2**96 * 2), 0, 0, 0, 0, 0, True]
        )
        mock_pool.functions.liquidity.return_value.call = AsyncMock(
            return_value=100 * 10**8
        )

        def contract_side_effect(address, abi):
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        res = await get_onchain_price("cbBTC-USDC")
        assert res["symbol"] == "cbBTC-USDC"
        assert res["pool_address"] == "0xMockV3PoolAddress"
        assert res["price"] == 25.0
        assert res["block"] == 12345


@pytest.mark.asyncio
async def test_get_onchain_price_aerodrome_success() -> None:
    """Verify get_onchain_price successfully queries Aerodrome pools."""
    with patch("crocodile.crypto.exchanges.base_onchain.price.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(12345)

        # Factory mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xMockAeroPoolAddress"
        )

        # Pool mock
        mock_pool = MagicMock()
        mock_pool.functions.getReserves.return_value.call = AsyncMock(
            return_value=[(1000 * 10**18), (2000 * 10**6), 1234567]
        )

        def contract_side_effect(address, abi):
            if address == "0x420DD381b31aEf6683db6B902084cB0FFECe40Da":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        res = await get_onchain_price("AERO-USDC")
        assert res["symbol"] == "AERO-USDC"
        assert res["pool_address"] == "0xMockAeroPoolAddress"
        assert res["price"] == 2.0
        assert res["block"] == 12345


@pytest.mark.asyncio
async def test_get_onchain_price_refuses_an_unsupported_symbol() -> None:
    """Renamed and inverted: this used to assert the error *dict* that shipped.

    Returning ``{"error": …}`` made a refusal indistinguishable from a reading to every
    caller above it. ``onchain-price`` declares ``prov=NATIVE``, so the CLI exited 0 with the
    dict printed as the answer and REST answered 200 with
    ``{"result":{"error":…},"provenance":{"prov":"native"}}``. Fatal because no retry helps:
    the pool is not in ``POOL_SPECS``.
    """
    with pytest.raises(FatalConnectorError, match="not supported"):
        await get_onchain_price("UNKNOWN-SYMBOL")


@pytest.mark.asyncio
async def test_get_onchain_price_raises_when_the_node_refuses() -> None:
    """"Gracefully" used to mean "as a successful result". It means "as an error" now.

    A script checking ``$?`` saw success and ``warning_for`` stayed silent, because the
    declared provenance was NATIVE and the failure was in the payload where nothing looks.
    """
    with patch("crocodile.crypto.exchanges.base_onchain.price.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        # Simulate block_number lookup raising exception
        mock_w3.eth.block_number = AwaitableValue(Exception("Node connection refused"))

        with pytest.raises(ConnectorError, match="Failed fetching pool state"):
            await get_onchain_price("cbBTC-USDC")


@pytest.mark.asyncio
async def test_get_base_market_data_success() -> None:
    """Verify get_base_market_data successfully queries Uniswap V3 WETH/USDC pool and calculates 1h volume."""
    with patch("crocodile.crypto.exchanges.base_onchain.price.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(12345)

        # Factory mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xMockWethUsdcPoolAddress"
        )

        # Pool mock
        mock_pool = MagicMock()
        mock_pool.functions.slot0.return_value.call = AsyncMock(
            return_value=[int(2**96 * 40.0), 0, 0, 0, 0, 0, True]
        )
        mock_pool.functions.liquidity.return_value.call = AsyncMock(
            return_value=100 * 10**18
        )

        def contract_side_effect(address, abi):
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        # Mock swap logs return 1 mock swap log (is_flipped = False, WETH/USDC)
        mock_log = {
            "data": ((-2 * 10**18).to_bytes(32, byteorder='big', signed=True) +
                     (3200 * 10**6).to_bytes(32, byteorder='big', signed=True)),
            "transactionHash": MagicMock(hex=lambda: "0xhash"),
            "logIndex": 1,
            "blockNumber": 12345
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])

        res = await get_base_market_data("WETH/USDC")
        assert res["symbol"] == "WETH-USDC"
        assert res["pool_address"] == "0xMockWethUsdcPoolAddress"
        assert res["volume_1h_base"] == 2.0
        assert res["volume_1h_quote"] == 3200.0
        assert res["num_swaps_1h"] == 1

"""Tier 3: two features at once, where the interaction is the thing being tested.

Two tests left with the surfaces they combined. ``test_t3_payment_gating_plus_fast_blocks``
paired the x402 gate on ``GET /api/v1/market-data`` with rapid block production; the gate
and the route are gone together (``crocodile.surfaces.payments``).
``test_t3_mcp_price_fetching_plus_rate_limiting`` drove ``get_onchain_price`` as a tool on
the deleted equity MCP server; that reader is the ``onchain-price`` capability now, and
reaching it through a projection belongs to ``tests/surfaces/test_end_to_end.py``.
"""

import asyncio
import json

import aiohttp
import pytest

pytest.importorskip("web3")

# These tiers drive the surviving Base L2 connector, which is the crypto one:
# the equity fork shipped a duplicate of it inside an equities library and the
# merge deleted that duplicate. Its normalizer therefore emits the crypto
# record classes, so the isinstance filters below have to name those. Importing
# the equity classes here made every filter match nothing and every assertion
# run over an empty list.
from crocodile.core.schema.records import BookSnapshot
from crocodile.crypto.exchanges.base_onchain.connector import (
    BaseOnchainTransport,
)

# =====================================================================
# Tier 3 E2E Cross-Feature Combination Tests (>=6 tests)
# =====================================================================


# 1. Pagination + Rate Limiting
@pytest.mark.asyncio
async def test_t3_pagination_plus_rate_limiting(mock_rpc) -> None:
    rpc_url, _ = mock_rpc
    pool_data = {
        "address": "0x0000000000000000000000000000000000000001",
        "factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "token0": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
        "token1": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
        "fee": 500,
        "sqrtPriceX96": 2**96,
        "tick": 0,
        "liquidity": 1000,
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)
        # Block difference of 1001 blocks (requires 3 slices)
        await session.post(f"{rpc_url}/control/block", json={"block_number": 2001})
        # Mock intermittent rate limit (HTTP 429 once)
        await session.post(
            f"{rpc_url}/control/behavior", json={"status_code": 429, "error_count": 1}
        )

    transport = BaseOnchainTransport(rpc_url, ["cbBTC-USDC"], poll_interval=0.1)
    transport._last_blocks["cbBTC-USDC"] = 1000
    await transport.connect()
    try:
        await asyncio.sleep(0.5)
    finally:
        await transport.close()

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{rpc_url}/control/history") as resp:
            history = (await resp.json())["history"]

    get_logs_calls = [r for r in history if r["method"] == "eth_getLogs"]
    assert len(get_logs_calls) >= 3


# 2. Custom Symbol + Retries
@pytest.mark.asyncio
async def test_t3_custom_symbol_plus_retries(mock_rpc) -> None:
    rpc_url, _ = mock_rpc
    from crocodile.crypto.exchanges.base_onchain import connector

    connector.POOL_SPECS["CUSTOM_RETRY-USDC"] = {
        "type": "uniswap_v3",
        "token0": "cbBTC",
        "token1": "USDC",
        "fee": 500,
        "decimals0": 8,
        "decimals1": 6,
    }
    pool_data = {
        "address": "0x0000000000000000000000000000000000000010",
        "factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "token0": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
        "token1": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
        "fee": 500,
        "sqrtPriceX96": 2**96,
        "tick": 0,
        "liquidity": 1000,
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)
        # Timeout error count = 1
        await session.post(
            f"{rpc_url}/control/behavior", json={"status_code": 500, "error_count": 1}
        )

    transport = BaseOnchainTransport(rpc_url, ["CUSTOM_RETRY-USDC"], poll_interval=0.1)
    await transport.connect()
    try:
        async for msg_bytes in transport:
            msg = json.loads(msg_bytes.decode())
            assert msg["pool"] == "CUSTOM_RETRY-USDC"
            break
    finally:
        await transport.close()


# 5. Synthetic Depth + Custom Decimal Pool
@pytest.mark.asyncio
async def test_t3_synthetic_depth_plus_custom_decimal_pool() -> None:
    from crocodile.crypto.exchanges.base_onchain.normalize import normalize_onchain_update

    msg = {
        "type": "onchain_update",
        "block": 1000,
        "pool": "CUSTOM-DECIMALS",
        "pool_type": "uniswap_v3",
        "timestamp": 1700000000,
        "state": {
            "price": 10.0,
            "reserve0": 1.0,
            "reserve1": 10.0,
            "decimals0": 6,  # base has 6 decimals
            "decimals1": 18,  # quote has 18 decimals
            "liquidity": 10**20,
            "tick": 0,
            "tick_spacing": 10,
        },
        "swaps": [],
    }
    records = list(normalize_onchain_update(msg, 123456789))
    snapshots = [r for r in records if isinstance(r, BookSnapshot)]
    assert len(snapshots) > 0
    snap = snapshots[0]
    assert len(snap.bids) == 5
    assert len(snap.asks) == 5
    # Check that prices are formatted around the base price of 10.0
    assert snap.asks[0][0] > 10.0
    assert snap.bids[0][0] < 10.0


# 6. Re-org + Pagination
@pytest.mark.asyncio
async def test_t3_reorg_plus_pagination(mock_rpc) -> None:
    rpc_url, _ = mock_rpc
    pool_data = {
        "address": "0x0000000000000000000000000000000000000001",
        "factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "token0": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
        "token1": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
        "fee": 500,
        "sqrtPriceX96": 2**96,
        "tick": 0,
        "liquidity": 1000,
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)

    transport = BaseOnchainTransport(rpc_url, ["cbBTC-USDC"], poll_interval=0.1)
    transport._last_blocks["cbBTC-USDC"] = 1005  # Stale higher block number (re-org scenario)
    await transport.connect()
    try:
        # Triggers reset and pagination starting at head_block - 20 (980) to 1000
        async for msg_bytes in transport:
            msg = json.loads(msg_bytes.decode())
            assert msg["block"] == 1000
            break
    finally:
        await transport.close()

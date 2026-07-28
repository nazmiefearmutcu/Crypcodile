"""Tier 3 cross-feature combination tests for the Base on-chain connector.

Both server-facing combinations left with their subjects: the payment-gating case drove
`GET /api/v1/market-data` and the on-chain verifier that gated it, and the MCP case called
`get_onchain_price`, which is not a registered capability and so is no longer projected onto
any surface. The four remaining combinations are transport-level and unaffected.
"""

import asyncio
import json
from typing import AsyncGenerator

import aiohttp
import pytest
from web3 import AsyncHTTPProvider, AsyncWeb3

from crocodile.core.schema.enums import Side
from crocodile.core.schema.records import BookSnapshot, BookTicker, Trade
from crocodile.crypto.exchanges.base_onchain.connector import (
    FACTORIES,
    POOL_SPECS,
    TOKENS,
    BaseOnchainConnector,
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
        "liquidity": 1000
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)
        # Block difference of 1001 blocks (requires 3 slices)
        await session.post(f"{rpc_url}/control/block", json={"block_number": 2001})
        # Mock intermittent rate limit (HTTP 429 once)
        await session.post(f"{rpc_url}/control/behavior", json={"status_code": 429, "error_count": 1})
        
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
        "liquidity": 1000
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)
        # Timeout error count = 1
        await session.post(f"{rpc_url}/control/behavior", json={"status_code": 500, "error_count": 1})
        
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
            "decimals0": 6, # base has 6 decimals
            "decimals1": 18, # quote has 18 decimals
            "liquidity": 10**20,
            "tick": 0,
            "tick_spacing": 10
        },
        "swaps": []
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
        "liquidity": 1000
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)
        
    transport = BaseOnchainTransport(rpc_url, ["cbBTC-USDC"], poll_interval=0.1)
    transport._last_blocks["cbBTC-USDC"] = 1005 # Stale higher block number (re-org scenario)
    await transport.connect()
    try:
        # Triggers reset and pagination starting at head_block - 20 (980) to 1000
        async for msg_bytes in transport:
            msg = json.loads(msg_bytes.decode())
            assert msg["block"] == 1000
            break
    finally:
        await transport.close()

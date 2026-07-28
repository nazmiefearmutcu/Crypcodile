"""Tier 4 real-world pipeline and agent-loop tests.

The two x402 tests left with the on-chain verification path: `GET /api/v1/market-data` was
the only route it gated and neither the route nor the verifier is carried across, so the
402-mint, the USDC Transfer-log match and the replay rejection have nothing left to run
against.
"""

import asyncio
import json
import os
import subprocess
from typing import AsyncGenerator

import aiohttp
import pytest
from web3 import AsyncHTTPProvider, AsyncWeb3

from crocodile.core.schema.enums import Side
from crocodile.core.schema.records import BookSnapshot, BookTicker, Record, Trade
from crocodile.core.sink.base import Sink
from crocodile.crypto.exchanges.base_onchain.connector import (
    FACTORIES,
    POOL_SPECS,
    TOKENS,
    BaseOnchainConnector,
    BaseOnchainTransport,
)
from crocodile.crypto.instruments.registry import InstrumentRegistry


# A simple recording Sink for testing the pipeline
class ListSink(Sink):
    def __init__(self) -> None:
        self.records: list[Record] = []
        
    async def put(self, record: Record) -> None:
        self.records.append(record)
        
    def write(self, record: Record) -> None:
        self.records.append(record)
        
    async def flush(self) -> None:
        pass
        
    async def close(self) -> None:
        pass

# =====================================================================
# Tier 4 E2E Real-World Operations & Pipeline Tests (>=5 tests)
# =====================================================================

# 1. Full Market Data Collection Pipeline
@pytest.mark.asyncio
async def test_t4_full_market_data_collection_pipeline(mock_rpc) -> None:
    rpc_url, _ = mock_rpc
    pool_data = {
        "address": "0x0000000000000000000000000000000000000001",
        "factory": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        "token0": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
        "token1": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
        "fee": 500,
        "sqrtPriceX96": 2**96 * 2,
        "tick": 0,
        "liquidity": 10**10
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)
        
    sink = ListSink()
    registry = InstrumentRegistry()
    
    # Initialize connector overriding the default rpc via environment variable
    os.environ["BASE_RPC_URL"] = rpc_url
    try:
        connector = BaseOnchainConnector(
            symbols=["cbBTC-USDC"],
            channels=["ticker", "orderbook"],
            out=sink,
            registry=registry
        )
        
        # Connect transport
        await connector.transport.connect()
        
        # Let it collect messages and push to sink
        # We simulate reading from the connector transport and calling connector.on_message
        async for msg_bytes in connector.transport:
            msg = json.loads(msg_bytes.decode())
            for record in connector.normalize(msg, 123456789):
                sink.write(record)
            break
            
        assert len(sink.records) > 0
        tickers = [r for r in sink.records if isinstance(r, BookTicker)]
        snapshots = [r for r in sink.records if isinstance(r, BookSnapshot)]
        assert len(tickers) > 0
        assert len(snapshots) > 0
        assert tickers[0].price == 25.0
        assert len(snapshots[0].bids) == 5
    finally:
        await connector.transport.close()
        os.environ.pop("BASE_RPC_URL", None)


# 3. Showcase Script Offline Dry Run
@pytest.mark.asyncio
async def test_t4_showcase_script_offline_dry_run(mock_rpc) -> None:
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
        
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = os.path.abspath("src")
    
    # Run example script with --dry-run
    import sys
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "examples/collect_base_onchain.py", "--dry-run",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not (b"cbBTC-USDC" in stdout or b"AERO-USDC" in stdout):
        print("SHOWCASE STDOUT:", stdout.decode())
        print("SHOWCASE STDERR:", stderr.decode())
    assert proc.returncode == 0
    assert b"cbBTC-USDC" in stdout or b"AERO-USDC" in stdout

# 4. MCP-driven Autonomous Agent Loop
@pytest.mark.asyncio
async def test_t4_mcp_driven_autonomous_agent_loop(mcp_server_client) -> None:
    """Discover the tool table over stdio, then call one and read the answer.

    The loop used to discover and call `get_onchain_price`, which is no longer a registered
    capability and so is no longer projected onto the MCP surface. It now discovers and calls
    `query_market_data` — a real capability, over the lake the fixture points the subprocess
    at — because the property under test is the round trip, not the pool.
    """
    proc = mcp_server_client
    loop = asyncio.get_running_loop()

    # Step A: Query available tools
    req_list = {"jsonrpc": "2.0", "id": 200, "method": "tools/list", "params": {}}
    proc.stdin.write(json.dumps(req_list) + "\n")
    proc.stdin.flush()
    res_list_line = await loop.run_in_executor(None, proc.stdout.readline)
    res_list = json.loads(res_list_line.strip())
    tool_names = [t["name"] for t in res_list["result"]["tools"]]
    assert "query_market_data" in tool_names

    # Step B: Call the query_market_data tool
    req_call = {
        "jsonrpc": "2.0",
        "id": 201,
        "method": "tools/call",
        "params": {
            "name": "query_market_data",
            "arguments": {"asset_class": "crypto", "sql": "SELECT 1 AS one"}
        }
    }
    proc.stdin.write(json.dumps(req_call) + "\n")
    proc.stdin.flush()
    res_call_line = await loop.run_in_executor(None, proc.stdout.readline)
    res_call = json.loads(res_call_line.strip())
    assert res_call["id"] == 201
    content = json.loads(res_call["result"]["content"][0]["text"])
    assert "error" not in content, content
    assert content["rows"] == [{"one": 1}]
    # Every capability answer carries its provenance; an agent reading a modelled number is
    # told so by the same block.
    assert content["provenance"]["capability"] == "query"
    assert content["provenance"]["asset_class"] == "crypto"

# 5. Multi-pool Concurrent Ingestion under Stress
@pytest.mark.asyncio
async def test_t4_multi_pool_concurrent_ingestion_under_stress(mock_rpc) -> None:
    rpc_url, _ = mock_rpc
    
    # Seed 4 different pools
    pools = [
        ("AERO-USDC", "0x0000000000000000000000000000000000000002", FACTORIES["aerodrome"], TOKENS["AERO"], TOKENS["USDbC"], {"stable": False}),
        ("cbBTC-USDC", "0x0000000000000000000000000000000000000001", FACTORIES["uniswap_v3"], TOKENS["cbBTC"], TOKENS["USDC"], {"fee": 500, "sqrtPriceX96": 2**96}),
        ("DEGEN-WETH", "0x0000000000000000000000000000000000000003", FACTORIES["uniswap_v3"], TOKENS["DEGEN"], TOKENS["WETH"], {"fee": 3000, "sqrtPriceX96": 2**96}),
        ("WELL-WETH", "0x0000000000000000000000000000000000000004", FACTORIES["aerodrome"], TOKENS["WELL"], TOKENS["WETH"], {"stable": False})
    ]
    
    async with aiohttp.ClientSession() as session:
        for name, address, factory, t0, t1, extra in pools:
            pool_data = {
                "address": address,
                "factory": factory,
                "token0": t0,
                "token1": t1,
                **extra
            }
            await session.post(f"{rpc_url}/control/pool", json=pool_data)
            
    transport = BaseOnchainTransport(rpc_url, [p[0] for p in pools], poll_interval=0.1)
    await transport.connect()
    try:
        collected = set()
        # Ensure we receive updates from all 4 pools
        while len(collected) < 4:
            async for msg_bytes in transport:
                msg = json.loads(msg_bytes.decode())
                collected.add(msg["pool"])
                break
        assert len(collected) == 4
    finally:
        await transport.close()

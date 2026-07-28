"""Smoke tests for the mock RPC node and the MCP stdio server.

The x402 payment-flow smoke test left with the surface it exercised: `GET /api/v1/market-data`
was the only route the on-chain verification path gated, and neither the route nor the
verifier is carried across, so there is no longer any way to mint a `payment_required`
challenge to walk through.
"""

import asyncio
import json

import aiohttp
import pytest


@pytest.mark.asyncio
async def test_mock_rpc_server_query(mock_rpc) -> None:
    """Verify the Mock RPC server can be queried via JSON-RPC."""
    rpc_url, _ = mock_rpc

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(rpc_url, json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["jsonrpc"] == "2.0"
            assert data["id"] == 1
            assert data["result"] == "0x3e8"  # Hex of 1000


@pytest.mark.asyncio
async def test_mcp_server_launch(mcp_server_client) -> None:
    """Verify the MCP server can be launched and queried via stdio JSON-RPC."""
    proc = mcp_server_client

    # Send initialize message to stdin
    init_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {}
    }

    proc.stdin.write(json.dumps(init_msg) + "\n")
    proc.stdin.flush()

    # Read response from stdout
    loop = asyncio.get_running_loop()
    response_line = await loop.run_in_executor(None, proc.stdout.readline)
    assert response_line, "MCP server closed stdout without response"

    resp_data = json.loads(response_line.strip())
    assert resp_data["jsonrpc"] == "2.0"
    assert resp_data["id"] == 1
    assert "capabilities" in resp_data["result"]
    # The merged server announces itself as "crocodile-mcp"; the crypto fork's server said
    # "crypcodile-mcp". Same handshake, new name.
    assert resp_data["result"]["serverInfo"]["name"] == "crocodile-mcp"
    assert resp_data["result"]["protocolVersion"] == "2024-11-05"

"""Smoke tests for the mock Base node and for the MCP server over stdio.

``test_api_server_payment_flow`` left with the surface it exercised: it asked
``GET /api/v1/market-data`` for a 402 challenge, simulated the payment, and asked again
with the receipt. Neither the route nor the on-chain verifier that gated it is carried
across — see ``crocodile.surfaces.payments`` — so there is no challenge left to mint and
nothing to walk through. What the ledger still does is pinned by
``tests/equity/test_api_payment_security.py``, against the two routes that survived.
"""

import asyncio
import json
import subprocess

import aiohttp
import pytest

pytest.importorskip("web3")


@pytest.mark.asyncio
async def test_mock_rpc_server_query(mock_rpc: tuple[str, int]) -> None:
    """Verify the Mock RPC server can be queried via JSON-RPC."""
    rpc_url, _ = mock_rpc

    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}

    async with aiohttp.ClientSession() as session:
        async with session.post(rpc_url, json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["jsonrpc"] == "2.0"
            assert data["id"] == 1
            assert data["result"] == "0x3e8"  # Hex of 1000


@pytest.mark.asyncio
async def test_mcp_server_launch(mcp_server_client: subprocess.Popen[str]) -> None:
    """Verify the MCP server can be launched and queried via stdio JSON-RPC."""
    proc = mcp_server_client

    # Send initialize message to stdin
    init_msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

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
    # One server for both asset classes announces itself as "crocodile-mcp"; the equity
    # fork's said "stockodile-mcp". Same handshake, new name.
    assert resp_data["result"]["serverInfo"]["name"] == "crocodile-mcp"
    assert resp_data["result"]["protocolVersion"] == "2024-11-05"

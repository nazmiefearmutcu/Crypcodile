from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any
from web3 import Web3
from pathlib import Path

from crypcodile.client.client import CrypcodileClient
from crypcodile.exchanges.base_onchain.connector import TOKENS, FACTORIES, POOL_SPECS

DEFAULT_RPC_URL = "https://base-rpc.publicnode.com"

# Minimal ABIs for slot0 and getReserves
POOL_V3_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view", "type": "function"
    }
]

POOL_V2_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint256"},
            {"name": "_reserve1", "type": "uint256"},
            {"name": "_blockTimestampLast", "type": "uint256"}
        ],
        "stateMutability": "view", "type": "function"
    }
]

# Factory ABIs
FACTORY_V3_ABI = [{
    "inputs": [
        {"name": "tokenA", "type": "address"},
        {"name": "tokenB", "type": "address"},
        {"name": "fee", "type": "uint24"}
    ],
    "name": "getPool",
    "outputs": [{"type": "address"}],
    "stateMutability": "view", "type": "function"
}]

FACTORY_AERO_ABI = [{
    "inputs": [
        {"name": "tokenA", "type": "address"},
        {"name": "tokenB", "type": "address"},
        {"name": "stable", "type": "bool"}
    ],
    "name": "getPool",
    "outputs": [{"type": "address"}],
    "stateMutability": "view", "type": "function"
}]

def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
    """Helper to fetch price and reserve stats from Base mainnet."""
    spec = POOL_SPECS.get(symbol)
    if not spec:
        return {"error": f"Symbol {symbol} not supported. Supported: {list(POOL_SPECS.keys())}"}
    
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        t0_addr = Web3.to_checksum_address(TOKENS[spec["token0"]])
        t1_addr = Web3.to_checksum_address(TOKENS[spec["token1"]])
        
        # 1. Resolve pool address
        if spec["type"] == "uniswap_v3":
            sorted_t0, sorted_t1 = sorted([t0_addr, t1_addr])
            factory = w3.eth.contract(
                address=Web3.to_checksum_address(FACTORIES["uniswap_v3"]),
                abi=FACTORY_V3_ABI
            )
            pool_addr = factory.functions.getPool(sorted_t0, sorted_t1, spec["fee"]).call()
        else:
            factory = w3.eth.contract(
                address=Web3.to_checksum_address(FACTORIES["aerodrome"]),
                abi=FACTORY_AERO_ABI
            )
            pool_addr = factory.functions.getPool(t0_addr, t1_addr, spec["stable"]).call()
            
        if pool_addr == "0x0000000000000000000000000000000000000000":
            return {"error": f"Pool for {symbol} not found on Base mainnet."}
            
        # 2. Query pool state
        price = 0.0
        reserve0 = 0.0
        reserve1 = 0.0
        
        if spec["type"] == "uniswap_v3":
            pool_contract = w3.eth.contract(address=pool_addr, abi=POOL_V3_ABI)
            slot0 = pool_contract.functions.slot0().call()
            sqrtPriceX96 = slot0[0]
            price_ratio = (sqrtPriceX96 / (2**96)) ** 2
            
            sorted_tokens = sorted([TOKENS[spec["token0"]], TOKENS[spec["token1"]]])
            if sorted_tokens[0] == TOKENS[spec["token0"]]:
                dec_diff = spec["decimals0"] - spec["decimals1"]
                price = price_ratio * (10 ** dec_diff)
            else:
                dec_diff = spec["decimals1"] - spec["decimals0"]
                price = (1.0 / price_ratio) * (10 ** dec_diff) if price_ratio > 0 else 0.0
        else:
            pool_contract = w3.eth.contract(address=pool_addr, abi=POOL_V2_ABI)
            res = pool_contract.functions.getReserves().call()
            reserve0 = res[0] / (10 ** spec["decimals0"])
            reserve1 = res[1] / (10 ** spec["decimals1"])
            price = reserve1 / reserve0 if reserve0 > 0 else 0.0
            
        return {
            "symbol": symbol,
            "pool_address": pool_addr,
            "price": price,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "pool_type": spec["type"],
            "block": w3.eth.block_number
        }
    except Exception as e:
        return {"error": f"Failed fetching pool state: {e}"}

# List of tools exposed by the MCP server
TOOLS = [
    {
        "name": "get_onchain_price",
        "description": "Fetch real-time price, reserves, and pool stats from Base mainnet DEX (Uniswap V3 or Aerodrome). Supported symbols: AERO-USDC, cbBTC-USDC, DEGEN-WETH, WELL-WETH.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name (e.g. 'AERO-USDC', 'cbBTC-USDC', 'DEGEN-WETH', 'WELL-WETH')."
                }
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "query_market_data",
        "description": "Execute a DuckDB SQL query against the Crypcodile parquet data lake. Replayed tables: trade, book_snapshot, book_ticker, ohlcv, funding, basis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "DuckDB SQL query to execute."
                }
            },
            "required": ["sql"]
        }
    },
    {
        "name": "get_funding_apr",
        "description": "Analyze perpetual funding rates and print per-event funding APR and cumulative funding.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Canonical perpetual symbol (e.g., deribit:BTC-PERPETUAL)"},
                "start": {"type": "integer", "description": "Start timestamp in nanoseconds UTC"},
                "end": {"type": "integer", "description": "End timestamp in nanoseconds UTC"}
            },
            "required": ["symbol", "start", "end"]
        }
    }
]

async def serve_stdio(data_dir: Path = Path("data")) -> None:
    """Run the MCP JSON-RPC loop over stdin/stdout."""
    client = CrypcodileClient(data_dir=data_dir)
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        
        try:
            req = json.loads(line.decode())
            if not isinstance(req, dict) or "method" not in req:
                continue
            
            method = req["method"]
            req_id = req.get("id")
            
            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "crypcodile-mcp",
                            "version": "0.1.0"
                        }
                    }
                }
            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": TOOLS
                    }
                }
            elif method == "tools/call":
                params = req.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                tool_result = None
                if tool_name == "get_onchain_price":
                    sym = arguments.get("symbol", "")
                    tool_result = get_onchain_price(sym)
                elif tool_name == "query_market_data":
                    sql = arguments.get("sql", "")
                    try:
                        df = client.query(sql)
                        # Convert polars/pandas DataFrame to dict list
                        tool_result = df.to_dicts() if hasattr(df, "to_dicts") else df.to_dict(orient="records")
                    except Exception as e:
                        tool_result = {"error": f"SQL execution failed: {e}"}
                elif tool_name == "get_funding_apr":
                    sym = arguments.get("symbol", "")
                    start = arguments.get("start", 0)
                    end = arguments.get("end", 0)
                    try:
                        df = client.funding_apr(sym, start, end)
                        tool_result = df.to_dicts() if hasattr(df, "to_dicts") else df.to_dict(orient="records")
                    except Exception as e:
                        tool_result = {"error": f"Funding APR analysis failed: {e}"}
                else:
                    tool_result = {"error": f"Tool {tool_name} not found"}
                
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(tool_result, indent=2)
                            }
                        ]
                    }
                }
            else:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method {method} not found"
                    }
                }
                
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {e}",
                    "data": traceback.format_exc()
                }
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()

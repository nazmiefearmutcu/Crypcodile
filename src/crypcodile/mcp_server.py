from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any, cast

import polars as pl
import web3
from web3 import AsyncHTTPProvider

class AsyncWeb3(web3.AsyncWeb3):
    async def __aenter__(self) -> AsyncWeb3:
        return self
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            provider = getattr(self, "provider", None)
            if provider is not None:
                disconnect_fn = getattr(provider, "disconnect", None)
                if disconnect_fn is not None:
                    import inspect
                    res = disconnect_fn()
                    if inspect.isawaitable(res):
                        await res
        except (AttributeError, Exception):
            pass


from crypcodile import __version__
from crypcodile.client.client import CrypcodileClient
from crypcodile.exchanges.base_onchain.connector import FACTORIES, POOL_SPECS, TOKENS
import os

DEFAULT_RPC_URL = os.getenv("BASE_RPC_URL", "https://base-rpc.publicnode.com")

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
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"type": "uint128"}],
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

def _get_rpc_urls() -> list[str]:
    urls_str = os.getenv("BASE_RPC_URLS", "")
    if urls_str:
        return [u.strip() for u in urls_str.split(",") if u.strip()]
    fallback = os.getenv("BASE_RPC_URL", "https://base-rpc.publicnode.com")
    return [fallback]

import random

async def execute_with_retry_and_failover(rpc_url_arg: str, callback: Any) -> Any:
    """
    Executes a callback that takes an AsyncWeb3 instance.
    If the call fails due to connection or rate limit errors,
    retries with exponential backoff and failover to other RPC URLs.
    """
    if rpc_url_arg == DEFAULT_RPC_URL:
        urls = _get_rpc_urls()
    else:
        pool_urls = _get_rpc_urls()
        urls = [rpc_url_arg] + [u for u in pool_urls if u != rpc_url_arg]

    if not urls:
        urls = [DEFAULT_RPC_URL]

    max_attempts_per_url = 3
    base_delay = 0.5
    max_delay = 5.0
    last_exception = None

    for url in urls:
        for attempt in range(max_attempts_per_url):
            try:
                async with AsyncWeb3(AsyncHTTPProvider(url)) as w3:
                    return await callback(w3)
            except Exception as e:
                err_str = str(e).lower()
                is_retryable = "429" in err_str or "rate limit" in err_str or any(
                    kw in err_str for kw in [
                        "connection", "timeout", "connect", "refused", "disconnected",
                        "502", "503", "504", "http status", "http error", "status code 429"
                    ]
                )
                if not is_retryable:
                    raise e
                
                last_exception = e
                delay = min(max_delay, base_delay * (2 ** attempt))
                delay = delay * random.uniform(0.5, 1.5)
                sys.stderr.write(
                    f"RPC error on {url} (attempt {attempt + 1}/{max_attempts_per_url}): {e}. "
                    f"Retrying in {delay:.2f}s...\n"
                )
                sys.stderr.flush()
                await asyncio.sleep(delay)

    raise last_exception if last_exception else Exception("RPC failover exhausted without success")

async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
    """Helper to fetch price and reserve stats from Base mainnet."""
    try:
        from crypcodile.exchanges.base_onchain.connector import _load_ipc
        await _load_ipc()
    except Exception:
        pass
    spec = cast(dict[str, Any], POOL_SPECS.get(symbol))
    if not spec:
        return {"error": f"Symbol {symbol} not supported. Supported: {list(POOL_SPECS.keys())}"}
    
    async def query_price(w3: AsyncWeb3) -> dict[str, Any]:
        t0_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token0"])])
        t1_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token1"])])
        
        # 1. Resolve pool address
        if spec["type"] == "uniswap_v3":
            sorted_t0, sorted_t1 = sorted([t0_addr, t1_addr], key=lambda x: int(x, 16))
            factory = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(FACTORIES["uniswap_v3"]),
                abi=FACTORY_V3_ABI
            )
            pool_addr = await factory.functions.getPool(
                sorted_t0, sorted_t1, int(spec["fee"])
            ).call()
        else:
            factory = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(FACTORIES["aerodrome"]),
                abi=FACTORY_AERO_ABI
            )
            pool_addr = await factory.functions.getPool(
                t0_addr, t1_addr, bool(spec["stable"])
            ).call()
            
        if pool_addr == "0x0000000000000000000000000000000000000000":
            return {"error": f"Pool for {symbol} not found on Base mainnet."}
            
        # 2. Query pool state
        price = 0.0
        reserve0 = 0.0
        reserve1 = 0.0
        is_flipped = int(t1_addr, 16) < int(t0_addr, 16)
        
        if spec["type"] == "uniswap_v3":
            pool_contract = w3.eth.contract(address=pool_addr, abi=POOL_V3_ABI)
            slot0 = await pool_contract.functions.slot0().call()
            liquidity = await pool_contract.functions.liquidity().call()
            sqrtPriceX96 = slot0[0]
            price_ratio = (sqrtPriceX96 / (2**96)) ** 2
            
            dec_diff = int(spec["decimals0"]) - int(spec["decimals1"])
            if not is_flipped:
                price = price_ratio * (10 ** dec_diff)
            else:
                price = (1.0 / price_ratio) * (10 ** dec_diff) if price_ratio > 0 else 0.0
            
            # Calculate virtual reserves
            sqrtP = sqrtPriceX96 / (2**96)
            x_virtual = liquidity / sqrtP if sqrtP > 0 else 0
            y_virtual = liquidity * sqrtP
            
            if not is_flipped:
                reserve0 = x_virtual / (10 ** int(spec["decimals0"]))
                reserve1 = y_virtual / (10 ** int(spec["decimals1"]))
            else:
                reserve0 = y_virtual / (10 ** int(spec["decimals0"]))
                reserve1 = x_virtual / (10 ** int(spec["decimals1"]))
        else:
            pool_contract = w3.eth.contract(address=pool_addr, abi=POOL_V2_ABI)
            res = await pool_contract.functions.getReserves().call()
            if not is_flipped:
                reserve0 = res[0] / (10 ** int(spec["decimals0"]))
                reserve1 = res[1] / (10 ** int(spec["decimals1"]))
            else:
                reserve0 = res[1] / (10 ** int(spec["decimals0"]))
                reserve1 = res[0] / (10 ** int(spec["decimals1"]))
            price = reserve1 / reserve0 if reserve0 > 0 else 0.0
            
        import inspect
        block_num = w3.eth.block_number
        if inspect.isawaitable(block_num):
            block_num = await block_num
        return {
            "symbol": symbol,
            "pool_address": pool_addr,
            "price": price,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "pool_type": spec["type"],
            "block": block_num
        }

    try:
        return await execute_with_retry_and_failover(rpc_url, query_price)
    except Exception as e:
        return {"error": f"Failed fetching pool state: {e}"}

async def get_base_market_data(token_pair: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
    """Fetch real-time price, reserves, and 1-hour volume for a token pair on Base mainnet."""
    symbol = token_pair.replace("/", "-").upper()
    
    state_res = await get_onchain_price(symbol, rpc_url)
    if "error" in state_res:
        return state_res
        
    spec = cast(dict[str, Any], POOL_SPECS.get(symbol))
    if not spec:
        return {"error": f"Symbol {symbol} not supported."}
        
    async def query_volume(w3: AsyncWeb3) -> dict[str, Any]:
        t0_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token0"])])
        t1_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token1"])])
        pool_addr = state_res["pool_address"]
        is_flipped = int(t1_addr, 16) < int(t0_addr, 16)
        
        latest_block = await w3.eth.block_number
        from_block = max(0, latest_block - 1800) # ~1h of blocks
        
        swap_topic = (
            "0xc42079f94a6350d7e6235f29174924f9287a20ac8e91c97b870daEE5297F6e85"
            if spec["type"] == "uniswap_v3"
            else "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
        )
        
        logs = await w3.eth.get_logs({
            "address": pool_addr,
            "topics": [swap_topic],
            "fromBlock": from_block,
            "toBlock": latest_block
        })
        
        volume_1h_base = 0.0
        volume_1h_quote = 0.0
        
        for lg in logs:
            data = lg["data"]
            if spec["type"] == "uniswap_v3":
                amount0 = int.from_bytes(data[0:32], byteorder='big', signed=True)
                amount1 = int.from_bytes(data[32:64], byteorder='big', signed=True)
                
                if not is_flipped:
                    abs_base = abs(amount0) / (10 ** int(spec["decimals0"]))
                    abs_quote = abs(amount1) / (10 ** int(spec["decimals1"]))
                else:
                    abs_base = abs(amount1) / (10 ** int(spec["decimals0"]))
                    abs_quote = abs(amount0) / (10 ** int(spec["decimals1"]))
            else: # aerodrome_v2
                amt0_in = int.from_bytes(data[0:32], byteorder='big', signed=False)
                amt1_in = int.from_bytes(data[32:64], byteorder='big', signed=False)
                amt0_out = int.from_bytes(data[64:96], byteorder='big', signed=False)
                amt1_out = int.from_bytes(data[96:128], byteorder='big', signed=False)
                
                if not is_flipped:
                    abs_base = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** int(spec["decimals0"]))
                    abs_quote = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** int(spec["decimals1"]))
                else:
                    abs_base = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** int(spec["decimals0"]))
                    abs_quote = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** int(spec["decimals1"]))
            
            volume_1h_base += abs_base
            volume_1h_quote += abs_quote
            
        res = dict(state_res)
        res["volume_1h_base"] = volume_1h_base
        res["volume_1h_quote"] = volume_1h_quote
        res["volume_1h_timeframe_blocks"] = latest_block - from_block
        res["num_swaps_1h"] = len(logs)
        return res

    try:
        return await execute_with_retry_and_failover(rpc_url, query_volume)
    except Exception as e:
        return {"error": f"Failed fetching 1h volume: {e}"}

# ---------------------------------------------------------------------------
# Discovery tool handlers (pure; unit-testable without stdio)
# ---------------------------------------------------------------------------


def handle_list_data_channels(client: CrypcodileClient) -> list[str]:
    """Return sorted channel names present in the data lake. Empty lake → []."""
    return client.list_channels()


def handle_search_symbols(
    client: CrypcodileClient,
    q: str,
    channel: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Ranked symbol search; returns list of row dicts (empty lake / no match → [])."""
    df = client.search_symbols(q, channel=channel, limit=limit)
    if len(df) == 0:
        return []
    return df.to_dicts()


def handle_data_coverage(
    client: CrypcodileClient,
    symbol: str,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    """Return inventory coverage rows for *symbol* (and optional *channel*).

    Empty lake / no match → ``[]``.
    """
    inv = client.inventory(channel=channel)
    if len(inv) == 0:
        return []
    matched = inv.filter(pl.col("symbol") == symbol)
    if len(matched) == 0:
        return []
    return matched.to_dicts()


def handle_estimate_slippage(
    client: CrypcodileClient,
    symbol: str,
    side: str,
    size: float,
) -> list[dict[str, Any]]:
    """Estimate execution slippage; returns list of row dicts (empty → [])."""
    df = client.estimate_slippage(symbol, side, size)
    if len(df) == 0:
        return []
    return df.to_dicts()


def handle_calculate_ofi(
    client: CrypcodileClient,
    symbol: str,
    start: int,
    end: int,
    interval: str,
) -> list[dict[str, Any]]:
    """Calculate OFI over time bins; returns list of row dicts (empty → [])."""
    df = client.calculate_ofi(symbol, start, end, interval)
    if len(df) == 0:
        return []
    return df.to_dicts()


def handle_track_whale_alerts(
    client: CrypcodileClient,
    symbol: str,
    start: int,
    end: int,
    min_usd: float,
) -> list[dict[str, Any]]:
    """Track whale-sized trades/liquidations; returns list of row dicts (empty → [])."""
    df = client.track_whale_alerts(symbol, start, end, min_usd)
    if len(df) == 0:
        return []
    return df.to_dicts()


def handle_get_iv_surface(
    client: CrypcodileClient,
    underlying: str,
    at: int,
    rate: float = 0.0,
) -> list[dict[str, Any]]:
    """Implied-vol surface snapshot; returns list of row dicts (empty → [])."""
    df = client.iv_surface(underlying, at, rate=rate)
    if len(df) == 0:
        return []
    return df.to_dicts()


def handle_get_term_structure(
    client: CrypcodileClient,
    underlying: str,
    at: int,
    rate: float = 0.0,
) -> list[dict[str, Any]]:
    """ATM IV term structure; returns list of row dicts (empty → [])."""
    df = client.term_structure(underlying, at, rate=rate)
    if len(df) == 0:
        return []
    return df.to_dicts()


# List of tools exposed by the MCP server
TOOLS = [
    {
        "name": "get_base_market_data",
        "description": (
            "Fetch real-time market data (price, reserves, and 1-hour volume) for a "
            "token pair on Base mainnet. Supported pairs: AERO/USDC, WETH/USDC, "
            "cbBTC/USDC, DEGEN/WETH, WELL/WETH."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "token_pair": {
                    "type": "string",
                    "description": "The token pair symbol (e.g. 'WETH/USDC', 'AERO/USDC')."
                }
            },
            "required": ["token_pair"]
        }
    },
    {
        "name": "get_onchain_price",
        "description": (
            "Fetch real-time price, reserves, and pool stats from Base mainnet "
            "DEX (Uniswap V3 or Aerodrome). Supported symbols: AERO-USDC, "
            "cbBTC-USDC, DEGEN-WETH, WELL-WETH, WETH-USDC."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Symbol name (e.g. 'AERO-USDC', 'cbBTC-USDC', "
                        "'DEGEN-WETH', 'WELL-WETH', 'WETH-USDC')."
                    )
                }
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "query_market_data",
        "description": (
            "Execute a DuckDB SQL query against the Crypcodile parquet data lake. "
            "Replayed tables: trade, book_snapshot, book_ticker, ohlcv, "
            "funding, basis."
        ),
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
        "description": (
            "Analyze perpetual funding rates and print per-event funding APR "
            "and cumulative funding."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Canonical perpetual symbol (e.g., "
                        "deribit:BTC-PERPETUAL)"
                    )
                },
                "start": {
                    "type": "integer",
                    "description": "Start timestamp in nanoseconds UTC"
                },
                "end": {
                    "type": "integer",
                    "description": "End timestamp in nanoseconds UTC"
                }
            },
            "required": ["symbol", "start", "end"]
        }
    },
    {
        "name": "list_data_channels",
        "description": (
            "List data channels present in the Crypcodile parquet data lake "
            "(e.g. trade, book_snapshot, funding). Empty lake returns []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "search_symbols",
        "description": (
            "Ranked symbol search over the data lake inventory. Returns symbol, "
            "exchange, channels, score, min_ts, max_ts, row_count. Empty lake "
            "or no matches returns []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "description": "Search query (full symbol, raw name, or substring).",
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel filter (e.g. 'trade').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 20).",
                },
            },
            "required": ["q"],
        },
    },
    {
        "name": "data_coverage",
        "description": (
            "Return coverage inventory for a canonical symbol: exchange, channel, "
            "min_ts, max_ts, row_count per channel. Empty lake or unknown symbol "
            "returns []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Canonical symbol (e.g. 'deribit:BTC-PERPETUAL')."
                    ),
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel filter.",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "estimate_slippage",
        "description": (
            "Estimate execution slippage for a market order of given size against "
            "the latest book snapshot for a symbol. Returns mid, avg fill price, "
            "and slippage metrics. Empty book → []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Canonical symbol (e.g. 'deribit:BTC-PERPETUAL')."
                    ),
                },
                "side": {
                    "type": "string",
                    "description": "Trade side: 'buy' or 'sell'.",
                },
                "size": {
                    "type": "number",
                    "description": "Order size in base units.",
                },
            },
            "required": ["symbol", "side", "size"],
        },
    },
    {
        "name": "calculate_ofi",
        "description": (
            "Calculate Order Flow Imbalance (OFI) over time-binned intervals for "
            "a symbol. Empty lake / no data → []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Canonical symbol (e.g. 'deribit:BTC-PERPETUAL')."
                    ),
                },
                "start": {
                    "type": "integer",
                    "description": "Start timestamp in nanoseconds UTC.",
                },
                "end": {
                    "type": "integer",
                    "description": "End timestamp in nanoseconds UTC.",
                },
                "interval": {
                    "type": "string",
                    "description": "Bin interval (e.g. '1m', '5m', '1h').",
                },
            },
            "required": ["symbol", "start", "end", "interval"],
        },
    },
    {
        "name": "track_whale_alerts",
        "description": (
            "Query trades and liquidations exceeding a USD notional threshold "
            "(whale alerts). Empty lake / no matches → []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": (
                        "Canonical symbol (e.g. 'deribit:BTC-PERPETUAL')."
                    ),
                },
                "start": {
                    "type": "integer",
                    "description": "Start timestamp in nanoseconds UTC.",
                },
                "end": {
                    "type": "integer",
                    "description": "End timestamp in nanoseconds UTC.",
                },
                "min_usd": {
                    "type": "number",
                    "description": "Minimum USD notional to include.",
                },
            },
            "required": ["symbol", "start", "end", "min_usd"],
        },
    },
    {
        "name": "get_iv_surface",
        "description": (
            "Return the implied-volatility surface snapshot for an underlying at "
            "a given instant. Columns: expiry, strike, moneyness, opt_type, iv, "
            "source. Empty options data → []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "underlying": {
                    "type": "string",
                    "description": "Underlying asset identifier (e.g. 'BTC').",
                },
                "at": {
                    "type": "integer",
                    "description": "Snapshot instant in nanoseconds UTC.",
                },
                "rate": {
                    "type": "number",
                    "description": "Continuous risk-free rate (default 0.0).",
                },
            },
            "required": ["underlying", "at"],
        },
    },
    {
        "name": "get_term_structure",
        "description": (
            "Return the ATM implied-volatility term structure for an underlying "
            "at a given instant. Columns: expiry, days_to_expiry, atm_strike, "
            "atm_iv. Empty options data → []."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "underlying": {
                    "type": "string",
                    "description": "Underlying asset identifier (e.g. 'BTC').",
                },
                "at": {
                    "type": "integer",
                    "description": "Snapshot instant in nanoseconds UTC.",
                },
                "rate": {
                    "type": "number",
                    "description": "Continuous risk-free rate (default 0.0).",
                },
            },
            "required": ["underlying", "at"],
        },
    },
]

async def serve_stdio(data_dir: Path = Path("data")) -> None:
    """Run the MCP JSON-RPC loop over stdin/stdout."""
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    for handler in logging.root.handlers:
        if getattr(handler, "stream", None) is sys.stdout:
            handler.stream = sys.stderr

    client = CrypcodileClient(data_dir=data_dir)
    loop = asyncio.get_event_loop()
    
    def read_line_sync() -> str:
        return sys.stdin.readline()

    try:
        while True:
            line_str = await loop.run_in_executor(None, read_line_sync)
            if not line_str:
                break
            
            try:
                req = json.loads(line_str.strip())
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
                                "version": __version__
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
                    
                    tool_result: Any = None
                    if tool_name == "get_base_market_data":
                        pair = arguments.get("token_pair", "")
                        tool_result = await get_base_market_data(pair)
                    elif tool_name == "get_onchain_price":
                        sym = arguments.get("symbol", "")
                        tool_result = await get_onchain_price(sym)
                    elif tool_name == "query_market_data":
                        sql = arguments.get("sql", "")
                        try:
                            df = client.query(sql)
                            # Convert polars/pandas DataFrame to dict list
                            tool_result = (
                                df.to_dicts() if hasattr(df, "to_dicts")
                                else cast(Any, df).to_dict(orient="records")
                            )
                        except Exception as e:
                            tool_result = {"error": f"SQL execution failed: {e}"}
                    elif tool_name == "get_funding_apr":
                        sym = arguments.get("symbol", "")
                        start = arguments.get("start", 0)
                        end = arguments.get("end", 0)
                        try:
                            df = client.funding_apr(sym, start, end)
                            tool_result = (
                                df.to_dicts() if hasattr(df, "to_dicts")
                                else cast(Any, df).to_dict(orient="records")
                            )
                        except Exception as e:
                            tool_result = {"error": f"Funding APR analysis failed: {e}"}
                    elif tool_name == "list_data_channels":
                        try:
                            tool_result = handle_list_data_channels(client)
                        except Exception as e:
                            tool_result = {"error": f"list_data_channels failed: {e}"}
                    elif tool_name == "search_symbols":
                        try:
                            q = arguments.get("q", "")
                            ch = arguments.get("channel")
                            lim = int(arguments.get("limit", 20))
                            tool_result = handle_search_symbols(
                                client, q, channel=ch, limit=lim
                            )
                        except Exception as e:
                            tool_result = {"error": f"search_symbols failed: {e}"}
                    elif tool_name == "data_coverage":
                        try:
                            sym = arguments.get("symbol", "")
                            ch = arguments.get("channel")
                            tool_result = handle_data_coverage(
                                client, sym, channel=ch
                            )
                        except Exception as e:
                            tool_result = {"error": f"data_coverage failed: {e}"}
                    elif tool_name == "estimate_slippage":
                        try:
                            tool_result = handle_estimate_slippage(
                                client,
                                arguments.get("symbol", ""),
                                arguments.get("side", ""),
                                float(arguments.get("size", 0)),
                            )
                        except Exception as e:
                            tool_result = {"error": f"estimate_slippage failed: {e}"}
                    elif tool_name == "calculate_ofi":
                        try:
                            tool_result = handle_calculate_ofi(
                                client,
                                arguments.get("symbol", ""),
                                int(arguments.get("start", 0)),
                                int(arguments.get("end", 0)),
                                arguments.get("interval", ""),
                            )
                        except Exception as e:
                            tool_result = {"error": f"calculate_ofi failed: {e}"}
                    elif tool_name == "track_whale_alerts":
                        try:
                            tool_result = handle_track_whale_alerts(
                                client,
                                arguments.get("symbol", ""),
                                int(arguments.get("start", 0)),
                                int(arguments.get("end", 0)),
                                float(arguments.get("min_usd", 0)),
                            )
                        except Exception as e:
                            tool_result = {"error": f"track_whale_alerts failed: {e}"}
                    elif tool_name == "get_iv_surface":
                        try:
                            tool_result = handle_get_iv_surface(
                                client,
                                arguments.get("underlying", ""),
                                int(arguments.get("at", 0)),
                                rate=float(arguments.get("rate", 0.0)),
                            )
                        except Exception as e:
                            tool_result = {"error": f"get_iv_surface failed: {e}"}
                    elif tool_name == "get_term_structure":
                        try:
                            tool_result = handle_get_term_structure(
                                client,
                                arguments.get("underlying", ""),
                                int(arguments.get("at", 0)),
                                rate=float(arguments.get("rate", 0.0)),
                            )
                        except Exception as e:
                            tool_result = {"error": f"get_term_structure failed: {e}"}
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
    finally:
        try:
            await loop.shutdown_default_executor()
        except Exception:
            pass



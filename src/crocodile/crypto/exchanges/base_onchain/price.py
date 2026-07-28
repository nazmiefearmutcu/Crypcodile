"""Reading a Base mainnet pool's current price, straight from chain state.

These two readers spent the merge inside ``crypto/legacy/mcp_server.py``, and a verbatim
copy of them inside ``equity/legacy/mcp_server.py`` — which is how an equities library came
to import ``POOL_SPECS``, ``TOKENS`` and a private ``_load_ipc`` out of a crypto connector,
carry Uniswap V3 ABI fragments, and answer a request for the stock ticker ``IBM`` with
``Supported: ['AERO-USDC', 'cbBTC-USDC', 'DEGEN-WETH', 'WELL-WETH', 'WETH-USDC']``.

They belong here: they read the same pools ``connector.py`` streams, through the same
``POOL_SPECS`` and ``FACTORIES`` registries, and they are the only part of the deleted MCP
servers that was not a hand-copy of something the capability registry now declares.

They **are** capabilities, and this paragraph used to say the opposite. It argued that every
capability answers out of the lake while these two read the head block over RPC, so neither
had an asset class, a stored record, or a ``prov`` that survives the answer being different
one block later. The surface-parity gate disagreed on the first point — three wire names had
been on the wire and were served by nothing — and ``crocodile.capabilities.onchain`` now
declares both, crypto-only and on ``IRREDUCIBLE``. A pool contract reporting its own
``slot0`` is a venue reporting itself, which is what ``prov`` had to be a claim about. They
also stay reachable as plain functions, which is how ``examples/base_dashboard.py`` and
``examples/farcaster_frame.py`` use them.

**A failure here raises.** Both readers returned ``{"error": …}`` from five places, which
every caller above them read as an answer: the CLI exited 0 with the dict printed as the
result, and REST answered 200 with ``{"result":{"error":"403 Forbidden"},"provenance":
{"prov":"native"}}`` — a provenance block describing a reading that does not exist, and a
``warning_for`` that stays silent because the level claimed is ``NATIVE``. An unsupported
pool raises :class:`~crocodile.core.errors.FatalConnectorError` because no retry helps, and
an exhausted RPC failover raises :class:`~crocodile.core.errors.ConnectorError`.

The equity fork's yfinance branch did not come with them. It hardcoded eight tickers,
returned ``pool_address="equity_feed"`` with ``reserve0``/``reserve1`` of ``0.0``, and fell
through to the DEX pool list for the ninth. ``search`` and ``indicators`` answer an equity
question properly, out of the lake, for every ticker.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from typing import Any, cast

import web3
from web3 import AsyncHTTPProvider

from crocodile.core.errors import ConnectorError, CrocodileError, FatalConnectorError
from crocodile.crypto.exchanges.base_onchain.connector import FACTORIES, POOL_SPECS, TOKENS

__all__ = [
    "DEFAULT_RPC_URL",
    "AsyncWeb3",
    "execute_with_retry_and_failover",
    "get_base_market_data",
    "get_onchain_price",
]


class AsyncWeb3(web3.AsyncWeb3):  # type: ignore[misc]
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
        from crocodile.crypto.exchanges.base_onchain.connector import _load_ipc
        await _load_ipc()
    except Exception:
        pass
    spec = cast(dict[str, Any], POOL_SPECS.get(symbol))
    if not spec:
        raise FatalConnectorError(
            f"Symbol {symbol} not supported. Supported: {list(POOL_SPECS.keys())}"
        )
    
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
            raise FatalConnectorError(f"Pool for {symbol} not found on Base mainnet.")
            
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
    except CrocodileError:
        raise
    except Exception as e:
        raise ConnectorError(f"Failed fetching pool state: {e}") from e

async def get_base_market_data(token_pair: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
    """Fetch real-time price, reserves, and 1-hour volume for a token pair on Base mainnet."""
    symbol = token_pair.replace("/", "-").upper()
    
    state_res = await get_onchain_price(symbol, rpc_url)

    spec = cast(dict[str, Any], POOL_SPECS.get(symbol))
    if not spec:
        raise FatalConnectorError(f"Symbol {symbol} not supported.")
        
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
                
                scale0 = 10 ** int(spec["decimals0"])
                scale1 = 10 ** int(spec["decimals1"])
                if not is_flipped:
                    abs_base = (amt0_in if amt0_in > 0 else amt0_out) / scale0
                    abs_quote = (amt1_in if amt1_in > 0 else amt1_out) / scale1
                else:
                    abs_base = (amt1_in if amt1_in > 0 else amt1_out) / scale0
                    abs_quote = (amt0_in if amt0_in > 0 else amt0_out) / scale1
            
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
    except CrocodileError:
        raise
    except Exception as e:
        raise ConnectorError(f"Failed fetching 1h volume: {e}") from e

# ---------------------------------------------------------------------------
# Discovery tool handlers (pure; unit-testable without stdio)
# ---------------------------------------------------------------------------



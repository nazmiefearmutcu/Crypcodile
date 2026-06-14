from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from collections.abc import AsyncIterator, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from crypcodile.exchanges.base import Connector
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink

from .normalize import normalize_onchain_update

log = logging.getLogger(__name__)

EXCHANGE = "base_onchain"
DEFAULT_RPC_URL = "https://base-rpc.publicnode.com"

# Token & Factory mappings
FACTORIES = {
    "uniswap_v3": "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    "aerodrome": "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
}

def _get_ipc_file() -> str:
    return os.getenv("CUSTOM_POOLS_IPC_FILE", "/Users/nazmi/Crypcodile/.custom_pools_ipc.json")

_ipc_executor = ThreadPoolExecutor(max_workers=1)

def _write_ipc_to_file(name: str, data_dict: dict[str, Any]) -> None:
    try:
        data = {}
        ipc_file = _get_ipc_file()
        if os.path.exists(ipc_file):
            try:
                with open(ipc_file, "r") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception:
                pass
        data[name] = data_dict
        
        tmp_file = ipc_file + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, ipc_file)
    except Exception:
        pass

def _load_ipc_sync() -> None:
    pass

class IPCDict(dict[str, Any]):
    def __init__(self, name: str, default_data: dict[str, Any] | None = None) -> None:
        if default_data is None:
            default_data = {}
        super().__init__(default_data)
        self._name = name
        self._default = default_data
        self._last_ipc_file = ""

    def _sync(self) -> None:
        current_file = _get_ipc_file()
        if current_file != self._last_ipc_file:
            dict.clear(self)
            dict.update(self, self._default)
            try:
                if os.path.exists(current_file):
                    with open(current_file, "r") as f:
                        content = f.read().strip()
                        if content:
                            file_data = json.loads(content)
                            if self._name in file_data:
                                dict.update(self, file_data[self._name])
            except Exception:
                pass
            self._last_ipc_file = current_file

    def __contains__(self, key: object) -> bool:
        self._sync()
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        self._sync()
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self._sync()
        super().__setitem__(key, value)
        self._write_ipc()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._sync()
        super().update(*args, **kwargs)
        self._write_ipc()

    def _write_ipc(self) -> None:
        data_copy = dict(self)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(asyncio.to_thread(_write_ipc_to_file, self._name, data_copy))
        except RuntimeError:
            _ipc_executor.submit(_write_ipc_to_file, self._name, data_copy)

    def get(self, key: str, default: Any = None) -> Any:
        self._sync()
        return super().get(key, default)

    def keys(self) -> Any:
        self._sync()
        return super().keys()

    def values(self) -> Any:
        self._sync()
        return super().values()

    def items(self) -> Any:
        self._sync()
        return super().items()

    def __len__(self) -> int:
        self._sync()
        return super().__len__()

    def __iter__(self) -> Any:
        self._sync()
        return super().__iter__()

    def __repr__(self) -> str:
        self._sync()
        return super().__repr__()

async def _load_ipc() -> None:
    await asyncio.to_thread(_load_ipc_sync)

if "pytest" in sys.modules:
    try:
        ipc_f = _get_ipc_file()
        if os.path.exists(ipc_f):
            os.remove(ipc_f)
    except Exception:
        pass

TOKENS = IPCDict("TOKENS", {
    "AERO": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    "cbBTC": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
    "DEGEN": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",
    "WELL": "0xA88594D404727625A9437C3f886C7643872296AE",
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
    "USDbC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
    "WETH": "0x4200000000000000000000000000000000000006",
})

# Pool specifications for the supported symbols
POOL_SPECS = IPCDict("POOL_SPECS", {
    "AERO-USDC": {
        "type": "aerodrome_v2",
        "token0": "AERO",
        "token1": "USDbC",
        "stable": False,
        "decimals0": 18,
        "decimals1": 6,
    },
    "cbBTC-USDC": {
        "type": "uniswap_v3",
        "token0": "cbBTC",
        "token1": "USDC",
        "fee": 500,
        "decimals0": 8,
        "decimals1": 6,
    },
    "DEGEN-WETH": {
        "type": "uniswap_v3",
        "token0": "DEGEN",
        "token1": "WETH",
        "fee": 3000,
        "decimals0": 18,
        "decimals1": 18,
    },
    "WELL-WETH": {
        "type": "aerodrome_v2",
        "token0": "WELL",
        "token1": "WETH",
        "stable": False,
        "decimals0": 18,
        "decimals1": 18,
    }
})

_load_ipc_sync()

SWAP_TOPIC_V3 = "0xc42079f94a6350d7e6235f29174924f9287a20ac8e91c97b870daEE5297F6e85"
SWAP_TOPIC_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"


def _register_custom_pools(custom_pools: dict[str, dict[str, Any]] | None) -> None:
    if not custom_pools:
        return
    for sym, cfg in custom_pools.items():
        pool_type = cfg.get("factory_type") or cfg.get("type") or "uniswap_v3"
        t0 = str(cfg.get("token0", "T0"))
        t1 = str(cfg.get("token1", "T1"))
        t0_addr = str(cfg.get("token0_address") or cfg.get("token0"))
        t1_addr = str(cfg.get("token1_address") or cfg.get("token1"))
        
        if t0 not in TOKENS:
            TOKENS[t0] = t0_addr
        if t1 not in TOKENS:
            TOKENS[t1] = t1_addr
        if t0_addr not in TOKENS:
            TOKENS[t0_addr] = t0_addr
        if t1_addr not in TOKENS:
            TOKENS[t1_addr] = t1_addr
            
        spec = {
            "type": pool_type,
            "token0": t0,
            "token1": t1,
            "decimals0": cfg.get("decimals0", 18),
            "decimals1": cfg.get("decimals1", 18),
        }
        if "fee" in cfg:
            spec["fee"] = cfg["fee"]
        if "stable" in cfg:
            spec["stable"] = cfg["stable"]
        if "address" in cfg:
            spec["address"] = cfg["address"]
            
        POOL_SPECS[sym] = spec





class BaseOnchainTransport:
    """A polling-based transport that queries pool state and swap events from Base mainnet."""

    def __init__(
        self,
        rpc_url: str,
        symbols: list[str],
        poll_interval: float = 5.0,
        custom_pools: dict[str, dict[str, Any]] | None = None
    ) -> None:
        self.rpc_url = rpc_url
        self.symbols = symbols
        self.poll_interval = poll_interval
        self._connected = False
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._poll_task: asyncio.Task[None] | None = None
        self._last_blocks: dict[str, int] = {}
        self._block_cache: dict[int, int] = {}
        self._seen_logs: set[tuple[str, int]] = set()
        _register_custom_pools(custom_pools)

    async def _call_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        import inspect
        det_excs = []
        try:
            from web3.exceptions import ContractLogicError
            det_excs.append(ContractLogicError)
        except ImportError:
            pass
        try:
            from web3.exceptions import BadFunctionCallOutput
            det_excs.append(BadFunctionCallOutput)
        except ImportError:
            pass
        try:
            from web3.exceptions import Web3ValidationError
            det_excs.append(Web3ValidationError)
        except ImportError:
            pass
        try:
            from web3.exceptions import ValidationError as Web3ValidationError2
            det_excs.append(Web3ValidationError2)
        except ImportError:
            pass
        try:
            from eth_utils.exceptions import ValidationError as EthValidationError
            det_excs.append(EthValidationError)
        except ImportError:
            pass
        deterministic_exceptions = tuple(det_excs)
        
        attempt = 0
        max_attempts = 5
        base_delay = kwargs.pop("base_delay", 0.0001 if self.poll_interval < 0.2 else 1.0)
        max_delay = 10.0
        
        while True:
            try:
                if callable(func):
                    res = func(*args, **kwargs)
                else:
                    res = func
                
                while inspect.isawaitable(res):
                    res = await res
                return res
            except Exception as e:
                if deterministic_exceptions and isinstance(e, deterministic_exceptions):
                    log.error(f"Deterministic RPC exception encountered, raising immediately: {e}")
                    raise
                attempt += 1
                if attempt >= max_attempts:
                    log.error(f"RPC call failed after {attempt} attempts: {e}")
                    raise
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay = delay * random.uniform(0.5, 1.0)
                log.warning(
                    f"RPC call failed: {e}. Retrying in {delay:.4f}s... "
                    f"(Attempt {attempt}/{max_attempts})"
                )
                await asyncio.sleep(delay)

    async def _get_block_number(self, w3: Any) -> int:
        async def get_bn() -> int:
            import inspect
            val = w3.eth.block_number
            while inspect.isawaitable(val):
                val = await val
            return int(val)
        return int(await self._call_with_retry(get_bn))

    async def _get_block_timestamp(self, w3: Any, block_number: int) -> int:
        if block_number in self._block_cache:
            return self._block_cache[block_number]
        blk = await self._call_with_retry(w3.eth.get_block, block_number)
        ts = int(blk["timestamp"])
        if len(self._block_cache) > 1000:
            self._block_cache.clear()
        self._block_cache[block_number] = ts
        return ts

    async def connect(self) -> None:
        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[bytes]:
        while self._connected or not self._queue.empty():
            try:
                val = await self._queue.get()
                if val is None:
                    break
                yield val
            except asyncio.CancelledError:
                break

    async def send(self, data: bytes) -> None:
        pass  # subscription logic is handled natively in loop

    async def close(self) -> None:
        self._connected = False
        await self._queue.put(None)
        if self._poll_task:
            if self._poll_task != asyncio.current_task():
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except asyncio.CancelledError:
                    pass
                self._poll_task = None

    async def _poll_loop(self) -> None:
        from web3 import AsyncHTTPProvider, AsyncWeb3
        
        provider = AsyncHTTPProvider(self.rpc_url)
        w3 = AsyncWeb3(provider)
        
        async def get_bn() -> int:
            import inspect
            val = w3.eth.block_number
            if inspect.isawaitable(val):
                return await val
            return val

        try:
            # ABIs for factories and pools
            factory_v3_abi = [{
                "inputs": [
                    {"name": "tokenA", "type": "address"},
                    {"name": "tokenB", "type": "address"},
                    {"name": "fee", "type": "uint24"}
                ],
                "name": "getPool",
                "outputs": [{"type": "address"}],
                "stateMutability": "view", "type": "function"
            }]
            
            factory_aero_abi = [{
                "inputs": [
                    {"name": "tokenA", "type": "address"},
                    {"name": "tokenB", "type": "address"},
                    {"name": "stable", "type": "bool"}
                ],
                "name": "getPool",
                "outputs": [{"type": "address"}],
                "stateMutability": "view", "type": "function"
            }]
            
            pool_v3_abi = [
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
                },
                {
                    "inputs": [],
                    "name": "tickSpacing",
                    "outputs": [{"type": "int24"}],
                    "stateMutability": "view", "type": "function"
                }
            ]
            
            pool_v2_abi = [{
                "inputs": [],
                "name": "getReserves",
                "outputs": [
                    {"name": "_reserve0", "type": "uint256"},
                    {"name": "_reserve1", "type": "uint256"},
                    {"name": "_blockTimestampLast", "type": "uint256"}
                ],
                "stateMutability": "view", "type": "function"
            }]

            resolved_pools: dict[str, dict[str, Any]] = {}

            # 2. Main polling loop
            while self._connected:
                await asyncio.get_running_loop().run_in_executor(_ipc_executor, _load_ipc_sync)
                try:
                    # 1. Resolve pool addresses dynamically inside the polling loop concurrently
                    async def resolve_single_pool(sym: str) -> None:
                        if sym in resolved_pools:
                            return
                        spec = cast(dict[str, Any], POOL_SPECS.get(sym))
                        if not spec:
                            return
                        
                        try:
                            token0_name = str(spec["token0"])
                            token1_name = str(spec["token1"])
                            t0_val = TOKENS.get(token0_name, token0_name)
                            t0_addr = AsyncWeb3.to_checksum_address(t0_val)
                            t1_val = TOKENS.get(token1_name, token1_name)
                            t1_addr = AsyncWeb3.to_checksum_address(t1_val)
                            is_flipped = int(t1_addr, 16) < int(t0_addr, 16)
                            
                            if "address" in spec:
                                pool_addr = AsyncWeb3.to_checksum_address(spec["address"])
                            elif spec["type"] == "uniswap_v3":
                                # Sort token addresses for Uniswap V3
                                sorted_t0, sorted_t1 = sorted([t0_addr, t1_addr])
                                factory = w3.eth.contract(
                                    address=AsyncWeb3.to_checksum_address(FACTORIES["uniswap_v3"]),
                                    abi=factory_v3_abi
                                )
                                fee = int(spec["fee"])
                                pool_addr = await self._call_with_retry(
                                    factory.functions.getPool(sorted_t0, sorted_t1, fee).call
                                )
                            else: # aerodrome_v2
                                factory = w3.eth.contract(
                                    address=AsyncWeb3.to_checksum_address(FACTORIES["aerodrome"]),
                                    abi=factory_aero_abi
                                )
                                stable = bool(spec["stable"])
                                pool_addr = await self._call_with_retry(
                                    factory.functions.getPool(t0_addr, t1_addr, stable).call
                                )
                            
                            if pool_addr != "0x0000000000000000000000000000000000000000":
                                resolved_pools[sym] = {
                                    "address": AsyncWeb3.to_checksum_address(pool_addr),
                                    "spec": spec,
                                    "contract": w3.eth.contract(
                                        address=AsyncWeb3.to_checksum_address(pool_addr),
                                        abi=(pool_v3_abi if spec["type"] == "uniswap_v3"
                                             else pool_v2_abi)
                                    ),
                                    "is_flipped": is_flipped
                                }
                                log.info(
                                    f"base_onchain: Resolved pool {sym} to {pool_addr} "
                                    f"(flipped: {is_flipped})"
                                )
                        except Exception as e:
                            log.error(f"base_onchain: Failed resolving pool {sym}: {e}")

                    resolution_tasks = [
                        resolve_single_pool(sym)
                        for sym in self.symbols
                        if sym not in resolved_pools
                    ]
                    if resolution_tasks:
                        await asyncio.gather(*resolution_tasks, return_exceptions=True)

                    current_block = await self._get_block_number(w3)
                    
                    async def poll_single_pool(sym: str, pool: dict[str, Any]) -> None:
                        spec = pool["spec"]
                        addr = pool["address"]
                        contract = pool["contract"]
                        is_flipped = pool["is_flipped"]
                        
                        price = 0.0
                        reserve0 = 0.0
                        reserve1 = 0.0
                        swaps = []
                        
                        if sym not in self._last_blocks:
                            self._last_blocks[sym] = max(0, current_block - 20)
                        
                        async def fetch_state() -> dict[str, Any]:
                            if spec["type"] == "uniswap_v3":
                                slot0_fut = self._call_with_retry(contract.functions.slot0().call)
                                liquidity_fut = self._call_with_retry(contract.functions.liquidity().call)
                                if hasattr(contract.functions, "tickSpacing"):
                                    tick_spacing_fut = self._call_with_retry(contract.functions.tickSpacing().call)
                                else:
                                    async def _dummy_spacing() -> Any:
                                        raise AttributeError("tickSpacing not supported on contract functions")
                                    tick_spacing_fut = _dummy_spacing()
                                
                                results = await asyncio.gather(
                                    slot0_fut,
                                    liquidity_fut,
                                    tick_spacing_fut,
                                    return_exceptions=True
                                )
                                slot0_val = results[0]
                                liquidity_val = results[1]
                                tick_spacing_val = results[2]
                                
                                if isinstance(slot0_val, Exception):
                                    raise slot0_val
                                if isinstance(liquidity_val, Exception):
                                    raise liquidity_val
                                    
                                if isinstance(tick_spacing_val, Exception):
                                    log.warning(
                                        f"base_onchain: Failed to fetch tickSpacing dynamically: {tick_spacing_val}. "
                                        f"Deriving from fee tier."
                                    )
                                    fee = int(spec.get("fee", 3000))
                                    tick_spacing_val = (
                                        1 if fee == 100 else
                                        10 if fee == 500 else
                                        60 if fee == 3000 else
                                        200 if fee == 10000 else
                                        max(1, fee // 50)
                                    )
                                return {
                                    "slot0": slot0_val,
                                    "liquidity": liquidity_val,
                                    "tick_spacing": tick_spacing_val
                                }
                            else: # aerodrome_v2
                                res_val = await self._call_with_retry(contract.functions.getReserves().call)
                                return {"reserves": res_val}

                        async def fetch_logs() -> list[Any]:
                            swap_topic = (
                                SWAP_TOPIC_V3 if spec["type"] == "uniswap_v3"
                                else SWAP_TOPIC_V2
                            )
                            overlap = 5
                            start_block = max(0, self._last_blocks[sym] + 1 - overlap)
                            end_block = current_block
                            
                            logs_list = []
                            if start_block <= end_block:
                                chunk_size = 500
                                for from_b in range(start_block, end_block + 1, chunk_size):
                                    to_b = min(from_b + chunk_size - 1, end_block)
                                    chunk_logs = await self._call_with_retry(
                                        w3.eth.get_logs,
                                        {
                                            "address": addr,
                                            "fromBlock": from_b,
                                            "toBlock": to_b,
                                            "topics": [swap_topic]
                                        }
                                    )
                                    logs_list.extend(chunk_logs)
                                    self._last_blocks[sym] = max(self._last_blocks[sym], to_b)
                            return logs_list

                        initial_last_block = self._last_blocks[sym]
                        try:
                            state_task = asyncio.create_task(fetch_state())
                            logs_task = asyncio.create_task(fetch_logs())
                            try:
                                await asyncio.gather(state_task, logs_task)
                                state_res = state_task.result()
                                logs = logs_task.result()
                            except BaseException as e:
                                for task in (state_task, logs_task):
                                    if not task.done():
                                        task.cancel()
                                for task in (state_task, logs_task):
                                    try:
                                        await task
                                    except BaseException:
                                        pass
                                is_cancelled = isinstance(e, asyncio.CancelledError)
                                state_failed = False
                                try:
                                    if state_task.done() and not state_task.cancelled() and state_task.exception() is not None:
                                        state_failed = True
                                except Exception:
                                    pass
                                if is_cancelled or state_failed:
                                    self._last_blocks[sym] = initial_last_block
                                raise
                            
                            if spec["type"] == "uniswap_v3":
                                slot0 = state_res["slot0"]
                                liquidity = state_res["liquidity"]
                                tick_spacing = state_res["tick_spacing"]
                                
                                sqrtPriceX96 = slot0[0]
                                price_ratio = (sqrtPriceX96 / (2**96)) ** 2
                                
                                # Price of base in terms of quote
                                dec_diff = int(spec["decimals0"]) - int(spec["decimals1"])
                                if not is_flipped:
                                    price = price_ratio * (10 ** dec_diff)
                                else:
                                    price = (
                                        (1.0 / price_ratio) * (10 ** dec_diff)
                                        if price_ratio > 0 else 0.0
                                    )
                                
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
                            
                            else: # aerodrome_v2
                                res = state_res["reserves"]
                                if not is_flipped:
                                    reserve0 = res[0] / (10 ** int(spec["decimals0"]))
                                    reserve1 = res[1] / (10 ** int(spec["decimals1"]))
                                else:
                                    reserve0 = res[1] / (10 ** int(spec["decimals0"]))
                                    reserve1 = res[0] / (10 ** int(spec["decimals1"]))
                                price = reserve1 / reserve0 if reserve0 > 0 else 0.0
                            
                            for lg in logs:
                                data = lg["data"]
                                tx_hash = lg["transactionHash"].hex()
                                log_index = lg["logIndex"]
                                
                                # Deduplicate seen logs
                                log_key = (tx_hash, log_index)
                                if log_key in self._seen_logs:
                                    continue
                                self._seen_logs.add(log_key)
                                if len(self._seen_logs) > 5000:
                                    self._seen_logs = set(list(self._seen_logs)[2500:])
                                    
                                ts = await self._get_block_timestamp(w3, lg["blockNumber"])
                                
                                if spec["type"] == "uniswap_v3":
                                    amount0 = int.from_bytes(
                                        data[0:32], byteorder='big', signed=True
                                    )
                                    amount1 = int.from_bytes(
                                        data[32:64], byteorder='big', signed=True
                                    )
                                    
                                    if not is_flipped:
                                        abs_base = abs(amount0) / (10 ** int(spec["decimals0"]))
                                        abs_quote = abs(amount1) / (10 ** int(spec["decimals1"]))
                                        is_buy = amount0 < 0
                                    else:
                                        abs_base = abs(amount1) / (10 ** int(spec["decimals0"]))
                                        abs_quote = abs(amount0) / (10 ** int(spec["decimals1"]))
                                        is_buy = amount1 < 0
                                    
                                    sw_price = (
                                        abs_quote / abs_base if abs_base > 0
                                        else 0.0
                                    )
                                    
                                    swaps.append({
                                        "tx_hash": tx_hash,
                                        "log_index": log_index,
                                        "timestamp": ts,
                                        "price": sw_price,
                                        "amount": abs_base,
                                        "is_buy": is_buy
                                    })
                                else: # aerodrome_v2
                                    amt0_in = int.from_bytes(
                                        data[0:32], byteorder='big', signed=False
                                    )
                                    amt1_in = int.from_bytes(
                                        data[32:64], byteorder='big', signed=False
                                    )
                                    amt0_out = int.from_bytes(
                                        data[64:96], byteorder='big', signed=False
                                    )
                                    amt1_out = int.from_bytes(
                                        data[96:128], byteorder='big', signed=False
                                    )
                                    
                                    if not is_flipped:
                                        amt_base = (
                                            (amt0_in if amt0_in > 0 else amt0_out)
                                            / (10 ** int(spec["decimals0"]))
                                        )
                                        amt_quote = (
                                            (amt1_in if amt1_in > 0 else amt1_out)
                                            / (10 ** int(spec["decimals1"]))
                                        )
                                        is_buy = amt0_out > 0
                                    else:
                                        amt_base = (
                                            (amt1_in if amt1_in > 0 else amt1_out)
                                            / (10 ** int(spec["decimals0"]))
                                        )
                                        amt_quote = (
                                            (amt0_in if amt0_in > 0 else amt0_out)
                                            / (10 ** int(spec["decimals1"]))
                                        )
                                        is_buy = amt1_out > 0
                                    
                                    sw_price = (
                                        amt_quote / amt_base if amt_base > 0
                                        else 0.0
                                    )
                                    
                                    swaps.append({
                                        "tx_hash": tx_hash,
                                        "log_index": log_index,
                                        "timestamp": ts,
                                        "price": sw_price,
                                        "amount": amt_base,
                                        "is_buy": is_buy
                                    })
                            
                            state_payload = {
                                "price": price,
                                "reserve0": reserve0,
                                "reserve1": reserve1,
                                "is_flipped": is_flipped,
                                "decimals0": spec["decimals0"],
                                "decimals1": spec["decimals1"],
                            }
                            if spec["type"] == "uniswap_v3":
                                state_payload["tick"] = int(slot0[1])
                                state_payload["liquidity"] = int(liquidity)
                                state_payload["tickSpacing"] = int(tick_spacing)
                                state_payload["tick_spacing"] = int(tick_spacing)
                            
                            update_msg = {
                                "type": "onchain_update",
                                "block": current_block,
                                "pool": sym,
                                "pool_type": spec["type"],
                                "timestamp": await self._get_block_timestamp(w3, current_block),
                                "state": state_payload,
                                "swaps": swaps
                            }
                            await self._queue.put(json.dumps(update_msg).encode())
                            self._last_blocks[sym] = max(self._last_blocks[sym], current_block)
                        except Exception as e:
                            log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
                            raise
                    
                    poll_tasks = [
                        poll_single_pool(sym, pool)
                        for sym, pool in resolved_pools.items()
                    ]
                    if poll_tasks:
                        results = await asyncio.gather(*poll_tasks, return_exceptions=True)
                        for res_err in results:
                            if isinstance(res_err, Exception):
                                log.error(f"base_onchain: Error polling pool concurrently: {res_err}")
                    
                except Exception as e:
                    log.error(f"base_onchain: Error polling pool data: {e}")
                
                await asyncio.sleep(self.poll_interval)
        finally:
            try:
                import inspect
                res = w3.provider.disconnect()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass


class BaseOnchainConnector(Connector):
    """On-chain Base Ecosystem DEX connector.
    
    Subscribes to dynamic reserves, prices, and trades from major Aerodrome and Uniswap V3 pools.
    """

    name = EXCHANGE
    ws_url = "wss://base-rpc.publicnode.com"  # placeholder
    rest_url = "https://base-rpc.publicnode.com"

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        custom_pools: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        rpc_url = os.getenv("BASE_RPC_URL", DEFAULT_RPC_URL)
        _register_custom_pools(custom_pools)
        self.transport = BaseOnchainTransport(rpc_url, symbols, custom_pools=custom_pools)

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict) and msg.get("type") == "onchain_update":
            yield from normalize_onchain_update(msg, local_ts)

    async def list_instruments(self) -> list[Instrument]:
        custom_ticks = {
            "AERO-USDC": 1e-6,
            "cbBTC-USDC": 1e-2,
            "DEGEN-WETH": 1e-8,
            "WELL-WETH": 1e-8,
        }
        instruments = []
        for sym in self.symbols:
            spec = POOL_SPECS.get(sym)
            if not spec:
                continue
            tick_size = custom_ticks.get(sym, 10 ** (-int(spec.get("decimals1", 6))))
            instruments.append(
                Instrument(
                    canonical=f"base_onchain:{sym}",
                    exchange="base_onchain",
                    symbol_raw=sym,
                    kind=Kind.SPOT,
                    base=spec["token0"],
                    quote=spec["token1"],
                    tick_size=tick_size,
                )
            )
        return instruments

    def subscribe_channels(self) -> list[str]:
        return self.channels

    async def _subscribe(self, transport: Transport) -> None:
        pass  # subscription handled in the poll transport

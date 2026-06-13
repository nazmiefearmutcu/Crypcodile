from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable, AsyncIterator
from typing import Any

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

TOKENS = {
    "AERO": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    "cbBTC": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
    "DEGEN": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",
    "WELL": "0xA88594D404727625A9437C3f886C7643872296AE",
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
    "USDbC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
    "WETH": "0x4200000000000000000000000000000000000006",
}

# Pool specifications for the supported symbols
POOL_SPECS = {
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
}

SWAP_TOPIC_V3 = "0xc42079f94a6350d7e6235f29174924f9287a20ac8e91c97b870daEE5297F6e85"
SWAP_TOPIC_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"


class BaseOnchainTransport:
    """A polling-based transport that queries pool state and swap events from Base mainnet."""

    def __init__(self, rpc_url: str, symbols: list[str], poll_interval: float = 5.0) -> None:
        self.rpc_url = rpc_url
        self.symbols = symbols
        self.poll_interval = poll_interval
        self._connected = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._poll_task: asyncio.Task | None = None
        self._last_block: int | None = None

    async def connect(self) -> None:
        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_loop())

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[bytes]:
        while self._connected or not self._queue.empty():
            try:
                val = await self._queue.get()
                yield val
            except asyncio.CancelledError:
                break

    async def send(self, data: bytes) -> None:
        pass  # subscription logic is handled natively in loop

    async def close(self) -> None:
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _poll_loop(self) -> None:
        from web3 import Web3
        
        w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        
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

        # 1. Resolve pool addresses dynamically on start
        resolved_pools = {}
        for sym in self.symbols:
            spec = POOL_SPECS.get(sym)
            if not spec:
                continue
            
            t0_addr = Web3.to_checksum_address(TOKENS[spec["token0"]])
            t1_addr = Web3.to_checksum_address(TOKENS[spec["token1"]])
            
            try:
                if spec["type"] == "uniswap_v3":
                    # Sort token addresses for Uniswap V3
                    sorted_t0, sorted_t1 = sorted([t0_addr, t1_addr])
                    factory = w3.eth.contract(
                        address=Web3.to_checksum_address(FACTORIES["uniswap_v3"]),
                        abi=factory_v3_abi
                    )
                    pool_addr = factory.functions.getPool(sorted_t0, sorted_t1, spec["fee"]).call()
                else: # aerodrome_v2
                    factory = w3.eth.contract(
                        address=Web3.to_checksum_address(FACTORIES["aerodrome"]),
                        abi=factory_aero_abi
                    )
                    pool_addr = factory.functions.getPool(t0_addr, t1_addr, spec["stable"]).call()
                
                if pool_addr != "0x0000000000000000000000000000000000000000":
                    resolved_pools[sym] = {
                        "address": Web3.to_checksum_address(pool_addr),
                        "spec": spec,
                        "contract": w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=pool_v3_abi if spec["type"] == "uniswap_v3" else pool_v2_abi)
                    }
                    log.info(f"base_onchain: Resolved pool {sym} to {pool_addr}")
            except Exception as e:
                log.error(f"base_onchain: Failed resolving pool {sym}: {e}")

        # 2. Main polling loop
        while self._connected:
            try:
                current_block = w3.eth.block_number
                if self._last_block is None:
                    self._last_block = current_block - 20  # query last 20 blocks initially (~40 seconds)
                
                for sym, pool in resolved_pools.items():
                    spec = pool["spec"]
                    addr = pool["address"]
                    contract = pool["contract"]
                    
                    price = 0.0
                    reserve0 = 0.0
                    reserve1 = 0.0
                    
                    # A. Query current price and reserves/liquidity
                    if spec["type"] == "uniswap_v3":
                        slot0 = contract.functions.slot0().call()
                        liquidity = contract.functions.liquidity().call()
                        
                        sqrtPriceX96 = slot0[0]
                        price_ratio = (sqrtPriceX96 / (2**96)) ** 2
                        
                        # Price of token0 in terms of token1
                        # Sort tokens to ensure decimal correction is correct
                        sorted_tokens = sorted([TOKENS[spec["token0"]], TOKENS[spec["token1"]]])
                        if sorted_tokens[0] == TOKENS[spec["token0"]]:
                            dec_diff = spec["decimals0"] - spec["decimals1"]
                            price = price_ratio * (10 ** dec_diff)
                        else:
                            dec_diff = spec["decimals1"] - spec["decimals0"]
                            price = (1.0 / price_ratio) * (10 ** dec_diff) if price_ratio > 0 else 0.0
                        
                        # Calculate virtual reserves
                        sqrtP = sqrtPriceX96 / (2**96)
                        x_virtual = liquidity / sqrtP if sqrtP > 0 else 0
                        y_virtual = liquidity * sqrtP
                        
                        # Sort virtual reserves matching spec token order
                        if sorted_tokens[0] == TOKENS[spec["token0"]]:
                            reserve0 = x_virtual / (10 ** spec["decimals0"])
                            reserve1 = y_virtual / (10 ** spec["decimals1"])
                        else:
                            reserve0 = y_virtual / (10 ** spec["decimals0"])
                            reserve1 = x_virtual / (10 ** spec["decimals1"])
                    
                    else: # aerodrome_v2
                        res = contract.functions.getReserves().call()
                        reserve0 = res[0] / (10 ** spec["decimals0"])
                        reserve1 = res[1] / (10 ** spec["decimals1"])
                        price = reserve1 / reserve0 if reserve0 > 0 else 0.0

                    # B. Fetch Swap logs
                    swaps = []
                    swap_topic = SWAP_TOPIC_V3 if spec["type"] == "uniswap_v3" else SWAP_TOPIC_V2
                    
                    try:
                        logs = w3.eth.get_logs({
                            "address": addr,
                            "fromBlock": self._last_block + 1,
                            "toBlock": current_block,
                            "topics": [swap_topic]
                        })
                        
                        for lg in logs:
                            data = lg["data"]
                            tx_hash = lg["transactionHash"].hex()
                            log_index = lg["logIndex"]
                            block_hash = lg["blockHash"].hex()
                            
                            # Fetch block to get timestamp
                            blk = w3.eth.get_block(lg["blockNumber"])
                            ts = blk["timestamp"]
                            
                            if spec["type"] == "uniswap_v3":
                                # Decode Swap(address,address,int256,int256,uint160,uint128,int24)
                                amount0 = int.from_bytes(data[0:32], byteorder='big', signed=True)
                                amount1 = int.from_bytes(data[32:64], byteorder='big', signed=True)
                                
                                abs0 = abs(amount0) / (10 ** spec["decimals0"])
                                abs1 = abs(amount1) / (10 ** spec["decimals1"])
                                
                                sw_price = abs1 / abs0 if abs0 > 0 else 0.0
                                # If amount0 is negative, token0 was bought (bought using token1)
                                is_buy = amount0 < 0
                                
                                swaps.append({
                                    "tx_hash": tx_hash,
                                    "log_index": log_index,
                                    "timestamp": ts,
                                    "price": sw_price,
                                    "amount": abs0,
                                    "is_buy": is_buy
                                })
                            else: # aerodrome_v2
                                # Decode Swap(address,address,uint256,uint256,uint256,uint256)
                                amt0_in = int.from_bytes(data[0:32], byteorder='big', signed=False)
                                amt1_in = int.from_bytes(data[32:64], byteorder='big', signed=False)
                                amt0_out = int.from_bytes(data[64:96], byteorder='big', signed=False)
                                amt1_out = int.from_bytes(data[96:128], byteorder='big', signed=False)
                                
                                # Token0 amount traded
                                amt0 = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** spec["decimals0"])
                                amt1 = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** spec["decimals1"])
                                
                                sw_price = amt1 / amt0 if amt0 > 0 else 0.0
                                is_buy = amt1_in > 0 # token1 deposited to buy token0
                                
                                swaps.append({
                                    "tx_hash": tx_hash,
                                    "log_index": log_index,
                                    "timestamp": ts,
                                    "price": sw_price,
                                    "amount": amt0,
                                    "is_buy": is_buy
                                })
                    except Exception as e:
                        log.error(f"base_onchain: Error querying swap logs: {e}")

                    # C. Push state update to queue
                    update_msg = {
                        "type": "onchain_update",
                        "block": current_block,
                        "pool": sym,
                        "pool_type": spec["type"],
                        "timestamp": w3.eth.get_block(current_block)["timestamp"],
                        "state": {
                            "price": price,
                            "reserve0": reserve0,
                            "reserve1": reserve1,
                        },
                        "swaps": swaps
                    }
                    await self._queue.put(json.dumps(update_msg).encode())
                
                self._last_block = current_block
                
            except Exception as e:
                log.error(f"base_onchain: Error polling pool data: {e}")
            
            await asyncio.sleep(self.poll_interval)


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
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        rpc_url = os.getenv("BASE_RPC_URL", DEFAULT_RPC_URL)
        self.transport = BaseOnchainTransport(rpc_url, symbols)

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict) and msg.get("type") == "onchain_update":
            yield from normalize_onchain_update(msg, local_ts)

    async def list_instruments(self) -> list[Instrument]:
        return [
            Instrument(
                canonical="base_onchain:AERO-USDC",
                exchange="base_onchain",
                symbol_raw="AERO-USDC",
                kind=Kind.SPOT,
                base="AERO",
                quote="USDC",
                tick_size=1e-6,
            ),
            Instrument(
                canonical="base_onchain:cbBTC-USDC",
                exchange="base_onchain",
                symbol_raw="cbBTC-USDC",
                kind=Kind.SPOT,
                base="cbBTC",
                quote="USDC",
                tick_size=1e-2,
            ),
            Instrument(
                canonical="base_onchain:DEGEN-WETH",
                exchange="base_onchain",
                symbol_raw="DEGEN-WETH",
                kind=Kind.SPOT,
                base="DEGEN",
                quote="WETH",
                tick_size=1e-8,
            ),
            Instrument(
                canonical="base_onchain:WELL-WETH",
                exchange="base_onchain",
                symbol_raw="WELL-WETH",
                kind=Kind.SPOT,
                base="WELL",
                quote="WETH",
                tick_size=1e-8,
            ),
        ]

    def subscribe_channels(self) -> list[str]:
        return self.channels

    async def _subscribe(self, transport: Transport) -> None:
        pass  # subscription handled in the poll transport

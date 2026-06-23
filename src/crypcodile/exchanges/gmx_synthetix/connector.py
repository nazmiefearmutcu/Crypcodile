from __future__ import annotations

import asyncio
import json
import logging
import inspect
from collections.abc import AsyncIterator, Iterable
from typing import Any

from web3 import Web3
from hexbytes import HexBytes

from crypcodile.exchanges.base import Connector
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Record, Trade, Funding, Liquidation

log = logging.getLogger(__name__)

# ABIs for events
GMX_VAULT_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "bytes32", "name": "key", "type": "bytes32"},
            {"indexed": False, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "collateralToken", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "indexToken", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "sizeDelta", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "collateralDelta", "type": "uint256"},
            {"indexed": False, "internalType": "bool", "name": "isLong", "type": "bool"},
            {"indexed": False, "internalType": "uint256", "name": "price", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "fee", "type": "uint256"}
        ],
        "name": "IncreasePosition",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "bytes32", "name": "key", "type": "bytes32"},
            {"indexed": False, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "collateralToken", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "indexToken", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "sizeDelta", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "collateralDelta", "type": "uint256"},
            {"indexed": False, "internalType": "bool", "name": "isLong", "type": "bool"},
            {"indexed": False, "internalType": "uint256", "name": "price", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "fee", "type": "uint256"}
        ],
        "name": "DecreasePosition",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "bytes32", "name": "key", "type": "bytes32"},
            {"indexed": False, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "collateralToken", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "indexToken", "type": "address"},
            {"indexed": False, "internalType": "bool", "name": "isLong", "type": "bool"},
            {"indexed": False, "internalType": "uint256", "name": "size", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "collateral", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "reserveAmount", "type": "uint256"},
            {"indexed": False, "internalType": "int256", "name": "realisedPnl", "type": "int256"},
            {"indexed": False, "internalType": "uint256", "name": "markPrice", "type": "uint256"}
        ],
        "name": "LiquidatePosition",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "bytes32", "name": "key", "type": "bytes32"},
            {"indexed": False, "internalType": "uint256", "name": "size", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "collateral", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "averagePrice", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "entryFundingRate", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "reserveAmount", "type": "uint256"},
            {"indexed": False, "internalType": "int256", "name": "realisedPnl", "type": "int256"},
            {"indexed": False, "internalType": "uint256", "name": "markPrice", "type": "uint256"}
        ],
        "name": "UpdatePosition",
        "type": "event"
    }
]

SYNTHETIX_PERPS_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint256", "name": "id", "type": "uint256"},
            {"indexed": True, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "margin", "type": "uint256"},
            {"indexed": False, "internalType": "int256", "name": "size", "type": "int256"},
            {"indexed": False, "internalType": "int256", "name": "tradeSize", "type": "int256"},
            {"indexed": False, "internalType": "uint256", "name": "lastPrice", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "fundingIndex", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "fee", "type": "uint256"}
        ],
        "name": "PositionModified",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "uint256", "name": "id", "type": "uint256"},
            {"indexed": False, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "liquidator", "type": "address"},
            {"indexed": False, "internalType": "int256", "name": "size", "type": "int256"},
            {"indexed": False, "internalType": "uint256", "name": "price", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "fee", "type": "uint256"}
        ],
        "name": "PositionLiquidated",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "int256", "name": "funding", "type": "int256"},
            {"indexed": False, "internalType": "int256", "name": "fundingRate", "type": "int256"},
            {"indexed": False, "internalType": "uint256", "name": "index", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "timestamp", "type": "uint256"}
        ],
        "name": "FundingRecomputed",
        "type": "event"
    }
]

def reconstruct_log(msg: dict[str, Any]) -> dict[str, Any]:
    topics = []
    for t in msg.get("topics", []):
        if isinstance(t, str):
            try:
                topics.append(HexBytes(t))
            except ValueError:
                topics.append(t.encode() if hasattr(t, "encode") else t)
        else:
            try:
                topics.append(HexBytes(t))
            except ValueError:
                topics.append(t)
            
    data = msg.get("data", "")
    if isinstance(data, str):
        try:
            data = HexBytes(data)
        except ValueError:
            data = data.encode() if hasattr(data, "encode") else data
        
    return {
        "address": msg.get("address"),
        "topics": topics,
        "data": data,
        "blockNumber": int(msg.get("blockNumber", 0)) if msg.get("blockNumber") is not None else None,
        "transactionHash": HexBytes(msg.get("transactionHash", "")) if msg.get("transactionHash") is not None else None,
        "logIndex": int(msg.get("logIndex", 0)) if msg.get("logIndex") is not None else None,
    }

class GMXSynthetixTransport:
    def __init__(
        self,
        rpc_url: str,
        gmx_vault_address: str,
        synthetix_market_address: str,
        poll_interval: float = 1.0,
        w3: Any = None,
    ) -> None:
        self.rpc_url = rpc_url
        self.gmx_vault_address = gmx_vault_address
        self.synthetix_market_address = synthetix_market_address
        self.poll_interval = poll_interval
        self._connected = False
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._poll_task: asyncio.Task[None] | None = None
        self.w3 = w3
        self._last_block = None

    async def connect(self) -> None:
        self._connected = True
        if self.w3 is None:
            from web3 import AsyncHTTPProvider, AsyncWeb3
            self.w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def close(self) -> None:
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

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

    async def _poll_loop(self) -> None:
        while self._connected:
            try:
                val = self.w3.eth.block_number
                while inspect.isawaitable(val):
                    val = await val
                current_block = int(val)

                if self._last_block is None:
                    self._last_block = current_block - 1

                if current_block > self._last_block:
                    await self._fetch_and_process_logs(self._last_block + 1, current_block)
                    self._last_block = current_block

            except Exception as e:
                log.error(f"Error in poll loop: {e}")

            await asyncio.sleep(self.poll_interval)

    async def _fetch_and_process_logs(self, from_block: int, to_block: int) -> None:
        # GMX logs
        try:
            gmx_logs = await self.w3.eth.get_logs({
                "address": self.gmx_vault_address,
                "fromBlock": from_block,
                "toBlock": to_block,
            })
            for log_entry in gmx_logs:
                event_data = self._serialize_log(log_entry, "gmx")
                await self._queue.put(json.dumps(event_data).encode())
        except Exception as e:
            log.warning(f"Failed to fetch GMX logs: {e}")

        # Synthetix logs
        try:
            syn_logs = await self.w3.eth.get_logs({
                "address": self.synthetix_market_address,
                "fromBlock": from_block,
                "toBlock": to_block,
            })
            for log_entry in syn_logs:
                event_data = self._serialize_log(log_entry, "synthetix")
                await self._queue.put(json.dumps(event_data).encode())
        except Exception as e:
            log.warning(f"Failed to fetch Synthetix logs: {e}")

    def _serialize_log(self, log_entry: Any, protocol: str) -> dict[str, Any]:
        res = {}
        for k, v in log_entry.items():
            if hasattr(v, "hex"):
                res[k] = v.hex()
            elif isinstance(v, bytes):
                res[k] = v.hex()
            elif isinstance(v, list):
                res[k] = [x.hex() if hasattr(x, "hex") or isinstance(x, bytes) else x for x in v]
            else:
                res[k] = v
        res["protocol"] = protocol
        return res


class GMXSynthetixConnector(Connector):
    name = "gmx_synthetix"
    ws_url = "wss://base-rpc.publicnode.com"
    rest_url = "https://base-rpc.publicnode.com"

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        w3: Any = None,
        gmx_vault_address: str = "0x489ee077994B6c121ac0F0870243d56b0d9a6560",
        synthetix_market_address: str = "0xC8eD9D2C6740Bbf7D844A895F0b08053a9EDc06F",
        rpc_url: str = "https://base-rpc.publicnode.com",
        **kwargs: Any
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.w3 = w3
        self.gmx_vault_address = gmx_vault_address
        self.synthetix_market_address = synthetix_market_address
        self.rpc_url = rpc_url
        self.transport = GMXSynthetixTransport(
            rpc_url=self.rpc_url,
            gmx_vault_address=self.gmx_vault_address,
            synthetix_market_address=self.synthetix_market_address,
            w3=self.w3,
        )
        self.decoder_w3 = Web3()
        self.gmx_contract = self.decoder_w3.eth.contract(address=Web3.to_checksum_address(gmx_vault_address), abi=GMX_VAULT_ABI)
        self.syn_contract = self.decoder_w3.eth.contract(address=Web3.to_checksum_address(synthetix_market_address), abi=SYNTHETIX_PERPS_ABI)

        self.gmx_events = {
            "IncreasePosition": self.gmx_contract.events.IncreasePosition(),
            "DecreasePosition": self.gmx_contract.events.DecreasePosition(),
            "LiquidatePosition": self.gmx_contract.events.LiquidatePosition(),
            "UpdatePosition": self.gmx_contract.events.UpdatePosition(),
        }
        self.syn_events = {
            "PositionModified": self.syn_contract.events.PositionModified(),
            "PositionLiquidated": self.syn_contract.events.PositionLiquidated(),
            "FundingRecomputed": self.syn_contract.events.FundingRecomputed(),
        }

    async def _subscribe(self, transport: Transport) -> None:
        pass

    async def list_instruments(self) -> list[Instrument]:
        instruments = []
        for symbol in self.symbols:
            # Parse base and quote from symbol, e.g. "GMX:BTC-USD" -> "BTC", "USD"
            # or "BTC-USD" -> "BTC", "USD"
            parts = symbol.split(":")
            symbol_core = parts[-1]
            core_parts = symbol_core.split("-")
            base = core_parts[0] if len(core_parts) > 0 else symbol
            quote = core_parts[1] if len(core_parts) > 1 else "USD"
            instruments.append(
                Instrument(
                    canonical=symbol,
                    exchange=self.name,
                    symbol_raw=symbol,
                    kind=Kind.PERPETUAL,
                    base=base,
                    quote=quote,
                )
            )
        return instruments

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if not isinstance(msg, dict):
            return
        
        protocol = msg.get("protocol")
        if protocol == "gmx":
            yield from self._normalize_gmx(msg, local_ts)
        elif protocol == "synthetix":
            yield from self._normalize_synthetix(msg, local_ts)

    def _normalize_gmx(self, msg: dict[str, Any], local_ts: int) -> Iterable[Record]:
        log_obj = reconstruct_log(msg)
        decoded = None
        event_name_found = None
        for event_name, event_handler in self.gmx_events.items():
            try:
                decoded = event_handler.process_log(log_obj)
                event_name_found = event_name
                break
            except Exception:
                continue

        if not decoded:
            return

        # Try to map token address to a friendly symbol name
        raw_symbol = str(decoded.args.get("indexToken", "unknown"))
        # Match symbol in self.symbols if possible
        symbol = self._match_symbol(raw_symbol, "gmx")

        if event_name_found == "IncreasePosition":
            price = float(decoded.args["price"]) / 1e30
            size_delta = float(decoded.args["sizeDelta"]) / 1e30
            amount = size_delta / price if price > 0 else 0.0
            is_long = decoded.args["isLong"]
            side = Side.BUY if is_long else Side.SELL
            yield Trade(
                exchange="gmx",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts, # Fallback to local_ts as EVM log block timestamp isn't directly inside log dict
                local_ts=local_ts,
                price=price,
                amount=amount,
                side=side,
                id=f"gmx-{local_ts}-{price}-{amount}",
            )
        elif event_name_found == "DecreasePosition":
            price = float(decoded.args["price"]) / 1e30
            size_delta = float(decoded.args["sizeDelta"]) / 1e30
            amount = size_delta / price if price > 0 else 0.0
            is_long = decoded.args["isLong"]
            side = Side.SELL if is_long else Side.BUY
            yield Trade(
                exchange="gmx",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts,
                local_ts=local_ts,
                price=price,
                amount=amount,
                side=side,
                id=f"gmx-{local_ts}-{price}-{amount}",
            )
        elif event_name_found == "LiquidatePosition":
            price = float(decoded.args["markPrice"]) / 1e30
            size = float(decoded.args["size"]) / 1e30
            amount = size / price if price > 0 else 0.0
            is_long = decoded.args["isLong"]
            side = Side.SELL if is_long else Side.BUY
            yield Liquidation(
                exchange="gmx",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts,
                local_ts=local_ts,
                price=price,
                amount=amount,
                side=side,
            )
        elif event_name_found == "UpdatePosition":
            # entryFundingRate is uint256
            funding_rate = float(decoded.args["entryFundingRate"]) / 1e9 # illustrative scale
            yield Funding(
                exchange="gmx",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts,
                local_ts=local_ts,
                funding_rate=funding_rate,
            )

    def _normalize_synthetix(self, msg: dict[str, Any], local_ts: int) -> Iterable[Record]:
        log_obj = reconstruct_log(msg)
        decoded = None
        event_name_found = None
        for event_name, event_handler in self.syn_events.items():
            try:
                decoded = event_handler.process_log(log_obj)
                event_name_found = event_name
                break
            except Exception:
                continue

        if not decoded:
            return

        raw_symbol = str(decoded.args.get("id", "unknown"))
        symbol = self._match_symbol(raw_symbol, "synthetix")

        if event_name_found == "PositionModified":
            price = float(decoded.args["lastPrice"]) / 1e18
            trade_size = float(decoded.args["tradeSize"]) / 1e18
            amount = abs(trade_size)
            side = Side.BUY if trade_size > 0 else Side.SELL
            yield Trade(
                exchange="synthetix",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts,
                local_ts=local_ts,
                price=price,
                amount=amount,
                side=side,
                id=f"synthetix-{local_ts}-{price}-{amount}",
            )
        elif event_name_found == "PositionLiquidated":
            price = float(decoded.args["price"]) / 1e18
            size = float(decoded.args["size"]) / 1e18
            amount = abs(size)
            side = Side.SELL if size > 0 else Side.BUY
            yield Liquidation(
                exchange="synthetix",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts,
                local_ts=local_ts,
                price=price,
                amount=amount,
                side=side,
            )
        elif event_name_found == "FundingRecomputed":
            funding_rate = float(decoded.args["fundingRate"]) / 1e18
            yield Funding(
                exchange="synthetix",
                symbol=symbol,
                symbol_raw=raw_symbol,
                exchange_ts=local_ts,
                local_ts=local_ts,
                funding_rate=funding_rate,
            )

    def _match_symbol(self, raw_symbol: str, protocol: str) -> str:
        # Match against our list of symbols. 
        # For simplicity, if we have a matching symbol in the registry/symbols list, return it.
        # Else construct a default one like: "gmx:BTC-USD"
        for sym in self.symbols:
            if raw_symbol.lower() in sym.lower():
                return sym
        return f"{protocol}:{raw_symbol}"

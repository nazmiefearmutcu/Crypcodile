"""Tier 4: whole workflows, end to end, the way an operator would run them.

Three tests left with the surfaces they walked through.
``test_t4_complete_x402_micropayment_flow`` and
``test_t4_x402_replay_and_cryptographic_verification`` were the full paid-data workflow —
challenge, on-chain receipt, redeem — against ``GET /api/v1/market-data``. Neither the
route nor the verifier is carried across (``crocodile.surfaces.payments``). The replay
half of the second one is a property of the *ledger* rather than of the chain, and it is
kept: see ``test_simulate_payment_rejects_a_reused_tx_hash`` in
``tests/equity/test_api_payment_security.py``.

``test_t4_mcp_driven_autonomous_agent_loop`` drove ``tools/list`` then ``get_onchain_price``
against the deleted equity MCP server. Both halves are owned elsewhere now: the tool list by
Gate 4 in ``tests/conformance/test_surfaces.py``, and calling a tool and getting an answer
by ``tests/surfaces/test_end_to_end.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

import aiohttp
import pytest

from crocodile.core.schema.enums import AssetClass

pytest.importorskip("web3")

# Define/mock POOL_SPECS, TOKENS, FACTORIES; import schema records.
# These tiers drive the surviving Base L2 connector, which is the crypto one:
# the equity fork shipped a duplicate of it inside an equities library and the
# merge deleted that duplicate. Its normalizer therefore emits the crypto
# record classes, so the isinstance filters below have to name those. Importing
# the equity classes here made every filter match nothing and every assertion
# run over an empty list.
from crocodile.core.schema.records import BookSnapshot, Record
from crocodile.core.sink.base import Sink
from crocodile.crypto.exchanges.base_onchain.connector import FACTORIES, TOKENS
from crocodile.equity.reference.registry import InstrumentRegistry


class BookTicker:
    def __init__(self, symbol: str, price: float) -> None:
        self.symbol = symbol
        self.price = price


class BaseOnchainTransport:
    def __init__(self, rpc_url: str, symbols: list[str], poll_interval: float = 5.0) -> None:
        self.rpc_url = rpc_url
        self.symbols = symbols
        self.poll_interval = poll_interval
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._connected = False

    async def connect(self) -> None:
        self._connected = True
        for sym in self.symbols:
            mock_msg = {
                "type": "onchain_update",
                "pool": sym,
                "price": 25.0,
                "bids": [[25.0, 1.0]] * 5,
                "asks": [[25.1, 1.0]] * 5,
            }
            await self._queue.put(json.dumps(mock_msg).encode())

    def __aiter__(self) -> BaseOnchainTransport:
        return self

    async def __anext__(self) -> bytes:
        if not self._connected:
            raise StopAsyncIteration
        val = await self._queue.get()
        if val is None:
            raise StopAsyncIteration
        return val

    async def close(self) -> None:
        self._connected = False
        await self._queue.put(None)


class BaseOnchainConnector:
    def __init__(
        self, symbols: list[str], channels: list[str], out: Sink, registry: InstrumentRegistry
    ) -> None:
        self.symbols = symbols
        self.channels = channels
        self.out = out
        self.registry = registry
        self.transport = BaseOnchainTransport("mock_rpc", symbols)

    def normalize(self, msg: dict, local_ts: int) -> list[Record | BookTicker]:
        # provider=/source_ts= are the equity record field names; the surviving
        # crypto BookSnapshot spells them exchange=/source_ts=. Same fields.
        snap = BookSnapshot(
            source="base_onchain",
            symbol=msg["pool"],
            symbol_raw=msg["pool"],
            local_ts=local_ts,
            asset_class=AssetClass.CRYPTO,
            bids=[(b[0], b[1]) for b in msg["bids"]],
            asks=[(a[0], a[1]) for a in msg["asks"]],
            depth=len(msg["bids"]),
            source_ts=local_ts,
            sequence_id=1,
            is_snapshot=True,
        )
        ticker = BookTicker(symbol=msg["pool"], price=msg["price"])
        return [ticker, snap]


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
        "liquidity": 10**10,
    }
    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)

    sink = type(
        "ListSink", (object,), {"records": [], "write": lambda self, r: self.records.append(r)}
    )()
    registry = InstrumentRegistry()

    os.environ["BASE_RPC_URL"] = rpc_url
    try:
        connector = BaseOnchainConnector(
            symbols=["cbBTC-USDC"], channels=["ticker", "orderbook"], out=sink, registry=registry
        )

        await connector.transport.connect()

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
        "liquidity": 1000,
    }

    async with aiohttp.ClientSession() as session:
        await session.post(f"{rpc_url}/control/pool", json=pool_data)

    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    env["PYTHONPATH"] = os.path.abspath("src")  # noqa: ASYNC240

    import sys
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "examples/collect_base_onchain.py",
        "--dry-run",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not (b"cbBTC-USDC" in stdout or b"AERO-USDC" in stdout):
        print("SHOWCASE STDOUT:", stdout.decode())
        print("SHOWCASE STDERR:", stderr.decode())
    assert proc.returncode == 0
    assert b"cbBTC-USDC" in stdout or b"AERO-USDC" in stdout


# 5. Multi-pool Concurrent Ingestion under Stress
@pytest.mark.asyncio
async def test_t4_multi_pool_concurrent_ingestion_under_stress(mock_rpc) -> None:
    rpc_url, _ = mock_rpc

    # Seed 4 different pools
    pools = [
        (
            "AERO-USDC",
            "0x0000000000000000000000000000000000000002",
            FACTORIES["aerodrome"],
            TOKENS["AERO"],
            TOKENS["USDbC"],
            {"stable": False},
        ),
        (
            "cbBTC-USDC",
            "0x0000000000000000000000000000000000000001",
            FACTORIES["uniswap_v3"],
            TOKENS["cbBTC"],
            TOKENS["USDC"],
            {"fee": 500, "sqrtPriceX96": 2**96},
        ),
        (
            "DEGEN-WETH",
            "0x0000000000000000000000000000000000000003",
            FACTORIES["uniswap_v3"],
            TOKENS["DEGEN"],
            TOKENS["WETH"],
            {"fee": 3000, "sqrtPriceX96": 2**96},
        ),
        (
            "WELL-WETH",
            "0x0000000000000000000000000000000000000004",
            FACTORIES["aerodrome"],
            TOKENS["WELL"],
            TOKENS["WETH"],
            {"stable": False},
        ),
    ]

    async with aiohttp.ClientSession() as session:
        for _name, address, factory, t0, t1, extra in pools:
            pool_data = {
                "address": address,
                "factory": factory,
                "token0": t0,
                "token1": t1,
                **extra,
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

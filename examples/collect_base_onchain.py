"""Example: collect Base on-chain data.

Usage:
    uv run python examples/collect_base_onchain.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from crypcodile.exchanges.base_onchain.connector import (  # noqa: E402
    DEFAULT_RPC_URL,
    BaseOnchainConnector,
)
from crypcodile.instruments.registry import InstrumentRegistry  # noqa: E402
from crypcodile.schema.records import Record  # noqa: E402
from crypcodile.sink.base import Sink  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s  %(message)s"
)
log = logging.getLogger("collect_base_onchain")


class PrintSink(Sink):
    """A simple Sink that prints records to stdout."""
    def __init__(self) -> None:
        self.count = 0

    async def put(self, record: Record) -> None:
        print(f"[{record.__class__.__name__}] {record}")
        self.count += 1

    async def flush(self) -> None:
        pass

    async def close(self) -> None:
        pass


async def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Base on-chain DEX data.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run with mock Web3 to test end-to-end wiring offline"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="cbBTC-USDC",
        help="Symbol to subscribe to (e.g. cbBTC-USDC, AERO-USDC)"
    )
    args = parser.parse_args()

    rpc_url = os.getenv("BASE_RPC_URL", DEFAULT_RPC_URL)
    log.info("Initializing BaseOnchainConnector. RPC URL: %s", rpc_url)

    sink = PrintSink()
    registry = InstrumentRegistry()

    connector = BaseOnchainConnector(
        symbols=[args.symbol],
        channels=["trade", "book_delta"],
        out=sink,
        registry=registry,
    )

    if args.dry_run:
        log.info("Running in DRY RUN mode with mocked Web3 provider...")
        
        # Configure Web3 mocks
        with patch("web3.Web3") as mock_web3_class, \
             patch("web3.AsyncWeb3") as mock_async_web3_class:
            mock_w3 = MagicMock()
            mock_web3_class.return_value = mock_w3
            mock_async_web3_class.return_value = mock_w3
            mock_web3_class.to_checksum_address = lambda x: x
            mock_async_web3_class.to_checksum_address = lambda x: x

            class AwaitableInt(int):
                def __await__(self):
                    async def _async_val():
                        return int(self)
                    return _async_val().__await__()

            mock_w3.eth.block_number = AwaitableInt(12345)
            
            # Mock getPool
            mock_factory = MagicMock()
            mock_factory.functions.getPool.return_value.call.return_value = (
                "0xMockPoolAddress"
            )
            
            # Mock slot0 and liquidity for Uniswap V3, or getReserves for Aerodrome
            mock_pool = MagicMock()
            mock_pool.functions.slot0.return_value.call.return_value = [
                int(2**96 * 1.5), 0, 0, 0, 0, 0, True
            ]
            mock_pool.functions.liquidity.return_value.call.return_value = 500 * 10**8
            mock_pool.functions.getReserves.return_value.call.return_value = [
                (1000 * 10**18), (2000 * 10**6), 1234567
            ]
            
            def contract_side_effect(address, abi):
                if address in [
                    "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
                    "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
                ]:
                    return mock_factory
                return mock_pool

            mock_w3.eth.contract.side_effect = contract_side_effect
            
            # Mock swap logs: return one simulated swap log
            mock_log = {
                "data": ((-1 * 10**8).to_bytes(32, byteorder='big', signed=True) +
                         (60000 * 10**6).to_bytes(32, byteorder='big', signed=True)),
                "transactionHash": MagicMock(hex=lambda: "0xhash"),
                "logIndex": 1,
                "blockNumber": 12345
            }
            mock_w3.eth.get_logs.return_value = [mock_log]
            mock_w3.eth.get_block.return_value = {"timestamp": 1234567890}

            # Run loop for a single iteration and stop
            original_sleep = asyncio.sleep
            async def mock_sleep(delay):
                connector.transport._connected = False
                await connector.transport.close()
                await original_sleep(0)

            with patch("asyncio.sleep", mock_sleep):
                await connector.run(max_reconnects=0)
                
            log.info("Dry run complete. Printed %d records.", sink.count)
    else:
        log.info(
            "Starting live connector for symbol %s (Press Ctrl+C to exit)...",
            args.symbol
        )
        try:
            await connector.run()
        except KeyboardInterrupt:
            log.info("Interrupted by user.")
        finally:
            await connector.transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

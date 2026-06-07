"""Example: collect live Deribit data and write to hive-partitioned Parquet.

Run this script to subscribe to BTC-PERPETUAL trades + book deltas on Deribit
and persist every received record to the local data lake under ``./data/``.
Press Ctrl-C to stop gracefully — the sink is flushed before exit.

Usage::

    uv run python examples/collect_deribit.py

The data lake layout after collection::

    data/
      exchange=deribit/
        channel=trade/
          date=YYYY-MM-DD/
            bucket=<0-127>/
              part-<uuid>.parquet
        channel=book_delta/
          ...

Query the data afterwards with::

    crypcodile catalog --data-dir data
    crypcodile query "SELECT count(*) FROM trade" --data-dir data
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from crypcodile.client.collect import collect
from crypcodile.exchanges.deribit.connector import DeribitConnector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.store.parquet_sink import ParquetSink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s  %(message)s")
log = logging.getLogger("collect_deribit")

DATA_DIR = Path("data")

# ---------------------------------------------------------------------------
# Configuration — edit these to subscribe to different symbols / channels
# ---------------------------------------------------------------------------
SYMBOLS = ["BTC-PERPETUAL"]
CHANNELS = ["trade", "book_delta", "derivative_ticker"]


async def main() -> None:
    sink = ParquetSink(
        data_dir=DATA_DIR,
        max_buffer_rows=10_000,
        flush_interval_seconds=5.0,
    )
    registry = InstrumentRegistry()

    connector = DeribitConnector(
        symbols=SYMBOLS,
        channels=CHANNELS,
        out=sink,
        registry=registry,
    )

    log.info(
        "Starting Deribit collection: symbols=%s channels=%s -> %s",
        SYMBOLS,
        CHANNELS,
        DATA_DIR,
    )

    # Graceful shutdown on SIGINT (Ctrl-C) / SIGTERM.
    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _handle_signal() -> None:
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    collect_task = asyncio.create_task(
        collect([connector], sink, max_reconnects=-1)
    )

    # asyncio.wait accepts both Tasks and Futures natively; pass ``stop``
    # (a Future) directly rather than wrapping it in create_task, which would
    # produce a Task whose result is the Future's result — semantically wrong
    # and non-idiomatic.
    _done, pending = await asyncio.wait(
        {collect_task, stop}, return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    log.info("Stopped. Data written to %s", DATA_DIR)


if __name__ == "__main__":
    asyncio.run(main())

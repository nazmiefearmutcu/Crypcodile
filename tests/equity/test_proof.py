"""Live end-to-end integration and proof test for Stockodile.

This test connects to real live endpoints (Yahoo Finance) to fetch actual stock data,
normalizes it, buffers it through the ParquetSink, writes it to a hive-partitioned Parquet
database, and scans the database using the DuckDB Catalog.

Saves the successful execution proof logs to ``proof.log`` next to this file.
The path is derived from ``__file__`` rather than written out: the fork kept it
as an absolute path into a checkout that no longer exists, and the merge moved
the file from ``src/_incoming_stockodile/tests/`` to ``tests/equity/``.
"""

import os
import pathlib
import time

import polars as pl
import pytest

from crocodile.equity.providers.yahoo.client import YahooClient
from crocodile.equity.schema.records import Bar
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink


# Resolved at import time, not inside the async test: ``Path.resolve()`` touches
# the filesystem, which ASYNC240 rightly flags in a coroutine.
PROOF_LOG_PATH = pathlib.Path(__file__).resolve().parent / "proof.log"


def _write_proof_log(proof_path: pathlib.Path, proof_content: str) -> None:
    """Helper to write proof log synchronously to avoid ASYNC230/ASYNC240 lint issues."""
    with open(proof_path, "w", encoding="utf-8") as f:
        f.write(proof_content)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("CROCODILE_LIVE_TESTS"),
    reason=(
        "reaches Yahoo Finance over the network. It passes in 1.6s alone, takes 41s "
        "behind a 429, and hangs past the 120s timeout when run beside its neighbours, "
        "so leaving it in the default suite makes the Phase 1 exit gate a function of "
        "someone else's rate limiter. Run it deliberately: CROCODILE_LIVE_TESTS=1 pytest "
        "tests/equity/test_proof.py"
    ),
)
async def test_live_end_to_end_proof(tmp_path: pathlib.Path) -> None:
    # 1. Fetch live historical data from Yahoo Finance for a short date range
    symbol = "AAPL"
    start_date = "2026-06-01"
    end_date = "2026-06-05"

    print("\n--- Running Stockodile Live Proof Test ---")
    print(
        f"Step 1: Instantiating YahooClient and fetching live data for "
        f"{symbol} ({start_date} to {end_date})..."
    )

    async with YahooClient() as client:
        records = await client.fetch_eod_history(symbol, start=start_date, end=end_date)

    print(f"Result: Successfully retrieved {len(records)} records from Yahoo Finance.")
    assert len(records) > 0, "No records retrieved from Yahoo Finance"

    # Separate bars and actions
    bars = [r for r in records if isinstance(r, Bar)]
    print(f"Result: Mapped {len(bars)} canonical Bar (OHLCV) records.")
    assert len(bars) > 0, "No canonical Bar records mapped"

    # 2. Write the retrieved live records to a partitioned Parquet database via ParquetSink
    print(f"Step 2: Feeding records to ParquetSink writing to: {tmp_path}...")
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1000, flush_interval_seconds=9999)
    for record in records:
        await sink.put(record)

    await sink.flush()
    print("Result: Parquet files flushed successfully.")

    # 3. Instantiate DuckDB Catalog over the written Parquet database and query it
    print("Step 3: Instantiating Catalog and running DuckDB scans...")
    catalog = Catalog(data_dir=tmp_path)

    # Get bounds
    min_ts = min(r.local_ts for r in records)
    max_ts = max(r.local_ts for r in records)

    # Scan for AAPL
    df = catalog.scan(
        channel="bar",
        symbol=symbol,
        start_ns=min_ts,
        end_ns=max_ts,
    )

    print(f"Result: DuckDB scan returned Polars DataFrame with {len(df)} rows.")
    assert isinstance(df, pl.DataFrame)
    assert len(df) == len(bars), f"Expected {len(bars)} rows in catalog scan, got {len(df)}"

    # 4. Generate proof log
    proof_path = PROOF_LOG_PATH
    proof_content = (
        "STOCKODILE E2E PROOF OF INTEGRATION LOG\n"
        "======================================\n"
        f"Execution Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
        f"Target Stock Symbol: {symbol}\n"
        f"Dates Scraped: {start_date} to {end_date}\n"
        f"Total Mapped Records: {len(records)}\n"
        f"Bar OHLCV Count: {len(bars)}\n"
        f"Database Write Path: {tmp_path}\n"
        f"Catalog Query Result: {len(df)} rows found in DuckDB view.\n"
        "DataFrame Output Schema & Samples:\n"
        f"{df.head(5)}\n"
        "======================================\n"
        "STATUS: SUCCESS\n"
    )
    _write_proof_log(proof_path, proof_content)
    print(f"Proof log file created at: {proof_path}")

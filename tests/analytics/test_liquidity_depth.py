import pytest
import pathlib
import polars as pl
from crypcodile.schema.records import BookSnapshot
from crypcodile.store.parquet_sink import ParquetSink
from crypcodile.store.catalog import Catalog
from crypcodile.analytics.liquidity_depth import calculate_block_liquidity_depth

_BASE_TS = 1_700_000_000_000_000_000

@pytest.mark.asyncio
async def test_block_liquidity_depth_calculations(tmp_path: pathlib.Path):
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    
    # Write a BookSnapshot for block 100
    # mid-price is 100.0 (bids[0] is 100.0, asks[0] is 100.0)
    await sink.put(BookSnapshot(
        exchange="base_onchain",
        symbol="base_onchain:DEGEN-WETH",
        symbol_raw="DEGEN-WETH",
        exchange_ts=_BASE_TS,
        local_ts=_BASE_TS,
        bids=[(100.0, 10.0), (99.0, 5.0), (98.0, 3.0), (97.0, 2.0), (94.0, 1.0)], # levels: 0%, -1%, -2%, -3%, -6%
        asks=[(100.0, 8.0), (101.0, 4.0), (102.0, 3.0), (103.0, 2.0), (106.0, 1.0)], # levels: 0%, +1%, +2%, +3%, +6%
        depth=5,
        sequence_id=100,
        is_snapshot=True
    ))

    # Write a BookSnapshot for block 101
    await sink.put(BookSnapshot(
        exchange="base_onchain",
        symbol="base_onchain:DEGEN-WETH",
        symbol_raw="DEGEN-WETH",
        exchange_ts=_BASE_TS + 1_000_000_000,
        local_ts=_BASE_TS + 1_000_000_000,
        bids=[(100.0, 20.0), (99.0, 10.0), (98.0, 5.0), (97.0, 4.0), (94.0, 2.0)],
        asks=[(100.0, 16.0), (101.0, 8.0), (102.0, 6.0), (103.0, 4.0), (106.0, 2.0)],
        depth=5,
        sequence_id=101,
        is_snapshot=True
    ))
    
    await sink.flush()
    
    catalog = Catalog(tmp_path)
    
    df = calculate_block_liquidity_depth(catalog, "base_onchain:DEGEN-WETH")
    
    assert len(df) == 2
    # Verify block 100 depth
    assert df["block"][0] == 100
    # bid_depth_1pct (bids >= 99.0): 10.0 + 5.0 = 15.0
    assert df["bid_depth_1pct"][0] == 15.0
    # ask_depth_1pct (asks <= 101.0): 8.0 + 4.0 = 12.0
    assert df["ask_depth_1pct"][0] == 12.0
    # bid_depth_2pct (bids >= 98.0): 10.0 + 5.0 + 3.0 = 18.0
    assert df["bid_depth_2pct"][0] == 18.0
    # bid_depth_5pct (bids >= 95.0): 10.0 + 5.0 + 3.0 + 2.0 = 20.0 (excludes 94.0 because 94.0 < 95.0)
    assert df["bid_depth_5pct"][0] == 20.0

    # Verify block 101 depth (double the sizes)
    assert df["block"][1] == 101
    assert df["bid_depth_1pct"][1] == 30.0
    assert df["ask_depth_1pct"][1] == 24.0

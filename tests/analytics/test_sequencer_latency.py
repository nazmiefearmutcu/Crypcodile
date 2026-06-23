import pytest
import pathlib
import polars as pl
from crypcodile.schema.records import BookTicker
from crypcodile.store.parquet_sink import ParquetSink
from crypcodile.store.catalog import Catalog
from crypcodile.analytics.sequencer_latency import calculate_sequencer_latency

_BASE_TS = 1_700_000_000_000_000_000

@pytest.mark.asyncio
async def test_sequencer_latency_calculations(tmp_path: pathlib.Path):
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    
    # Write BookTickers with 2-second intervals
    # local_ts matches exchange_ts + delay:
    # 1. exchange_ts = BASE, local_ts = BASE + 0.5s
    # 2. exchange_ts = BASE + 2.0s, local_ts = BASE + 2.0s + 0.7s (diff = 2.0s, delay = 0.7s)
    # 3. exchange_ts = BASE + 4.0s, local_ts = BASE + 4.0s + 0.9s (diff = 2.0s, delay = 0.9s)
    await sink.put(BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:AERO-USDC",
        symbol_raw="AERO-USDC",
        exchange_ts=_BASE_TS,
        local_ts=_BASE_TS + int(0.5 * 1e9),
        bid_px=1.0,
        bid_sz=1.0,
        ask_px=1.01,
        ask_sz=1.0
    ))
    
    await sink.put(BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:AERO-USDC",
        symbol_raw="AERO-USDC",
        exchange_ts=_BASE_TS + int(2.0 * 1e9),
        local_ts=_BASE_TS + int(2.7 * 1e9),
        bid_px=1.0,
        bid_sz=1.0,
        ask_px=1.01,
        ask_sz=1.0
    ))
    
    await sink.put(BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:AERO-USDC",
        symbol_raw="AERO-USDC",
        exchange_ts=_BASE_TS + int(4.0 * 1e9),
        local_ts=_BASE_TS + int(4.9 * 1e9),
        bid_px=1.0,
        bid_sz=1.0,
        ask_px=1.01,
        ask_sz=1.0
    ))
    
    await sink.flush()
    
    catalog = Catalog(tmp_path)
    
    df = calculate_sequencer_latency(catalog, "base_onchain")
    
    assert len(df) == 2
    # Row 0: production_interval
    assert df["metric"][0] == "production_interval"
    # avg production interval is 2.0s
    assert df["avg_seconds"][0] == pytest.approx(2.0)
    assert df["max_seconds"][0] == pytest.approx(2.0)
    
    # Row 1: ingestion_delay
    assert df["metric"][1] == "ingestion_delay"
    # avg delay: (0.5 + 0.7 + 0.9) / 3 = 0.7s
    # but wait, we filtered out the first row where production interval is null:
    # let's check: the code does: `df_clean = df.filter(pl.col("prod_int_sec").is_not_null())`
    # this filters out the first row (exchange_ts = _BASE_TS), so we only average over rows 2 and 3:
    # delays: row 2 = 0.7s, row 3 = 0.9s. avg = (0.7 + 0.9) / 2 = 0.8s
    assert df["avg_seconds"][1] == pytest.approx(0.8)
    assert df["max_seconds"][1] == pytest.approx(0.9)

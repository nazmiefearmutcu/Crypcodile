import pytest
import pathlib
import polars as pl
from crypcodile.schema.records import BookTicker, BookSnapshot
from crypcodile.store.parquet_sink import ParquetSink
from crypcodile.store.catalog import Catalog
from crypcodile.analytics.peg_deviation import calculate_peg_deviation, check_live_peg_deviation

_BASE_TS = 1_700_000_000_000_000_000

@pytest.mark.asyncio
async def test_peg_deviation_calculations(tmp_path: pathlib.Path):
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    
    # Write a BookTicker showing normal peg ($1.00)
    await sink.put(BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:USDC-USDbC",
        symbol_raw="USDC-USDbC",
        exchange_ts=_BASE_TS,
        local_ts=_BASE_TS,
        bid_px=0.999,
        bid_sz=1.0,
        ask_px=1.001,
        ask_sz=1.0
    ))
    
    # Write a BookTicker showing peg deviation ($0.98, which is 2% deviation)
    await sink.put(BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:USDC-USDbC",
        symbol_raw="USDC-USDbC",
        exchange_ts=_BASE_TS + 1_000_000_000,
        local_ts=_BASE_TS + 1_000_000_000,
        bid_px=0.979,
        bid_sz=1.0,
        ask_px=0.981,
        ask_sz=1.0
    ))
    
    await sink.flush()
    
    catalog = Catalog(tmp_path)
    
    # Threshold at 1% (0.01)
    df = calculate_peg_deviation(catalog, "base_onchain:USDC-USDbC", threshold=0.01)
    
    assert len(df) == 2
    # Verify first is not alert (deviation ~0%)
    assert df["is_alert_triggered"][0] is False
    assert abs(df["price"][0] - 1.0) < 0.005
    
    # Verify second is alert (deviation ~2%)
    assert df["is_alert_triggered"][1] is True
    assert df["deviation_pct"][1] == pytest.approx(0.02)

def test_live_peg_deviation_helper():
    # Helper should trigger alert when deviation is >= threshold
    alert_ticker = BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:USDC-USDbC",
        symbol_raw="USDC-USDbC",
        exchange_ts=_BASE_TS,
        local_ts=_BASE_TS,
        bid_px=0.97,
        bid_sz=1.0,
        ask_px=0.97, # mid is 0.97 (3% deviation)
        ask_sz=1.0
    )
    
    normal_ticker = BookTicker(
        exchange="base_onchain",
        symbol="base_onchain:USDC-USDbC",
        symbol_raw="USDC-USDbC",
        exchange_ts=_BASE_TS,
        local_ts=_BASE_TS,
        bid_px=0.995,
        bid_sz=1.0,
        ask_px=1.005, # mid is 1.0 (0% deviation)
        ask_sz=1.0
    )
    
    assert check_live_peg_deviation(alert_ticker, threshold=0.01) is True
    assert check_live_peg_deviation(normal_ticker, threshold=0.01) is False

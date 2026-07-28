"""Pure-helper and client tests for Base risk analytics (open-interest, peg-deviation).

Everything that drove ``open-interest``, ``peg-deviation``, ``chaos-score``,
``lending-stress``, ``gas-vol`` and ``funding-predict`` through the hand-written crypto Typer
app went with that app: the capabilities themselves are covered by
``tests/capabilities/test_ops.py`` and ``tests/capabilities/test_analytics.py``, their
reachability from every surface by ``tests/conformance/test_surfaces.py``, and their
rendering by ``tests/surfaces/test_end_to_end.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.records import BookTicker, OpenInterest
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.peg_deviation import peg_deviation_from_price
from crocodile.crypto.client.client import CrypcodileClient

_BASE_TS = 1_700_000_000_000_000_000


def _make_oi(ts: int, exchange: str, symbol: str, oi: float) -> OpenInterest:
    return OpenInterest(
        source=exchange,
        symbol=symbol,
        symbol_raw=symbol.split(":")[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        open_interest=oi,
    )


async def _write_records(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for rec in records:
        await sink.put(rec)
    await sink.flush()


@pytest.fixture
def oi_lake(tmp_path: Path) -> Path:
    records = [
        _make_oi(_BASE_TS, "binance", "binance:BTCUSDT", 100.0),
        _make_oi(_BASE_TS, "okx", "okx:BTC-USDT-SWAP", 50.0),
        _make_oi(_BASE_TS + 1_000, "binance", "binance:BTCUSDT", 110.0),
        _make_oi(_BASE_TS + 1_000, "okx", "okx:BTC-USDT-SWAP", 60.0),
    ]
    asyncio.run(_write_records(tmp_path, records))
    return tmp_path


@pytest.fixture
def peg_lake(tmp_path: Path) -> Path:
    async def _setup() -> None:
        sink = ParquetSink(tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
        await sink.put(
            BookTicker(
                source="base_onchain",
                symbol="base_onchain:USDC-USDbC",
                symbol_raw="USDC-USDbC",
                source_ts=_BASE_TS,
                local_ts=_BASE_TS,
                asset_class=AssetClass.CRYPTO,
                bid_px=0.999,
                bid_sz=1.0,
                ask_px=1.001,
                ask_sz=1.0,
            )
        )
        await sink.put(
            BookTicker(
                source="base_onchain",
                symbol="base_onchain:USDC-USDbC",
                symbol_raw="USDC-USDbC",
                source_ts=_BASE_TS + 1_000_000_000,
                local_ts=_BASE_TS + 1_000_000_000,
                asset_class=AssetClass.CRYPTO,
                bid_px=0.979,
                bid_sz=1.0,
                ask_px=0.981,
                ask_sz=1.0,
            )
        )
        await sink.flush()

    asyncio.run(_setup())
    return tmp_path


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_peg_deviation_from_price_alert() -> None:
    res = peg_deviation_from_price(0.98, threshold=0.01)
    assert res["deviation_pct"] == pytest.approx(0.02)
    assert res["is_alert_triggered"] is True


def test_peg_deviation_from_price_ok() -> None:
    res = peg_deviation_from_price(1.0, threshold=0.01)
    assert res["deviation_pct"] == pytest.approx(0.0)
    assert res["is_alert_triggered"] is False


# ---------------------------------------------------------------------------
# Client wrappers
# ---------------------------------------------------------------------------


def test_client_aggregate_open_interest(oi_lake: Path) -> None:
    client = CrypcodileClient(oi_lake)
    df = client.aggregate_open_interest("BTC", _BASE_TS, _BASE_TS + 1_000)
    assert len(df) == 2
    assert "total_oi" in df.columns
    assert df["total_oi"][0] == pytest.approx(150.0)


def test_client_calculate_peg_deviation(peg_lake: Path) -> None:
    client = CrypcodileClient(peg_lake)
    df = client.calculate_peg_deviation("base_onchain:USDC-USDbC", threshold=0.01)
    assert len(df) == 2
    assert df["is_alert_triggered"][1] is True

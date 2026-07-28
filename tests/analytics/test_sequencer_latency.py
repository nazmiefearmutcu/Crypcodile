"""Tests for sequencer latency analytics and its client wrapper.

The four CLI tests left with the hand-written crypto Typer app; reaching
``sequencer-latency`` from a surface is now ``tests/conformance/test_surfaces.py`` and
``tests/surfaces/test_end_to_end.py``, and the blank-exchange default is
``tests/capabilities/test_ops.py``.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.records import BookTicker
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.sequencer_latency import calculate_sequencer_latency
from crocodile.crypto.client.client import CrypcodileClient

_BASE_TS = 1_700_000_000_000_000_000
_EXCHANGE = "base_onchain"


async def _write_latency_lake(data_dir: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)

    # BookTickers with 2-second production intervals and rising ingestion delay:
    # 1. source_ts = BASE, local_ts = BASE + 0.5s
    # 2. source_ts = BASE + 2.0s, local_ts = BASE + 2.7s (interval=2.0, delay=0.7)
    # 3. source_ts = BASE + 4.0s, local_ts = BASE + 4.9s (interval=2.0, delay=0.9)
    await sink.put(
        BookTicker(
            source=_EXCHANGE,
            symbol="base_onchain:AERO-USDC",
            symbol_raw="AERO-USDC",
            source_ts=_BASE_TS,
            local_ts=_BASE_TS + int(0.5 * 1e9),
            asset_class=AssetClass.CRYPTO,
            bid_px=1.0,
            bid_sz=1.0,
            ask_px=1.01,
            ask_sz=1.0,
        )
    )
    await sink.put(
        BookTicker(
            source=_EXCHANGE,
            symbol="base_onchain:AERO-USDC",
            symbol_raw="AERO-USDC",
            source_ts=_BASE_TS + int(2.0 * 1e9),
            local_ts=_BASE_TS + int(2.7 * 1e9),
            asset_class=AssetClass.CRYPTO,
            bid_px=1.0,
            bid_sz=1.0,
            ask_px=1.01,
            ask_sz=1.0,
        )
    )
    await sink.put(
        BookTicker(
            source=_EXCHANGE,
            symbol="base_onchain:AERO-USDC",
            symbol_raw="AERO-USDC",
            source_ts=_BASE_TS + int(4.0 * 1e9),
            local_ts=_BASE_TS + int(4.9 * 1e9),
            asset_class=AssetClass.CRYPTO,
            bid_px=1.0,
            bid_sz=1.0,
            ask_px=1.01,
            ask_sz=1.0,
        )
    )
    await sink.flush()


@pytest.fixture
def latency_lake(tmp_path: pathlib.Path) -> pathlib.Path:
    asyncio.run(_write_latency_lake(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_sequencer_latency_calculations(tmp_path: pathlib.Path):
    await _write_latency_lake(tmp_path)

    catalog = Catalog(tmp_path)
    df = calculate_sequencer_latency(catalog, _EXCHANGE)

    assert len(df) == 2
    # Row 0: production_interval
    assert df["metric"][0] == "production_interval"
    # avg production interval is 2.0s
    assert df["avg_seconds"][0] == pytest.approx(2.0)
    assert df["max_seconds"][0] == pytest.approx(2.0)

    # Row 1: ingestion_delay
    assert df["metric"][1] == "ingestion_delay"
    # first row filtered (prod_int null); delays 0.7 + 0.9 -> avg 0.8
    assert df["avg_seconds"][1] == pytest.approx(0.8)
    assert df["max_seconds"][1] == pytest.approx(0.9)


def test_a_crafted_exchange_cannot_end_the_string_it_sits_in(
    latency_lake: pathlib.Path,
) -> None:
    """`exchange` is a free-text query parameter on the public REST route.

    It used to be f-string-interpolated into ``WHERE source = '{exchange}'``, so a value
    carrying a quote could close the literal and continue the statement. The lake here
    holds rows for one exchange; a working injection would make the predicate true for
    all of them, which is the one answer this must never give.

    A failed injection and a placeholder both return nothing, so an empty result proves
    little on its own — the assertion that separates them is that the *same* payload
    passed as a plain name is also empty while the honest name still answers.
    """
    from crocodile.crypto.analytics.sequencer_latency import calculate_sequencer_latency

    catalog = Catalog(latency_lake)
    assert calculate_sequencer_latency(catalog, "' OR 1=1 --").is_empty()
    assert calculate_sequencer_latency(catalog, "'; DROP TABLE book_ticker; --").is_empty()

    # The table the injection tried to drop is still there, and the honest name still
    # answers over it — an injection that silently emptied the lake would otherwise look
    # exactly like a placeholder doing its job.
    assert len(calculate_sequencer_latency(catalog, _EXCHANGE)) == 2


def test_client_calculate_sequencer_latency(latency_lake: pathlib.Path) -> None:
    client = CrypcodileClient(latency_lake)
    df = client.calculate_sequencer_latency(_EXCHANGE)
    assert len(df) == 2
    assert df["metric"][0] == "production_interval"
    assert df["avg_seconds"][0] == pytest.approx(2.0)
    assert "ingestion_delay" in df["metric"].to_list()

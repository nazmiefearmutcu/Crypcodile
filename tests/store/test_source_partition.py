"""The unified ``source=`` partition, and what the legacy prefixes still owe.

Task 14 moved the writer onto one partition key while leaving both legacy keys
readable. A gate that only checks "the reader globs three prefixes" would pass
with a reader that finds the directories and returns nothing from them, so the
tests here read real Parquet through the real Catalog and compare answers
across the migration boundary.
"""

from __future__ import annotations

import logging
import os
import pathlib

import pytest
from crocodile.core.store.parquet_sink import ParquetSink

from crocodile.core.store.catalog import Catalog
from crocodile.core.store.migrate import migrate_lake
from crocodile.core.schema.legacy.enums import Side
from crocodile.core.schema.legacy.records import Trade

_TS = 1700000000000000000


def _trade(price: float, exchange: str = "deribit") -> Trade:
    return Trade(
        exchange=exchange,
        symbol=f"{exchange}:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=_TS,
        local_ts=_TS,
        id=str(price),
        price=price,
        amount=2.0,
        side=Side.BUY,
    )


async def _write(data_dir: pathlib.Path, *trades: Trade) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=1000, flush_interval_seconds=9999)
    for trade in trades:
        await sink.put(trade)
    await sink.flush()


def _demote(data_dir: pathlib.Path, prefix: str) -> None:
    """Rewrite ``source=`` partitions back to a legacy prefix.

    Produces exactly what a lake written before the merge looks like, without
    keeping a fixture lake in the repo that would rot.
    """
    for child in list(data_dir.iterdir()):
        if child.is_dir() and child.name.startswith("source="):
            child.rename(child.with_name(prefix + child.name[len("source=") :]))


async def test_the_writer_emits_only_the_unified_key(tmp_path: pathlib.Path) -> None:
    await _write(tmp_path, _trade(1.0))
    # os.listdir, not Path.iterdir: ruff's ASYNC240 bans pathlib in async defs.
    assert os.listdir(tmp_path) == ["source=deribit"]


def test_the_data_provider_wins_over_the_listing_venue() -> None:
    """A record naming both must partition by who served it, not where it lists.

    Equity's ``Instrument`` carries ``provider`` (the data source) *and*
    ``exchange`` (the listing venue). Reading ``exchange`` first filed an
    Alpaca-sourced instrument under ``source=NASDAQ`` — the wrong partition, and
    silently so, because both fields are populated strings.
    """
    from crocodile.core.store.rows import to_row
    from crocodile.equity.schema.records import Instrument

    row = to_row(
        Instrument(
            provider="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=_TS,
            source_ts=_TS,
            name="Apple Inc.",
            exchange="NASDAQ",
        )
    )

    assert row["source"] == "alpaca"


@pytest.mark.parametrize("prefix", ["exchange=", "provider="])
async def test_a_legacy_lake_is_still_readable(tmp_path: pathlib.Path, prefix: str) -> None:
    """Rows come back, not just directories.

    The symmetry this restores is the one that matters: a lake written by
    either fork before the merge answers the same query it always did.
    """
    await _write(tmp_path, _trade(1.0), _trade(2.0))
    _demote(tmp_path, prefix)

    catalog = Catalog(tmp_path)
    df = catalog.scan("trade", "deribit:BTC-PERPETUAL", _TS - 1, _TS + 1)

    assert sorted(df["price"].to_list()) == [1.0, 2.0]
    assert catalog.list_channels() == ["trade"]
    assert catalog.list_dates("trade") == ["2023-11-14"]
    assert catalog.list_exchanges_on_disk() == ["deribit"]


async def test_migration_does_not_change_the_answer(tmp_path: pathlib.Path) -> None:
    """The point of the whole task: same query, same rows, before and after.

    Renaming a directory is only safe if the reader cannot tell. This asserts
    the equality directly rather than trusting that both code paths exist.
    """
    await _write(tmp_path, _trade(1.0), _trade(2.0))
    _demote(tmp_path, "exchange=")

    before = Catalog(tmp_path).scan("trade", "deribit:BTC-PERPETUAL", _TS - 1, _TS + 1)
    assert migrate_lake(tmp_path) == 1
    after = Catalog(tmp_path).scan("trade", "deribit:BTC-PERPETUAL", _TS - 1, _TS + 1)

    assert before.drop("source").equals(after.drop("source"))
    assert after["source"].unique().to_list() == ["deribit"]


async def test_a_half_migrated_lake_returns_both_halves(tmp_path: pathlib.Path) -> None:
    """A migration interrupted midway must not silently halve the result set."""
    await _write(tmp_path, _trade(1.0, exchange="deribit"))
    _demote(tmp_path, "exchange=")
    await _write(tmp_path, _trade(2.0, exchange="binance"))

    assert set(os.listdir(tmp_path)) == {"exchange=deribit", "source=binance"}

    catalog = Catalog(tmp_path)
    df = catalog.scan(
        "trade",
        ["deribit:BTC-PERPETUAL", "binance:BTC-PERPETUAL"],
        _TS - 1,
        _TS + 1,
    )

    assert sorted(df["price"].to_list()) == [1.0, 2.0]
    assert catalog.list_exchanges_on_disk() == ["binance", "deribit"]


async def test_a_legacy_lake_warns_once_naming_the_fix(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One warning per legacy prefix — a per-query warning would be noise."""
    await _write(tmp_path, _trade(1.0))
    _demote(tmp_path, "exchange=")

    with caplog.at_level(logging.WARNING, logger="crocodile.core.store.catalog"):
        catalog = Catalog(tmp_path)
        catalog.list_channels()
        catalog.list_exchanges_on_disk()

    warnings = [r for r in caplog.records if "legacy" in r.getMessage()]
    assert len(warnings) == 1
    assert "migrate-lake" in warnings[0].getMessage()


async def test_a_migrated_lake_is_silent(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    await _write(tmp_path, _trade(1.0))

    with caplog.at_level(logging.WARNING, logger="crocodile.core.store.catalog"):
        Catalog(tmp_path).list_channels()

    assert [r for r in caplog.records if "legacy" in r.getMessage()] == []

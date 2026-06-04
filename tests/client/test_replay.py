"""Acceptance tests for CrocodileClient.replay (Task 3.2).

.replay(channels, symbols, frm, to) -> Iterator[Record]

Reads matching partitions sorted by local_ts, applies the M2 k-way merge,
and yields time-ordered Records across channels and symbols.
"""

from __future__ import annotations

import pathlib

from crocodile.schema.enums import Side
from crocodile.schema.records import Trade
from crocodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


def _trade(
    local_ts: int,
    price: float = 1.0,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
) -> Trade:
    return Trade(
        exchange=exchange,
        symbol=symbol,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(local_ts),
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def _eth_trade(local_ts: int, price: float = 2000.0) -> Trade:
    return Trade(
        exchange="deribit",
        symbol="deribit:ETH-PERPETUAL",
        symbol_raw="ETH-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=f"eth-{local_ts}",
        price=price,
        amount=0.5,
        side=Side.SELL,
    )


async def _write_two_symbol_fixtures(data_dir: pathlib.Path) -> None:
    """Write interleaved trade records for two symbols."""
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=100, flush_interval_seconds=9999)
    # Interleave timestamps from two symbols
    await sink.put(_trade(_BASE_TS + 0, price=1.0))
    await sink.put(_eth_trade(_BASE_TS + 500_000_000, price=2000.0))
    await sink.put(_trade(_BASE_TS + 1_000_000_000, price=2.0))
    await sink.put(_eth_trade(_BASE_TS + 1_500_000_000, price=2001.0))
    await sink.put(_trade(_BASE_TS + 2_000_000_000, price=3.0))
    await sink.flush()


async def test_replay_returns_iterator_of_records(tmp_path: pathlib.Path) -> None:
    """replay() returns an Iterator[Record]."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    result = client.replay(
        channels=["trade"],
        symbols=["deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"],
        frm=_BASE_TS,
        to=_BASE_TS + 3_000_000_000,
    )
    assert hasattr(result, "__iter__"), "replay() must return an iterator"
    records = list(result)
    assert len(records) == 5, f"Expected 5 records, got {len(records)}"


async def test_replay_records_are_time_ordered(tmp_path: pathlib.Path) -> None:
    """Records yielded by replay() are non-decreasing in local_ts."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade"],
            symbols=["deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"],
            frm=_BASE_TS,
            to=_BASE_TS + 3_000_000_000,
        )
    )
    local_tss = [r.local_ts for r in records]
    assert local_tss == sorted(local_tss), f"replay() output is not sorted: {local_tss}"


async def test_replay_across_two_symbols(tmp_path: pathlib.Path) -> None:
    """Records from two symbols are interleaved in time order."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade"],
            symbols=["deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"],
            frm=_BASE_TS,
            to=_BASE_TS + 3_000_000_000,
        )
    )
    # Both symbols should be present
    symbols = {r.symbol for r in records}
    assert "deribit:BTC-PERPETUAL" in symbols
    assert "deribit:ETH-PERPETUAL" in symbols
    # local_ts must be globally sorted
    local_tss = [r.local_ts for r in records]
    assert local_tss == sorted(local_tss)


async def test_replay_single_symbol(tmp_path: pathlib.Path) -> None:
    """replay() on a single symbol returns only that symbol's records."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade"],
            symbols=["deribit:BTC-PERPETUAL"],
            frm=_BASE_TS,
            to=_BASE_TS + 3_000_000_000,
        )
    )
    assert len(records) == 3
    assert all(r.symbol == "deribit:BTC-PERPETUAL" for r in records)
    local_tss = [r.local_ts for r in records]
    assert local_tss == sorted(local_tss)


async def test_replay_empty_range_returns_empty(tmp_path: pathlib.Path) -> None:
    """replay() with out-of-range time yields nothing."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade"],
            symbols=["deribit:BTC-PERPETUAL"],
            frm=_BASE_TS + 999_000_000_000,
            to=_BASE_TS + 999_999_000_000,
        )
    )
    assert records == []


async def test_replay_empty_symbols_returns_empty(tmp_path: pathlib.Path) -> None:
    """replay() with empty symbols list yields nothing."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade"],
            symbols=[],
            frm=_BASE_TS,
            to=_BASE_TS + 3_000_000_000,
        )
    )
    assert records == []


async def test_replay_record_types_are_correct(tmp_path: pathlib.Path) -> None:
    """replay() yields actual Record objects (Trade, not dicts)."""
    from crocodile.client.client import CrocodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade"],
            symbols=["deribit:BTC-PERPETUAL"],
            frm=_BASE_TS,
            to=_BASE_TS + 3_000_000_000,
        )
    )
    assert all(isinstance(r, Trade) for r in records), (
        f"Expected all Trade, got {[type(r).__name__ for r in records]}"
    )

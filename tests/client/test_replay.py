"""Acceptance tests for CrypcodileClient.replay (Task 3.2).

.replay(channels, symbols, frm, to) -> Iterator[Record]

Reads matching partitions sorted by local_ts, applies the M2 k-way merge,
and yields time-ordered Records across channels and symbols.
"""

from __future__ import annotations

import pathlib

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookDelta, Trade
from crypcodile.store.parquet_sink import ParquetSink

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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
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


async def test_replay_empty_channels_returns_empty(tmp_path: pathlib.Path) -> None:
    """replay() with empty channels list yields nothing (early-return guard)."""
    from crypcodile.client.client import CrypcodileClient

    await _write_two_symbol_fixtures(tmp_path)
    client = CrypcodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=[],
            symbols=["deribit:BTC-PERPETUAL"],
            frm=_BASE_TS,
            to=_BASE_TS + 3_000_000_000,
        )
    )
    assert records == [], f"Expected empty list, got {records}"


async def test_replay_multi_channel_interleaved(tmp_path: pathlib.Path) -> None:
    """replay() across trade + book_delta channels yields all records globally sorted."""
    from crypcodile.client.client import CrypcodileClient

    # Write Trade records at even offsets and BookDelta records at odd offsets
    # so they interleave strictly when merged by local_ts.
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)

    symbol = "deribit:BTC-PERPETUAL"

    # Trades at _BASE_TS + 0, +2s, +4s
    for i, price in enumerate([1.0, 2.0, 3.0]):
        ts = _BASE_TS + i * 2_000_000_000
        await sink.put(
            Trade(
                exchange="deribit",
                symbol=symbol,
                symbol_raw="BTC-PERPETUAL",
                exchange_ts=ts,
                local_ts=ts,
                id=str(ts),
                price=price,
                amount=1.0,
                side=Side.BUY,
            )
        )

    # BookDeltas at _BASE_TS + 1s, +3s (interleaved between trades)
    for i in range(2):
        ts = _BASE_TS + 1_000_000_000 + i * 2_000_000_000
        await sink.put(
            BookDelta(
                exchange="deribit",
                symbol=symbol,
                symbol_raw="BTC-PERPETUAL",
                exchange_ts=ts,
                local_ts=ts,
                bids=[(100.0 + i, 1.0)],
                asks=[],
                seq_id=i + 1,
                prev_seq_id=i if i > 0 else None,
                is_snapshot=False,
            )
        )

    await sink.flush()

    client = CrypcodileClient(data_dir=tmp_path)
    records = list(
        client.replay(
            channels=["trade", "book_delta"],
            symbols=[symbol],
            frm=_BASE_TS,
            to=_BASE_TS + 5_000_000_000,
        )
    )

    # All 5 records (3 trades + 2 book_deltas) should appear
    assert len(records) == 5, f"Expected 5 records, got {len(records)}: {records}"

    # Output must be globally non-decreasing in local_ts
    local_tss = [r.local_ts for r in records]
    assert local_tss == sorted(local_tss), f"replay() output is not sorted: {local_tss}"

    # Both channel types must be present
    types = {type(r).__name__ for r in records}
    assert "Trade" in types, "Expected Trade records in multi-channel replay"
    assert "BookDelta" in types, "Expected BookDelta records in multi-channel replay"

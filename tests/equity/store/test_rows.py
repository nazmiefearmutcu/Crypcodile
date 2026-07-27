"""The equity fork's flattener, exercised the only way it still matters.

``crocodile.equity.store.rows`` has no producer any more: the equity connectors build
canonical records, and ``StockodileClient`` reads them with ``core``'s ``from_row``. What
this module still is, until the union merge that deletes it, is the reader for
pre-migration equity Parquet files — files ``migrate_lake`` renames the directories of and
never rewrites a byte of.

So every case below starts from a **row**, the way one arrives off disk, rather than from
a struct. That is deliberate and not merely convenient: no live code path can produce a
legacy equity struct now, so a test that built one would be testing a shape nothing writes
against a reader whose whole remaining job is a shape nothing writes *any more*. Starting
from the row also means this file needs no import of the retired union, which is what lets
that union's last import site be a single module.

The reconstructed records are identified by tag and by field rather than by ``isinstance``
for the same reason.
"""

from __future__ import annotations

from typing import Any

import pytest

from crocodile.equity.store.rows import from_row, to_row

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


def _tag(record: object) -> str:
    return str(type(record).__struct_config__.tag)  # type: ignore[attr-defined]


def _base(channel: str) -> dict[str, Any]:
    """The columns every pre-migration equity row carries.

    ``provider`` is the fork's word for the origin and ``source`` is the hive partition
    the migration renamed the directory to; a real read supplies both.
    """
    return {
        "channel": channel,
        "provider": "alpaca",
        "symbol": "AAPL",
        "symbol_raw": "AAPL",
        "source_ts": _BASE_TS,
        "local_ts": _BASE_TS,
        "date": "2023-11-14",
        "bucket": 42,
        "source": "alpaca",
    }


def test_from_row_trade() -> None:
    rec = from_row(
        {
            **_base("trade"),
            "id": "1",
            "price": 150.0,
            "size": 10.0,
            "conditions": ["@"],
            "tape": "A",
            "venue": "NASDAQ",
        }
    )
    assert _tag(rec) == "trade"
    assert rec.price == 150.0
    # ``size`` on the legacy struct is ``amount`` on the canonical one. This reader is
    # the legacy dialect and keeps the legacy spelling.
    assert rec.size == 10.0
    assert rec.tape == "A"
    assert rec.venue == "NASDAQ"


def test_from_row_quote() -> None:
    rec = from_row(
        {
            **_base("quote"),
            "bid_px": 150.0,
            "bid_sz": 100.0,
            "ask_px": 151.0,
            "ask_sz": 50.0,
            "is_nbbo": True,
            "is_consolidated": True,
            "conditions": ["R"],
            "tape": "B",
        }
    )
    assert _tag(rec) == "quote"
    assert (rec.bid_px, rec.bid_sz, rec.ask_px, rec.ask_sz) == (150.0, 100.0, 151.0, 50.0)
    assert rec.is_nbbo is True
    assert rec.is_consolidated is True
    assert rec.tape == "B"


def test_from_row_book_snapshot() -> None:
    """Equity lakes spell a level ``{price, size}``; the canonical reader says ``amount``.

    That is why the two readers cannot yet be one function, and why the zero level is in
    the fixture: ``0.0`` is the removal signal, not a missing value.
    """
    rec = from_row(
        {
            **_base("book_snapshot"),
            "bids": [{"price": 150.0, "size": 100.0}, {"price": 149.0, "size": 0.0}],
            "asks": [{"price": 151.0, "size": 50.0}],
            "depth": 2,
            "sequence_id": 42,
            "is_snapshot": True,
        }
    )
    assert _tag(rec) == "book_snapshot"
    assert rec.bids == [(150.0, 100.0), (149.0, 0.0)]
    assert rec.asks == [(151.0, 50.0)]
    assert rec.depth == 2
    assert rec.sequence_id == 42
    assert rec.is_snapshot is True


def test_from_row_book_delta() -> None:
    rec = from_row(
        {
            **_base("book_delta"),
            "bids": [{"price": 150.0, "size": 100.0}],
            "asks": [{"price": 151.0, "size": 50.0}],
            "seq_id": 43,
            "prev_seq_id": 42,
            "is_snapshot": False,
        }
    )
    assert _tag(rec) == "book_delta"
    assert rec.bids == [(150.0, 100.0)]
    assert rec.seq_id == 43
    assert rec.prev_seq_id == 42
    assert rec.is_snapshot is False


def test_from_row_bar() -> None:
    """``channel=bar`` partitions exist on disk and nothing writes them any more.

    The tag was absorbed into ``ohlcv`` when the two identical bar structs collapsed, so
    this is the reader that keeps those partitions readable until a ``migrate-lake`` step
    rewrites them.
    """
    rec = from_row(
        {
            **_base("bar"),
            "interval": "1m",
            "open": 150.0,
            "high": 151.0,
            "low": 149.0,
            "close": 150.5,
            "volume": 10000.0,
            "vwap": 150.2,
            "trade_count": 120,
        }
    )
    assert _tag(rec) == "bar"
    assert rec.interval == "1m"
    assert rec.open == 150.0
    assert rec.vwap == 150.2
    assert rec.trade_count == 120


def test_from_row_unknown_channel_raises() -> None:
    with pytest.raises(ValueError, match="Unknown channel tag"):
        from_row({**_base("not_a_channel")})


def test_a_row_survives_a_round_trip_back_through_the_flattener() -> None:
    """``to_row`` still has to invert ``from_row`` for the legacy dialect.

    The partition columns are recomputed from the record, so this also pins that a
    rebuilt row lands in the partition the file it came from is already in.
    """
    row = {
        **_base("trade"),
        "id": "1",
        "price": 150.0,
        "size": 10.0,
        "conditions": ["@"],
        "tape": "A",
        "venue": "NASDAQ",
    }
    rebuilt = to_row(from_row(row))

    assert rebuilt["channel"] == "trade"
    assert rebuilt["date"] == "2023-11-14"
    assert 0 <= rebuilt["bucket"] < 128
    assert rebuilt["source"] == "alpaca", "the unified partition key, from the fork's provider"
    assert rebuilt["tape"] == "A"
    assert from_row(rebuilt) == from_row(row)


def test_a_row_with_no_source_timestamp_round_trips_as_none() -> None:
    row = {**_base("trade"), "source_ts": None, "id": "2", "price": 200.0, "size": 5.0}
    rebuilt = to_row(from_row(row))
    assert rebuilt["source_ts"] is None
    assert rebuilt["tape"] is None


def test_onchain_limit_order_fill_round_trips() -> None:
    """One of the five on-chain records the equity fork carried and no longer emits.

    They are read back here rather than constructed for the same reason as everything
    else in this file: the struct has no producer, the rows are on disk.
    """
    row = {
        **_base("limit_order_fill"),
        "provider": "base_onchain",
        "source": "base_onchain",
        "symbol": "ETH-USDC",
        "symbol_raw": "ETH-USDC",
        "exchange_ts": _BASE_TS,
        "tx_hash": "0xabc",
        "log_index": 1,
        "protocol": "1inch",
        "maker": "0x1",
        "taker": "0x2",
        "maker_token": "0xa",
        "taker_token": "0xb",
        "maker_amount": 1.5,
        "taker_amount": 2.5,
        "order_hash": "0xord",
    }
    rec = from_row(row)
    assert _tag(rec) == "limit_order_fill"
    assert rec.tx_hash == "0xabc"
    assert rec.maker_amount == 1.5

    rebuilt = to_row(rec)
    assert rebuilt["channel"] == "limit_order_fill"
    assert rebuilt["maker_amount"] == 1.5
    assert from_row(rebuilt) == rec


def test_onchain_por_update_round_trips() -> None:
    row = {
        **_base("por_update"),
        "provider": "base_onchain",
        "source": "base_onchain",
        "symbol": "cbBTC",
        "symbol_raw": "cbBTC",
        "exchange_ts": _BASE_TS,
        "feed_address": "0xfeed",
        "token_address": "0xtok",
        "reserves": 100.0,
        "total_supply": 100.0,
        "backing_ratio": 1.0,
        "is_backed": True,
    }
    rec = from_row(row)
    assert _tag(rec) == "por_update"
    assert rec.is_backed is True
    assert rec.reserves == 100.0
    assert from_row(to_row(rec)) == rec

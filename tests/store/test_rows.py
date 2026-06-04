from crocodile.schema.enums import Side
from crocodile.schema.records import BookDelta, BookSnapshot, Trade
from crocodile.store.rows import to_row


def test_to_row_adds_partition_cols():
    t = Trade(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=1700000000000000000,
        local_ts=1700000000000000000,
        id="1",
        price=1.0,
        amount=2.0,
        side=Side.BUY,
    )
    row = to_row(t)
    assert row["channel"] == "trade"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["side"] == "buy"


def test_to_row_exchange_ts_none():
    t = Trade(
        exchange="binance-spot",
        symbol="binance-spot:BTC-USDT",
        symbol_raw="BTCUSDT",
        exchange_ts=None,
        local_ts=1700000000000000000,
        id="2",
        price=50000.0,
        amount=0.5,
        side=Side.SELL,
    )
    row = to_row(t)
    assert row["exchange_ts"] is None
    assert row["channel"] == "trade"
    assert row["side"] == "sell"


def test_to_row_book_snapshot_levels():
    snap = BookSnapshot(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=1700000000000000000,
        local_ts=1700000000000000000,
        bids=[(100.0, 5.0), (99.0, 2.0)],
        asks=[(101.0, 4.0)],
        depth=3,
        sequence_id=100,
        is_snapshot=True,
    )
    row = to_row(snap)
    assert row["channel"] == "book_snapshot"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["bids"] == [(100.0, 5.0), (99.0, 2.0)]
    assert row["asks"] == [(101.0, 4.0)]


def test_to_row_book_delta_zero_amount_round_trips():
    delta = BookDelta(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=1700000000100000000,
        bids=[(99.0, 0.0), (100.0, 7.0)],
        asks=[(102.0, 1.0)],
        seq_id=101,
        prev_seq_id=100,
        is_snapshot=False,
    )
    row = to_row(delta)
    assert row["channel"] == "book_delta"
    # amount=0.0 (canonical removal) must survive round-trip
    assert (99.0, 0.0) in row["bids"]
    assert (100.0, 7.0) in row["bids"]


def test_bucket_is_deterministic():
    t = Trade(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=1700000000000000000,
        id="3",
        price=1.0,
        amount=1.0,
        side=Side.BUY,
    )
    row1 = to_row(t)
    row2 = to_row(t)
    assert row1["bucket"] == row2["bucket"]

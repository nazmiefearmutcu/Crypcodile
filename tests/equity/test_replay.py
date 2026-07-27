import pytest

from crocodile.core.replay.merge import replay
from crocodile.core.replay.orderbook import BookGap, OrderBook
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import BookDelta, BookSnapshot, Trade


def test_orderbook_spot_basic() -> None:
    book = OrderBook()
    assert not book._initialized

    # Apply snapshot
    snap = BookSnapshot(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1000,
        local_ts=1000,
        bids=[(100.0, 10.0), (99.0, 20.0)],
        asks=[(101.0, 15.0), (102.0, 25.0)],
        depth=2,
        sequence_id=100,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(snap)
    assert book._initialized
    assert book._last_seq_id == 100
    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0

    # Apply correct spot delta (seq_id == last_seq_id + 1)
    delta1 = BookDelta(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1001,
        local_ts=1001,
        bids=[(100.0, 12.0), (99.0, 0.0)],  # update bid, remove bid
        asks=[(101.5, 5.0)],
        seq_id=101,
        prev_seq_id=None,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(delta1)
    assert book._last_seq_id == 101
    assert book.bids == {100.0: 12.0}  # 99.0 was removed
    assert book.asks == {101.0: 15.0, 101.5: 5.0, 102.0: 25.0}

    # Apply spot delta with gap
    delta_gap = BookDelta(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1002,
        local_ts=1002,
        bids=[],
        asks=[],
        seq_id=103,  # Gap: expected 102
        prev_seq_id=None,
        asset_class=AssetClass.EQUITY,
    )
    with pytest.raises(BookGap):
        book.apply(delta_gap)


def test_orderbook_futures_first_delta() -> None:
    book = OrderBook()
    snap = BookSnapshot(
        source="dummy",
        symbol="BTCUSDT",
        symbol_raw="BTCUSDT",
        source_ts=1000,
        local_ts=1000,
        bids=[(50000.0, 1.0)],
        asks=[(50001.0, 2.0)],
        depth=1,
        sequence_id=200,
        asset_class=AssetClass.EQUITY,  # lastUpdateId
    )
    book.apply(snap)

    # First futures delta: U <= lastUpdateId <= u
    # which translates to: prev_seq_id < last_seq_id <= seq_id
    # e.g., prev_seq_id=195, seq_id=205
    delta = BookDelta(
        source="dummy",
        symbol="BTCUSDT",
        symbol_raw="BTCUSDT",
        source_ts=1001,
        local_ts=1001,
        bids=[(50000.0, 1.5)],
        asks=[],
        seq_id=205,
        prev_seq_id=195,
        asset_class=AssetClass.EQUITY,
    )
    # This should succeed due to relaxed check
    book.apply(delta)
    assert book._last_seq_id == 205
    assert book.bids[50000.0] == 1.5

    # Subsequent futures delta: prev_seq_id == last_seq_id (205)
    delta2 = BookDelta(
        source="dummy",
        symbol="BTCUSDT",
        symbol_raw="BTCUSDT",
        source_ts=1002,
        local_ts=1002,
        bids=[],
        asks=[(50001.0, 0.0)],
        seq_id=210,
        prev_seq_id=205,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(delta2)
    assert book._last_seq_id == 210

    # Subsequent futures delta with gap
    delta_gap = BookDelta(
        source="dummy",
        symbol="BTCUSDT",
        symbol_raw="BTCUSDT",
        source_ts=1003,
        local_ts=1003,
        bids=[],
        asks=[],
        seq_id=220,
        prev_seq_id=215,
        asset_class=AssetClass.EQUITY,  # Gap: expected 210
    )
    with pytest.raises(BookGap):
        book.apply(delta_gap)


def test_orderbook_duplicate_delta_checks() -> None:
    book = OrderBook()
    snap = BookSnapshot(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1000,
        local_ts=1000,
        bids=[(100.0, 10.0)],
        asks=[(101.0, 10.0)],
        depth=1,
        sequence_id=100,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(snap)

    delta = BookDelta(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1001,
        local_ts=1001,
        bids=[(100.0, 11.0)],
        asks=[],
        seq_id=101,
        prev_seq_id=None,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(delta)

    # Re-apply identical delta (same seq_id, same bids/asks)
    identical_delta = BookDelta(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1001,
        local_ts=1001,
        bids=[(100.0, 11.0)],
        asks=[],
        seq_id=101,
        prev_seq_id=None,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(identical_delta)  # should succeed and do nothing

    # Re-apply non-identical delta (same seq_id, different bids/asks)
    non_identical_delta = BookDelta(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1001,
        local_ts=1001,
        bids=[(100.0, 12.0)],
        asks=[],
        seq_id=101,
        prev_seq_id=None,
        asset_class=AssetClass.EQUITY,
    )
    with pytest.raises(BookGap):
        book.apply(non_identical_delta)


def test_orderbook_float_rounding_and_validation() -> None:
    book = OrderBook()
    snap = BookSnapshot(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1000,
        local_ts=1000,
        bids=[(100.10000000000001, 10.0)],
        asks=[(101.0, 10.0)],
        depth=1,
        sequence_id=100,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(snap)
    # The price should be rounded to 8 decimals
    assert 100.1 in book.bids

    # Update/Delete level with slightly imprecise float
    delta = BookDelta(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1001,
        local_ts=1001,
        bids=[(100.1, 0.0)],
        asks=[],
        seq_id=101,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(delta)
    assert 100.1 not in book.bids

    # Validations. The equity engine used bare `assert`, which python -O strips
    # out — the merged engine raises ValueError so the guard survives an
    # optimised run. What is rejected is unchanged; only the class is.
    with pytest.raises(ValueError):
        # negative price
        book._apply_levels([(-10.0, 1.0)], book.bids)

    with pytest.raises(ValueError):
        # negative size
        book._apply_levels([(100.0, -1.0)], book.bids)


def test_orderbook_apply_batch() -> None:
    book = OrderBook()
    snap = BookSnapshot(
        source="dummy",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1000,
        local_ts=1000,
        bids=[(100.0, 10.0)],
        asks=[(101.0, 10.0)],
        depth=1,
        sequence_id=100,
        asset_class=AssetClass.EQUITY,
    )
    book.apply(snap)

    deltas = [
        BookDelta(
            source="dummy",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=1001,
            local_ts=1001,
            bids=[(100.0, 11.0)],
            asks=[],
            seq_id=101,
            asset_class=AssetClass.EQUITY,
        ),
        BookDelta(
            source="dummy",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=1001,
            local_ts=1001,
            bids=[],
            asks=[(101.0, 12.0)],
            seq_id=102,
            asset_class=AssetClass.EQUITY,
        ),
    ]
    book.apply_batch(deltas)
    assert book._last_seq_id == 102
    assert book.bids[100.0] == 11.0
    assert book.asks[101.0] == 12.0

    # Batch with gap inside
    deltas_gap = [
        BookDelta(
            source="dummy",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=1002,
            local_ts=1002,
            bids=[(100.0, 15.0)],
            asks=[],
            seq_id=103,
            asset_class=AssetClass.EQUITY,
        ),
        BookDelta(
            source="dummy",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=1002,
            local_ts=1002,
            bids=[],
            asks=[(101.0, 15.0)],
            seq_id=105,
            asset_class=AssetClass.EQUITY,  # Gap! Expected 104
        ),
    ]
    with pytest.raises(BookGap):
        book.apply_batch(deltas_gap)


def test_merge_replay_determinism() -> None:
    t1 = Trade(
        source="provA",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1000,
        local_ts=2000,
        id="1",
        price=150.0,
        amount=10.0,
        asset_class=AssetClass.EQUITY,
        side=Side.UNKNOWN,
    )
    t2 = Trade(
        source="provB",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1000,
        local_ts=2000,
        id="2",
        price=150.0,
        amount=10.0,
        asset_class=AssetClass.EQUITY,
        side=Side.UNKNOWN,
    )
    t3 = Trade(
        source="provA",
        symbol="MSFT",
        symbol_raw="MSFT",
        source_ts=1000,
        local_ts=2000,
        id="3",
        price=300.0,
        amount=5.0,
        asset_class=AssetClass.EQUITY,
        side=Side.UNKNOWN,
    )

    res1 = list(replay([iter([t2]), iter([t1]), iter([t3])]))
    assert res1 == [t1, t3, t2]

    res2 = list(replay([iter([t3]), iter([t2]), iter([t1])]))
    assert res2 == [t1, t3, t2]

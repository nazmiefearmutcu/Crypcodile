"""Acceptance tests for the k-way merge replay engine (Task 2.4).

The plan specifies:
  - replay(streams) -> Iterator[Record] using heapq.merge
  - Sort key: (local_ts, exchange_ts or -inf, seq or 0)
  - NULL exchange_ts sorts BEFORE a present one (treated as -inf)
  - Outputs are globally non-decreasing in local_ts
"""

from crocodile.replay.merge import replay
from crocodile.schema.enums import Side
from crocodile.schema.records import BookDelta, Trade


def _trade(local_ts: int, exchange_ts: int | None, price: float = 1.0) -> Trade:
    """Helper: build a minimal Trade record."""
    return Trade(
        exchange="test",
        symbol="test:BTC",
        symbol_raw="BTC",
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        id=f"t{local_ts}",
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def _delta(local_ts: int, exchange_ts: int | None, seq_id: int | None = None) -> BookDelta:
    """Helper: build a minimal BookDelta record."""
    return BookDelta(
        exchange="test",
        symbol="test:BTC",
        symbol_raw="BTC",
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        bids=[],
        asks=[],
        seq_id=seq_id,
    )


def test_two_streams_merge_non_decreasing():
    """Basic merge: two sorted streams, interleaved local_ts, output is ordered."""
    stream_a = [
        _trade(100, 90),
        _trade(200, 190),
        _trade(400, 390),
    ]
    stream_b = [
        _trade(150, 140),
        _trade(250, 240),
        _trade(350, 340),
    ]
    result = list(replay([iter(stream_a), iter(stream_b)]))
    local_tss = [r.local_ts for r in result]
    assert local_tss == sorted(local_tss), f"Output not sorted: {local_tss}"
    assert local_tss == [100, 150, 200, 250, 350, 400]


def test_tie_break_null_exchange_ts_sorts_before_present():
    """When local_ts ties, NULL exchange_ts (treated as -inf) sorts BEFORE a present exchange_ts."""
    # Both records have local_ts=100; one has exchange_ts=None, one has exchange_ts=50
    record_with_null = _trade(100, None, price=1.0)    # exchange_ts=None → -inf
    record_with_ts = _trade(100, 50, price=2.0)        # exchange_ts=50

    result = list(replay([iter([record_with_null]), iter([record_with_ts])]))
    assert len(result) == 2
    # NULL sorts first
    assert result[0].exchange_ts is None
    assert result[1].exchange_ts == 50


def test_tie_break_seq_used_when_exchange_ts_equal():
    """When local_ts and exchange_ts tie, seq_id breaks the tie."""
    delta_seq1 = _delta(100, 90, seq_id=1)
    delta_seq2 = _delta(100, 90, seq_id=2)

    result = list(replay([iter([delta_seq2]), iter([delta_seq1])]))
    assert len(result) == 2
    # Lower seq first
    assert result[0].seq_id == 1  # type: ignore[union-attr]
    assert result[1].seq_id == 2  # type: ignore[union-attr]


def test_single_stream_passthrough():
    """A single stream is returned as-is."""
    stream = [_trade(100, 90), _trade(200, 190), _trade(300, 290)]
    result = list(replay([iter(stream)]))
    assert [r.local_ts for r in result] == [100, 200, 300]


def test_empty_streams():
    """Empty or no streams produce empty output."""
    assert list(replay([])) == []
    assert list(replay([iter([])])) == []


def test_three_streams_interleaved():
    """Three-way merge stays globally sorted."""
    stream_a = [_trade(10, 5), _trade(40, 35)]
    stream_b = [_trade(20, 15), _trade(50, 45)]
    stream_c = [_trade(30, 25), _trade(60, 55)]

    result = list(replay([iter(stream_a), iter(stream_b), iter(stream_c)]))
    local_tss = [r.local_ts for r in result]
    assert local_tss == sorted(local_tss)
    assert local_tss == [10, 20, 30, 40, 50, 60]


def test_null_exchange_ts_consistent_with_present_across_streams():
    """NULL exchange_ts sorts before a present exchange_ts at the same local_ts."""
    # Simulate: stream A has NULL exchange_ts, stream B has present exchange_ts — local_ts=500
    null_trade = _trade(500, None, price=1.0)
    present_trade = _trade(500, 1, price=2.0)   # exchange_ts=1 (small but present)

    result = list(replay([iter([present_trade]), iter([null_trade])]))
    assert result[0].exchange_ts is None   # NULL comes first
    assert result[1].exchange_ts == 1

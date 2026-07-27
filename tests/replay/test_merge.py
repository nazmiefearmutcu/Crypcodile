"""Acceptance tests for the k-way merge replay engine (Task 2.4).

The plan specifies:
  - replay(streams) -> Iterator[Record] using heapq.merge
  - Sort key: (local_ts, source_ts or -inf, seq or 0)
  - NULL source_ts sorts BEFORE a present one (treated as -inf)
  - Outputs are globally non-decreasing in local_ts
"""

import msgspec.structs

from crocodile.core.replay.merge import replay
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import BookDelta, Trade


def _trade(local_ts: int, source_ts: int | None, price: float = 1.0) -> Trade:
    """Helper: build a minimal Trade record."""
    return Trade(
        source="test",
        symbol="test:BTC",
        symbol_raw="BTC",
        source_ts=source_ts,
        local_ts=local_ts,
        asset_class=AssetClass.CRYPTO,
        id=f"t{local_ts}",
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def _delta(local_ts: int, source_ts: int | None, seq_id: int | None = None) -> BookDelta:
    """Helper: build a minimal BookDelta record."""
    return BookDelta(
        source="test",
        symbol="test:BTC",
        symbol_raw="BTC",
        source_ts=source_ts,
        local_ts=local_ts,
        asset_class=AssetClass.CRYPTO,
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


def test_tie_break_null_source_ts_sorts_before_present():
    """When local_ts ties, NULL source_ts (treated as -inf) sorts BEFORE a present source_ts."""
    # Both records have local_ts=100; one has source_ts=None, one has source_ts=50
    record_with_null = _trade(100, None, price=1.0)    # source_ts=None → -inf
    record_with_ts = _trade(100, 50, price=2.0)        # source_ts=50

    result = list(replay([iter([record_with_null]), iter([record_with_ts])]))
    assert len(result) == 2
    # NULL sorts first
    assert result[0].source_ts is None
    assert result[1].source_ts == 50


def test_tie_break_seq_used_when_source_ts_equal():
    """When local_ts and source_ts tie, seq_id breaks the tie."""
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


def test_null_source_ts_consistent_with_present_across_streams():
    """NULL source_ts sorts before a present source_ts at the same local_ts."""
    # Simulate: stream A has NULL source_ts, stream B has present source_ts — local_ts=500
    null_trade = _trade(500, None, price=1.0)
    present_trade = _trade(500, 1, price=2.0)   # source_ts=1 (small but present)

    result = list(replay([iter([present_trade]), iter([null_trade])]))
    assert result[0].source_ts is None   # NULL comes first
    assert result[1].source_ts == 1


# ---------------------------------------------------------------------------
# The fourth element of the sort key: which origin field wins
# ---------------------------------------------------------------------------


def test_the_origin_tie_break_reads_the_provider_before_the_listing_venue() -> None:
    """Nothing else in this suite can tell the two orders apart.

    ``_ORIGIN_FIELDS`` was reordered to put ``provider`` ahead of ``exchange``,
    which is right — equity's ``Instrument`` carries both and they mean different
    things, the data source versus where the security is listed — but it changes
    the fourth element of ``_sort_key`` and so the replay order of records that
    tie on ``(local_ts, source_ts, seq)``. Reverting it would produce a
    differently ordered stream with every other test still green, because only a
    record naming *both* fields can distinguish the two orders and no canonical
    record does.
    """
    from crocodile.core.replay.merge import _sort_key
    from crocodile.equity.schema.records import Instrument

    key = _sort_key(
        Instrument(  # type: ignore[arg-type]
            provider="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=100,
            source_ts=90,
            name="Apple Inc.",
            exchange="NASDAQ",
        )
    )

    assert key[3] == "alpaca", "the tie-break must name who served the data, not where it lists"


def test_records_tying_on_everything_else_are_ordered_by_origin() -> None:
    """The determinism claim, exercised rather than assumed.

    Two records at the same instant with no sequence number tie on the first
    three key elements. The heap would otherwise return them in whatever order
    the streams were passed in, so the order is asserted both ways round.
    """
    binance = _trade(100, 90)
    deribit = msgspec.structs.replace(binance, source="deribit")
    assert binance.source == "test"

    forwards = [r.source for r in replay([iter([binance]), iter([deribit])])]
    backwards = [r.source for r in replay([iter([deribit]), iter([binance])])]

    assert forwards == backwards == ["deribit", "test"]


def test_the_replay_and_the_store_agree_on_the_origin_order() -> None:
    """Two copies of one precedence, kept honest by comparison.

    ``replay.merge`` states the order separately from ``store.rows`` rather than
    importing it, so the replay layer does not depend on the store. That is a
    defensible split only while something notices when they diverge — and the
    divergence this guards is the one that already happened once, in ``_header``.
    """
    from crocodile.core.replay.merge import _ORIGIN_FIELDS as MERGE_ORDER
    from crocodile.core.store.rows import _ORIGIN_FIELDS as STORE_ORDER

    assert MERGE_ORDER == STORE_ORDER

"""The crypto depth ladder: what it cuts, what it refuses to cut, and what it cannot see.

The statement under test is SQL, so the lake here is a real DuckDB connection over a real
``list[struct{price, amount}]`` column rather than a stub returning a prepared frame. A stub
would exercise the code around the query and never the query — and the two properties this
module exists to pin, *newest at or before the requested instant* and *nothing older than the
declared window*, are both entirely inside the ``WHERE`` and ``ORDER BY``. Nothing here
touches disk or the network: an in-memory connection over a registered frame is the whole
fixture.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance, confidence_for
from crocodile.core.schema.records import DepthProfile
from crocodile.crypto.depth import LakeQuery, depth_from_book_snapshots

_BASE_NS = 1_704_067_200_000_000_000
_SECOND_NS = 1_000_000_000
_SYMBOL = "deribit:BTC-PERPETUAL"

_BIDS = [{"price": 99.0, "amount": 5.0}, {"price": 98.0, "amount": 4.0}]
_ASKS = [{"price": 101.0, "amount": 5.0}, {"price": 102.0, "amount": 4.0}]


def _row(
    *,
    local_ts: int,
    bids: list[dict[str, float]] | None = None,
    asks: list[dict[str, float]] | None = None,
    symbol: str = _SYMBOL,
    source_ts: int | None = None,
) -> dict[str, object]:
    return {
        "source": "deribit",
        "symbol": symbol,
        "symbol_raw": symbol.split(":")[-1],
        "local_ts": local_ts,
        "source_ts": source_ts,
        "bids": _BIDS if bids is None else bids,
        "asks": _ASKS if asks is None else asks,
    }


def _lake(*rows: dict[str, object]) -> LakeQuery:
    """A DuckDB view named ``book_snapshot`` over ``rows``, and a reader for it."""
    frame = pl.DataFrame(
        list(rows),
        schema={
            "source": pl.String,
            "symbol": pl.String,
            "symbol_raw": pl.String,
            "local_ts": pl.Int64,
            "source_ts": pl.Int64,
            "bids": pl.List(pl.Struct({"price": pl.Float64, "amount": pl.Float64})),
            "asks": pl.List(pl.Struct({"price": pl.Float64, "amount": pl.Float64})),
        },
        orient="row",
    )
    connection = duckdb.connect()
    connection.register("book_snapshot", frame)

    def query(sql: str) -> pl.DataFrame:
        return connection.execute(sql).pl()

    return query


def _empty_lake() -> LakeQuery:
    """A lake with no ``book_snapshot`` view at all — the state before anything is collected."""
    connection = duckdb.connect()

    def query(sql: str) -> pl.DataFrame:
        return connection.execute(sql).pl()

    return query


def _slice(
    query: LakeQuery,
    *,
    as_of_ns: int = _BASE_NS,
    top_n: int = 2,
    max_age_ns: int = 60 * _SECOND_NS,
) -> DepthProfile:
    """The call under test, with the arguments each case is not about held still."""
    return depth_from_book_snapshots(
        query,
        _SYMBOL,
        asset_class=AssetClass.CRYPTO,
        as_of_ns=as_of_ns,
        top_n=top_n,
        max_age_ns=max_age_ns,
    )


# ---------------------------------------------------------------------------
# The shape, which is the whole of what "symmetric" means here
# ---------------------------------------------------------------------------


def test_the_slice_is_the_ladder_shape_the_equity_half_already_returns() -> None:
    """Bids descending, asks ascending, a midpoint reference, and a counted depth.

    The equity half builds this shape out of a VAP profile
    (``crocodile.equity.depth.vap.split_ladder``) or out of a top-of-book quote. Symmetry for
    ``depth`` is one parameter struct and one return shape; this asserts the second half.
    """
    profile = _slice(_lake(_row(local_ts=_BASE_NS)))
    assert profile.bids == [(99.0, 5.0), (98.0, 4.0)]
    assert profile.asks == [(101.0, 5.0), (102.0, 4.0)]
    assert profile.reference_price == 100.0
    assert profile.depth == 4
    assert profile.symbol == _SYMBOL
    assert profile.asset_class is AssetClass.CRYPTO


def test_the_record_claims_the_instant_asked_for_and_keeps_the_venues_own_stamp() -> None:
    """``local_ts`` is the instant the ladder describes; ``source_ts`` stays the venue's.

    Which makes the age the confidence was computed over legible on the row rather than only
    inside the formula — the ratio is relative to a window the caller declared, so anyone
    re-grading it against a window of their own needs the raw gap.
    """
    profile = _slice(
        _lake(_row(local_ts=_BASE_NS - 5 * _SECOND_NS, source_ts=_BASE_NS - 6 * _SECOND_NS))
    )
    assert profile.local_ts == _BASE_NS
    assert profile.source_ts == _BASE_NS - 6 * _SECOND_NS


def test_a_slice_is_derived_and_never_reads_as_synthetic() -> None:
    """Nothing in it is modelled, which is the line ``DepthProfile.is_synthetic`` sits on.

    The crypto half is the one place in this capability where no part of the ladder stands in
    for another data class — the venue published resting size and this reports resting size.
    ``yahoo_1m_vap`` is SYNTHETIC because traded volume stands in for resting size; a consumer
    filtering ``WHERE NOT is_synthetic`` must get this back and that back.
    """
    profile = _slice(_lake(_row(local_ts=_BASE_NS)))
    assert profile.prov is Provenance.DERIVED
    assert profile.prov_basis == "book_snapshot_slice"
    assert profile.prov_inputs == ["book_snapshot"]
    assert not profile.is_synthetic


# ---------------------------------------------------------------------------
# Lookahead: the property `core.resample.book` paid for once already
# ---------------------------------------------------------------------------


def test_the_slice_cannot_see_past_the_instant_it_claims_to_describe() -> None:
    """A later snapshot exists, is closer to now, and must not be the answer.

    This is the lookahead bias ``crocodile.core.resample.book`` records in full: a boundary
    stamped 10:00:00 reporting a bid that had already been pulled at 10:00:00.200, twenty-four
    times the tradeable depth, in the direction that flatters a backtest. A depth slice is the
    same hazard with the caller naming the instant instead of an interval boundary, so the
    same rule has to hold — and the newer row is deliberately the *better looking* one, so a
    reader that sorted by distance rather than by direction would fail here.
    """
    past = _row(
        local_ts=_BASE_NS - 10 * _SECOND_NS,
        bids=[{"price": 99.0, "amount": 5.0}],
        asks=[{"price": 101.0, "amount": 5.0}],
    )
    future = _row(
        local_ts=_BASE_NS + _SECOND_NS,
        bids=[{"price": 99.5, "amount": 500.0}],
        asks=[{"price": 100.5, "amount": 500.0}],
    )
    profile = _slice(_lake(past, future))
    assert profile.bids == [(99.0, 5.0)]
    assert profile.asks == [(101.0, 5.0)]
    assert profile.local_ts == _BASE_NS


def test_a_snapshot_stamped_exactly_on_the_instant_is_information_that_existed_then() -> None:
    """The boundary is inclusive, on ``core.resample.book``'s own rule for a record at *B*."""
    profile = _slice(_lake(_row(local_ts=_BASE_NS)))
    assert profile.prov_confidence == 1.0


def test_only_the_future_being_available_is_an_absence_rather_than_a_fallback() -> None:
    """No usable past means no answer; falling forward is the bias wearing a default."""
    with pytest.raises(ValueError, match="no stored book snapshot"):
        _slice(_lake(_row(local_ts=_BASE_NS + _SECOND_NS)))


# ---------------------------------------------------------------------------
# The window, which is a bound before it is a denominator
# ---------------------------------------------------------------------------


def test_a_book_older_than_the_declared_window_is_refused_rather_than_served_stale() -> None:
    older = _row(local_ts=_BASE_NS - 61 * _SECOND_NS)
    with pytest.raises(ValueError, match="no stored book snapshot"):
        _slice(_lake(older), max_age_ns=60 * _SECOND_NS)
    # The same row answers once the caller says a longer staleness still describes the instant.
    assert _slice(_lake(older), max_age_ns=120 * _SECOND_NS).depth == 4


def test_a_book_at_exactly_the_window_edge_is_admitted_and_scores_nothing() -> None:
    """The edge is reachable, and 0.0 there is the freshness term meaning what it says.

    Not the same claim as ``Provenance.UNAVAILABLE``: the record exists and its levels are
    the venue's. The number says this answer used up every bit of the tolerance the caller
    declared, which is the reading the registry's docstring gives 0.0 throughout.
    """
    window = 60 * _SECOND_NS
    profile = _slice(_lake(_row(local_ts=_BASE_NS - window)), max_age_ns=window)
    assert profile.prov_confidence == 0.0
    assert profile.prov is Provenance.DERIVED
    assert profile.depth == 4


def test_the_freshness_term_is_what_moves_between_those_two_ends() -> None:
    window = 60 * _SECOND_NS
    profile = _slice(_lake(_row(local_ts=_BASE_NS - window // 4)), max_age_ns=window)
    assert profile.prov_confidence == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# The fill term: what the store held against what the caller asked for
# ---------------------------------------------------------------------------


def test_asking_for_more_levels_than_the_store_holds_is_measured_not_hidden() -> None:
    """Two levels a side against a request for five is a ladder the lake could not fill.

    Distinct from ``book_resample``'s truncation note, which is about a caller asking for
    *fewer* levels than the engine holds and being answered exactly. Here the trimming
    already happened upstream, at whatever depth the collector subscribed the book at.
    """
    profile = _slice(_lake(_row(local_ts=_BASE_NS)), top_n=5)
    assert profile.depth == 4
    assert profile.prov_confidence == pytest.approx(0.4)


def test_asking_for_fewer_levels_than_the_store_holds_is_a_full_answer() -> None:
    """The case ``book_resample`` argues is not a deficiency, scoring 1.0 here as it does there."""
    deep = _row(
        local_ts=_BASE_NS,
        bids=[{"price": 99.0 - i, "amount": 1.0} for i in range(8)],
        asks=[{"price": 101.0 + i, "amount": 1.0} for i in range(8)],
    )
    profile = _slice(_lake(deep), top_n=2)
    assert profile.depth == 4
    assert profile.prov_confidence == 1.0
    assert profile.bids == [(99.0, 1.0), (98.0, 1.0)]
    assert profile.asks == [(101.0, 1.0), (102.0, 1.0)]


def test_the_confidence_is_the_registered_formula_over_what_was_measured() -> None:
    """The record's number and the registry's number are the same call, not two spellings."""
    profile = _slice(_lake(_row(local_ts=_BASE_NS - 30 * _SECOND_NS)), top_n=3)
    assert profile.prov_confidence == confidence_for(
        "book_snapshot_slice",
        {
            "n_levels": 4,
            "n_requested": 6,
            "age_ns": 30 * _SECOND_NS,
            "window_ns": 60 * _SECOND_NS,
        },
    )


# ---------------------------------------------------------------------------
# Level hygiene, and the refusal that keeps a fabricated number off the record
# ---------------------------------------------------------------------------


def test_a_zero_size_entry_is_a_deletion_marker_and_not_a_level() -> None:
    """Counting it would report depth at a price where nothing rests.

    ``crocodile.core.store.rows`` refuses to default a null size for the same reason: 0.0 is
    not a missing value in this protocol, it is the canonical removal of that price level.
    """
    with_deletions = _row(
        local_ts=_BASE_NS,
        bids=[{"price": 99.0, "amount": 5.0}, {"price": 98.0, "amount": 0.0}],
        asks=[{"price": 101.0, "amount": 0.0}, {"price": 102.0, "amount": 4.0}],
    )
    profile = _slice(_lake(with_deletions))
    assert profile.bids == [(99.0, 5.0)]
    assert profile.asks == [(102.0, 4.0)]
    assert profile.depth == 2
    assert profile.reference_price == pytest.approx(100.5)


def test_a_stored_side_is_sorted_before_it_is_read_for_a_best_price() -> None:
    """The equity slippage fork's contribution; its crypto twin trusted the stored order."""
    unsorted = _row(
        local_ts=_BASE_NS,
        bids=[{"price": 98.0, "amount": 4.0}, {"price": 99.0, "amount": 5.0}],
        asks=[{"price": 102.0, "amount": 4.0}, {"price": 101.0, "amount": 5.0}],
    )
    profile = _slice(_lake(unsorted), top_n=1)
    assert profile.bids == [(99.0, 5.0)]
    assert profile.asks == [(101.0, 5.0)]


def test_one_sided_book_takes_its_reference_from_the_side_that_exists() -> None:
    """A real state, and the same rule ``alpaca_l1`` applies to a one-sided latest quote."""
    profile = _slice(_lake(_row(local_ts=_BASE_NS, asks=[])))
    assert profile.asks == []
    assert profile.reference_price == 99.0
    assert profile.prov_confidence == pytest.approx(0.5)


def test_a_book_with_no_usable_level_is_refused_rather_than_given_a_reference_price() -> None:
    """``reference_price`` is required, and there is nothing to derive it from.

    Emitting the record with a zero would be the fabricated measurement Gate 3b scans the
    tree for — a literal standing where a measurement belongs — and a confidence of 0.0 does
    not redeem it, because the fabricated field is not the one the confidence describes.
    """
    with pytest.raises(ValueError, match="no level with a positive size"):
        _slice(_lake(_row(local_ts=_BASE_NS, bids=[], asks=[])))


# ---------------------------------------------------------------------------
# The lake's own absences, and the value that must stay a value
# ---------------------------------------------------------------------------


def test_a_lake_that_has_never_stored_a_book_reads_as_no_book_and_not_as_a_crash() -> None:
    """No ``book_snapshot`` view is the same answer as no matching row, and says so."""
    with pytest.raises(ValueError, match="no stored book snapshot"):
        _slice(_empty_lake())


def test_a_symbol_carrying_a_quote_stays_a_value_rather_than_becoming_syntax() -> None:
    """``sequencer-latency`` interpolated a free-text parameter on a public route.

    ``CapabilityContext.query`` takes no bound parameters, so the symbol is quoted rather than
    placed, and a symbol that can end the literal and continue the statement is the whole of
    what that costs. The assertion is that a hostile spelling reaches the ``WHERE`` intact and
    simply matches nothing.
    """
    awkward = "deribit:BTC' OR 1=1 --"
    query = _lake(_row(local_ts=_BASE_NS), _row(local_ts=_BASE_NS, symbol=awkward))
    matched = depth_from_book_snapshots(
        query,
        awkward,
        asset_class=AssetClass.CRYPTO,
        as_of_ns=_BASE_NS,
        top_n=2,
        max_age_ns=60 * _SECOND_NS,
    )
    assert matched.symbol == awkward

    with pytest.raises(ValueError, match="no stored book snapshot"):
        depth_from_book_snapshots(
            query,
            "deribit:NOTHING' OR 1=1 --",
            asset_class=AssetClass.CRYPTO,
            as_of_ns=_BASE_NS,
            top_n=2,
            max_age_ns=60 * _SECOND_NS,
        )


@pytest.mark.parametrize(("field", "value"), [("top_n", 0), ("max_age_ns", 0)])
def test_a_ladder_of_no_levels_or_a_window_of_no_width_is_rejected_by_name(
    field: str, value: int
) -> None:
    """Both are denominators of the confidence, so neither can be zero anywhere downstream.

    Caught here rather than left to the formula so the caller is told which parameter is
    wrong, instead of reading a ``ConfidenceInputError`` about an input they never supplied.
    """
    with pytest.raises(ValueError, match=field):
        _slice(_lake(_row(local_ts=_BASE_NS)), **{field: value})

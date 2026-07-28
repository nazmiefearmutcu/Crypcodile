"""The forward fill both halves of ``open-interest`` widen their samples through.

Exercised here directly as well as through the two aggregators, because it is the piece
that makes the capability *one* capability: the two callers disagree about what a series
is and about which channel it comes from, and agree on nothing else but this frame. A
change to the column order or to the starting value would be invisible in either half's
own suite until someone compared the two boards.
"""

from __future__ import annotations

import polars as pl
import pytest

from crocodile.core.analytics.open_interest import SeriesKey, align_open_interest

_T1, _T2, _T3 = 1_000, 2_000, 3_000


def test_the_frame_is_local_ts_then_sources_by_name_then_total() -> None:
    """The column order two implementations promise a surface, so it is stated once here."""
    frame = align_open_interest(
        [_T1],
        [("okx", "BTC"), ("binance", "BTC")],
        {_T1: {("binance", "BTC"): 3.0, ("okx", "BTC"): 4.0}},
    )
    assert frame.columns == ["local_ts", "binance", "okx", "total_oi"]
    assert frame.row(0, named=True) == {
        "local_ts": _T1,
        "binance": 3.0,
        "okx": 4.0,
        "total_oi": 7.0,
    }


def test_a_source_column_is_the_sum_of_that_sources_series() -> None:
    """One venue quoting several instruments is one column, not several."""
    keys: list[SeriesKey] = [("binance", "BTCUSDT"), ("binance", "ETHUSDT")]
    frame = align_open_interest(
        [_T1], keys, {_T1: {("binance", "BTCUSDT"): 100.0, ("binance", "ETHUSDT"): 7.0}}
    )
    assert frame["binance"].to_list() == pytest.approx([107.0])


def test_a_series_holds_its_last_value_across_instants_it_did_not_report_at() -> None:
    """Two sources on their own clocks share almost no timestamps.

    Without the fill, ``total_oi`` would sawtooth between one source's figure and the
    other's rather than reporting the market — which is the entire reason a board of
    several series needs an alignment at all.
    """
    keys: list[SeriesKey] = [("a", "X"), ("b", "X")]
    frame = align_open_interest(
        [_T1, _T2, _T3],
        keys,
        {_T1: {("a", "X"): 10.0}, _T2: {("b", "X"): 4.0}, _T3: {("a", "X"): 12.0}},
    )
    assert frame["a"].to_list() == pytest.approx([10.0, 10.0, 12.0])
    assert frame["b"].to_list() == pytest.approx([0.0, 4.0, 4.0])
    assert frame["total_oi"].to_list() == pytest.approx([10.0, 14.0, 16.0])


def test_a_series_that_has_not_reported_yet_contributes_zero_rather_than_a_null() -> None:
    """A column that is null until its first sample cannot be summed into ``total_oi``.

    0.0 is the honest reading here and not a fabrication: before a series' first
    observation this board has seen no open interest from it, and the alternative — a null
    that poisons the total, or dropping the leading rows — loses the other sources'
    genuine samples at those instants.
    """
    frame = align_open_interest(
        [_T1, _T2], [("a", "X"), ("b", "X")], {_T2: {("b", "X"): 5.0}}
    )
    assert frame["b"].to_list() == pytest.approx([0.0, 5.0])
    assert frame["total_oi"].to_list() == pytest.approx([0.0, 5.0])


def test_no_timestamps_yields_an_empty_frame() -> None:
    assert align_open_interest([], [], {}).is_empty()
    assert isinstance(align_open_interest([], [("a", "X")], {}), pl.DataFrame)

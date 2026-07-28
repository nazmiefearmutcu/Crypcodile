"""The band predicate both halves of ``liquidity-depth`` sum with.

The crypto half reads it over a stored book sequence and the equity half over the ladder M6
builds, so the sums themselves are pinned here once. The six band edges used to be six
literals written out inline (``mid * 0.99``, ``mid * 1.01``, …); the properties below are
the ones a mistyped literal would have broken silently, since a wrong edge returns a
plausible number rather than an error.
"""

from __future__ import annotations

import pytest

from crocodile.core.analytics.liquidity_depth import (
    DEPTH_BANDS,
    band_columns,
    depth_within_bands,
)

_BIDS = [(99.5, 10.0), (99.0, 20.0), (98.0, 40.0), (95.0, 80.0), (90.0, 160.0)]
_ASKS = [(100.5, 1.0), (101.0, 2.0), (102.0, 4.0), (105.0, 8.0), (110.0, 16.0)]
_REF = 100.0


def test_the_columns_are_the_keys_and_the_keys_are_the_columns() -> None:
    """One source for the name list, so an empty frame and a populated one cannot differ."""
    assert set(band_columns()) == set(depth_within_bands(_BIDS, _ASKS, _REF))
    assert band_columns() == (
        "bid_depth_1pct",
        "ask_depth_1pct",
        "bid_depth_2pct",
        "ask_depth_2pct",
        "bid_depth_5pct",
        "ask_depth_5pct",
    )


def test_each_band_sums_the_size_standing_inside_it() -> None:
    sums = depth_within_bands(_BIDS, _ASKS, _REF)
    # 1%: bids at 99.5 and 99.0 (>= 99.0); asks at 100.5 and 101.0 (<= 101.0).
    assert sums["bid_depth_1pct"] == pytest.approx(30.0)
    assert sums["ask_depth_1pct"] == pytest.approx(3.0)
    # 2%: adds the 98.0 bid and the 102.0 ask.
    assert sums["bid_depth_2pct"] == pytest.approx(70.0)
    assert sums["ask_depth_2pct"] == pytest.approx(7.0)
    # 5%: adds the 95.0 bid and the 105.0 ask; 90.0 and 110.0 stay outside.
    assert sums["bid_depth_5pct"] == pytest.approx(150.0)
    assert sums["ask_depth_5pct"] == pytest.approx(15.0)


def test_the_bands_nest_because_the_edges_are_inclusive() -> None:
    """Everything inside 1 % is inside 2 % is inside 5 %.

    The property a mistyped edge breaks first, and the reason ``>=``/``<=`` rather than
    strict comparisons: a level sitting exactly on the 1 % edge belongs to the 1 % band, so
    the bands are a chain of supersets rather than three overlapping windows.
    """
    sums = depth_within_bands(_BIDS, _ASKS, _REF)
    for side in ("bid", "ask"):
        widths = [sums[f"{side}_depth_{round(band * 100)}pct"] for band in DEPTH_BANDS]
        assert widths == sorted(widths), f"{side} bands do not nest: {widths}"


def test_a_level_exactly_on_an_edge_is_inside_it() -> None:
    sums = depth_within_bands([(99.0, 5.0)], [(101.0, 7.0)], 100.0)
    assert sums["bid_depth_1pct"] == pytest.approx(5.0)
    assert sums["ask_depth_1pct"] == pytest.approx(7.0)


def test_a_crossed_book_is_counted_rather_than_filtered_out() -> None:
    """A bid above the reference is a real state a venue publishes.

    Excluding it would report a locked or crossed market as having no near depth, which is
    the opposite of what a crossed book means. The band is a price window around the
    reference, not a claim about which side of it a level may sit on.
    """
    sums = depth_within_bands([(100.4, 5.0)], [(99.6, 5.0)], 100.0)
    assert sums["bid_depth_1pct"] == pytest.approx(5.0)
    assert sums["ask_depth_1pct"] == pytest.approx(5.0)


def test_a_non_positive_reference_is_refused_rather_than_summed_to_zero() -> None:
    """Every band is multiplicative, so a zero centre collapses all of them onto it.

    The sums would come back 0.0 for a book that may be full — a fabricated "no liquidity"
    reading, in the same shape the provenance gates exist to catch one layer up.
    """
    with pytest.raises(ValueError, match="reference_price must be positive"):
        depth_within_bands(_BIDS, _ASKS, 0.0)


@pytest.mark.parametrize("band", [0.0, -0.01, 1.5])
def test_a_band_outside_the_open_unit_interval_is_refused(band: float) -> None:
    with pytest.raises(ValueError, match=r"band must be in \(0, 1\]"):
        depth_within_bands(_BIDS, _ASKS, _REF, bands=(band,))


def test_an_empty_side_sums_to_zero_rather_than_raising() -> None:
    """A one-sided ladder is a state both markets produce, and zero size is the true sum."""
    sums = depth_within_bands([], _ASKS, _REF)
    assert sums["bid_depth_5pct"] == pytest.approx(0.0)
    assert sums["ask_depth_5pct"] == pytest.approx(15.0)

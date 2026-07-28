"""The order-flow imbalance definition itself, away from either asset class's read.

``crypto/analytics/ofi.py`` and ``equity/analytics/ofi.py`` differ only in which channel
they pull four numbers off. The four numbers are the measurement, and this file is where the
measurement is pinned — so a regression in it fails once, by name, rather than twice in two
lake-building integration tests that would each blame their own reader.
"""

from __future__ import annotations

import polars as pl
import pytest

from crocodile.core.analytics.ofi import OFI_SCHEMA, TopOfBook, bin_ofi, ofi_increment

_SEC = 1_000_000_000
_T0 = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC


def _naive(prev: TopOfBook, curr: TopOfBook) -> float:
    """The statistic OFI is not: size differences with no conditioning on price.

    Written here rather than described, because the whole claim of ``ofi_increment`` is that
    these two disagree on the cases that matter, and a claim about a function nobody calls
    is a claim nobody checks.
    """
    return (curr.bid_sz - prev.bid_sz) - (curr.ask_sz - prev.ask_sz)


def test_a_bid_that_steps_up_contributes_its_whole_new_size() -> None:
    """New demand at a better price is new demand, not the difference against the old queue."""
    prev = TopOfBook(bid_px=100.0, bid_sz=2.0, ask_px=101.0, ask_sz=1.0)
    curr = TopOfBook(bid_px=101.0, bid_sz=4.0, ask_px=102.0, ask_sz=1.0)
    # Bid stepped up: +4. Ask stepped up (worsened): -1 removed, so -(-1) = +1.
    assert ofi_increment(prev, curr) == pytest.approx(5.0)


def test_a_bid_that_steps_down_removes_its_whole_old_size() -> None:
    """The queue that was there is gone; what stands one tick lower is a different queue."""
    prev = TopOfBook(bid_px=101.0, bid_sz=4.0, ask_px=102.0, ask_sz=1.0)
    curr = TopOfBook(bid_px=100.0, bid_sz=2.0, ask_px=102.0, ask_sz=1.0)
    # Bid fell: -4. Ask unchanged in price and size: 0.
    assert ofi_increment(prev, curr) == pytest.approx(-4.0)


def test_a_held_price_contributes_only_the_size_difference() -> None:
    prev = TopOfBook(bid_px=100.0, bid_sz=2.0, ask_px=101.0, ask_sz=1.0)
    curr = TopOfBook(bid_px=100.0, bid_sz=3.0, ask_px=101.0, ask_sz=2.0)
    assert ofi_increment(prev, curr) == pytest.approx(0.0)


def test_the_ask_side_is_the_mirror_image_because_an_ask_improves_by_falling() -> None:
    """An ask stepping *down* is new supply; the bid rule with the inequality reversed."""
    prev = TopOfBook(bid_px=100.0, bid_sz=1.0, ask_px=102.0, ask_sz=3.0)
    curr = TopOfBook(bid_px=100.0, bid_sz=1.0, ask_px=101.0, ask_sz=5.0)
    # Bid unchanged: 0. Ask improved: +5 of new supply, entering as -5 of imbalance.
    assert ofi_increment(prev, curr) == pytest.approx(-5.0)


@pytest.mark.parametrize(
    ("prev", "curr", "ofi", "naive"),
    [
        (
            TopOfBook(100.0, 2.0, 101.0, 1.0),
            TopOfBook(101.0, 4.0, 101.0, 1.0),
            4.0,
            2.0,
        ),
        (
            TopOfBook(101.0, 4.0, 102.0, 1.0),
            TopOfBook(100.0, 2.0, 102.0, 1.0),
            -4.0,
            -2.0,
        ),
    ],
)
def test_dropping_the_price_conditioning_is_a_different_statistic(
    prev: TopOfBook, curr: TopOfBook, ofi: float, naive: float
) -> None:
    """The failure mode the definition exists to prevent, measured rather than asserted.

    A queue that emptied and refilled one tick away reads as a mild size change without the
    conditioning and as the whole queue leaving with it. Both versions return a number and
    neither raises, which is why the wrong one is easy to ship: it is only visible against
    the right one.
    """
    assert ofi_increment(prev, curr) == pytest.approx(ofi)
    assert _naive(prev, curr) == pytest.approx(naive)
    assert ofi_increment(prev, curr) != pytest.approx(_naive(prev, curr))


def test_bins_are_aligned_to_the_callers_window_and_not_to_the_epoch() -> None:
    """A caller asking for 15s from T0 gets bins starting at T0, not at the last epoch tick."""
    start = _T0 + 7 * _SEC
    tops = [
        (start, TopOfBook(100.0, 1.0, 101.0, 1.0)),
        (start + 3 * _SEC, TopOfBook(100.0, 4.0, 101.0, 1.0)),
        (start + 20 * _SEC, TopOfBook(100.0, 5.0, 101.0, 1.0)),
    ]
    frame = bin_ofi(tops, start_ns=start, interval_ns=15 * _SEC)
    assert frame["timestamp"].to_list() == [start, start + 15 * _SEC]
    assert frame["ofi"].to_list() == pytest.approx([3.0, 1.0])


def test_the_last_step_in_a_bin_supplies_the_prices_the_bin_closed_on() -> None:
    tops = [
        (_T0, TopOfBook(100.0, 1.0, 101.0, 1.0)),
        (_T0 + _SEC, TopOfBook(100.0, 2.0, 101.0, 1.0)),
        (_T0 + 2 * _SEC, TopOfBook(103.0, 2.0, 104.0, 1.0)),
    ]
    frame = bin_ofi(tops, start_ns=_T0, interval_ns=60 * _SEC)
    assert len(frame) == 1
    assert frame["best_bid"][0] == pytest.approx(103.0)
    assert frame["best_ask"][0] == pytest.approx(104.0)


def test_observations_out_of_order_are_sorted_before_they_are_differenced() -> None:
    """Not a tidiness rule. An increment is a function of two *consecutive* observations,
    so a shuffled frame would produce a plausible number against the wrong predecessor
    rather than an error anybody notices."""
    ordered = [
        (_T0, TopOfBook(100.0, 1.0, 101.0, 1.0)),
        (_T0 + _SEC, TopOfBook(101.0, 4.0, 102.0, 1.0)),
        (_T0 + 2 * _SEC, TopOfBook(100.0, 2.0, 101.0, 3.0)),
    ]
    shuffled = [ordered[2], ordered[0], ordered[1]]
    assert bin_ofi(shuffled, start_ns=_T0, interval_ns=_SEC).equals(
        bin_ofi(ordered, start_ns=_T0, interval_ns=_SEC)
    )


@pytest.mark.parametrize("count", [0, 1])
def test_fewer_than_two_observations_is_an_empty_table_and_not_an_empty_frame(
    count: int,
) -> None:
    """An imbalance is defined over a step, and a quiet window is still a table.

    The columns matter as much as the row count: ``crypto/analytics/ofi.py`` used to return
    a bare ``pl.DataFrame()`` here, so ``df["ofi"]`` on a symbol with no book raised
    ``ColumnNotFound`` instead of yielding nothing.
    """
    tops = [(_T0, TopOfBook(100.0, 1.0, 101.0, 1.0))][:count]
    frame = bin_ofi(tops, start_ns=_T0, interval_ns=_SEC)
    assert frame.is_empty()
    assert frame.schema == pl.Schema(OFI_SCHEMA)


def test_a_non_positive_interval_is_refused_rather_than_dividing_by_zero() -> None:
    tops = [
        (_T0, TopOfBook(100.0, 1.0, 101.0, 1.0)),
        (_T0 + _SEC, TopOfBook(100.0, 2.0, 101.0, 1.0)),
    ]
    with pytest.raises(ValueError, match="interval_ns must be positive"):
        bin_ofi(tops, start_ns=_T0, interval_ns=0)

"""The equity chaos composite: the same arithmetic, two re-specified terms, honest weights.

Two claims carry this half and both are checkable, which is why they are here rather than
only in the module docstring. The first is that nothing was re-tuned: for four finite
readings the equity index is the crypto index, to the last bit. The second is that a term
with no reading is dropped rather than invented — the divergence the first claim would
otherwise hide, and the one the crypto half gets wrong in two opposite directions at once.
"""

from __future__ import annotations

import math

import pytest

from crocodile.crypto.analytics.risk import calculate_chaos_score
from crocodile.equity.analytics.chaos import EQUITY_TERMS, chaos_score_equities

_FIELDS = ("volatility", "stablecoin_deviation", "orderbook_imbalance", "sequencer_delay")

_FINITE_CASES = (
    (0.0, 0.0, 0.0, 0.0),
    (0.01, 0.001, 0.05, 0.25),
    (0.1, 0.01, 0.5, 5.0),
    (0.09, 0.05, 0.95, 30.0),
    (1e9, 1e9, 1.0, 1e9),
    # Negative readings: an ask-heavy book is as lopsided as a bid-heavy one, and both
    # halves take the magnitude before squashing.
    (-0.04, -0.02, -0.7, -2.0),
    # An imbalance outside its declared [-1, 1] range, which both halves clamp.
    (0.02, 0.0, 4.0, 0.0),
)


@pytest.mark.parametrize("readings", _FINITE_CASES)
def test_four_finite_readings_reproduce_the_crypto_index(
    readings: tuple[float, float, float, float],
) -> None:
    """The claim that the thresholds were not re-tuned, diffed as arithmetic.

    The equity half re-specifies what two of the four fields *mean* and keeps every constant
    the crypto half squashes them with — 0.1 for volatility, 0.01 for the band deviation,
    5.0 seconds for latency, and ``min(1, |x|)`` for the already-bounded imbalance. A second
    calibration under one capability name would produce two indices sharing a scale, a range
    and a name while measuring different things, and no consumer could tell which they held.
    Comparing the numbers is what stops that happening by accident later: a constant edited
    on one side fails against the other rather than quietly rescaling half the product.

    The tolerance is one part in 1e12 rather than exact equality, and it is a floating-point
    fact rather than slack. CPython 3.12's ``sum()`` compensates its float additions and the
    crypto half writes ``(a + b + c + d) / 4.0``, which does not; on the fourth case below
    they differ by one unit in the last place, with this side correctly rounded. Adopting
    the less accurate sum to force bit-equality was the alternative and is the wrong
    direction — the comparison exists to catch a re-tuned constant, and a re-tuned constant
    moves a term by percent, not by an ULP.
    """
    supplied = dict(zip(_FIELDS, readings, strict=True))
    equity = chaos_score_equities(**supplied)
    assert equity["chaos_score"] == pytest.approx(calculate_chaos_score(**supplied), rel=1e-12)


def test_every_term_carries_its_weight_its_reading_and_what_that_reading_means() -> None:
    result = chaos_score_equities(
        volatility=0.02, stablecoin_deviation=0.005, orderbook_imbalance=0.3, sequencer_delay=1.0
    )
    assert set(result["terms"]) == set(_FIELDS)
    for term in EQUITY_TERMS:
        entry = result["terms"][term.field]
        assert entry["weight"] == pytest.approx(0.25)
        assert 0.0 <= entry["normalised"] <= 1.0
        assert entry["reads"] == term.reads
    assert sum(e["weight"] for e in result["terms"].values()) == pytest.approx(1.0)


def test_a_reading_that_is_not_a_number_is_dropped_and_its_weight_redistributed() -> None:
    """The one place the two halves genuinely differ, and the reason it is this way round.

    ``calculate_realized_volatility`` and ``calculate_beta`` in this same package return
    ``float("nan")`` for a series too short to measure, so a caller piping equity analytics
    into this capability meets NaN on the ordinary path rather than on an exotic one. The
    crypto half would score that NaN as 0.0 — a quarter of the index asserting calm about a
    quantity nobody measured.
    """
    three = chaos_score_equities(
        volatility=float("nan"),
        stablecoin_deviation=0.01,
        orderbook_imbalance=1.0,
        sequencer_delay=5.0,
    )
    assert three["terms"]["volatility"]["normalised"] is None
    assert three["terms"]["volatility"]["weight"] == 0.0
    for field in ("stablecoin_deviation", "orderbook_imbalance", "sequencer_delay"):
        assert three["terms"][field]["weight"] == pytest.approx(1.0 / 3.0)
    # 0.5 + 1.0 + 0.5 over three terms, not over four.
    assert three["chaos_score"] == pytest.approx(200.0 / 3.0)


def test_dropping_a_term_is_not_the_same_as_reading_it_as_calm() -> None:
    """The measurement behind the divergence, so it is a number rather than an opinion."""
    supplied = {
        "stablecoin_deviation": 0.01,
        "orderbook_imbalance": 1.0,
        "sequencer_delay": 5.0,
    }
    dropped = chaos_score_equities(volatility=float("nan"), **supplied)["chaos_score"]
    as_calm = calculate_chaos_score(volatility=float("nan"), **supplied)
    assert dropped == pytest.approx(66.67, abs=0.01)
    assert as_calm == pytest.approx(50.0)
    assert dropped > as_calm


def test_an_infinite_reading_is_no_reading_either() -> None:
    """The crypto half returns NaN for it — ``inf / (inf + k)`` — which is not in [0, 100].

    Excluding it keeps the index inside the range the capability's summary promises, and
    says the same thing about the input that NaN says: nothing was measured.
    """
    result = chaos_score_equities(
        volatility=math.inf,
        stablecoin_deviation=0.01,
        orderbook_imbalance=0.0,
        sequencer_delay=0.0,
    )
    assert result["terms"]["volatility"]["weight"] == 0.0
    assert 0.0 <= result["chaos_score"] <= 100.0
    assert math.isnan(
        calculate_chaos_score(
            volatility=math.inf,
            stablecoin_deviation=0.01,
            orderbook_imbalance=0.0,
            sequencer_delay=0.0,
        )
    )


def test_no_finite_reading_at_all_is_refused_rather_than_scored_zero() -> None:
    """An index over no terms is not 0.0.

    ``ChaosScoreParams`` already refuses this at the parameter layer by making all four
    fields required — the CLI and REST both defaulted them to 0.0, which reported "perfectly
    calm" for a market nobody looked at. Four NaNs are the same request wearing a different
    spelling, and they get the same answer.
    """
    with pytest.raises(ValueError, match="no finite reading"):
        chaos_score_equities(
            volatility=float("nan"),
            stablecoin_deviation=float("nan"),
            orderbook_imbalance=float("nan"),
            sequencer_delay=float("nan"),
        )


def test_the_index_stays_inside_the_range_its_summary_promises() -> None:
    saturated = chaos_score_equities(
        volatility=1e12, stablecoin_deviation=1e12, orderbook_imbalance=5.0, sequencer_delay=1e12
    )
    assert saturated["chaos_score"] == pytest.approx(100.0)
    calm = chaos_score_equities(
        volatility=0.0, stablecoin_deviation=0.0, orderbook_imbalance=0.0, sequencer_delay=0.0
    )
    assert calm["chaos_score"] == pytest.approx(0.0)


def test_the_two_re_specified_terms_are_calibrated_by_the_constants_they_inherited() -> None:
    """Why keeping the crypto constants costs nothing once the unit is stated.

    A 1 % departure from the LULD reference is half-chaotic and the 5 % Tier-1 band edge —
    where the security halts — is 0.83; an ordinary large-cap session standard deviation of
    1 % is 0.09 and March 2020's 9 % is 0.47; a one-second-stale quote is 0.17 and a
    thirty-second-stale one 0.86. Those readings are the argument for not re-tuning, so they
    are asserted rather than left in prose where nothing checks them.
    """

    def _term(field: str, reading: float) -> float:
        supplied = dict.fromkeys(_FIELDS, 0.0)
        supplied[field] = reading
        value = chaos_score_equities(**supplied)["terms"][field]["normalised"]
        assert isinstance(value, float)
        return value

    assert _term("stablecoin_deviation", 0.01) == pytest.approx(0.5)
    assert _term("stablecoin_deviation", 0.05) == pytest.approx(0.833, abs=0.001)
    assert _term("volatility", 0.01) == pytest.approx(0.091, abs=0.001)
    assert _term("volatility", 0.09) == pytest.approx(0.474, abs=0.001)
    assert _term("sequencer_delay", 1.0) == pytest.approx(0.167, abs=0.001)
    assert _term("sequencer_delay", 30.0) == pytest.approx(0.857, abs=0.001)


def test_every_term_says_what_it_is_read_as_for_equities() -> None:
    """Two of the four subjects are re-specified, so a bare field name is not enough.

    ``stablecoin_deviation`` and ``sequencer_delay`` name phenomena ``IRREDUCIBLE`` says
    have no equity form, and what fills those slots here is a different measurement in the
    same role. A caller reading the result has to be able to see that without finding the
    module, which is what the sentence on each term is for.
    """
    reads = {term.field: term.reads for term in EQUITY_TERMS}
    assert "LULD" in reads["stablecoin_deviation"]
    assert "latency" in reads["sequencer_delay"]
    assert all(text.strip() for text in reads.values())

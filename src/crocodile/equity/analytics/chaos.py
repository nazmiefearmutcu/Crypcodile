"""The equity reading of the chaos index — four stress terms, re-specified and re-weighted.

``chaos-score`` blends four readings into one soft-thresholded number in ``[0, 100]``. Its
crypto half is :func:`~crocodile.crypto.analytics.risk.calculate_chaos_score`, and the
capability's parameter struct is shared by both asset classes, so this half is handed the
same four numbers under the same four crypto-shaped field names. That makes the equity work
a question about *meaning* rather than about arithmetic: what does a market supply for each
of these four slots when it has no stablecoins and no sequencer, and what does the composite
do when one of the four has no reading at all.

What each field reads as, for equities
--------------------------------------

``volatility`` — unchanged in kind. A return standard deviation over the window, as a
fraction. Both markets have one and neither had to invent it; this is the term the ledger
entry in ``crocodile.capabilities.analytics`` called "computable from equity bars today".

``stablecoin_deviation`` — **re-specified**, to the fraction by which a price has departed
from the reference its own price band is measured against: ``|last - reference| /
reference``, where the reference is the National Market System limit up-limit down plan's
rolling five-minute average. The role the crypto term plays is "the mechanism that is
supposed to hold this price near a reference is straining", and equities have such a
mechanism — it is a regulatory circuit breaker rather than an issuer's redemption promise,
which is precisely why this is a re-specification and not a port.

It is also why re-specifying it does not disprove ``peg-deviation``'s entry on
:data:`~crocodile.core.capability.IRREDUCIBLE`. That entry says no equity instrument
behaves like a stablecoin, and it is right: a stablecoin's peg is to a *constant* nominal
value that an issuer undertakes to redeem at, and nothing listed on an exchange promises
that. An LULD reference is not a peg — it floats, it is recomputed every five minutes, and
no one redeems against it. The capability ``peg-deviation`` still has no equity
implementation and this module does not give it one; what is borrowed is the *slot in a
composite*, not the measurement.

``orderbook_imbalance`` — unchanged in kind, and the reason this whole capability was
scheduled against M6. It wants both sides' resting size, which equities had no source for
until :func:`~crocodile.equity.depth.select_depth_source` existed. The reading is the
signed size imbalance in ``[-1, 1]``, which ``liquidity-depth``'s equity half now reports
the ingredients of for any band a caller cares about.

``sequencer_delay`` — **re-specified**, to consolidated-tape latency in seconds: how stale
the freshest quote was when it reached the observer, which is ``local_ts - source_ts`` on a
:class:`~crocodile.core.schema.records.Quote`. The crypto half's own dynamic path computes
exactly that expression over ``book_ticker``
(``crocodile.crypto.analytics.risk.calculate_dynamic_chaos_score``, step 5), so the
arithmetic is not being invented here — only its subject is being named for a market that
has no sequencer. ``sequencer-latency``'s ``IRREDUCIBLE`` entry survives this for the same
reason ``peg-deviation``'s does: that capability reports an L2 sequencer's block cadence,
which is a property of a chain, and nothing here reports one.

Why the four thresholds are the crypto half's, unchanged
--------------------------------------------------------

Each term is squashed by ``x / (x + k)`` with the ``k`` the crypto half uses — 0.1 for
volatility, 0.01 for the band deviation, 5.0 seconds for latency — and the imbalance term is
``min(1, |x|)``, which is already bounded. Re-tuning them was considered and rejected: a
chaos score is comparable with nothing but another chaos score, so a second calibration
under one capability name would produce two indices that share a scale, a name and a range
while measuring different things, and no consumer could tell which one it held.

Keeping them costs nothing because the ``k`` values turn out to be calibrated for equities
once the reading's *unit* is stated, which is what the field documentation now does. A
per-session return standard deviation of 1 %, which is an ordinary large-cap day, scores
0.09; the 9 % daily standard deviation of March 2020 scores 0.47. A 1 % departure from the
LULD reference scores 0.5, and the 5 % Tier-1 band edge — where the security halts — scores
0.83. A one-second-stale quote scores 0.17 and a thirty-second-stale one 0.86. Those are the
numbers a stress index should give for those states, and none of them was chosen here.

The one place the two halves genuinely differ
---------------------------------------------

A reading that is not a finite number is **excluded**, and the remaining terms are
re-weighted to divide the whole index between them. The crypto half maps a NaN volatility,
deviation or delay to ``0.0`` — "perfectly calm" — and a NaN imbalance to ``1.0`` — maximally
chaotic. Those are two opposite inventions from one absence, in one function, and both of
them are the fabricated reading ``ChaosScoreParams`` was written to refuse when it made all
four fields required rather than defaulting them to zero.

Refusing matters more for equities than it would for crypto, because this tree's equity
analytics *produce* NaN as their "not enough data" answer:
:func:`~crocodile.equity.analytics.metrics.calculate_realized_volatility` and
:func:`~crocodile.equity.analytics.calculate_beta` both return ``float("nan")`` for a
series too short to measure. A caller piping those into this capability is the expected
path, not an edge case, and turning their NaN into 25 points of calm is exactly the
zero-standing-in-for-a-hole one layer up.

So the weights are data-dependent, which is why they are returned rather than documented:
four readings weigh 0.25 each and reproduce the crypto half's number exactly, three weigh
one third, and a caller can see from the result which terms the score was actually built
from. That is also why this half returns an object where the crypto half returns a bare
float — its weights are constant, so it has nothing to publish, and a scalar that never
varies is better left off the wire.
"""

from __future__ import annotations

import math
from typing import Any, Final, NamedTuple

__all__ = ["EQUITY_TERMS", "Term", "chaos_score_equities"]


class Term(NamedTuple):
    """One term of the composite: the field it reads, what that field means, and its scale."""

    field: str
    """The :class:`~crocodile.capabilities.analytics.ChaosScoreParams` field it consumes."""

    reads: str
    """What a caller must put there for equities, unit included. Returned with the score, so
    a reader of the result does not have to find this module to interpret a weight."""

    half_point: float | None
    """The ``k`` in ``x / (x + k)``: the reading at which this term scores 0.5. ``None`` for
    a term that arrives already bounded to ``[-1, 1]`` and is squashed by ``min(1, |x|)``
    instead — the crypto half's treatment of the imbalance, kept."""


EQUITY_TERMS: Final[tuple[Term, ...]] = (
    Term(
        field="volatility",
        reads="return standard deviation over the window, as a fraction (per session)",
        half_point=0.1,
    ),
    Term(
        field="stablecoin_deviation",
        reads="departure from the LULD reference price, as a fraction of it",
        half_point=0.01,
    ),
    Term(
        field="orderbook_imbalance",
        reads="signed depth-ladder size imbalance in [-1, 1]",
        half_point=None,
    ),
    Term(
        field="sequencer_delay",
        reads="consolidated-tape latency in seconds (local_ts - source_ts)",
        half_point=5.0,
    ),
)
"""The four terms in the order the index averages them, with their equity subjects.

A tuple rather than four constants because the weighting divides one whole across whichever
of them was read, so the set has to be enumerable at runtime rather than only in prose.
"""

_FULL_SCALE: Final[float] = 100.0
"""The index's upper end. The crypto half's ``/ 4.0 * 100.0`` says the same thing for a
composite that always has four terms; stated separately here because the divisor is no
longer fixed."""


def _normalise(term: Term, reading: float) -> float:
    """Squash one reading into ``[0, 1]``, by the crypto half's arithmetic exactly.

    Sign is dropped first: every term is a magnitude of stress, and a bid-heavy book is as
    lopsided as an ask-heavy one. That is ``calculate_chaos_score``'s ``abs()`` on all four,
    kept so the two halves cannot disagree about a negative imbalance.
    """
    magnitude = abs(reading)
    if term.half_point is None:
        return min(1.0, magnitude)
    if magnitude <= 0.0:
        return 0.0
    return magnitude / (magnitude + term.half_point)


def chaos_score_equities(
    *,
    volatility: float,
    stablecoin_deviation: float,
    orderbook_imbalance: float,
    sequencer_delay: float,
) -> dict[str, Any]:
    """Blend the four equity stress readings into an index, over the terms that were read.

    Returns a mapping with ``chaos_score`` — the index in ``[0, 100]`` — and ``terms``, one
    entry per :data:`EQUITY_TERMS` member carrying what was supplied, what it normalised to,
    the weight it received, and the sentence saying what that field is read as for equities.
    An excluded term is present with ``normalised: None`` and ``weight: 0.0``, so a dropped
    reading is visible in the answer rather than only in the number's being lower.

    Raises:
        ValueError: if none of the four readings is finite. There is no composite of nothing:
            returning 0.0 would report a market nobody measured as perfectly calm, which is
            the defaulting ``ChaosScoreParams`` already refuses at the parameter layer, and
            returning NaN would put a non-number in a field documented as ``[0, 100]``.
    """
    supplied: dict[str, float] = {
        "volatility": volatility,
        "stablecoin_deviation": stablecoin_deviation,
        "orderbook_imbalance": orderbook_imbalance,
        "sequencer_delay": sequencer_delay,
    }
    read = [term for term in EQUITY_TERMS if math.isfinite(supplied[term.field])]
    if not read:
        raise ValueError(
            "chaos-score was given no finite reading in any of "
            f"{[term.field for term in EQUITY_TERMS]}; an index over no terms is not 0.0, "
            "which would report a market nobody measured as perfectly calm"
        )

    weight = 1.0 / len(read)
    normalised = {term.field: _normalise(term, supplied[term.field]) for term in read}
    # Summed and divided rather than weighted term by term, so that the four-term case is
    # the mean `calculate_chaos_score` computes rather than a rearrangement of it.
    #
    # It is still not bit-identical to that function, and the reason is worth stating where
    # someone comparing the two will hit it: CPython 3.12's `sum()` applies Neumaier
    # compensation to floats, while the crypto half writes `(a + b + c + d) / 4.0` and gets
    # plain left-to-right addition. On the readings (0.09, 0.05, 0.95, 30.0) that is
    # 77.85401002506265 here against 77.85401002506266 there — one unit in the last place,
    # with this side being the correctly rounded one. Reaching for `functools.reduce` to
    # match the other half exactly was the alternative and it is the wrong direction: it
    # would adopt a less accurate sum to satisfy a comparison, and the comparison exists to
    # catch a re-tuned *constant*, which a last-bit tolerance still catches.
    score = sum(normalised.values()) / len(read) * _FULL_SCALE

    return {
        "chaos_score": score,
        "terms": {
            term.field: {
                "supplied": supplied[term.field],
                "normalised": normalised.get(term.field),
                "weight": weight if term.field in normalised else 0.0,
                "reads": term.reads,
            }
            for term in EQUITY_TERMS
        },
    }

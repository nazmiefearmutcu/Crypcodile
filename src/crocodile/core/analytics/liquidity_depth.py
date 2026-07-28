"""Cumulative resting size inside a percentage band of a reference price.

``liquidity-depth`` answers one question — how much size stands within *n* percent of the
middle — and the arithmetic that answers it is six sums over two lists. It lives here
rather than in either asset class because M6 gives the question a second caller: the crypto
half sums the levels of a stored :class:`~crocodile.core.schema.records.BookSnapshot` per
book sequence, and the equity half sums the levels of the ladder
:func:`~crocodile.equity.depth.select.select_depth_source` produces. Same bands, same
predicate, two ladders.

**Why the band edges are a parameter of the reference and not of the book.** A level counts
when its price is within ``band`` of the reference *multiplicatively* — ``price >= ref *
(1 - band)`` on the bid side, ``price <= ref * (1 + band)`` on the ask side — which is what
makes a 1 % band on a $4 stock and a 1 % band on a $400 one the same question. The crypto
implementation this generalises used exactly that form, spelled as three hardcoded pairs of
literals (``mid_price * 0.99``, ``* 1.01``, ``* 0.98`` …); a hardcoded pair per band is a
place for the two sides of one band to stop being symmetric, and one of the six literals
being mistyped would have produced a plausible number rather than an error.

**What is *not* shared: which price is the reference.** Crypto takes the mid of the touch,
because a stored book has a real best bid and a real best ask. The equity ladder takes the
profile's own ``reference_price``, which is that same mid on the Alpaca L1 branch and the
last close on the synthetic volume-at-price branch — where the two innermost buckets are
histogram bins either side of the close rather than quotes anyone posted, so their mid is
not a price the market ever showed. Each caller therefore states its reference and this
module does not guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from crocodile.core.schema.records import Level

__all__ = ["DEPTH_BANDS", "band_columns", "depth_within_bands"]

DEPTH_BANDS: Final[tuple[float, ...]] = (0.01, 0.02, 0.05)
"""The 1 %, 2 % and 5 % bands both asset classes report.

The three the crypto implementation shipped and the three named in ``liquidity-depth``'s
summary, so they are the capability's contract rather than a default: changing them changes
the columns every surface publishes.
"""


def band_columns(bands: Sequence[float] = DEPTH_BANDS) -> tuple[str, ...]:
    """Return the column names ``depth_within_bands`` fills, in order.

    Exposed so a caller building an empty frame declares the same columns a populated one
    carries, from one source. Two spellings of this list is how an empty result comes back
    with a column set a populated result does not have.
    """
    names: list[str] = []
    for band in bands:
        names.append(f"bid_depth_{_band_label(band)}")
        names.append(f"ask_depth_{_band_label(band)}")
    return tuple(names)


def _band_label(band: float) -> str:
    """Spell a fractional band as the percentage suffix the columns are named for.

    ``0.01`` becomes ``1pct``. Rounded rather than truncated, because ``int(0.29 * 100)`` is
    28 in binary floating point and a column silently named for a band it does not describe
    is worse than a rejected one.
    """
    return f"{round(band * 100)}pct"


def depth_within_bands(
    bids: Sequence[Level],
    asks: Sequence[Level],
    reference_price: float,
    bands: Sequence[float] = DEPTH_BANDS,
) -> dict[str, float]:
    """Sum the size standing within each band of ``reference_price``, per side.

    Returns ``{"bid_depth_1pct": …, "ask_depth_1pct": …, …}`` in :func:`band_columns` order.
    A level exactly on a band edge is inside it, which is the crypto implementation's
    ``>=``/``<=`` and the reading that makes the bands nest: everything in the 1 % band is
    also in the 2 % one.

    Levels on the wrong side of the reference are summed if they fall in the band, and this
    is deliberate. A bid above the mid is a crossed book and a real state a venue can
    publish; excluding it would report a locked market as having no near depth, which is the
    opposite of what a crossed book means.

    Raises:
        ValueError: if ``reference_price`` is not positive, or a band is not in ``(0, 1]``.
            A non-positive reference makes every multiplicative band collapse onto it, so
            the sums would come back zero — a fabricated "no liquidity" reading over a book
            that may be full. Refusing is the loud form of the same fact.
    """
    if reference_price <= 0.0:
        raise ValueError(f"reference_price must be positive, got {reference_price}")
    out: dict[str, float] = {}
    for band in bands:
        if not 0.0 < band <= 1.0:
            raise ValueError(f"band must be in (0, 1], got {band}")
        floor = reference_price * (1.0 - band)
        ceiling = reference_price * (1.0 + band)
        label = _band_label(band)
        out[f"bid_depth_{label}"] = float(sum(size for price, size in bids if price >= floor))
        out[f"ask_depth_{label}"] = float(sum(size for price, size in asks if price <= ceiling))
    return out

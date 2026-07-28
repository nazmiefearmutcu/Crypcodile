"""Order Flow Imbalance (OFI) analytics (Task R2).

Calculates the OFI index over time-binned intervals using historical book snapshots.
"""

from __future__ import annotations

import polars as pl

from crocodile.core.analytics.ofi import OFI_SCHEMA, TopOfBook, bin_ofi
from crocodile.core.resample._interval import parse_interval
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.rows import _coerce_levels_from_row

__all__ = ["calculate_ofi", "parse_interval_to_ns"]


def parse_interval_to_ns(interval_str: str) -> int:
    """Parse interval duration string (e.g. ``'1s'``, ``'5m'``, ``'1h'``) to nanoseconds.

    Args:
        interval_str: Interval duration — digits followed by ``s``, ``m``, ``h``, ``d`` or
            ``w``.

    Returns:
        Interval duration in nanoseconds.

    Raises:
        ValueError: the string is not a quantity followed by a supported unit.

    **One capability cannot have two vocabularies.** This used to be a hand-rolled parser
    accepting ``s/m/h/d``, while the equity half of the same ``ofi`` capability
    (``equity/analytics/ofi.py``) parses with
    :func:`~crocodile.core.resample._interval.parse_interval`, which also accepts ``w``. So
    ``ofi --interval 1w`` answered for equities and raised ``Unknown interval unit 'w'`` for
    crypto — one wire schema, one ``OfiParams.interval`` field, two grammars, and a split
    that no gate keyed on *type* could see, because both spellings are ``str``.

    Widening rather than narrowing, for two reasons. A weekly OFI bin is a real question and
    the equity half already answers it, so narrowing would remove a working answer to make a
    disagreement symmetric. And the shared parser is the one the resamplers use, so ``1w``
    means the same width and the same Monday-anchored grid here as it does everywhere else —
    which is the property a second implementation could not have promised.

    The name stays: it is public API, pinned by the pre-merge surface inventory
    (``tests/conformance/premerge_public_api.json``). What is behind it is now the same
    function the rest of the tree parses intervals with.
    """
    return parse_interval(interval_str).ns


def calculate_ofi(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    interval: str,
) -> pl.DataFrame:
    """Calculate the Order Flow Imbalance (OFI) index over time-binned intervals.

    Args:
        catalog: A :class:`~crocodile.core.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.
        start_ns: Inclusive lower bound on local_ts (nanoseconds UTC).
        end_ns: Inclusive upper bound on local_ts (nanoseconds UTC).
        interval: Time-bin interval string, e.g. "1s", "5m", "1h".

    Returns:
        A Polars DataFrame with the columns
        :data:`~crocodile.core.analytics.ofi.OFI_SCHEMA` declares — ``timestamp``,
        ``best_bid``, ``best_ask``, ``ofi`` — and no rows where the window holds fewer
        than two usable snapshots.

    The imbalance arithmetic and the binning are
    :func:`~crocodile.core.analytics.ofi.ofi_increment` and
    :func:`~crocodile.core.analytics.ofi.bin_ofi`, which used to be sixteen lines inline
    here. They moved when M7 gave them a second caller: an equity L1 quote is the same
    two prices and two sizes as this function's top of book, so the alternative was a
    second copy of the conditioning — and a copy that dropped it would still return
    numbers, just a different statistic under the same capability name. What stays here
    is the part that is genuinely crypto's: reading ``book_snapshot`` and taking the
    first level of each side.
    """
    interval_ns = parse_interval_to_ns(interval)

    try:
        catalog.refresh_views()
        df = catalog.scan("book_snapshot", symbol, start_ns, end_ns)
    except Exception:
        df = pl.DataFrame()

    if df.is_empty():
        return pl.DataFrame(schema=OFI_SCHEMA)

    tops: list[tuple[int, TopOfBook]] = []
    for r in df.to_dicts():
        bids = _coerce_levels_from_row(r.get("bids"))
        asks = _coerce_levels_from_row(r.get("asks"))
        # A snapshot missing a side is not a top of book: there is no imbalance between
        # one price and nothing.
        if not bids or not asks:
            continue
        tops.append((
            int(r["local_ts"]),
            TopOfBook(
                bid_px=float(bids[0][0]),
                bid_sz=float(bids[0][1]),
                ask_px=float(asks[0][0]),
                ask_sz=float(asks[0][1]),
            ),
        ))

    return bin_ofi(tops, start_ns=start_ns, interval_ns=interval_ns)

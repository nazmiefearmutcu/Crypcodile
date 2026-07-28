"""Order-flow imbalance over an equity L1 quote stream — M7's equity half.

The crypto half differences consecutive top-of-book snapshots out of ``book_snapshot``
(``crocodile.crypto.analytics.ofi``). An equity feed publishes the same object under a
different name: a :class:`~crocodile.core.schema.records.Quote` carries ``bid_px``,
``bid_sz``, ``ask_px`` and ``ask_sz`` and nothing about the imbalance cares which channel
they arrived on. So this module is the *read*, and the measurement itself is
:func:`~crocodile.core.analytics.ofi.ofi_increment`, shared with crypto — which is what
makes the two halves of ``ofi`` one statistic rather than two functions that happen to be
declared under one name.

**Why a quote is the right input and a bar is not.** ``OHLCV.buy_volume`` and
``sell_volume`` look like the obvious ingredients for an equity order-flow number and they
are set by no connector in this tree — every row in the lake carries ``0.0`` for both. An
imbalance built on them would return a column of zeros for every symbol and every window,
which is a fabricated calm rather than a missing answer. Quotes are what the equity
providers actually write, and OFI is defined over quote revisions anyway: it measures the
size that entered or left the *touch*, not the size that traded.

**What the equity read has to do that the crypto one does not.** Nothing structural, which
is the finding worth recording. A crypto snapshot is two lists and the top of book is their
first elements, so the reader has to reach in; an equity quote *is* the top of book, so the
four fields are read straight off the row. The only shared judgement is what to do with a
one-sided observation, and both halves drop it — there is no imbalance between one price
and nothing, and treating the missing side as a zero-size quote would report the whole
remaining side as flow.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from crocodile.core.analytics.ofi import OFI_SCHEMA, TopOfBook, bin_ofi
from crocodile.core.resample._interval import parse_interval
from crocodile.core.store.catalog import Catalog

__all__ = ["calculate_quote_ofi"]


def _float_or_none(value: Any) -> float | None:
    """Read a quote field as a float, or ``None`` if the row does not carry one.

    A null in one of the four fields is a row that cannot describe a top of book, and
    coercing it to ``0.0`` would turn "the feed did not say" into "there was no size" —
    which OFI would then report as an entire side being cancelled.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_quote_ofi(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    interval: str,
) -> pl.DataFrame:
    """Order-flow imbalance per time bin, from consecutive stored quotes.

    Args:
        catalog: The lake to read. Reached through ``scan``, which is the crypto half's
            path too: the read is by channel, symbol and time range, so there is no
            caller-supplied string to interpolate into SQL.
        symbol: Canonical symbol string.
        start_ns: Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns: Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        interval: Bin width, e.g. ``"1s"``, ``"5m"``.

    Returns:
        A frame with :data:`~crocodile.core.analytics.ofi.OFI_SCHEMA`'s columns —
        ``timestamp``, ``best_bid``, ``best_ask``, ``ofi`` — the same four the crypto half
        reports, so a caller reading ``ofi`` does not have to know which market answered.

    The interval is parsed by :func:`~crocodile.core.resample._interval.parse_interval`,
    the shared parser every resampler uses, rather than by the crypto half's own
    ``parse_interval_to_ns``. They agree on ``s``/``m``/``h``/``d`` and the shared one also
    accepts ``w``; the crypto spelling is public API pinned by the pre-merge surface
    inventory, so it stays where it is rather than being narrowed to match.
    """
    interval_ns = parse_interval(interval).ns

    try:
        catalog.refresh_views()
        df = catalog.scan("quote", symbol, start_ns, end_ns)
    except Exception:
        # The same swallow the crypto half makes, and for the same reason: a lake with no
        # `quote` partitions at all raises out of DuckDB rather than returning nothing, and
        # "this symbol has no quotes" is an empty answer, not a failure.
        df = pl.DataFrame()

    if df.is_empty():
        return pl.DataFrame(schema=OFI_SCHEMA)

    tops: list[tuple[int, TopOfBook]] = []
    for row in df.to_dicts():
        bid_px = _float_or_none(row.get("bid_px"))
        ask_px = _float_or_none(row.get("ask_px"))
        bid_sz = _float_or_none(row.get("bid_sz"))
        ask_sz = _float_or_none(row.get("ask_sz"))
        if bid_px is None or ask_px is None or bid_sz is None or ask_sz is None:
            continue
        # A zero price is how an equity feed spells "no quote on this side" — Alpaca sends
        # `bp: 0` outside a symbol's quoting hours. It is the equity form of the crypto
        # half's empty `bids`/`asks` list, and it is dropped for the same reason.
        if bid_px <= 0.0 or ask_px <= 0.0:
            continue
        tops.append(
            (
                int(row["local_ts"]),
                TopOfBook(bid_px=bid_px, bid_sz=bid_sz, ask_px=ask_px, ask_sz=ask_sz),
            )
        )

    return bin_ofi(tops, start_ns=start_ns, interval_ns=interval_ns)

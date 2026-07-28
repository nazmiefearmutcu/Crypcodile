"""Execution slippage against the latest stored book snapshot, for both asset classes.

Queries the most recent ``book_snapshot`` for a symbol and walks it to the requested size,
reporting the expected execution price and the distance from the touch.

One function, where there were two
----------------------------------
``crypto/analytics/slippage.py`` and ``equity/analytics/slippage.py`` were a fork of one
another — same module docstring, same helpers, same arithmetic in the middle — that had
drifted apart at both ends. The crypto copy took ``size: float | str`` plus a
``size_unit`` and could walk the book denominated in either asset; the equity copy took
``size: float`` only. One capability means one parameter struct, so one of those had to
give.

``size_unit`` survives. It is measured, tested behaviour — quote-denominated sizing is
pinned by ``tests/analytics/test_analytics_new.py::test_estimate_slippage_quote_denominated``
— and deleting working behaviour to make a struct narrower is a loss with nothing on the
other side of it. An optional parameter costs a caller that ignores it nothing, and the
equity side has a real use for it the moment anyone wants to size an order in dollars
rather than shares.

What came the other way is the equity copy's level hygiene: it drops zero and negative
levels and sorts the book before walking it, where the crypto copy trusted the stored
order. A book snapshot whose levels arrive out of order — or that still carries the
zero-quantity entries a venue uses to signal a deletion — gives the crypto walk a wrong
``best_price`` and a wrong fill. Neither fork was a superset; this is both halves.
"""

from __future__ import annotations

import re

import duckdb
import polars as pl

from crocodile.core.store.catalog import Catalog
from crocodile.core.store.rows import _coerce_levels_from_row

__all__ = [
    "estimate_slippage",
    "parse_base_quote",
    "parse_size_input",
    "slippage_over_levels",
]

_SIZE_WITH_UNIT = re.compile(r"^([0-9.]+)\s*([a-zA-Z]+)?$")

_COMMON_QUOTES = (
    "USDT",
    "USDC",
    "USDbC",
    "USD",
    "EUR",
    "TRY",
    "GBP",
    "JPY",
    "BTC",
    "ETH",
    "BNB",
    "DAI",
    "BUSD",
    "TUSD",
    "FDUSD",
    "PLN",
    "RUB",
)


def parse_base_quote(symbol: str) -> tuple[str, str]:
    """Parse a symbol (e.g. ``binance-spot:BTCUSDT`` or ``AERO-USDC``) into base and quote.

    Only ever called when a caller actually supplied a ``size_unit``, because the tail of
    this function is a guess: with no separator and no recognised quote suffix it splits
    the ticker by length. On ``AAPL`` that yields ``("AA", "PL")``, which is meaningless —
    fine as a failed match, and not something to report back as the denomination of
    anything. See :func:`estimate_slippage`.
    """
    raw = symbol.split(":")[-1] if ":" in symbol else symbol

    for sep in ("-", "_", "/"):
        if sep in raw:
            parts = raw.split(sep)
            if len(parts) >= 2:
                return parts[0].upper(), parts[1].upper()

    raw_upper = raw.upper()
    for quote in _COMMON_QUOTES:
        if raw_upper.endswith(quote) and len(raw_upper) > len(quote):
            return raw_upper[: -len(quote)], quote

    if len(raw_upper) > 4:
        return raw_upper[:-4], raw_upper[-4:]
    mid = len(raw_upper) // 2
    return raw_upper[:mid], raw_upper[mid:]


def parse_size_input(size_input: str) -> tuple[float, str | None]:
    """Parse a string size like ``"100 USDT"``, ``"100USDT"`` or ``"100"``."""
    parts = size_input.strip().split()
    if len(parts) == 1:
        match = _SIZE_WITH_UNIT.match(parts[0])
        if match:
            val_str, unit = match.groups()
            return float(val_str), unit.upper() if unit else None
        return float(parts[0]), None
    if len(parts) >= 2:
        return float(parts[0]), parts[1].upper()
    raise ValueError(f"Invalid size input: {size_input}")


def _walk_levels(
    levels: list[tuple[float, float]], size: float
) -> tuple[float, float]:
    """Walk the book consuming base quantity. Returns ``(filled, total_cost)``."""
    filled = 0.0
    total_cost = 0.0
    for price, amount in levels:
        if filled >= size:
            break
        to_fill = min(amount, size - filled)
        total_cost += to_fill * price
        filled += to_fill
    return filled, total_cost


def _walk_levels_quote(
    levels: list[tuple[float, float]], size: float
) -> tuple[float, float]:
    """Walk the book consuming quote notional. Returns ``(filled_quote, filled_base)``."""
    filled_quote = 0.0
    filled_base = 0.0
    for price, amount in levels:
        if filled_quote >= size:
            break
        to_fill_quote = min(amount * price, size - filled_quote)
        filled_base += to_fill_quote / price
        filled_quote += to_fill_quote
    return filled_quote, filled_base


def _load_book(catalog: Catalog, symbol: str) -> tuple[list[tuple[float, float]], ...]:
    """Return ``(bids, asks)`` from the latest snapshot, or raise saying which it was.

    A missing ``book_snapshot`` view means no book snapshot has ever been written, which is
    the same answer as a query that returns no rows, so both become the ``ValueError``
    callers already map to a 400. Anything else — a corrupt part file, a permission error —
    is re-raised: the fork that swallowed every exception here reported a broken lake as an
    empty one, which is a diagnosis the caller cannot tell from the truth.
    """
    try:
        catalog.refresh_views()
        df = catalog.connection.execute(
            "SELECT bids, asks FROM book_snapshot WHERE symbol = ? ORDER BY local_ts DESC LIMIT 1",
            [symbol],
        ).pl()
    except (duckdb.CatalogException, duckdb.IOException):
        df = pl.DataFrame()
    except Exception as exc:
        raise RuntimeError(f"Failed to load book snapshots for symbol '{symbol}': {exc}") from exc

    if df.is_empty():
        raise ValueError(f"No book snapshots found for symbol '{symbol}'.")

    row = df.to_dicts()[0]
    return _coerce_levels_from_row(row.get("bids")), _coerce_levels_from_row(row.get("asks"))


def estimate_slippage(
    catalog: Catalog,
    symbol: str,
    side: str,
    size: float | str,
    size_unit: str | None = None,
) -> pl.DataFrame:
    """Calculate the expected execution price and slippage against the stored book.

    Args:
        catalog: A :class:`~crocodile.core.store.catalog.Catalog` instance.
        symbol: Canonical symbol string.
        side: ``"buy"``/``"b"`` or ``"sell"``/``"s"``, case-insensitive.
        size: The execution size. A string may carry its own unit (``"305 USDT"``), which
            overrides *size_unit*.
        size_unit: What *size* is denominated in. When it names the symbol's quote asset
            the book is walked by notional rather than by quantity; otherwise the walk is
            by quantity, which is what a caller sizing in shares wants and gets by leaving
            this unset.

    Returns:
        The one-row frame :func:`slippage_over_levels` describes.

    Raises:
        ValueError: on a non-positive size, an unrecognised side, a symbol with no stored
            book snapshot, an empty side of the book, or a size deeper than the book.
        RuntimeError: if the snapshot query fails for a reason other than the view not
            existing.
    """
    bids, asks = _load_book(catalog, symbol)
    return slippage_over_levels(symbol, side, size, bids, asks, size_unit=size_unit)


def slippage_over_levels(
    symbol: str,
    side: str,
    size: float | str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    size_unit: str | None = None,
) -> pl.DataFrame:
    """Walk a ladder to the requested size, wherever the ladder came from.

    Separated from :func:`estimate_slippage` because the arithmetic is not the part that
    differs between the two asset classes — the *ladder* is. Crypto venues stream a book and
    the lake stores it; no equity provider in this tree emits a ``BookSnapshot`` at all, so
    the equity half of the ``slippage`` capability was calling the lake reader above and
    raising ``ValueError: No book snapshots found`` on every call, while its declaration
    published ``prov_basis: "yahoo_1m_vap"`` — a basis naming a synthetic VAP ladder that
    the code path never touched.

    The ladder that basis names is real: ``crocodile.equity.depth.select_depth_source``
    builds it, and ``depth`` already serves it. So the walk takes levels rather than a
    catalog, and each asset class brings its own book to it.

    Args:
        symbol: Canonical symbol string, echoed into the result.
        side: ``"buy"``/``"b"`` or ``"sell"``/``"s"``, case-insensitive. A buy walks the
            asks and a sell walks the bids.
        size: The execution size. A string may carry its own unit (``"305 USDT"``), which
            overrides *size_unit*.
        bids: Resting bid levels as ``(price, amount)``, in any order.
        asks: Resting ask levels as ``(price, amount)``, in any order.
        size_unit: See :func:`estimate_slippage`.

    Returns:
        A one-row Polars DataFrame: ``symbol``, ``side``, ``size``, ``size_unit``,
        ``best_price``, ``expected_price``, ``slippage_usd``, ``slippage_pct``.

        ``slippage_pct`` is a **percent**: a one-percent walk reports ``1.0``. That is the
        odd one out in this package — ``basis_pct``, ``annualized_pct``, ``carry_pct`` and
        ``deviation_pct`` are all decimal *fractions*, so the same suffix means two things a
        hundredfold apart. It stays a percent because the column is shipped under this name
        in the pre-merge surface inventory, and changing the unit under an unchanged name is
        a silent break for every consumer that already divides by a hundred. The full
        inventory of ``_pct`` columns and their units is declared and gated in
        ``tests/conformance/test_units.py``.

        ``size_unit`` is null when the caller named none. The fork used to fill it in from
        :func:`parse_base_quote`, which on a symbol with no separator is a guess made from
        the ticker's length — so an equity caller asking for 10 shares of ``AAPL`` was told
        the size was denominated in ``AA``. Reporting nothing is the honest answer to a
        question nobody asked.

    Raises:
        ValueError: on a non-positive size, an unrecognised side, an empty side of the
            book, or a size deeper than the book.
    """
    if isinstance(size, str):
        size_val, parsed_unit = parse_size_input(size)
        size_unit = parsed_unit or size_unit
    else:
        size_val = float(size)

    if size_val <= 0:
        raise ValueError("Size must be greater than zero.")

    side_lower = side.lower().strip()
    if side_lower in ("b", "buy"):
        side_lower = "buy"
    elif side_lower in ("s", "sell"):
        side_lower = "sell"
    else:
        raise ValueError(f"Invalid side '{side}'. Must be 'buy' or 'sell'.")

    # Only consult parse_base_quote when there is a unit to recognise; see its docstring.
    is_quote = False
    final_unit: str | None = None
    if size_unit:
        final_unit = size_unit.upper()
        _base, quote = parse_base_quote(symbol)
        is_quote = final_unit == quote.upper()

    raw_levels = bids if side_lower == "sell" else asks

    # Drop deleted/empty levels and enforce the walk order, rather than trusting however
    # the snapshot happened to be stored: an out-of-order book gives a wrong best_price and
    # a wrong fill, silently.
    levels = [(p, a) for p, a in raw_levels if p is not None and p > 0 and a is not None and a > 0]
    if not levels:
        raise ValueError(f"Order book for symbol '{symbol}' has no levels on the {side} side.")
    levels.sort(key=lambda level: level[0], reverse=side_lower == "sell")

    best_price = levels[0][0]
    unit_suffix = f" {final_unit}" if final_unit else ""

    if is_quote:
        filled_quote, filled_base = _walk_levels_quote(levels, size_val)
        if filled_quote < size_val:
            raise ValueError(
                f"Requested size {size_val}{unit_suffix} exceeds total order book depth "
                f"({filled_quote:.6f}{unit_suffix}) for symbol '{symbol}' on the {side} side."
            )
        expected_price = size_val / filled_base
    else:
        filled, total_cost = _walk_levels(levels, size_val)
        if filled < size_val:
            raise ValueError(
                f"Requested size {size_val}{unit_suffix} exceeds total order book depth "
                f"({filled:.6f}{unit_suffix}) for symbol '{symbol}' on the {side} side."
            )
        expected_price = total_cost / size_val

    slippage_usd = (
        expected_price - best_price if side_lower == "buy" else best_price - expected_price
    )
    slippage_pct = (slippage_usd / best_price) * 100.0 if best_price > 0 else 0.0

    return pl.DataFrame(
        {
            "symbol": [symbol],
            "side": [side_lower],
            "size": [size_val],
            "size_unit": [final_unit],
            "best_price": [best_price],
            "expected_price": [expected_price],
            "slippage_usd": [slippage_usd],
            "slippage_pct": [slippage_pct],
        }
    )

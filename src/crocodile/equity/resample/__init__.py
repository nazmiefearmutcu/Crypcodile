"""Resampling algorithms for the equity (stockodile) side of crocodile.

Supports converting trades, quotes, and lower-resolution bars into
higher-resolution bars, both stream-based (``*_to_bars``, operating on
``crocodile.core.schema.records`` structs stamped ``AssetClass.EQUITY``) and
Polars DataFrame-based (``*_df``).

``parse_interval`` and ``resample_ohlcv`` re-exported here are ``core``'s
--------------------------------------------------------------------------
Both used to be separate equity implementations sharing a name and a signature with a
``core`` function that answered differently — ``parse_interval`` by arity, and
``resample_ohlcv`` by schema. They are one implementation each now, in ``core``, and the
names stay exported here so equity callers keep their import path. See
``crocodile.core.resample._interval`` and ``crocodile.core.resample.ohlcv`` for what each
merge decided and why.

Why the rest live here and not in ``crocodile.core.resample``
--------------------------------------------------------------
Not the record types — both sides build the same canonical structs. What still separates
them is the aggregation: the coverage and provenance arithmetic below is built on equity
session widths, which is a statement about the market these bars come from.

``resample_book_snapshots`` is re-exported from ``crocodile.equity.resample.book`` and is
*not* the same function as ``crocodile.core.resample.book``'s: the core copy uses
lookahead-biased boundary arithmetic. That pair is a live collision of the same kind as
the two above and it has not been resolved — see that module's docstring.
"""

from crocodile.core.resample._interval import parse_interval
from crocodile.equity.resample.book import resample_book_snapshots
from crocodile.equity.resample.ohlcv import (
    resample_bars_df,
    resample_bars_to_bars,
    resample_ohlcv,
    resample_quotes_df,
    resample_quotes_to_bars,
    resample_trades_df,
    resample_trades_to_bars,
)

__all__ = [
    "parse_interval",
    "resample_bars_df",
    "resample_bars_to_bars",
    "resample_book_snapshots",
    "resample_ohlcv",
    "resample_quotes_df",
    "resample_quotes_to_bars",
    "resample_trades_df",
    "resample_trades_to_bars",
]

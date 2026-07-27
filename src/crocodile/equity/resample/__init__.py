"""Resampling algorithms for the equity (stockodile) side of crocodile.

Supports converting trades, quotes, and lower-resolution bars into
higher-resolution bars, both stream-based (``*_to_bars``, operating on
``crocodile.core.schema.records`` structs stamped ``AssetClass.EQUITY``) and
Polars DataFrame-based (``*_df``).

``parse_interval``, ``resample_ohlcv`` and ``resample_book_snapshots`` re-exported here
are ``core``'s
------------------------------------------------------------------------------------------
All three used to be separate equity implementations sharing a name and a signature with a
``core`` function that answered differently — ``parse_interval`` by arity,
``resample_ohlcv`` by schema, and ``resample_book_snapshots`` by lookahead bias. They are
one implementation each now, in ``core``, and the names stay exported here so equity callers
keep their import path. See ``crocodile.core.resample._interval``,
``crocodile.core.resample.ohlcv`` and ``crocodile.core.resample.book`` for what each merge
decided and why.

The book one is the only merge of the three that changed a *number* on the crypto side: the
surviving rule is equity's, which emits every boundary strictly below a record before
applying it, so a snapshot stamped *B* holds nothing from after *B*. The crypto copy applied
first, and on a book carrying one delta 200 ms past the boundary it reported 5 units at the
touch where the surviving rule reports 120.

Why the rest live here and not in ``crocodile.core.resample``
--------------------------------------------------------------
Not the record types — both sides build the same canonical structs. What still separates
them is the aggregation: the coverage and provenance arithmetic below is built on equity
session widths, which is a statement about the market these bars come from.
"""

from crocodile.core.resample._interval import parse_interval
from crocodile.core.resample.book import resample_book_snapshots
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

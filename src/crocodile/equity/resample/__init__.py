"""Resampling algorithms for the equity (stockodile) side of crocodile.

Supports converting trades, quotes, and lower-resolution bars into
higher-resolution bars, both stream-based (``*_to_bars``, operating on
``crocodile.core.schema.records`` structs stamped ``AssetClass.EQUITY``) and
Polars DataFrame-based (``*_df``).

Why these live here and not in ``crocodile.core.resample``
----------------------------------------------------------

Not the record types any more — both sides build the same canonical structs. What
still separates them is the aggregation: these emit the columns an equity bar has
and the core ones emit the columns a crypto bar has, and each set is only correct
for the market it came from.

``parse_interval`` re-exported here is the equity **3-tuple** implementation.
``crocodile.core.resample`` has a *different* function of the same name and
signature returning a **2-tuple**. Read
``crocodile.equity.resample._interval``'s module docstring before touching
either — they are not interchangeable and the duplication is deliberate.

``resample_ohlcv`` re-exported here is the equity DuckDB/Catalog resampler,
restored from the fork. ``crocodile.core.resample.ohlcv`` has a *different*
function of the same name and signature: it splits ``amount`` by ``side`` into
``buy_volume``/``sell_volume``/``num_trades``, while this one emits
``vwap``/``trade_count``. Both read ``amount`` now, so the core one no longer
returns zero rows on an equity lake — it returns bars that credit the whole
session to ``sell_volume``, because an equity print states ``Side.UNKNOWN``. Read
``crocodile.equity.resample.ohlcv``'s module docstring before touching either.

``resample_book_snapshots`` is re-exported from ``crocodile.equity.resample.book``
and is *not* the same function as ``crocodile.core.resample.book``'s: the core
copy emits crypto records and uses lookahead-biased boundary arithmetic. See
that module's docstring.
"""

from crocodile.equity.resample._interval import parse_interval
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

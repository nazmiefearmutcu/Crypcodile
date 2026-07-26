"""Resampling algorithms for the equity (stockodile) side of crocodile.

Supports converting trades, quotes, and lower-resolution bars into
higher-resolution bars, both stream-based (``*_to_bars``, operating on
``crocodile.equity.schema.records`` structs) and Polars DataFrame-based
(``*_df``).

Why these live here and not in ``crocodile.core.resample``
----------------------------------------------------------

The stream resamplers are typed against the equity record classes
(``Bar``/``Quote``/``Trade`` from ``crocodile.equity.schema.records``), whose
field names differ from the crypto records' (``provider``/``source_ts`` vs
``exchange``/``exchange_ts``). ``core`` must not import from ``equity``, so
these cannot be promoted into ``core`` without inverting the layering.

``parse_interval`` re-exported here is the equity **3-tuple** implementation.
``crocodile.core.resample`` has a *different* function of the same name and
signature returning a **2-tuple**. Read
``crocodile.equity.resample._interval``'s module docstring before touching
either — they are not interchangeable and the duplication is deliberate.

``resample_ohlcv`` re-exported here is the equity DuckDB/Catalog resampler,
restored from the fork. ``crocodile.core.resample.ohlcv`` has a *different*
function of the same name and signature: it sums an ``amount`` column and splits
it by ``side`` into ``buy_volume``/``sell_volume``/``num_trades``, while this one
sums ``size`` and emits ``vwap``/``trade_count``. Equity prints have no aggressor
side and no ``amount`` column, so the core one returns zero rows on an equity
lake rather than raising. Read ``crocodile.equity.resample.ohlcv``'s module
docstring before touching either.

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

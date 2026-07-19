"""CoinGecko connector — the whole coin universe, including tokens no CEX lists.

ccxt covers everything that trades on an *exchange*; it cannot see the long tail
of the market — the ~17k coins tracked by aggregators, many with no orderbook on
any single venue.  This connector closes that gap: it pulls CoinGecko's global
market state and its full paginated coin list (keyless public API) and emits a
24 h :class:`~crypcodile.schema.records.OHLCV` candle per coin into the same
lake, so "the entire crypto market" includes the assets, not just the venues.

``client.py`` holds the async fetch helpers (also used by the ``census``
command); ``normalize.py`` is the pure coin-dict -> record transform;
``connector.py`` is the poll connector wired into the factory as ``coingecko``.
"""

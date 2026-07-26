"""Analytics that serve both asset classes.

A module lands here once it is asset-class-agnostic — its input is a record type
both markets produce, not a venue quirk of one of them. :mod:`indicators` is the
first: its input is OHLCV.
"""

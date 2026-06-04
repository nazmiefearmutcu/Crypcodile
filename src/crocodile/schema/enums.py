from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class OptType(StrEnum):
    CALL = "C"
    PUT = "P"


class Channel(StrEnum):
    TRADE = "trade"
    BOOK_SNAPSHOT = "book_snapshot"
    BOOK_DELTA = "book_delta"
    BOOK_TICKER = "book_ticker"
    DERIVATIVE_TICKER = "derivative_ticker"
    OPTIONS_CHAIN = "options_chain"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"
    OHLCV = "ohlcv"

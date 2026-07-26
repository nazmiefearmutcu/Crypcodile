"""The enumerations both asset classes share.

Crypto and equity arrived with two vocabularies for one set of ideas. These are
the reconciled forms: :class:`Side` and :class:`OptType` were already common,
:class:`Tape`, :class:`SecurityType` and :class:`FundPeriod` came from equity,
and :class:`CorpActionType` and :class:`Channel` are unions of both.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "Channel",
    "CorpActionType",
    "FundPeriod",
    "OptType",
    "SecurityType",
    "Side",
    "Tape",
]


class Side(StrEnum):
    """Aggressor side of a trade."""

    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class OptType(StrEnum):
    """Option right.

    Crypto spelled these ``CALL``/``PUT`` and equity spelled them ``C``/``P``, over
    identical values, so persisted data is compatible with either spelling. The
    crypto member names win because they read as English at a call site.
    """

    CALL = "C"
    PUT = "P"


class Tape(StrEnum):
    """US consolidated-tape identifier: NYSE (A), NYSE Arca/regional (B), Nasdaq (C).

    Equity-only. A crypto record leaves it unset.
    """

    A = "A"
    B = "B"
    C = "C"
    UNKNOWN = "unknown"


class SecurityType(StrEnum):
    """Instrument class of a listed equity security."""

    CS = "CS"
    ETF = "ETF"
    ADR = "ADR"
    REIT = "REIT"
    PFD = "PFD"
    WARRANT = "WARRANT"
    UNIT = "UNIT"
    RIGHT = "RIGHT"
    UNKNOWN = "unknown"


class CorpActionType(StrEnum):
    """An event that rebases a price or quantity series.

    The last two members are the crypto forms. A token redenomination and a chain
    migration change the units a series is quoted in exactly as a split does, and
    obey the same cumulative-adjustment-factor arithmetic — which is why one
    CRSP-style adjustment calculator can serve both asset classes.
    """

    SPLIT = "split"
    DIVIDEND_CASH = "dividend_cash"
    DIVIDEND_STOCK = "dividend_stock"
    SPINOFF = "spinoff"
    MERGER = "merger"
    TICKER_CHANGE = "ticker_change"
    TOKEN_SPLIT = "token_split"
    CHAIN_MIGRATION = "chain_migration"


class FundPeriod(StrEnum):
    """Reporting period a fundamental figure covers."""

    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    FY = "FY"
    TTM = "TTM"


class Channel(StrEnum):
    """Every record tag, from both asset classes.

    Both source enums were narrower than their own record unions — crypto listed 10
    of 16 tags and equity 20 of 25 — so a channel name could exist as a struct tag
    and not as an enum member. This is the union of every tag both record modules
    define; Task 5 adds the gate that keeps it that way.
    """

    # Shared between the asset classes.
    TRADE = "trade"
    QUOTE = "quote"
    BOOK_SNAPSHOT = "book_snapshot"
    BOOK_DELTA = "book_delta"
    BOOK_TICKER = "book_ticker"
    DEPTH = "depth"
    OHLCV = "ohlcv"
    BAR = "bar"

    # Crypto derivatives.
    DERIVATIVE_TICKER = "derivative_ticker"
    OPTIONS_CHAIN = "options_chain"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"

    # Equity reference, corporate and regulatory data.
    AUCTION = "auction"
    TRADING_STATUS = "trading_status"
    INSTRUMENT = "instrument"
    CORP_ACTION = "corp_action"
    FUNDAMENTAL = "fundamental"
    INSIDER = "insider"
    HOLDING_13F = "holding_13f"
    SHORT_INTEREST = "short_interest"
    SHORT_VOLUME = "short_volume"
    FILING = "filing"
    OPTION_QUOTE = "option_quote"
    INDEX_VALUE = "index_value"
    MACRO_SERIES = "macro_series"

    # On-chain and social, carried by both forks already.
    FARCASTER_CORRELATION = "farcaster_correlation"
    RESERVE_DATA_UPDATED = "reserve_data_updated"
    LIQUIDATION_CALL = "liquidation_call"
    LIMIT_ORDER_FILL = "limit_order_fill"
    BALANCE_CORRECTION = "balance_correction"
    POR_UPDATE = "por_update"

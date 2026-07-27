"""The enumerations both asset classes share.

Crypto and equity arrived with two vocabularies for one set of ideas. These are
the reconciled forms: :class:`Side` and :class:`OptType` were already common,
:class:`Tape`, :class:`SecurityType` and :class:`FundPeriod` came from equity,
and :class:`CorpActionType` and :class:`Channel` are unions of both.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = [
    "CHANNEL_SUCCESSORS",
    "AssetClass",
    "Channel",
    "CorpActionType",
    "FundPeriod",
    "OptType",
    "SecurityType",
    "Side",
    "Tape",
    "channel_predecessors",
    "channel_read_set",
    "successor_channel",
]


class AssetClass(StrEnum):
    """Which market a record came from.

    One struct serves both asset classes, so a consumer holding a record needs the record
    itself to say which of its fields are meaningfully absent: an unset ``tape`` is normal
    on a crypto trade and a defect on an equity one. ``source`` is a free-form venue or
    provider string and cannot answer that without an out-of-band registry.

    It also makes ``GROUP BY asset_class`` the natural way to ask the symmetry question of
    the lake. One low-cardinality string column is close to free under dictionary encoding.
    """

    CRYPTO = "crypto"
    EQUITY = "equity"


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


CHANNEL_SUCCESSORS: Final[dict[str, str]] = {
    Channel.BAR.value: Channel.OHLCV.value,
    Channel.OPTION_QUOTE.value: Channel.OPTIONS_CHAIN.value,
}
"""Retired tag → the tag whose record absorbed it.

Keeping the retired member declared prevents nothing on its own. ``channel=bar/``
directories exist in equity lakes on disk, and a reader that only globs the literal
string it was handed returns the ``ohlcv`` half of such a lake and calls it all of it —
no exception, no warning, a short answer. This table is what turns the member into a
read path: every glob widens a request for the surviving tag to cover its predecessors,
and :mod:`crocodile.core.store.rows` decodes a row carrying the retired tag into the
record that absorbed it.

The mapping is deliberately one-directional. Asking for ``bar`` reads ``channel=bar/``
alone, because a caller naming a retired tag is asking about the old files; asking for
``ohlcv`` reads both, because that is the whole of what the channel now means.
"""


def successor_channel(channel: str) -> str:
    """Return the surviving tag for ``channel``, or ``channel`` if it is not retired."""
    return CHANNEL_SUCCESSORS.get(channel, channel)


def channel_predecessors(channel: str) -> tuple[str, ...]:
    """Return the retired tags whose partitions belong to ``channel``."""
    return tuple(old for old, new in CHANNEL_SUCCESSORS.items() if new == channel)


def channel_read_set(channel: str) -> tuple[str, ...]:
    """Return every ``channel=`` partition name a read of ``channel`` must cover.

    The surviving tag first, then its predecessors. A caller naming a retired tag gets
    that tag alone; see :data:`CHANNEL_SUCCESSORS` for why the widening is one-way.
    """
    return (channel, *channel_predecessors(channel))

"""Canonical record types.

Every record leads with the ten fields of :class:`_Header`: the four that identify an
observation, ``asset_class``, ``source_ts``, and the four-field provenance tail. All ten
live on the base and no record can restate or reorder them.

``kw_only=True`` on each record is what makes that possible. msgspec forbids a required
field after a defaulted one, which would otherwise force every struct to repeat the
defaulted tail after its own required fields; keyword-only fields are exempt, and the
base's fields stay positional so they keep leading. The pairing is load-bearing in both
directions: ``kw_only`` on the base instead would order the subclass's fields first and
silently break the header. ``tests/conformance/test_gates.py`` enforces both halves.
"""

from __future__ import annotations

import math

import msgspec

from crocodile.core.schema.enums import AssetClass, Channel, OptType, Side, Tape
from crocodile.core.schema.provenance import Provenance

Level = tuple[float, float]
"""(price, size). A size of 0.0 means REMOVE this level."""


class _Header(msgspec.Struct, frozen=True):
    """The ten fields every record carries.

    Never mark this ``kw_only``: msgspec orders positional fields ahead of keyword-only
    ones, so doing so would move each record's own fields in front of the header.
    """

    source: str
    """Venue (crypto) or data provider (equity)."""

    symbol: str
    """Canonical symbol, e.g. ``deribit:BTC-PERPETUAL`` or ``AAPL``."""

    symbol_raw: str
    """The symbol exactly as the source spelled it."""

    local_ts: int
    """UTC epoch nanoseconds at which we observed the record."""

    asset_class: AssetClass
    """Which market this came from, so an absent field reads as normal or as a defect."""

    source_ts: int | None
    """UTC epoch nanoseconds the source stamped, or ``None`` if it stamped nothing.

    Required, with no default. An adapter must state which of those two happened; a
    default would let one of 108 adapters forget and report a silent ``None`` instead.
    """

    prov: Provenance = Provenance.NATIVE
    """How this record came to exist. Never ``UNAVAILABLE`` — see :class:`Provenance`."""

    prov_basis: str = "native"
    """The registered basis name, a key into the provenance registry."""

    prov_confidence: float = 1.0
    """Sampling adequacy within ``prov``'s level, from the basis's registered formula."""

    prov_inputs: list[str] = []
    """The channels the basis consumed.

    A mutable default is safe here and only here: msgspec copies it per instance rather
    than sharing one list across every record, which a conformance test pins down.
    """


class Trade(_Header, frozen=True, kw_only=True, tag=Channel.TRADE.value, tag_field="channel"):
    id: str
    price: float
    amount: float
    side: Side
    liquidation: str | None = None
    l1_gas_fee: float | None = None
    l2_gas_fee: float | None = None
    gas_price: float | None = None
    sender: str | None = None
    is_smart_wallet: bool | None = None
    conditions: list[str] | None = None
    tape: Tape | None = None
    venue: str | None = None


class BookSnapshot(
    _Header, frozen=True, kw_only=True, tag=Channel.BOOK_SNAPSHOT.value, tag_field="channel"
):
    bids: list[Level]
    asks: list[Level]
    depth: int
    sequence_id: int | None = None
    is_snapshot: bool = True


class BookDelta(
    _Header, frozen=True, kw_only=True, tag=Channel.BOOK_DELTA.value, tag_field="channel"
):
    bids: list[Level]
    asks: list[Level]
    seq_id: int | None = None
    prev_seq_id: int | None = None
    is_snapshot: bool = False


class BookTicker(
    _Header, frozen=True, kw_only=True, tag=Channel.BOOK_TICKER.value, tag_field="channel"
):
    bid_px: float
    bid_sz: float
    ask_px: float
    ask_sz: float
    update_id: int | None = None

    @property
    def price(self) -> float:
        return round(math.sqrt(self.bid_px * self.ask_px), 6)


class DerivativeTicker(
    _Header, frozen=True, kw_only=True, tag=Channel.DERIVATIVE_TICKER.value, tag_field="channel"
):
    last_price: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    funding_rate: float | None = None
    predicted_funding_rate: float | None = None
    funding_timestamp: int | None = None
    open_interest: float | None = None


class OptionsChain(
    _Header, frozen=True, kw_only=True, tag=Channel.OPTIONS_CHAIN.value, tag_field="channel"
):
    underlying: str
    underlying_price: float | None
    strike: float
    expiry: int
    opt_type: OptType
    mark_price: float | None = None
    mark_iv: float | None = None  # decimal fraction (0.65 == 65%); all *_iv fields are decimal
    bid_px: float | None = None
    bid_sz: float | None = None
    bid_iv: float | None = None  # decimal fraction
    ask_px: float | None = None
    ask_sz: float | None = None
    ask_iv: float | None = None  # decimal fraction
    last_price: float | None = None
    open_interest: float | None = None
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    rho: float | None = None


class Funding(_Header, frozen=True, kw_only=True, tag=Channel.FUNDING.value, tag_field="channel"):
    funding_rate: float
    funding_timestamp: int | None = None
    predicted_funding_rate: float | None = None
    interval_hours: int | None = None


class OpenInterest(
    _Header, frozen=True, kw_only=True, tag=Channel.OPEN_INTEREST.value, tag_field="channel"
):
    open_interest: float
    open_interest_value: float | None = None


class Liquidation(
    _Header, frozen=True, kw_only=True, tag=Channel.LIQUIDATION.value, tag_field="channel"
):
    price: float
    amount: float
    side: Side
    id: str | None = None


class OHLCV(_Header, frozen=True, kw_only=True, tag=Channel.OHLCV.value, tag_field="channel"):
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    num_trades: int | None = None


class FarcasterCorrelation(
    _Header, frozen=True, kw_only=True, tag=Channel.FARCASTER_CORRELATION.value, tag_field="channel"
):
    mentions_24h: int
    dev_activity_score: float
    trending_rank: int


class ReserveDataUpdated(
    _Header, frozen=True, kw_only=True, tag=Channel.RESERVE_DATA_UPDATED.value, tag_field="channel"
):
    reserve: str
    liquidity_rate: float
    stable_borrow_rate: float
    variable_borrow_rate: float
    liquidity_index: int
    variable_borrow_index: int


class LiquidationCall(
    _Header, frozen=True, kw_only=True, tag=Channel.LIQUIDATION_CALL.value, tag_field="channel"
):
    collateral_asset: str
    debt_asset: str
    user: str
    debt_to_cover: float
    liquidated_collateral_amount: float
    liquidator: str
    receive_a_token: bool


class LimitOrderFill(
    _Header, frozen=True, kw_only=True, tag=Channel.LIMIT_ORDER_FILL.value, tag_field="channel"
):
    tx_hash: str
    log_index: int
    protocol: str  # "1inch" | "0x"
    maker: str
    taker: str
    maker_token: str
    taker_token: str
    maker_amount: float
    taker_amount: float
    order_hash: str


class BalanceCorrection(
    _Header, frozen=True, kw_only=True, tag=Channel.BALANCE_CORRECTION.value, tag_field="channel"
):
    holder_address: str
    token_address: str
    local_balance: float
    onchain_balance: float
    correction_amount: float


class PoRUpdate(
    _Header, frozen=True, kw_only=True, tag=Channel.POR_UPDATE.value, tag_field="channel"
):
    feed_address: str
    token_address: str
    reserves: float
    total_supply: float
    backing_ratio: float
    is_backed: bool


Record = (
    Trade
    | BookSnapshot
    | BookDelta
    | BookTicker
    | DerivativeTicker
    | OptionsChain
    | Funding
    | OpenInterest
    | Liquidation
    | OHLCV
    | FarcasterCorrelation
    | ReserveDataUpdated
    | LiquidationCall
    | LimitOrderFill
    | BalanceCorrection
    | PoRUpdate
)

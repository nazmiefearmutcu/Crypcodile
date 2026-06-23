import msgspec

from crypcodile.schema.enums import OptType, Side

Level = tuple[float, float]  # (price, amount); amount == 0.0 means REMOVE this level


class Trade(msgspec.Struct, frozen=True, tag="trade", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    id: str
    price: float
    amount: float
    side: Side
    liquidation: str | None = None  # Deribit enum "M"/"T"/"MT" when present
    l1_gas_fee: float | None = None
    l2_gas_fee: float | None = None
    gas_price: float | None = None
    sender: str | None = None
    is_smart_wallet: bool | None = None


class BookSnapshot(msgspec.Struct, frozen=True, tag="book_snapshot", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    bids: list[Level]
    asks: list[Level]
    depth: int
    sequence_id: int | None = None
    is_snapshot: bool = True


class BookDelta(msgspec.Struct, frozen=True, tag="book_delta", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    bids: list[Level]
    asks: list[Level]
    seq_id: int | None = None
    prev_seq_id: int | None = None
    is_snapshot: bool = False


class BookTicker(msgspec.Struct, frozen=True, tag="book_ticker", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    bid_px: float
    bid_sz: float
    ask_px: float
    ask_sz: float
    update_id: int | None = None

    @property
    def price(self) -> float:
        import math
        return round(math.sqrt(self.bid_px * self.ask_px), 6)


class DerivativeTicker(msgspec.Struct, frozen=True, tag="derivative_ticker", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    last_price: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    funding_rate: float | None = None
    predicted_funding_rate: float | None = None
    funding_timestamp: int | None = None
    open_interest: float | None = None


class OptionsChain(msgspec.Struct, frozen=True, tag="options_chain", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
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


class Funding(msgspec.Struct, frozen=True, tag="funding", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    funding_rate: float
    funding_timestamp: int | None = None
    predicted_funding_rate: float | None = None
    interval_hours: int | None = None


class OpenInterest(msgspec.Struct, frozen=True, tag="open_interest", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    open_interest: float
    open_interest_value: float | None = None


class Liquidation(msgspec.Struct, frozen=True, tag="liquidation", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    price: float
    amount: float
    side: Side
    id: str | None = None


class OHLCV(msgspec.Struct, frozen=True, tag="ohlcv", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    num_trades: int | None = None


class FarcasterCorrelation(msgspec.Struct, frozen=True, tag="farcaster_correlation", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    mentions_24h: int
    dev_activity_score: float
    trending_rank: int


class ReserveDataUpdated(msgspec.Struct, frozen=True, tag="reserve_data_updated", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    reserve: str
    liquidity_rate: float
    stable_borrow_rate: float
    variable_borrow_rate: float
    liquidity_index: int
    variable_borrow_index: int


class LiquidationCall(msgspec.Struct, frozen=True, tag="liquidation_call", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    collateral_asset: str
    debt_asset: str
    user: str
    debt_to_cover: float
    liquidated_collateral_amount: float
    liquidator: str
    receive_a_token: bool


class LimitOrderFill(msgspec.Struct, frozen=True, tag="limit_order_fill", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
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


class BalanceCorrection(msgspec.Struct, frozen=True, tag="balance_correction", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    holder_address: str
    token_address: str
    local_balance: float
    onchain_balance: float
    correction_amount: float


class PoRUpdate(msgspec.Struct, frozen=True, tag="por_update", tag_field="channel"):
    exchange: str
    symbol: str
    symbol_raw: str
    exchange_ts: int | None
    local_ts: int
    feed_address: str
    token_address: str
    reserves: float
    total_supply: float
    backing_ratio: float
    is_backed: bool


Record = (
    Trade | BookSnapshot | BookDelta | BookTicker | DerivativeTicker
    | OptionsChain | Funding | OpenInterest | Liquidation | OHLCV
    | FarcasterCorrelation | ReserveDataUpdated | LiquidationCall
    | LimitOrderFill | BalanceCorrection | PoRUpdate
)


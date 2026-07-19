"""Unit tests for the pure ccxt -> Record transforms.

Fixtures are trimmed captures of **real** ccxt unified structures (probed live
from Kraken / KuCoin / MEXC), so these tests pin the exact shape gotchas the
normalizer must survive: 3-element order-book levels, ``None`` timestamps,
missing ``bid``/``ask``, and contract vs spot markets.
"""

from __future__ import annotations

from crypcodile.exchanges.ccxt_universal import normalize as norm
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import Side
from crypcodile.schema.records import (
    OHLCV,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    Trade,
)

LOCAL_TS = 1_700_000_000_000_000_000


# --------------------------------------------------------------------------- #
# markets -> instruments
# --------------------------------------------------------------------------- #

def test_spot_market_to_instrument():
    market = {
        "symbol": "BTC/USDT",
        "base": "BTC",
        "quote": "USDT",
        "type": "spot",
        "spot": True,
        "swap": False,
        "future": False,
        "option": False,
        "contract": False,
        "precision": {"amount": 1e-08, "price": 0.01},
        "active": True,
    }
    inst = norm.market_to_instrument(market, "mexc")
    assert inst is not None
    assert inst.canonical == "mexc:BTC/USDT"
    assert inst.exchange == "mexc"
    assert inst.symbol_raw == "BTC/USDT"
    assert inst.kind is Kind.SPOT
    assert inst.base == "BTC"
    assert inst.quote == "USDT"
    assert inst.tick_size == 0.01


def test_swap_market_maps_to_perpetual():
    market = {"symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
              "type": "swap", "swap": True, "contract": True, "settle": "USDT"}
    inst = norm.market_to_instrument(market, "bybit")
    assert inst is not None
    assert inst.kind is Kind.PERPETUAL
    assert inst.settlement_currency == "USDT"


def test_option_market_maps_to_option_with_type():
    market = {"symbol": "BTC/USD:BTC-240628-60000-C", "base": "BTC", "quote": "USD",
              "type": "option", "option": True, "contract": True,
              "strike": 60000.0, "optionType": "call", "expiry": 1719532800000}
    inst = norm.market_to_instrument(market, "deribit")
    assert inst is not None
    assert inst.kind is Kind.OPTION
    assert inst.opt_type == "C"
    assert inst.strike == 60000.0
    assert inst.expiry == 1719532800000 * 1_000_000  # ms -> ns


def test_market_without_symbol_returns_none():
    assert norm.market_to_instrument({"base": "BTC"}, "kraken") is None


def test_kind_from_market_prefers_option_then_swap_then_future():
    assert norm.kind_from_market({"type": "option"}) is Kind.OPTION
    assert norm.kind_from_market({"swap": True}) is Kind.PERPETUAL
    assert norm.kind_from_market({"future": True}) is Kind.FUTURE
    assert norm.kind_from_market({"type": "spot"}) is Kind.SPOT
    assert norm.kind_from_market({}) is Kind.SPOT


# --------------------------------------------------------------------------- #
# ticker -> BookTicker / DerivativeTicker
# --------------------------------------------------------------------------- #

def test_ticker_yields_book_ticker():
    ticker = {"symbol": "BTC/USDT", "last": 64659.58, "bid": 64659.4, "ask": 64659.41,
              "bidVolume": 0.790109, "askVolume": 0.07799746, "timestamp": 1784432846272}
    recs = list(norm.normalize_ticker(ticker, exchange="mexc", symbol_raw="BTC/USDT",
                                      local_ts=LOCAL_TS))
    assert len(recs) == 1
    bt = recs[0]
    assert isinstance(bt, BookTicker)
    assert bt.bid_px == 64659.4
    assert bt.ask_px == 64659.41
    assert bt.bid_sz == 0.790109
    assert bt.symbol == "mexc:BTC/USDT"
    assert bt.exchange_ts == 1784432846272 * 1_000_000


def test_ticker_null_timestamp_passes_through():
    # Kraken emits timestamp=None on spot tickers.
    ticker = {"symbol": "BTC/USD", "bid": 64614.9, "ask": 64615.0, "timestamp": None}
    recs = list(norm.normalize_ticker(ticker, exchange="kraken", symbol_raw="BTC/USD",
                                      local_ts=LOCAL_TS))
    assert recs[0].exchange_ts is None


def test_ticker_without_bid_ask_yields_nothing():
    ticker = {"symbol": "X/Y", "last": 1.0, "bid": None, "ask": None}
    recs = list(norm.normalize_ticker(ticker, exchange="kraken", symbol_raw="X/Y",
                                      local_ts=LOCAL_TS))
    assert recs == []


def test_contract_ticker_also_yields_derivative_ticker():
    ticker = {"symbol": "BTC/USDT:USDT", "last": 64660.0, "bid": 64659.0, "ask": 64661.0,
              "info": {"markPrice": "64660.5", "indexPrice": "64659.9", "fundingRate": "0.0001"}}
    recs = list(norm.normalize_ticker(ticker, exchange="bybit", symbol_raw="BTC/USDT:USDT",
                                      local_ts=LOCAL_TS, is_contract=True))
    kinds = {type(r) for r in recs}
    assert BookTicker in kinds and DerivativeTicker in kinds
    dt = next(r for r in recs if isinstance(r, DerivativeTicker))
    assert dt.mark_price == 64660.5
    assert dt.funding_rate == 0.0001


# --------------------------------------------------------------------------- #
# order book -> BookSnapshot (3-element levels, client-side depth cap)
# --------------------------------------------------------------------------- #

def test_order_book_snapshot_handles_three_element_levels():
    # Kraken levels are [price, amount, timestamp] — the 3rd field must be dropped.
    ob = {
        "bids": [[64614.9, 0.992, 1784432818], [64614.3, 0.004, 1784432834]],
        "asks": [[64615.0, 0.166, 1784432834], [64616.5, 0.001, 1784432823]],
        "timestamp": None,
        "nonce": None,
    }
    snap = norm.normalize_order_book(ob, exchange="kraken", symbol_raw="BTC/USD",
                                     local_ts=LOCAL_TS, depth=50)
    assert isinstance(snap, BookSnapshot)
    assert snap.bids == [(64614.9, 0.992), (64614.3, 0.004)]
    assert snap.asks[0] == (64615.0, 0.166)
    assert snap.is_snapshot is True
    assert snap.sequence_id is None


def test_order_book_depth_cap():
    ob = {"bids": [[i, 1.0] for i in range(100)], "asks": [[i, 1.0] for i in range(100)]}
    snap = norm.normalize_order_book(ob, exchange="mexc", symbol_raw="BTC/USDT",
                                     local_ts=LOCAL_TS, depth=5)
    assert len(snap.bids) == 5
    assert len(snap.asks) == 5
    assert snap.depth == 5


def test_order_book_nonce_becomes_sequence_id():
    ob = {
        "bids": [[1.0, 2.0]], "asks": [[3.0, 4.0]],
        "nonce": 77134799125, "timestamp": 1784432847967,
    }
    snap = norm.normalize_order_book(ob, exchange="mexc", symbol_raw="BTC/USDT", local_ts=LOCAL_TS)
    assert snap.sequence_id == 77134799125
    assert snap.exchange_ts == 1784432847967 * 1_000_000


# --------------------------------------------------------------------------- #
# trades -> Trade
# --------------------------------------------------------------------------- #

def test_trade_normalization():
    trade = {"id": "103932511", "timestamp": 1784432836043, "side": "buy",
             "price": 64615.0, "amount": 3.329e-05}
    rec = norm.normalize_trade(trade, exchange="kraken", symbol_raw="BTC/USD", local_ts=LOCAL_TS)
    assert isinstance(rec, Trade)
    assert rec.side is Side.BUY
    assert rec.price == 64615.0
    assert rec.id == "103932511"
    assert rec.exchange_ts == 1784432836043 * 1_000_000


def test_trade_missing_price_dropped():
    assert norm.normalize_trade({"amount": 1.0, "side": "sell"}, exchange="k",
                                symbol_raw="A/B", local_ts=LOCAL_TS) is None


def test_trade_unknown_side_falls_back():
    rec = norm.normalize_trade({"price": 1.0, "amount": 2.0}, exchange="k",
                               symbol_raw="A/B", local_ts=LOCAL_TS)
    assert rec is not None and rec.side is Side.UNKNOWN


def test_normalize_trades_batch_drops_invalid():
    trades = [
        {"id": "1", "price": 1.0, "amount": 2.0, "side": "buy"},
        {"id": "2", "price": None, "amount": 2.0},  # dropped
        {"id": "3", "price": 3.0, "amount": 4.0, "side": "sell"},
    ]
    out = norm.normalize_trades(trades, exchange="k", symbol_raw="A/B", local_ts=LOCAL_TS)
    assert [t.id for t in out] == ["1", "3"]


# --------------------------------------------------------------------------- #
# OHLCV + funding
# --------------------------------------------------------------------------- #

def test_ohlcv_row():
    candle = [1784432820000, 64600.0, 64700.0, 64550.0, 64660.0, 12.5]
    rec = norm.normalize_ohlcv(candle, interval="1m", exchange="mexc",
                               symbol_raw="BTC/USDT", local_ts=LOCAL_TS)
    assert isinstance(rec, OHLCV)
    assert rec.open == 64600.0 and rec.close == 64660.0 and rec.volume == 12.5
    assert rec.interval == "1m"


def test_ohlcv_malformed_row_returns_none():
    assert norm.normalize_ohlcv([1, 2, 3], interval="1m", exchange="m",
                                symbol_raw="A/B", local_ts=LOCAL_TS) is None


def test_funding_record():
    funding = {"symbol": "BTC/USDT:USDT", "fundingRate": 0.0001,
               "timestamp": 1784432846272, "fundingTimestamp": 1784460000000,
               "nextFundingRate": 0.00012}
    rec = norm.normalize_funding(funding, exchange="bybit", symbol_raw="BTC/USDT:USDT",
                                 local_ts=LOCAL_TS)
    assert isinstance(rec, Funding)
    assert rec.funding_rate == 0.0001
    assert rec.predicted_funding_rate == 0.00012


def test_funding_without_rate_returns_none():
    assert norm.normalize_funding({"symbol": "X"}, exchange="b", symbol_raw="X",
                                  local_ts=LOCAL_TS) is None


# --------------------------------------------------------------------------- #
# registry-aware canonical resolution
# --------------------------------------------------------------------------- #

def test_canonical_uses_registry_when_present():
    reg = InstrumentRegistry()
    reg.add(Instrument(canonical="kraken:BTC/USD", exchange="kraken",
                       symbol_raw="BTC/USD", kind=Kind.SPOT, base="BTC", quote="USD"))
    trade = {"id": "1", "price": 1.0, "amount": 2.0, "side": "buy"}
    rec = norm.normalize_trade(trade, exchange="kraken", symbol_raw="BTC/USD",
                               local_ts=LOCAL_TS, registry=reg)
    assert rec is not None and rec.symbol == "kraken:BTC/USD"

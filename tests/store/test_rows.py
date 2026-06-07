from crypcodile.schema.enums import OptType, Side
from crypcodile.schema.records import (
    OHLCV,
    BookDelta,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    Liquidation,
    OpenInterest,
    OptionsChain,
    Trade,
)
from crypcodile.store.rows import from_row, to_row

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


def test_to_row_adds_partition_cols():
    t = Trade(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=1700000000000000000,
        local_ts=1700000000000000000,
        id="1",
        price=1.0,
        amount=2.0,
        side=Side.BUY,
    )
    row = to_row(t)
    assert row["channel"] == "trade"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["side"] == "buy"


def test_to_row_exchange_ts_none():
    t = Trade(
        exchange="binance-spot",
        symbol="binance-spot:BTC-USDT",
        symbol_raw="BTCUSDT",
        exchange_ts=None,
        local_ts=1700000000000000000,
        id="2",
        price=50000.0,
        amount=0.5,
        side=Side.SELL,
    )
    row = to_row(t)
    assert row["exchange_ts"] is None
    assert row["channel"] == "trade"
    assert row["side"] == "sell"


def test_to_row_book_snapshot_levels():
    snap = BookSnapshot(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=1700000000000000000,
        local_ts=1700000000000000000,
        bids=[(100.0, 5.0), (99.0, 2.0)],
        asks=[(101.0, 4.0)],
        depth=3,
        sequence_id=100,
        is_snapshot=True,
    )
    row = to_row(snap)
    assert row["channel"] == "book_snapshot"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["bids"] == [(100.0, 5.0), (99.0, 2.0)]
    assert row["asks"] == [(101.0, 4.0)]


def test_to_row_book_delta_zero_amount_round_trips():
    delta = BookDelta(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=1700000000100000000,
        bids=[(99.0, 0.0), (100.0, 7.0)],
        asks=[(102.0, 1.0)],
        seq_id=101,
        prev_seq_id=100,
        is_snapshot=False,
    )
    row = to_row(delta)
    assert row["channel"] == "book_delta"
    # amount=0.0 (canonical removal) must survive round-trip
    assert (99.0, 0.0) in row["bids"]
    assert (100.0, 7.0) in row["bids"]


def test_bucket_is_deterministic():
    t = Trade(
        exchange="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=None,
        local_ts=1700000000000000000,
        id="3",
        price=1.0,
        amount=1.0,
        side=Side.BUY,
    )
    row1 = to_row(t)
    row2 = to_row(t)
    assert row1["bucket"] == row2["bucket"]


# ---------------------------------------------------------------------------
# from_row round-trip tests — one per channel type
# ---------------------------------------------------------------------------


def _base(channel: str) -> dict:
    """Shared fields present in every channel row."""
    return {
        "channel": channel,
        "exchange": "deribit",
        "symbol": "deribit:BTC-PERPETUAL",
        "symbol_raw": "BTC-PERPETUAL",
        "exchange_ts": _BASE_TS,
        "local_ts": _BASE_TS,
        "date": "2023-11-14",
        "bucket": 42,
    }


def test_from_row_book_snapshot():
    row = {
        **_base("book_snapshot"),
        "bids": [{"price": 100.0, "amount": 5.0}, {"price": 99.0, "amount": 2.0}],
        "asks": [{"price": 101.0, "amount": 4.0}],
        "depth": 3,
        "sequence_id": 100,
        "is_snapshot": True,
    }
    rec = from_row(row)
    assert isinstance(rec, BookSnapshot)
    assert rec.bids == [(100.0, 5.0), (99.0, 2.0)]
    assert rec.asks == [(101.0, 4.0)]
    assert rec.depth == 3
    assert rec.sequence_id == 100
    assert rec.is_snapshot is True


def test_from_row_book_snapshot_default_is_snapshot():
    """is_snapshot defaults to True when the key is missing."""
    row = {
        **_base("book_snapshot"),
        "bids": [],
        "asks": [],
        "depth": 0,
    }
    rec = from_row(row)
    assert isinstance(rec, BookSnapshot)
    assert rec.is_snapshot is True


def test_from_row_book_delta_zero_amount_preserved():
    row = {
        **_base("book_delta"),
        "bids": [{"price": 99.0, "amount": 0.0}, {"price": 100.0, "amount": 7.0}],
        "asks": [{"price": 102.0, "amount": 1.0}],
        "seq_id": 101,
        "prev_seq_id": 100,
        "is_snapshot": False,
    }
    rec = from_row(row)
    assert isinstance(rec, BookDelta)
    # amount=0.0 (canonical removal) must survive round-trip
    assert (99.0, 0.0) in rec.bids
    assert (100.0, 7.0) in rec.bids
    assert rec.seq_id == 101
    assert rec.prev_seq_id == 100
    assert rec.is_snapshot is False


def test_from_row_book_delta_default_is_snapshot():
    """is_snapshot defaults to False when the key is missing."""
    row = {
        **_base("book_delta"),
        "bids": [],
        "asks": [],
    }
    rec = from_row(row)
    assert isinstance(rec, BookDelta)
    assert rec.is_snapshot is False


def test_from_row_book_ticker():
    row = {
        **_base("book_ticker"),
        "bid_px": 49999.0,
        "bid_sz": 1.2,
        "ask_px": 50001.0,
        "ask_sz": 0.8,
        "update_id": 99,
    }
    rec = from_row(row)
    assert isinstance(rec, BookTicker)
    assert rec.bid_px == 49999.0
    assert rec.bid_sz == 1.2
    assert rec.ask_px == 50001.0
    assert rec.ask_sz == 0.8
    assert rec.update_id == 99


def test_from_row_book_ticker_no_update_id():
    """update_id is optional and defaults to None."""
    row = {
        **_base("book_ticker"),
        "bid_px": 1.0,
        "bid_sz": 1.0,
        "ask_px": 2.0,
        "ask_sz": 1.0,
    }
    rec = from_row(row)
    assert isinstance(rec, BookTicker)
    assert rec.update_id is None


def test_from_row_derivative_ticker():
    row = {
        **_base("derivative_ticker"),
        "last_price": 50000.0,
        "mark_price": 50000.4,
        "index_price": 50001.0,
        "funding_rate": 0.0001,
        "predicted_funding_rate": 0.0003,
        "funding_timestamp": _BASE_TS + 28800_000_000_000,
        "open_interest": 12345.0,
    }
    rec = from_row(row)
    assert isinstance(rec, DerivativeTicker)
    assert rec.mark_price == 50000.4
    assert rec.index_price == 50001.0
    assert rec.funding_rate == 0.0001
    assert rec.open_interest == 12345.0


def test_from_row_derivative_ticker_all_nullable_none():
    """All nullable fields absent → all default to None."""
    row = _base("derivative_ticker")
    rec = from_row(row)
    assert isinstance(rec, DerivativeTicker)
    assert rec.last_price is None
    assert rec.mark_price is None
    assert rec.funding_rate is None


def test_from_row_options_chain():
    row = {
        **_base("options_chain"),
        "symbol": "deribit:BTC-30JUN-50000-C",
        "symbol_raw": "BTC-30JUN-50000-C",
        "underlying": "BTC",
        "underlying_price": 50000.0,
        "strike": 50000.0,
        "expiry": 1_900_000_000_000_000_000,
        "opt_type": "C",
        "mark_price": 0.05,
        "mark_iv": 65.0,
        "bid_px": 0.04,
        "bid_sz": 2.0,
        "bid_iv": 64.0,
        "ask_px": 0.06,
        "ask_sz": 1.0,
        "ask_iv": 66.0,
        "last_price": 0.045,
        "open_interest": 10.0,
        "delta": 0.5,
        "gamma": 0.001,
        "vega": 12.0,
        "theta": -3.0,
        "rho": 1.0,
    }
    rec = from_row(row)
    assert isinstance(rec, OptionsChain)
    assert rec.opt_type == OptType.CALL
    assert rec.strike == 50000.0
    assert rec.mark_iv == 65.0
    assert rec.delta == 0.5
    assert rec.bid_iv == 64.0


def test_from_row_options_chain_put():
    """opt_type 'P' must deserialise to OptType.PUT."""
    row = {
        **_base("options_chain"),
        "symbol": "deribit:BTC-30JUN-50000-P",
        "symbol_raw": "BTC-30JUN-50000-P",
        "underlying": "BTC",
        "underlying_price": None,
        "strike": 50000.0,
        "expiry": 1_900_000_000_000_000_000,
        "opt_type": "P",
    }
    rec = from_row(row)
    assert isinstance(rec, OptionsChain)
    assert rec.opt_type == OptType.PUT


def test_from_row_funding():
    row = {
        **_base("funding"),
        "funding_rate": 0.0001,
        "funding_timestamp": _BASE_TS + 28800_000_000_000,
        "predicted_funding_rate": 0.0003,
        "interval_hours": 8,
    }
    rec = from_row(row)
    assert isinstance(rec, Funding)
    assert rec.funding_rate == 0.0001
    assert rec.predicted_funding_rate == 0.0003
    assert rec.interval_hours == 8


def test_from_row_funding_nullable_defaults():
    """predicted_funding_rate, funding_timestamp, interval_hours default to None."""
    row = {**_base("funding"), "funding_rate": 0.0002}
    rec = from_row(row)
    assert isinstance(rec, Funding)
    assert rec.funding_rate == 0.0002
    assert rec.predicted_funding_rate is None
    assert rec.funding_timestamp is None
    assert rec.interval_hours is None


def test_from_row_open_interest():
    row = {
        **_base("open_interest"),
        "open_interest": 99999.0,
        "open_interest_value": 5_000_000.0,
    }
    rec = from_row(row)
    assert isinstance(rec, OpenInterest)
    assert rec.open_interest == 99999.0
    assert rec.open_interest_value == 5_000_000.0


def test_from_row_open_interest_no_value():
    """open_interest_value is optional."""
    row = {**_base("open_interest"), "open_interest": 1.0}
    rec = from_row(row)
    assert isinstance(rec, OpenInterest)
    assert rec.open_interest_value is None


def test_from_row_liquidation():
    row = {
        **_base("liquidation"),
        "price": 48950.0,
        "amount": 1.5,
        "side": "sell",
        "id": "liq-001",
    }
    rec = from_row(row)
    assert isinstance(rec, Liquidation)
    assert rec.price == 48950.0
    assert rec.amount == 1.5
    assert rec.side == Side.SELL
    assert rec.id == "liq-001"


def test_from_row_liquidation_no_id():
    """id field is optional for Liquidation."""
    row = {**_base("liquidation"), "price": 1.0, "amount": 1.0, "side": "buy"}
    rec = from_row(row)
    assert isinstance(rec, Liquidation)
    assert rec.id is None


def test_from_row_ohlcv():
    row = {
        **_base("ohlcv"),
        "interval": "1m",
        "open": 50000.0,
        "high": 50500.0,
        "low": 49800.0,
        "close": 50200.0,
        "volume": 123.45,
        "buy_volume": 80.0,
        "sell_volume": 43.45,
        "num_trades": 1000,
    }
    rec = from_row(row)
    assert isinstance(rec, OHLCV)
    assert rec.interval == "1m"
    assert rec.open == 50000.0
    assert rec.close == 50200.0
    assert rec.volume == 123.45
    assert rec.buy_volume == 80.0
    assert rec.sell_volume == 43.45
    assert rec.num_trades == 1000


def test_from_row_ohlcv_buy_sell_volume_defaults():
    """buy_volume and sell_volume default to 0.0 when absent."""
    row = {
        **_base("ohlcv"),
        "interval": "1h",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10.0,
    }
    rec = from_row(row)
    assert isinstance(rec, OHLCV)
    assert rec.buy_volume == 0.0
    assert rec.sell_volume == 0.0
    assert rec.num_trades is None


def test_from_row_unknown_channel_raises():
    """from_row raises ValueError for unrecognised channel tags."""
    import pytest

    with pytest.raises(ValueError, match="Unknown channel tag"):
        from_row({**_base("not_a_channel")})

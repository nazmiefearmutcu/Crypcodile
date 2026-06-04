import logging

from crocodile.exchanges.deribit.connector import (
    DeribitConnector,
    build_channels,
    parse_instruments,
)
from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.records import Record, Trade
from crocodile.sink.base import Sink

# ---------------------------------------------------------------------------
# Minimal in-memory sink for testing
# ---------------------------------------------------------------------------

class _MemSink(Sink):
    def __init__(self) -> None:
        self.records: list[Record] = []

    async def put(self, record: Record) -> None:
        self.records.append(record)

    async def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# build_channels
# ---------------------------------------------------------------------------

def test_build_channels():
    chans = build_channels(["BTC-PERPETUAL"], ["trade", "book_delta", "derivative_ticker"])
    assert "trades.BTC-PERPETUAL.raw" in chans
    assert "book.BTC-PERPETUAL.raw" in chans
    assert "ticker.BTC-PERPETUAL" in chans


def test_build_channels_deduplicates_book():
    # book_delta and book_snapshot map to the same wire channel — must be collapsed.
    chans = build_channels(["ETH-PERPETUAL"], ["book_delta", "book_snapshot"])
    book_chans = [c for c in chans if "book" in c]
    assert len(book_chans) == 1


def test_build_channels_multiple_symbols():
    chans = build_channels(["BTC-PERPETUAL", "ETH-PERPETUAL"], ["trade"])
    assert "trades.BTC-PERPETUAL.raw" in chans
    assert "trades.ETH-PERPETUAL.raw" in chans


# ---------------------------------------------------------------------------
# parse_instruments
# ---------------------------------------------------------------------------

def test_parse_instruments():
    raw = {
        "result": [
            {
                "instrument_name": "BTC-30JUN-50000-C",
                "kind": "option",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "strike": 50000.0,
                "expiration_timestamp": 1700000000000,
                "option_type": "call",
                "tick_size": 0.0005,
                "contract_size": 1.0,
            }
        ]
    }
    insts = parse_instruments(raw)
    assert insts[0].canonical == "deribit:BTC-30JUN-50000-C"
    assert insts[0].opt_type == "C" and insts[0].expiry == 1700000000000 * 1_000_000


def test_parse_instruments_put():
    raw = {
        "result": [
            {
                "instrument_name": "BTC-30JUN-50000-P",
                "kind": "option",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "strike": 50000.0,
                "expiration_timestamp": 1700000000000,
                "option_type": "put",
                "tick_size": 0.0005,
                "contract_size": 1.0,
            }
        ]
    }
    insts = parse_instruments(raw)
    assert len(insts) == 1
    assert insts[0].opt_type == "P"


def test_parse_instruments_unknown_option_type_skipped(caplog):
    """Unknown option_type should log a warning and skip the instrument."""
    raw = {
        "result": [
            {
                "instrument_name": "BTC-30JUN-50000-X",
                "kind": "option",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "strike": 50000.0,
                "expiration_timestamp": 1700000000000,
                "option_type": "exotic",  # not 'call' or 'put'
                "tick_size": 0.0005,
                "contract_size": 1.0,
            }
        ]
    }
    with caplog.at_level(logging.WARNING, logger="crocodile.exchanges.deribit.connector"):
        insts = parse_instruments(raw)
    assert len(insts) == 0
    assert "exotic" in caplog.text


def test_parse_instruments_empty_result():
    insts = parse_instruments({"result": []})
    assert insts == []


def test_parse_instruments_missing_result_key():
    # Simulates an error response (e.g. rate-limit) that has no 'result' key.
    insts = parse_instruments({})
    assert insts == []


def test_parse_instruments_perpetual():
    raw = {
        "result": [
            {
                "instrument_name": "BTC-PERPETUAL",
                "kind": "future",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "tick_size": 0.5,
                "contract_size": 10.0,
            }
        ]
    }
    insts = parse_instruments(raw)
    assert len(insts) == 1
    assert insts[0].kind == "perpetual"
    assert insts[0].canonical == "deribit:BTC-PERPETUAL"


# ---------------------------------------------------------------------------
# DeribitConnector — construction, subscribe_channels, normalize delegation
# ---------------------------------------------------------------------------

def test_connector_subscribe_channels_wired():
    """_sub_channels is built from symbols x channels at construction time."""
    registry = InstrumentRegistry()
    sink = _MemSink()
    conn = DeribitConnector(
        symbols=["BTC-PERPETUAL"],
        channels=["trade", "book_delta"],
        out=sink,
        registry=registry,
    )
    chans = conn.subscribe_channels()
    assert "trades.BTC-PERPETUAL.raw" in chans
    assert "book.BTC-PERPETUAL.raw" in chans


def test_connector_subscribe_channels_returns_list():
    """subscribe_channels() must return a list (not a set or generator)."""
    registry = InstrumentRegistry()
    sink = _MemSink()
    conn = DeribitConnector(
        symbols=["ETH-PERPETUAL"],
        channels=["derivative_ticker"],
        out=sink,
        registry=registry,
    )
    result = conn.subscribe_channels()
    assert isinstance(result, list)


def test_connector_normalize_delegates_to_normalize_message():
    """normalize() should delegate to deribit normalize_message and yield Trade records."""
    registry = InstrumentRegistry()
    sink = _MemSink()
    conn = DeribitConnector(
        symbols=["BTC-PERPETUAL"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    # Minimal trades WS message
    msg = {
        "method": "subscription",
        "params": {
            "channel": "trades.BTC-PERPETUAL.raw",
            "data": [
                {
                    "trade_id": "abc123",
                    "trade_seq": 1,
                    "instrument_name": "BTC-PERPETUAL",
                    "price": 50000.0,
                    "amount": 1.0,
                    "direction": "buy",
                    "timestamp": 1700000000000,
                    "index_price": 50001.0,
                    "mark_price": 50000.5,
                }
            ],
        },
    }
    records = list(conn.normalize(msg, local_ts=999))
    assert len(records) == 1
    assert isinstance(records[0], Trade)
    assert records[0].price == 50000.0


def test_connector_normalize_non_dict_returns_empty():
    """normalize() with a non-dict message should yield nothing."""
    registry = InstrumentRegistry()
    sink = _MemSink()
    conn = DeribitConnector(
        symbols=["BTC-PERPETUAL"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    records = list(conn.normalize("not a dict", local_ts=0))
    assert records == []

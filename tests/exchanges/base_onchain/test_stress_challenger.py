import math
from collections.abc import AsyncIterator
from typing import cast

import pytest

from crocodile.core.ingest.transport import Transport
from crocodile.core.schema.enums import Side
from crocodile.core.schema.records import BookSnapshot, BookTicker, Trade
from crocodile.crypto.exchanges.base_onchain.normalize import normalize_onchain_update


def test_normalize_standard_case() -> None:
    """A standard update message to establish a baseline."""
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "reserve0": 10.0,
            "reserve1": 500000.0,
            # A pool that reports its tick curve is the baseline; a reserves-only payload
            # has no book to report, which would make this a test of nothing.
            "liquidity": 100000,
            "tick": 0,
        },
        "swaps": [
            {
                "tx_hash": "0x123",
                "log_index": 0,
                "timestamp": 1600000001,
                "price": 50100.0,
                "amount": 0.1,
                "is_buy": True,
            }
        ]
    }

    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 3

    trade = cast(Trade, records[0])
    ticker = cast(BookTicker, records[1])
    snapshot = cast(BookSnapshot, records[2])

    assert isinstance(trade, Trade)
    assert trade.price == 50100.0
    assert trade.amount == 0.1
    assert trade.side == Side.BUY

    assert isinstance(ticker, BookTicker)
    assert ticker.bid_px < 50000.0 < ticker.ask_px
    assert ticker.bid_sz > 0.0
    assert ticker.ask_sz > 0.0

    assert isinstance(snapshot, BookSnapshot)
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    assert snapshot.bids[0] == (ticker.bid_px, ticker.bid_sz)
    assert snapshot.asks[0] == (ticker.ask_px, ticker.ask_sz)


def test_normalize_extreme_prices() -> None:
    """Test price edge cases: zero, negative, extremely small, extremely large, inf, nan."""
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {},
        "swaps": []
    }
    
    # 1. Zero price -> should return early and yield no ticker/snapshot
    msg = dict(base_msg, state={"price": 0.0, "reserve0": 10.0, "reserve1": 10.0})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

    # 2. Negative price -> should return early and yield no ticker/snapshot
    msg = dict(base_msg, state={"price": -100.0, "reserve0": 10.0, "reserve1": 10.0})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

    # 3. Extremely small price (1e-30) -> should yield ticker and snapshot.
    # `liquidity` is what keeps this a price test: without a curve the payload has no book
    # at all, so an extreme price would no longer reach any of the arithmetic under test.
    msg = dict(
        base_msg,
        state={"price": 1e-30, "reserve0": 1.0, "reserve1": 1.0, "liquidity": 100000, "tick": 0},
    )
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    ticker = cast(BookTicker, records[0])
    assert ticker.bid_px < 1e-30 < ticker.ask_px
    assert math.isfinite(ticker.bid_sz)
    assert ticker.bid_sz > 0.0

    # 4. Extremely large price (1e30) -> should yield ticker and snapshot
    msg = dict(
        base_msg,
        state={"price": 1e30, "reserve0": 1.0, "reserve1": 1.0, "liquidity": 100000, "tick": 0},
    )
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    ticker = cast(BookTicker, records[0])
    assert ticker.bid_px < 1e30 < ticker.ask_px
    assert math.isfinite(ticker.bid_sz)
    assert math.isfinite(ticker.ask_sz)
    assert ticker.bid_sz > 0.0
    assert ticker.ask_sz > 0.0

    # 5. Infinity price -> should return early and yield no ticker/snapshot
    msg = dict(base_msg, state={"price": float("inf"), "reserve0": 1.0, "reserve1": 1.0})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

    # 6. NaN price -> should return early and yield no ticker/snapshot
    msg = dict(base_msg, state={"price": float("nan"), "reserve0": 1.0, "reserve1": 1.0})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0


def test_normalize_extreme_reserves() -> None:
    """Test reserve edge cases: zero, negative, very large, inf, nan."""
    # Every case carries `liquidity`/`tick` so the book comes off the curve and the reserve
    # value under test is exercised where it still has an effect: the validation that runs
    # before any book is built. Sizes are the curve's, so no reserve floor is asserted --
    # a level the curve holds nothing at is dropped now rather than floored to 0.0001.
    curve = {"liquidity": 100000, "tick": 0}
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {"price": 100.0, **curve},
        "swaps": []
    }

    # 1. Missing reserves -> should default to 0.0 and not disturb the book
    msg = dict(base_msg, state={"price": 100.0, **curve})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    ticker = cast(BookTicker, records[0])
    assert ticker.bid_sz > 0.0
    assert ticker.ask_sz > 0.0

    # 2. Zero reserves
    msg = dict(base_msg, state={"price": 100.0, "reserve0": 0.0, "reserve1": 0.0, **curve})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    ticker = cast(BookTicker, records[0])
    assert ticker.bid_sz > 0.0
    assert ticker.ask_sz > 0.0

    # 3. Negative reserves -> should be clamped to 0.0
    msg = dict(base_msg, state={"price": 100.0, "reserve0": -10.0, "reserve1": -20.0, **curve})
    records = list(normalize_onchain_update(msg, local_ts=9999))
    ticker = cast(BookTicker, records[0])
    assert ticker.bid_sz > 0.0
    assert ticker.ask_sz > 0.0

    # 4. Infinity reserves -> should be discarded
    msg = dict(
        base_msg,
        state={"price": 100.0, "reserve0": float("inf"), "reserve1": float("inf"), **curve},
    )
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

    # 5. NaN reserves -> should be discarded
    msg = dict(
        base_msg,
        state={"price": 100.0, "reserve0": float("nan"), "reserve1": float("nan"), **curve},
    )
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0


def test_normalize_large_swaps() -> None:
    """Test with a large number of swaps to verify scalability and speed."""
    num_swaps = 10000
    swaps = [
        {
            "tx_hash": f"0x{i:064x}",
            "log_index": i % 100,
            "timestamp": 1600000000 + i,
            "price": 50000.0 + (i / 100.0),
            "amount": 0.001 * (i + 1),
            "is_buy": (i % 2 == 0),
        }
        for i in range(num_swaps)
    ]
    
    msg = {
        "type": "onchain_update",
        "block": 12345,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "reserve0": 10.0,
            "reserve1": 500000.0,
            # Keeps the two book records in the count so the swap total stays the subject.
            "liquidity": 100000,
            "tick": 0,
        },
        "swaps": swaps
    }
    
    records = list(normalize_onchain_update(msg, local_ts=9999))
    # 10000 swaps -> 10000 Trade records + 1 BookTicker + 1 BookSnapshot = 10002 records
    assert len(records) == num_swaps + 2
    assert isinstance(records[0], Trade)
    assert isinstance(records[-2], BookTicker)
    assert isinstance(records[-1], BookSnapshot)


def test_normalize_corrupted_swaps() -> None:
    """Test with corrupted or unexpected types in swaps."""
    # 1. Swap with extreme values
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        # Keeps the two book records in the count so the corrupted swap stays the subject.
        "state": {"price": 100.0, "liquidity": 100000, "tick": 0},
        "swaps": [
            {
                "tx_hash": "0x123",
                "log_index": 0,
                "timestamp": 1600000001,
                "price": float("inf"),
                "amount": float("nan"),
                "is_buy": True,
            }
        ]
    }
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 3
    trade = cast(Trade, records[0])
    assert math.isinf(trade.price)
    assert math.isnan(trade.amount)


def test_normalize_missing_top_level_keys() -> None:
    """Verify missing top level keys trigger KeyError as expected,
    but confirm what crashes and where.
    """
    # If pool is missing
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {"price": 100.0},
    }
    with pytest.raises(KeyError) as excinfo:
        list(normalize_onchain_update(msg, local_ts=9999))
    assert excinfo.value.args[0] == "pool"

    # If pool_type is missing
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "timestamp": 1600000000,
        "state": {"price": 100.0},
    }
    with pytest.raises(KeyError) as excinfo:
        list(normalize_onchain_update(msg, local_ts=9999))
    assert excinfo.value.args[0] == "pool_type"

    # If state is missing
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
    }
    with pytest.raises(KeyError) as excinfo:
        list(normalize_onchain_update(msg, local_ts=9999))
    assert excinfo.value.args[0] == "state"


def test_normalize_missing_state_keys() -> None:
    """Verify state missing price triggers KeyError."""
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {"reserve0": 10.0},
    }
    with pytest.raises(KeyError) as excinfo:
        list(normalize_onchain_update(msg, local_ts=9999))
    assert excinfo.value.args[0] == "price"


def test_normalize_corrupted_types() -> None:
    """Verify normalizer handles corrupted types, e.g. state is None
    or state price is string, or swaps is not a list.
    """
    # State is None -> raises TypeError: 'NoneType' object is not subscriptable
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": None,
    }
    with pytest.raises(TypeError):
        list(normalize_onchain_update(msg, local_ts=9999))

    # Swaps is not iterable (e.g. an integer) -> raises TypeError: 'int' object is not iterable
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {"price": 100.0},
        "swaps": 12345
    }
    with pytest.raises(TypeError):
        list(normalize_onchain_update(msg, local_ts=9999))


@pytest.mark.asyncio
async def test_connector_dlq_on_corrupted_message() -> None:
    """Verify that a corrupted or invalid message in the connector run
    loop is caught and placed in DLQ.
    """
    from crocodile.core.sink.memory import MemorySink
    from crocodile.crypto.exchanges.base_onchain.connector import BaseOnchainConnector
    from crocodile.crypto.instruments.registry import InstrumentRegistry

    class MockTransport(Transport):
        def __init__(self, messages: list[bytes]) -> None:
            self.messages = messages

        async def connect(self) -> None:
            pass

        async def close(self) -> None:
            pass

        async def send(self, data: bytes) -> None:
            pass

        def __aiter__(self) -> AsyncIterator[bytes]:
            return self._iter()

        async def _iter(self) -> AsyncIterator[bytes]:
            while self.messages:
                yield self.messages.pop(0)

    sink = MemorySink()
    registry = InstrumentRegistry()
    connector = BaseOnchainConnector(
        symbols=["cbBTC-USDC"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )

    # We yield two raw byte strings: one unparseable JSON,
    # and one with missing pool_type (raises KeyError)
    messages = [
        b"invalid json {",
        (
            b'{"type": "onchain_update", "block": 100, "pool": "cbBTC-USDC", '
            b'"timestamp": 1600000000, "state": {"price": 100.0}}'
        )
    ]
    connector.transport = MockTransport(messages)

    # Run the connector with max_reconnects=0 so that any unhandled exception
    # fails immediately instead of hanging
    await connector.run(max_reconnects=0)

    # Verify DLQ
    dlq_items = connector._dlq.drain()
    assert len(dlq_items) == 2

    # First one is JSONDecodeError
    assert dlq_items[0].error_type == "JSONDecodeError"
    assert dlq_items[0].raw == b"invalid json {"

    # Second one is KeyError (missing pool_type)
    assert dlq_items[1].error_type == "KeyError"
    assert "pool_type" in dlq_items[1].traceback



import math
from typing import Any, cast

import pytest

from crocodile.core.schema.records import BookSnapshot, BookTicker
from crocodile.crypto.exchanges.base_onchain.normalize import normalize_onchain_update


def test_extreme_prices() -> None:
    """Verify normalize_onchain_update behavior with extreme prices."""
    # `liquidity`/`tick` keep every case on the tick-curve path: this test is about how
    # extreme prices are parsed and bounded, not about which pools have a book at all.
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "reserve0": 10.0,
            "reserve1": 500000.0,
            "decimals0": 8,
            "decimals1": 6,
            "liquidity": 100000,
            "tick": 0,
        },
        "swaps": []
    }

    # 1. Float string price (e.g., "50000.0") should be parsed successfully
    msg_str = dict(base_msg)
    msg_str["state"] = dict(base_msg["state"], price="50000.0")
    records = list(normalize_onchain_update(msg_str, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # 2. Extreme small price (underflow) e.g. 1e-300
    msg_underflow = dict(base_msg)
    msg_underflow["state"] = dict(base_msg["state"], price=1e-300, liquidity=100000, tick=12)
    records = list(normalize_onchain_update(msg_underflow, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # 3. Extreme large price (overflow) e.g. 1e300
    msg_overflow = dict(base_msg)
    msg_overflow["state"] = dict(base_msg["state"], price=1e300, liquidity=100000, tick=12)
    records = list(normalize_onchain_update(msg_overflow, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # 4. Negative, NaN, Inf, and -Inf prices should be discarded (return early/no records)
    for bad_price in [-50000.0, float("nan"), float("inf"), float("-inf")]:
        msg_bad = dict(base_msg)
        msg_bad["state"] = dict(base_msg["state"], price=bad_price)
        records = list(normalize_onchain_update(msg_bad, local_ts=9999))
        assert len(records) == 0

def test_extreme_reserves() -> None:
    """Verify behavior with extreme reserves."""
    # `liquidity`/`tick` keep the book on the tick-curve path so the reserve values below
    # are exercised where they still matter -- the shared validation above the branch.
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "decimals0": 8,
            "decimals1": 6,
            "liquidity": 100000,
            "tick": 0,
        },
        "swaps": []
    }

    # Underflow reserve (1e-300)
    msg_underflow = dict(base_msg)
    msg_underflow["state"] = dict(base_msg["state"], reserve0=1e-300, reserve1=1e-300)
    records = list(normalize_onchain_update(msg_underflow, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    # Sizes were floored at 0.0001 here, so `WHERE bid_sz > 0` answered yes for a level
    # the curve holds nothing at. A level with no size behind it is now dropped instead,
    # which is why every level that survives carries a real one.
    for _bid_px, bid_sz in snapshot.bids:
        assert bid_sz > 0.0
    for _ask_px, ask_sz in snapshot.asks:
        assert ask_sz > 0.0

    # Overflow reserve (1e300)
    msg_overflow = dict(base_msg)
    msg_overflow["state"] = dict(base_msg["state"], reserve0=1e300, reserve1=1e300)
    records = list(normalize_onchain_update(msg_overflow, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # NaN / Inf reserves are rejected before any book is built
    msg_nan = dict(base_msg)
    msg_nan["state"] = dict(base_msg["state"], reserve0=float("nan"), reserve1=float("inf"))
    records = list(normalize_onchain_update(msg_nan, local_ts=9999))
    assert len(records) == 0

    # Float strings for reserves
    msg_str = dict(base_msg)
    msg_str["state"] = dict(base_msg["state"], reserve0="10.5", reserve1="500000.5")
    records = list(normalize_onchain_update(msg_str, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

def test_float_inputs_for_integers() -> None:
    """Verify behavior when float values are provided for decimals or tickSpacing."""
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "liquidity": 100000,
            "decimals0": 8.7,
            "decimals1": 6.2,
            "tickSpacing": 10.9,
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(base_msg, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

def test_flipped_decimals_and_tick_spacing() -> None:
    """Verify flipped pool decimals, negative tick spacing, and zero tick spacing configurations."""
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "liquidity": 100000,
            "decimals0": 6,
            "decimals1": 18,
            "is_flipped": True,
            "tick": 12,
        },
        "swaps": []
    }

    # 1. Flipped pool decimals with is_flipped=True
    records = list(normalize_onchain_update(base_msg, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # 2. Negative tick spacing
    msg_neg_ts = dict(base_msg)
    msg_neg_ts["state"] = dict(base_msg["state"], tickSpacing=-10)
    records = list(normalize_onchain_update(msg_neg_ts, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # 3. Zero tick spacing
    msg_zero_ts = dict(base_msg)
    msg_zero_ts["state"] = dict(base_msg["state"], tickSpacing=0)
    records = list(normalize_onchain_update(msg_zero_ts, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

def test_the_tick_curve_gives_5_levels_and_aerodrome_gives_no_book() -> None:
    """Five levels is what reading a tick curve produces; Aerodrome has none to read.

    The Aerodrome half reported 5 levels too, off `price * (1 -/+ 0.0005 * i)`, so
    `SELECT avg(ask_px - bid_px) / avg(price)` over every base_onchain book_ticker row
    returned exactly 0.001 -- a constant 10bp spread presented as a measured top of book.
    """
    # Active Uniswap V3 path
    msg_v3 = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "liquidity": 100000,
            "decimals0": 8,
            "decimals1": 6,
            "tickSpacing": 10,
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(msg_v3, local_ts=9999))
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

    # Aerodrome path: reserves only, no tick curve
    msg_aero = {
        "type": "onchain_update",
        "block": 100,
        "pool": "AERO-USDC",
        "pool_type": "aerodrome_v2",
        "timestamp": 1600000000,
        "state": {
            "price": 2.0,
            "reserve0": 1000.0,
            "reserve1": 2000.0,
            "decimals0": 18,
            "decimals1": 6,
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(msg_aero, local_ts=9999))
    assert records == []

def test_unhandled_type_error_in_tick_fallback() -> None:
    """Test that underflow in price ratio leads to tick fallback, which doesn't crash on state['tick'] = None."""
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 1e-320,  # Underflow price_ratio -> 0.0
            "liquidity": 100000,
            "decimals0": 30,
            "decimals1": 0,
            "tick": None,     # Does not cause TypeError anymore
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

def test_unhandled_type_error_in_tick_overflow_fallback() -> None:
    """Test that overflow in price ratio does not silently emit invalid snapshots, but discards it."""
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 1e-300,
            "liquidity": 100000,
            "decimals0": 36,
            "decimals1": 6,
            "is_flipped": True,
            "tick": None,
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

def test_overflow_in_tick_power_calculation() -> None:
    """Verify that extremely large tick spacing does not crash, but discards the update."""
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 50000.0,
            "liquidity": 100000,
            "decimals0": 8,
            "decimals1": 6,
            "tickSpacing": 10**15,
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

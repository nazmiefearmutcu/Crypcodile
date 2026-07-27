from typing import Any, cast

import pytest

from crocodile.core.schema.records import BookSnapshot, BookTicker
from crocodile.crypto.exchanges.base_onchain.normalize import normalize_onchain_update


def test_price_zero_or_negative() -> None:
    # Price is 0 or negative should return empty list of records (no snapshot/ticker)
    for price in [0.0, -0.0, -100.0, -1e-5]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": price,
                "reserve0": 10.0,
                "reserve1": 500000.0,
            },
            "swaps": []
        }
        records = list(normalize_onchain_update(msg, local_ts=9999))
        assert len(records) == 0

def test_price_nan_inf() -> None:
    # NaN, inf, -inf prices should return empty list of records
    for price in [float('nan'), float('inf'), float('-inf')]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": price,
                "reserve0": 10.0,
                "reserve1": 500000.0,
            },
            "swaps": []
        }
        records = list(normalize_onchain_update(msg, local_ts=9999))
        assert len(records) == 0

def test_price_invalid_types() -> None:
    # price is None, boolean, or string-invalid should raise TypeError
    for price in [None, True, False, "invalid"]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": price,
                "reserve0": 10.0,
                "reserve1": 500000.0,
            },
            "swaps": []
        }
        with pytest.raises(TypeError):
            list(normalize_onchain_update(msg, local_ts=9999))

def test_extreme_overflow_underflow_prices() -> None:
    # Extreme price value that fits in float, e.g. 1e300, 1e-300
    for price in [1e300, 1e-300]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": price,
                "reserve0": 10.0,
                "reserve1": 500000.0,
                "liquidity": 1000000,
                "decimals0": 8,
                "decimals1": 6,
                "tickSpacing": 10
            },
            "swaps": []
        }
        # This shouldn't crash and should output 5 levels
        records = list(normalize_onchain_update(msg, local_ts=9999))
        assert len(records) == 2
        snapshot = cast(BookSnapshot, records[1])
        assert len(snapshot.bids) == 5
        assert len(snapshot.asks) == 5

def test_a_pool_with_no_tick_liquidity_yields_no_book_whatever_its_reserves() -> None:
    """Reserve validation still rejects None, and no reserve magnitude buys back a book.

    Reserves used to feed an invented ladder -- `SELECT avg(ask_px - bid_px) / avg(price)`
    over every base_onchain book_ticker row returned exactly 0.001, a constant 10bp spread
    presented as a measured top of book -- so 1e300 and 1e-300 both "produced depth". Only
    a tick curve produces depth now, and these payloads have none.
    """
    for r in [None]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": 100.0,
                "reserve0": r,
                "reserve1": 100.0
            },
            "swaps": []
        }
        with pytest.raises(TypeError):
            list(normalize_onchain_update(msg, local_ts=9999))

    for reserve in [1e300, 1e-300]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": 100.0,
                "reserve0": reserve,
                "reserve1": reserve
            },
            "swaps": []
        }
        records = list(normalize_onchain_update(msg, local_ts=9999))
        assert records == []

def test_float_inputs_for_integers() -> None:
    # decimals and tickSpacing can be float, or string representations of float/int
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 100.0,
            "liquidity": 100000.0, # float for integer liquidity
            "decimals0": 18.5,     # float for integer decimals0
            "decimals1": "6.5",    # invalid string float for decimals1 (falls back to default 18)
            "tickSpacing": 10.7,   # float for tick spacing
            "is_flipped": False
        },
        "swaps": []
    }
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

def test_flipped_decimals_config() -> None:
    # Test flipped config where decimals0 is larger and smaller than decimals1
    for dec0, dec1 in [(18, 6), (6, 18), (0, 36), (36, 0)]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": 100.0,
                "liquidity": 100000,
                "decimals0": dec0,
                "decimals1": dec1,
                "tickSpacing": 10,
                "is_flipped": True
            },
            "swaps": []
        }
        records = list(normalize_onchain_update(msg, local_ts=9999))
        assert len(records) == 2
        snapshot = cast(BookSnapshot, records[1])
        assert len(snapshot.bids) == 5
        assert len(snapshot.asks) == 5

def test_negative_and_zero_tick_spacing() -> None:
    # zero and negative tick spacing should be coerced to 1
    for spacing in [0, -1, -100, -10.5]:
        msg = {
            "type": "onchain_update",
            "block": 100,
            "pool": "cbBTC-USDC",
            "pool_type": "uniswap_v3",
            "timestamp": 1600000000,
            "state": {
                "price": 100.0,
                "liquidity": 100000,
                "decimals0": 18,
                "decimals1": 6,
                "tickSpacing": spacing,
                "is_flipped": False
            },
            "swaps": []
        }
        records = list(normalize_onchain_update(msg, local_ts=9999))
        assert len(records) == 2
        snapshot = cast(BookSnapshot, records[1])
        assert len(snapshot.bids) == 5
        assert len(snapshot.asks) == 5

def test_only_a_tick_curve_gives_depth_5_and_reserve_only_pools_give_no_book() -> None:
    """Depth 5 is a property of the tick curve, not of every base_onchain payload.

    Setups 2 and 3 also reported depth 5 before, off `price * (1 -/+ 0.0005 * i)`, so
    `SELECT avg(ask_px - bid_px) / avg(price)` over every base_onchain book_ticker row
    returned exactly 0.001 -- a constant 10bp spread presented as a measured top of book.
    """
    # Setup 1: Uniswap V3 active
    msg_active = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 100.0,
            "liquidity": 100000,
            "decimals0": 18,
            "decimals1": 6,
            "tickSpacing": 10,
            "is_flipped": False
        },
        "swaps": []
    }
    records_active = list(normalize_onchain_update(msg_active, local_ts=9999))
    snapshot_active = cast(BookSnapshot, records_active[1])
    assert len(snapshot_active.bids) == 5
    assert len(snapshot_active.asks) == 5
    assert snapshot_active.depth == 5
    
    # Setup 2: Uniswap V3 fallback
    msg_fallback = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 100.0,
            "reserve0": 10.0,
            "reserve1": 1000.0,
            "decimals0": 18,
            "decimals1": 6,
            "is_flipped": False
        },
        "swaps": []
    }
    records_fallback = list(normalize_onchain_update(msg_fallback, local_ts=9999))
    assert records_fallback == []

    # Setup 3: Aerodrome V2
    msg_aero = {
        "type": "onchain_update",
        "block": 100,
        "pool": "AERO-USDC",
        "pool_type": "aerodrome_v2",
        "timestamp": 1600000000,
        "state": {
            "price": 100.0,
            "reserve0": 10.0,
            "reserve1": 1000.0,
            "decimals0": 18,
            "decimals1": 6,
            "is_flipped": False
        },
        "swaps": []
    }
    records_aero = list(normalize_onchain_update(msg_aero, local_ts=9999))
    assert records_aero == []

def test_tick_overflow_raises_error() -> None:
    # Force underflow in price_ratio to use state["tick"], and set tick to a huge value to cause OverflowError
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
            "tickSpacing": 10,
            "tick": 1e9,  # This will trigger 1.0001 ** (tick / 2.0) overflow
            "is_flipped": False
        },
        "swaps": []
    }
    # With try/except in levels loop, it should discard the update (no exception raised)
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 0

def test_nan_inf_liquidity() -> None:
    """Neither NaN nor Inf liquidity may raise, and neither may produce a book.

    NaN fails `liquidity > 0`, so it is a payload with no usable curve; it used to land on
    the invented `price * (1 -/+ 0.0005 * i)` ladder and emit a top of book anyway.
    """
    # 1. NaN liquidity is not a curve to read (nan > 0 is False), so no book is emitted
    msg_nan = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 100.0,
            "liquidity": float('nan'),
            "reserve0": 1000.0,
            "reserve1": 100000.0,
            "decimals0": 18,
            "decimals1": 6,
            "tickSpacing": 10,
            "is_flipped": False
        },
        "swaps": []
    }
    records_nan = list(normalize_onchain_update(msg_nan, local_ts=9999))
    assert records_nan == []

    # 2. Inf liquidity executes the active V3 path (inf > 0 is True)
    msg_inf = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": 100.0,
            "liquidity": float('inf'),
            "decimals0": 18,
            "decimals1": 6,
            "tickSpacing": 10,
            "is_flipped": False
        },
        "swaps": []
    }
    records_inf = list(normalize_onchain_update(msg_inf, local_ts=9999))
    # It should discard the update because liquidity is inf
    assert len(records_inf) == 0

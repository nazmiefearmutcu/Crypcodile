import math
from typing import Any, cast
import pytest
from crypcodile.exchanges.base_onchain.normalize import normalize_onchain_update
from crypcodile.schema.records import BookSnapshot, BookTicker

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

def test_extreme_reserves_fallback() -> None:
    # test reserves overflow and underflow on fallback path
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

    # Test extreme reserves values (e.g. 1e300, 1e-300)
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
        assert len(records) == 2
        snapshot = cast(BookSnapshot, records[1])
        assert len(snapshot.bids) == 5
        assert len(snapshot.asks) == 5

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

def test_depth_is_exactly_5() -> None:
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
    snapshot_fallback = cast(BookSnapshot, records_fallback[1])
    assert len(snapshot_fallback.bids) == 5
    assert len(snapshot_fallback.asks) == 5
    assert snapshot_fallback.depth == 5

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
    snapshot_aero = cast(BookSnapshot, records_aero[1])
    assert len(snapshot_aero.bids) == 5
    assert len(snapshot_aero.asks) == 5
    assert snapshot_aero.depth == 5

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
    # 1. NaN liquidity causes fallback to CP path (because nan > 0 is False)
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
    assert len(records_nan) == 2
    snapshot_nan = cast(BookSnapshot, records_nan[1])
    # The sizes should be finite because it fell back to CP formula using finite reserves
    for bid_px, bid_sz in snapshot_nan.bids:
        assert math.isfinite(bid_sz)
        assert bid_sz > 0

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

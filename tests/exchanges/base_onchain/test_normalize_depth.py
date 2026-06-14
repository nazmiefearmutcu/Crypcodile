import math
from typing import Any, cast

from crypcodile.exchanges.base_onchain.normalize import normalize_onchain_update
from crypcodile.schema.records import BookSnapshot


def test_normalize_depth_5_levels_aerodrome() -> None:
    """Verify that Aerodrome V2 pools yield exactly 5 levels of depth and match CP formulas."""
    price = 2.0
    reserve0 = 1000.0
    reserve1 = 2000.0
    
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "AERO-USDC",
        "pool_type": "aerodrome_v2",
        "timestamp": 1600000000,
        "state": {
            "price": price,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "decimals0": 18,
            "decimals1": 6,
        },
        "swaps": []
    }
    
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    
    snapshot = cast(BookSnapshot, records[1])
    
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    
    # Verify exact prices and sizes for constant product AMM
    for i in range(1, 6):
        spread_prev = 0.0005 * (i - 1)
        spread_curr = 0.0005 * i
        
        expected_bid_px = price * (1.0 - spread_curr)
        expected_ask_px = price * (1.0 + spread_curr)
        
        expected_ask_sz = reserve0 * (
            1.0 / math.sqrt(1.0 + spread_prev) - 1.0 / math.sqrt(1.0 + spread_curr)
        )
        expected_bid_sz = reserve0 * (
            1.0 / math.sqrt(1.0 - spread_curr) - 1.0 / math.sqrt(1.0 - spread_prev)
        )
        
        bid_px, bid_sz = snapshot.bids[i - 1]
        ask_px, ask_sz = snapshot.asks[i - 1]
        
        assert math.isclose(bid_px, expected_bid_px, rel_tol=1e-9)
        assert math.isclose(ask_px, expected_ask_px, rel_tol=1e-9)
        assert math.isclose(bid_sz, expected_bid_sz, rel_tol=1e-9)
        assert math.isclose(ask_sz, expected_ask_sz, rel_tol=1e-9)


def test_normalize_depth_5_levels_uniswap_v3_fallback() -> None:
    """Verify Uniswap V3 fallback path has exactly 5 levels and uses CP formula."""
    price = 50000.0
    reserve0 = 10.0
    reserve1 = 500000.0
    
    # Uniswap V3 but WITHOUT 'liquidity' in state
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": price,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "decimals0": 8,
            "decimals1": 6,
        },
        "swaps": []
    }
    
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    
    for i in range(1, 6):
        spread_prev = 0.0005 * (i - 1)
        spread_curr = 0.0005 * i
        
        expected_bid_px = price * (1.0 - spread_curr)
        expected_ask_px = price * (1.0 + spread_curr)
        
        expected_ask_sz = reserve0 * (
            1.0 / math.sqrt(1.0 + spread_prev) - 1.0 / math.sqrt(1.0 + spread_curr)
        )
        expected_bid_sz = reserve0 * (
            1.0 / math.sqrt(1.0 - spread_curr) - 1.0 / math.sqrt(1.0 - spread_prev)
        )
        
        bid_px, bid_sz = snapshot.bids[i - 1]
        ask_px, ask_sz = snapshot.asks[i - 1]
        
        assert math.isclose(bid_px, expected_bid_px, rel_tol=1e-9)
        assert math.isclose(ask_px, expected_ask_px, rel_tol=1e-9)
        assert math.isclose(bid_sz, expected_bid_sz, rel_tol=1e-9)
        assert math.isclose(ask_sz, expected_ask_sz, rel_tol=1e-9)


def test_normalize_depth_uniswap_v3_active_unflipped() -> None:
    """Verify active path for Uniswap V3 (unflipped setup) matches the boundaries math."""
    price = 50000.0
    liquidity = 10000 * 10**8
    decimals0 = 8
    decimals1 = 6
    tick_spacing = 10
    
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": price,
            "liquidity": liquidity,
            "decimals0": decimals0,
            "decimals1": decimals1,
            "tickSpacing": tick_spacing,
            "is_flipped": False
        },
        "swaps": []
    }
    
    records = list(normalize_onchain_update(msg, local_ts=9999))
    snapshot = cast(BookSnapshot, records[1])
    
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    
    # Recalculate ticks and values manually for verification
    dec_diff = decimals0 - decimals1
    price_ratio = price / (10 ** dec_diff)
    tick = math.log(price_ratio) / math.log(1.0001)
    
    for i in range(1, 6):
        ask_t1 = tick + (i - 1) * tick_spacing
        ask_t2 = tick + i * tick_spacing
        bid_t1 = tick - i * tick_spacing
        bid_t2 = tick - (i - 1) * tick_spacing
        
        # Prices
        expected_ask_px = float((1.0001 ** ask_t2) * (10 ** dec_diff))
        expected_bid_px = float((1.0001 ** bid_t1) * (10 ** dec_diff))
        
        # Sizes
        sqrt_ask1 = 1.0001 ** (ask_t1 / 2.0)
        sqrt_ask2 = 1.0001 ** (ask_t2 / 2.0)
        sqrt_bid1 = 1.0001 ** (bid_t1 / 2.0)
        sqrt_bid2 = 1.0001 ** (bid_t2 / 2.0)
        
        expected_ask_sz = (liquidity * (1.0 / sqrt_ask1 - 1.0 / sqrt_ask2)) / (10 ** decimals0)
        expected_bid_sz = (
            ((liquidity * (sqrt_bid2 - sqrt_bid1)) / (10 ** decimals1)) / expected_bid_px
        )
        
        bid_px, bid_sz = snapshot.bids[i - 1]
        ask_px, ask_sz = snapshot.asks[i - 1]
        
        assert math.isclose(bid_px, expected_bid_px, rel_tol=1e-9)
        assert math.isclose(ask_px, expected_ask_px, rel_tol=1e-9)
        assert math.isclose(bid_sz, expected_bid_sz, rel_tol=1e-9)
        assert math.isclose(ask_sz, expected_ask_sz, rel_tol=1e-9)


def test_normalize_depth_uniswap_v3_active_flipped() -> None:
    """Verify active path for Uniswap V3 (flipped setup) matches the boundaries math."""
    price = 50000.0
    liquidity = 10000 * 10**8
    decimals0 = 6
    decimals1 = 8
    tick_spacing = 10
    
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "USDC-cbBTC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": price,
            "liquidity": liquidity,
            "decimals0": decimals0,
            "decimals1": decimals1,
            "tickSpacing": tick_spacing,
            "is_flipped": True
        },
        "swaps": []
    }
    
    records = list(normalize_onchain_update(msg, local_ts=9999))
    snapshot = cast(BookSnapshot, records[1])
    
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5
    
    # Recalculate ticks and values manually for verification
    dec_diff = decimals0 - decimals1
    price_ratio = (10 ** dec_diff) / price
    tick = math.log(price_ratio) / math.log(1.0001)
    
    for i in range(1, 6):
        ask_t1 = tick - i * tick_spacing
        ask_t2 = tick - (i - 1) * tick_spacing
        bid_t1 = tick + (i - 1) * tick_spacing
        bid_t2 = tick + i * tick_spacing
        
        # Prices (flipped setup)
        expected_ask_px = float((1.0001 ** (-ask_t1)) * (10 ** dec_diff))
        expected_bid_px = float((1.0001 ** (-bid_t2)) * (10 ** dec_diff))
        
        # Sizes
        sqrt_ask1 = 1.0001 ** (ask_t1 / 2.0)
        sqrt_ask2 = 1.0001 ** (ask_t2 / 2.0)
        sqrt_bid1 = 1.0001 ** (bid_t1 / 2.0)
        sqrt_bid2 = 1.0001 ** (bid_t2 / 2.0)
        
        expected_ask_sz = (liquidity * (sqrt_ask2 - sqrt_ask1)) / (10 ** decimals1)
        expected_bid_sz = (
            ((liquidity * (1.0 / sqrt_bid1 - 1.0 / sqrt_bid2)) / (10 ** decimals0))
            / expected_bid_px
        )
        
        bid_px, bid_sz = snapshot.bids[i - 1]
        ask_px, ask_sz = snapshot.asks[i - 1]
        
        assert math.isclose(bid_px, expected_bid_px, rel_tol=1e-9)
        assert math.isclose(ask_px, expected_ask_px, rel_tol=1e-9)
        assert math.isclose(bid_sz, expected_bid_sz, rel_tol=1e-9)
        assert math.isclose(ask_sz, expected_ask_sz, rel_tol=1e-9)


def test_normalize_depth_nan_inf_prices_discarded() -> None:
    """Verify that NaN and Inf price updates yield no BookTicker or BookSnapshot records."""
    base_msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "reserve0": 10.0,
            "reserve1": 500000.0,
        },
        "swaps": []
    }
    
    # 1. NaN price
    msg_nan = dict(base_msg)
    state_nan = dict(cast(dict[str, Any], base_msg["state"]))
    state_nan["price"] = float("nan")
    msg_nan["state"] = state_nan
    records_nan = list(normalize_onchain_update(msg_nan, local_ts=9999))
    assert len(records_nan) == 0
    
    # 2. Inf price
    msg_inf = dict(base_msg)
    state_inf = dict(cast(dict[str, Any], base_msg["state"]))
    state_inf["price"] = float("inf")
    msg_inf["state"] = state_inf
    records_inf = list(normalize_onchain_update(msg_inf, local_ts=9999))
    assert len(records_inf) == 0
    
    # 3. Neg Inf price
    msg_neginf = dict(base_msg)
    state_neginf = dict(cast(dict[str, Any], base_msg["state"]))
    state_neginf["price"] = float("-inf")
    msg_neginf["state"] = state_neginf
    records_neginf = list(normalize_onchain_update(msg_neginf, local_ts=9999))
    assert len(records_neginf) == 0


def test_normalize_depth_parameter_coercion() -> None:
    """Verify that decimals and tick spacing parameters are coerced properly."""
    price = 50000.0
    
    msg = {
        "type": "onchain_update",
        "block": 100,
        "pool": "cbBTC-USDC",
        "pool_type": "uniswap_v3",
        "timestamp": 1600000000,
        "state": {
            "price": price,
            "liquidity": 100000,
            # Explicitly None values
            "decimals0": None,
            "decimals1": None,
            "tickSpacing": None,
            "is_flipped": False
        },
        "swaps": []
    }
    
    # Should run fine without raising any TypeErrors/ValueErrors and output exactly 5 levels
    records = list(normalize_onchain_update(msg, local_ts=9999))
    assert len(records) == 2
    snapshot = cast(BookSnapshot, records[1])
    assert len(snapshot.bids) == 5
    assert len(snapshot.asks) == 5

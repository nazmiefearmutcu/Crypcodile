import polars as pl
from crypcodile.analytics.mev_sandwich import MEVSandwichFilter

def test_mev_sandwich_filter_positive():
    trades = [
        {"block": 100, "pool": "AERO-USDC", "log_index": 10, "sender": "0xattacker", "is_buy": True},
        {"block": 100, "pool": "AERO-USDC", "log_index": 11, "sender": "0xvictim", "is_buy": True},
        {"block": 100, "pool": "AERO-USDC", "log_index": 12, "sender": "0xattacker", "is_buy": False},
        {"block": 100, "pool": "AERO-USDC", "log_index": 13, "sender": "0xnormal", "is_buy": True},
    ]
    df = pl.DataFrame(trades)
    res = MEVSandwichFilter.detect_sandwiches(df)
    
    assert res.height == 4
    assert res[0, "is_sandwich"] is True
    assert res[1, "is_sandwich"] is True
    assert res[2, "is_sandwich"] is True
    assert res[3, "is_sandwich"] is False

def test_mev_sandwich_filter_negative():
    trades = [
        {"block": 100, "pool": "AERO-USDC", "log_index": 10, "sender": "0xattacker", "is_buy": True},
        {"block": 101, "pool": "AERO-USDC", "log_index": 11, "sender": "0xvictim", "is_buy": True},
        {"block": 102, "pool": "AERO-USDC", "log_index": 12, "sender": "0xattacker", "is_buy": False},
    ]
    df = pl.DataFrame(trades)
    res = MEVSandwichFilter.detect_sandwiches(df)
    
    assert res.height == 3
    assert not res["is_sandwich"].any()

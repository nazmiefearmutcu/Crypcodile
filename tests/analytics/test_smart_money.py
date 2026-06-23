from __future__ import annotations

from crypcodile.analytics.smart_money import SmartMoneyTracker


def test_smart_money_tracker() -> None:
    smart_addr1 = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"  # Vitalik's address
    smart_addr2 = "0xMEV1111111111111111111111111111111111111"
    random_addr = "0xNormalUser123456789012345678901234567"

    tracker = SmartMoneyTracker([smart_addr1, smart_addr2])

    # Initial states should be None
    assert tracker.get_address_state(smart_addr1) is None

    # 1. Outgoing transfer from smart_addr1 to random_addr
    tracker.process_transfer({
        "from": smart_addr1,
        "to": random_addr,
        "usd_value": 50000.0,
        "timestamp": 1000,
    })

    state1 = tracker.get_address_state(smart_addr1)
    assert state1 is not None
    assert state1["net_flow_usd"] == -50000.0
    assert state1["total_volume_usd"] == 50000.0
    assert state1["tx_count"] == 1
    assert state1["last_active_ts"] == 1000

    # Random address should not be tracked
    assert tracker.get_address_state(random_addr) is None

    # 2. Incoming transfer to smart_addr1 from smart_addr2 (internal transfer)
    tracker.process_transfer({
        "from": smart_addr2,
        "to": smart_addr1,
        "usd_value": 20000.0,
        "timestamp": 2000,
    })

    # smart_addr1 updates: net_flow gets +20000 -> -30000, total volume gets +20000 -> 70000, tx count -> 2
    state1 = tracker.get_address_state(smart_addr1)
    assert state1["net_flow_usd"] == -30000.0
    assert state1["total_volume_usd"] == 70000.0
    assert state1["tx_count"] == 2
    assert state1["last_active_ts"] == 2000

    # smart_addr2 updates: net_flow gets -20000, volume 20000, tx count 1
    state2 = tracker.get_address_state(smart_addr2)
    assert state2 is not None
    assert state2["net_flow_usd"] == -20000.0
    assert state2["total_volume_usd"] == 20000.0
    assert state2["tx_count"] == 1

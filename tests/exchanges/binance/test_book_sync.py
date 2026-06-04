from crocodile.exchanges.binance.book import OrderBookSync, SyncResult


def test_spot_first_event_offset_and_continuity():
    s = OrderBookSync(venue="spot")
    s.set_snapshot(last_update_id=100)
    # stale, dropped (u <= 100)
    assert s.feed(U=90, u=100, pu=None) == SyncResult.DROP
    # first valid: U<=101<=u
    assert s.feed(U=101, u=105, pu=None) == SyncResult.APPLY
    # continuity U == prev_u+1 == 106
    assert s.feed(U=106, u=110, pu=None) == SyncResult.APPLY
    # gap -> resync
    assert s.feed(U=120, u=130, pu=None) == SyncResult.RESYNC


def test_futures_first_event_no_offset_and_pu_continuity():
    s = OrderBookSync(venue="futures")
    s.set_snapshot(last_update_id=100)
    assert s.feed(U=80, u=99, pu=70) == SyncResult.DROP      # u < 100
    assert s.feed(U=95, u=100, pu=90) == SyncResult.APPLY    # U<=100<=u
    assert s.feed(U=101, u=110, pu=100) == SyncResult.APPLY  # pu == prev_u(100)
    assert s.feed(U=111, u=120, pu=999) == SyncResult.RESYNC  # pu != prev_u

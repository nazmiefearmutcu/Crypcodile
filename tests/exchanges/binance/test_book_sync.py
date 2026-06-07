import json
import pathlib

import pytest

from crypcodile.exchanges.binance.book import OrderBookSync, SyncResult, normalize_depth
from crypcodile.schema.records import BookDelta

P = pathlib.Path(__file__).parent / "fixtures"


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


def test_resync_cycle_resets_state_machine():
    """set_snapshot called a second time (resync) must reset _prev_u / _have_first
    so the state machine can re-sync cleanly from the new snapshot."""
    s = OrderBookSync(venue="spot")

    # --- first sync ---
    s.set_snapshot(last_update_id=100)
    assert s.feed(U=101, u=105, pu=None) == SyncResult.APPLY
    assert s.feed(U=106, u=110, pu=None) == SyncResult.APPLY

    # --- resync: caller re-fetches snapshot and calls set_snapshot again ---
    s.set_snapshot(last_update_id=200)

    # Events from the old sync that are now stale must be dropped (u <= 200)
    assert s.feed(U=106, u=110, pu=None) == SyncResult.DROP

    # First event that qualifies against the new snapshot must APPLY
    assert s.feed(U=201, u=205, pu=None) == SyncResult.APPLY

    # Continuity must be tracked relative to the *new* sync, not the old one
    assert s.feed(U=206, u=210, pu=None) == SyncResult.APPLY

    # A gap after the new sync triggers another RESYNC signal
    assert s.feed(U=300, u=310, pu=None) == SyncResult.RESYNC


def test_resync_cycle_futures():
    """Futures venue: second set_snapshot also resets the state machine correctly."""
    s = OrderBookSync(venue="futures")

    # --- first sync ---
    s.set_snapshot(last_update_id=100)
    assert s.feed(U=95, u=100, pu=90) == SyncResult.APPLY
    assert s.feed(U=101, u=110, pu=100) == SyncResult.APPLY

    # --- resync ---
    s.set_snapshot(last_update_id=200)

    # stale events (u < 200) are dropped
    assert s.feed(U=101, u=110, pu=100) == SyncResult.DROP

    # first valid event against new snapshot: U<=200 AND u>=200
    assert s.feed(U=195, u=200, pu=190) == SyncResult.APPLY

    # continuity via pu == prev_u (200)
    assert s.feed(U=201, u=210, pu=200) == SyncResult.APPLY

    # continuity break -> RESYNC
    assert s.feed(U=211, u=220, pu=999) == SyncResult.RESYNC


def test_invalid_venue_raises():
    with pytest.raises(ValueError, match="venue must be 'spot' or 'futures'"):
        OrderBookSync(venue="binance-spot")  # type: ignore[arg-type]


def test_normalize_depth_spot_fixture():
    """spot_depth.json: no pu -> prev_seq_id=None; E ms -> ns; zero-qty removal present."""
    msg = json.loads((P / "spot_depth.json").read_text())
    results = list(normalize_depth(msg, local_ts=42, venue="binance-spot"))
    assert len(results) == 1
    delta = results[0]
    assert isinstance(delta, BookDelta)
    # seq_id = u field from fixture = 105
    assert delta.seq_id == 105
    # spot has no pu -> prev_seq_id must be None
    assert delta.prev_seq_id is None
    # exchange_ts: E=1700000000200 ms -> ns
    assert delta.exchange_ts == 1700000000200 * 1_000_000
    # zero-quantity removal level must be present in bids
    assert (49999.0, 0.0) in delta.bids


def test_normalize_depth_futures_fixture():
    """usdm_depth.json: pu present -> prev_seq_id=pu; E ms -> ns; zero-qty removal in bids."""
    msg = json.loads((P / "usdm_depth.json").read_text())
    results = list(normalize_depth(msg, local_ts=99, venue="binance-usdm"))
    assert len(results) == 1
    delta = results[0]
    assert isinstance(delta, BookDelta)
    # seq_id = u = 100, prev_seq_id = pu = 90
    assert delta.seq_id == 100
    assert delta.prev_seq_id == 90
    # exchange_ts: E=1700000000300 ms -> ns
    assert delta.exchange_ts == 1700000000300 * 1_000_000
    # zero-quantity removal level in bids
    assert (49998.0, 0.0) in delta.bids

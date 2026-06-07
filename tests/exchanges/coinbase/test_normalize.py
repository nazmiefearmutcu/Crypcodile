"""Golden-fixture tests for Coinbase Advanced Trade message normalization (Task 4.6)."""

from __future__ import annotations

import json
import pathlib

from crypcodile.exchanges.coinbase.normalize import _parse_iso_ns, normalize_message
from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookDelta, BookSnapshot, BookTicker, Trade

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Trades via `matches` channel
# ---------------------------------------------------------------------------


def test_match_trade_buy() -> None:
    """``matches`` message → Trade with correct price/amount/side/ts."""
    msg = json.loads((FIXTURES / "match.json").read_text())
    out = list(normalize_message(msg, local_ts=42))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 1
    t = trades[0]
    assert t.price == 50000.10
    assert t.amount == 0.5
    assert t.side == Side.BUY
    assert t.id == "123456"
    assert t.exchange == "coinbase"
    assert t.symbol_raw == "BTC-USD"
    # time "2023-11-14T22:13:20.000000Z" → exchange_ts in ns
    assert t.exchange_ts is not None
    assert t.local_ts == 42
    # Coinbase product_id is canonical; symbol should be "coinbase:BTC-USD"
    assert t.symbol == "coinbase:BTC-USD"


def test_match_sell_side() -> None:
    """``side`` field ``sell`` maps to canonical Side.SELL."""
    msg = {
        "type": "match",
        "trade_id": 999,
        "product_id": "ETH-USD",
        "size": "1.0",
        "price": "2000.0",
        "side": "sell",
        "time": "2023-11-14T22:13:20.000000Z",
    }
    out = list(normalize_message(msg, local_ts=1))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 1
    assert trades[0].side == Side.SELL


# ---------------------------------------------------------------------------
# Book — level2 snapshot
# ---------------------------------------------------------------------------


def test_level2_snapshot() -> None:
    """``snapshot`` message → BookSnapshot with all bid/ask levels."""
    msg = json.loads((FIXTURES / "level2_snapshot.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    snaps = [r for r in out if isinstance(r, BookSnapshot)]
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.is_snapshot is True
    assert snap.symbol_raw == "BTC-USD"
    assert (50000.0, 1.5) in snap.bids
    assert (49999.0, 2.0) in snap.bids
    assert (50001.0, 0.8) in snap.asks
    assert snap.exchange == "coinbase"


def test_level2_snapshot_no_exchange_ts() -> None:
    """Coinbase snapshot has no timestamp → exchange_ts is None."""
    msg = json.loads((FIXTURES / "level2_snapshot.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    snaps = [r for r in out if isinstance(r, BookSnapshot)]
    assert snaps[0].exchange_ts is None


# ---------------------------------------------------------------------------
# Book — level2 incremental update (l2update)
# ---------------------------------------------------------------------------


def test_level2_update_maps_to_book_delta() -> None:
    """``l2update`` message → BookDelta with canonical amount=0.0 for removal."""
    msg = json.loads((FIXTURES / "level2_update.json").read_text())
    out = list(normalize_message(msg, local_ts=8))
    deltas = [r for r in out if isinstance(r, BookDelta)]
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.is_snapshot is False
    assert delta.symbol_raw == "BTC-USD"
    # size "0.0" (zero) → canonical removal signal
    assert (49999.0, 0.0) in delta.bids
    # new/update entries preserved
    assert (50000.0, 2.5) in delta.bids
    assert (50001.0, 1.0) in delta.asks


def test_level2_update_has_exchange_ts() -> None:
    """``l2update`` carries a ``time`` field → exchange_ts set."""
    msg = json.loads((FIXTURES / "level2_update.json").read_text())
    out = list(normalize_message(msg, local_ts=8))
    deltas = [r for r in out if isinstance(r, BookDelta)]
    assert deltas[0].exchange_ts is not None


# ---------------------------------------------------------------------------
# Ticker channel → BookTicker
# ---------------------------------------------------------------------------


def test_ticker_emits_book_ticker() -> None:
    """``ticker`` message → BookTicker with best bid/ask."""
    msg = json.loads((FIXTURES / "ticker.json").read_text())
    out = list(normalize_message(msg, local_ts=9))
    bts = [r for r in out if isinstance(r, BookTicker)]
    assert len(bts) == 1
    bt = bts[0]
    assert bt.bid_px == 50000.0
    assert bt.bid_sz == 0.5
    assert bt.ask_px == 50001.0
    assert bt.ask_sz == 0.3
    assert bt.symbol_raw == "BTC-USD"
    assert bt.exchange == "coinbase"


# ---------------------------------------------------------------------------
# Connector channel-builder + product parser
# ---------------------------------------------------------------------------


def test_build_channels() -> None:
    from crypcodile.exchanges.coinbase.connector import build_channels

    chans = build_channels(["BTC-USD", "ETH-USD"], ["trade", "book_delta"])
    assert set(chans) == {"matches", "level2", "ticker"}


def test_build_channels_ticker_only() -> None:
    from crypcodile.exchanges.coinbase.connector import build_channels

    chans = build_channels(["BTC-USD"], ["book_ticker"])
    assert "ticker" in chans


# ---------------------------------------------------------------------------
# T3-connmisc: ns precision — float64 loses up to ~32ns on microsecond timestamps
# ---------------------------------------------------------------------------


def test_parse_iso_ns_exact_microsecond_precision() -> None:
    """_parse_iso_ns must preserve microsecond precision without float64 rounding.

    ``2023-11-14T22:13:20.999999Z``  →  1_700_000_000_999_999_000 ns exactly.
    The float64 path (``int(dt.timestamp() * 1e9)``) returns 1_700_000_000_999_998_976,
    which is off by 24 ns.
    """
    ts = "2023-11-14T22:13:20.999999Z"
    # Expected: integer arithmetic only
    # 2023-11-14T22:13:20 UTC = 1700000000 s; +999999 µs = +999999000 ns
    expected = 1_700_000_000_999_999_000
    result = _parse_iso_ns(ts)
    assert result == expected, (
        f"_parse_iso_ns({ts!r}) = {result}, want {expected} "
        f"(error = {result - expected if result is not None else 'None'} ns)"
    )


def test_parse_products() -> None:
    import json

    from crypcodile.exchanges.coinbase.connector import parse_products
    from crypcodile.instruments.registry import Kind

    raw = json.loads((FIXTURES / "products.json").read_text())
    insts = parse_products(raw)
    assert len(insts) == 2
    btc = next(i for i in insts if i.symbol_raw == "BTC-USD")
    assert btc.canonical == "coinbase:BTC-USD"
    assert btc.kind == Kind.SPOT
    assert btc.base == "BTC"
    assert btc.quote == "USD"

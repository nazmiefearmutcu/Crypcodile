"""Tests for exchanges.factory.make_connector (Task T7a).

No live network calls.  Tests cover:
- make_connector returns the correct Connector subclass for each known exchange name
- Unknown exchange name raises ValueError with a clear message listing valid names
"""

from __future__ import annotations

import pytest

from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.sink.memory import MemorySink


def _make(exchange: str, **kw):  # type: ignore[no-untyped-def]
    from crypcodile.exchanges.factory import make_connector

    return make_connector(
        exchange=exchange,
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        **kw,
    )


# ---------------------------------------------------------------------------
# Correct class for each known exchange
# ---------------------------------------------------------------------------


def test_make_connector_binance() -> None:
    from crypcodile.exchanges.binance.connector import BinanceConnector

    conn = _make("binance")
    assert isinstance(conn, BinanceConnector)


def test_make_connector_bybit() -> None:
    from crypcodile.exchanges.bybit.connector import BybitConnector

    conn = _make("bybit")
    assert isinstance(conn, BybitConnector)


def test_make_connector_okx() -> None:
    from crypcodile.exchanges.okx.connector import OKXConnector

    conn = _make("okx")
    assert isinstance(conn, OKXConnector)


def test_make_connector_coinbase() -> None:
    from crypcodile.exchanges.coinbase.connector import CoinbaseConnector

    conn = _make("coinbase")
    assert isinstance(conn, CoinbaseConnector)


def test_make_connector_deribit() -> None:
    from crypcodile.exchanges.deribit.connector import DeribitConnector

    conn = _make("deribit")
    assert isinstance(conn, DeribitConnector)


def test_make_connector_base_onchain() -> None:
    from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector

    conn = _make("base_onchain")
    assert isinstance(conn, BaseOnchainConnector)


# ---------------------------------------------------------------------------
# Unknown exchange raises a clear ValueError
# ---------------------------------------------------------------------------


def test_make_connector_unknown_raises() -> None:
    with pytest.raises(ValueError, match="binance") as exc_info:
        _make("unknownexchange")
    # Error message must list all valid names
    msg = str(exc_info.value)
    for name in ("binance", "bybit", "okx", "coinbase", "deribit", "base_onchain"):
        assert name in msg, f"Expected {name!r} in error message: {msg!r}"



def test_make_connector_case_sensitive() -> None:
    """Exchange names are lowercase; 'Binance' (capitalised) must raise."""
    with pytest.raises(ValueError):
        _make("Binance")


# ---------------------------------------------------------------------------
# Extra keyword args are forwarded to the constructor
# ---------------------------------------------------------------------------


def test_make_connector_binance_market_kwarg() -> None:
    """market='usdm' kwarg is forwarded to BinanceConnector."""
    from crypcodile.exchanges.binance.connector import BinanceConnector

    conn = _make("binance", market="usdm")
    assert isinstance(conn, BinanceConnector)
    assert conn.market == "usdm"


def test_make_connector_bybit_category_kwarg() -> None:
    """category='spot' kwarg is forwarded to BybitConnector."""
    from crypcodile.exchanges.bybit.connector import BybitConnector

    conn = _make("bybit", category="spot")
    assert isinstance(conn, BybitConnector)
    assert conn.category == "spot"

"""Tests for exchanges.factory (Task T7a + list_exchanges).

No live network calls.  Tests cover:
- list_exchanges returns sorted registered names
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
# list_exchanges
# ---------------------------------------------------------------------------


def test_list_exchanges_sorted() -> None:
    from crypcodile.exchanges.factory import list_exchanges

    names = list_exchanges()
    assert names == sorted(names)
    assert names == [
        "base_onchain",
        "binance",
        "bybit",
        "coinbase",
        "deribit",
        "gmx_synthetix",
        "okx",
        "superchain",
    ]
    assert "superchain" in names


def test_list_exchanges_returns_copy() -> None:
    """Callers can mutate the returned list without affecting the registry."""
    from crypcodile.exchanges.factory import list_exchanges

    a = list_exchanges()
    a.append("not-an-exchange")
    b = list_exchanges()
    assert "not-an-exchange" not in b
    assert a is not b


def test_list_exchanges_usable_with_make_connector() -> None:
    """Every name from list_exchanges constructs without ValueError."""
    from crypcodile.exchanges.factory import list_exchanges

    for name in list_exchanges():
        conn = _make(name)
        assert conn is not None


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


def test_make_connector_superchain() -> None:
    from unittest.mock import MagicMock, patch

    from crypcodile.exchanges.superchain.connector import SuperchainConnector

    mock_transport = MagicMock()
    mock_transport.rpc_urls = ["http://localhost:8545"]
    with patch(
        "crypcodile.exchanges.base_onchain.connector.BaseOnchainTransport",
        return_value=mock_transport,
    ) as mock_cls:
        # Default SuperchainConnector.exchange is "optimism"; do not pass
        # exchange= here — it collides with make_connector's exchange name.
        conn = _make(
            "superchain",
            rpc_url="http://localhost:8545",
            chain_id=10,
        )
    assert isinstance(conn, SuperchainConnector)
    assert conn.chain_id == 10
    assert conn.name == "optimism"
    mock_cls.assert_called_once()
    # rpc_url is the first positional arg to BaseOnchainTransport
    assert mock_cls.call_args.args[0] == "http://localhost:8545"
    assert conn.transport is mock_transport


# ---------------------------------------------------------------------------
# Unknown exchange raises a clear ValueError
# ---------------------------------------------------------------------------


def test_make_connector_unknown_raises() -> None:
    with pytest.raises(ValueError, match="binance") as exc_info:
        _make("unknownexchange")
    # Error message must list all valid names
    msg = str(exc_info.value)
    for name in (
        "binance",
        "bybit",
        "okx",
        "coinbase",
        "deribit",
        "base_onchain",
        "superchain",
    ):
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

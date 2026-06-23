"""Connector factory — maps exchange name to the correct Connector subclass.

Usage::

    from crypcodile.exchanges.factory import make_connector

    conn = make_connector(
        exchange="binance",
        symbols=["BTCUSDT"],
        channels=["trade", "book_ticker"],
        out=sink,
        registry=registry,
        market="usdm",          # forwarded as **kw to BinanceConnector
    )

Valid exchange names: ``binance``, ``bybit``, ``okx``, ``coinbase``, ``deribit``.
Extra keyword arguments (e.g. ``market`` for Binance, ``category`` for Bybit,
``region`` for OKX) are forwarded to the connector constructor unchanged.
"""

from __future__ import annotations

from typing import Any

from crypcodile.exchanges.base import Connector
from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector
from crypcodile.exchanges.binance.connector import BinanceConnector
from crypcodile.exchanges.bybit.connector import BybitConnector
from crypcodile.exchanges.coinbase.connector import CoinbaseConnector
from crypcodile.exchanges.deribit.connector import DeribitConnector
from crypcodile.exchanges.okx.connector import OKXConnector
from crypcodile.exchanges.gmx_synthetix.connector import GMXSynthetixConnector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.sink.base import Sink

_REGISTRY: dict[str, type[Connector]] = {
    "binance": BinanceConnector,
    "bybit": BybitConnector,
    "coinbase": CoinbaseConnector,
    "deribit": DeribitConnector,
    "okx": OKXConnector,
    "base_onchain": BaseOnchainConnector,
    "gmx_synthetix": GMXSynthetixConnector,
}


_VALID_NAMES = sorted(_REGISTRY)


def make_connector(
    exchange: str,
    symbols: list[str],
    channels: list[str],
    out: Sink,
    registry: InstrumentRegistry,
    **kw: Any,
) -> Connector:
    """Instantiate and return the correct :class:`~.base.Connector` subclass.

    Parameters
    ----------
    exchange:
        Lowercase exchange name.  Valid values: ``binance``, ``bybit``,
        ``coinbase``, ``deribit``, ``okx``.
    symbols:
        List of symbol strings to subscribe to (exchange-native format).
    channels:
        List of canonical channel names (e.g. ``"trade"``, ``"book_ticker"``).
    out:
        Sink to receive normalised records.
    registry:
        Instrument registry for symbol resolution.
    **kw:
        Extra keyword arguments forwarded verbatim to the connector constructor
        (e.g. ``market="usdm"`` for Binance, ``category="spot"`` for Bybit,
        ``region="us"`` for OKX).

    Raises
    ------
    ValueError
        If *exchange* is not a recognised name.  The error message lists all
        valid names.
    """
    cls = _REGISTRY.get(exchange)
    if cls is None:
        raise ValueError(
            f"Unknown exchange {exchange!r}. Valid names: {_VALID_NAMES}"
        )
    return cls(
        symbols=symbols,
        channels=channels,
        out=out,
        registry=registry,
        **kw,
    )

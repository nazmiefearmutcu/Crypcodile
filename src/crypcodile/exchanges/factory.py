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

Valid exchange names: ``binance``, ``bybit``, ``coinbase``, ``deribit``,
``okx``, ``base_onchain``, ``derive``, ``gmx_synthetix``, ``superchain``.
Extra keyword arguments (e.g. ``market`` for Binance, ``category`` for Bybit,
``region`` for OKX, ``rpc_url`` / ``viewer_address`` for Derive) are
forwarded to the connector constructor unchanged.
"""

from __future__ import annotations

from typing import Any

from importlib import import_module

from crypcodile.exchanges.base import Connector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.sink.base import Sink

# Connectors are imported lazily so that pulling in one exchange never pays
# for another's dependency tree (e.g. derive -> analytics -> numpy).
_REGISTRY: dict[str, tuple[str, str]] = {
    "binance": ("crypcodile.exchanges.binance.connector", "BinanceConnector"),
    "bybit": ("crypcodile.exchanges.bybit.connector", "BybitConnector"),
    "coinbase": ("crypcodile.exchanges.coinbase.connector", "CoinbaseConnector"),
    "deribit": ("crypcodile.exchanges.deribit.connector", "DeribitConnector"),
    "derive": ("crypcodile.exchanges.derive.connector", "DerivePollConnector"),
    "okx": ("crypcodile.exchanges.okx.connector", "OKXConnector"),
    "base_onchain": ("crypcodile.exchanges.base_onchain.connector", "BaseOnchainConnector"),
    "gmx_synthetix": ("crypcodile.exchanges.gmx_synthetix.connector", "GMXSynthetixConnector"),
    "superchain": ("crypcodile.exchanges.superchain.connector", "SuperchainConnector"),
}


_VALID_NAMES = sorted(_REGISTRY)


def list_exchanges() -> list[str]:
    """Sorted registered exchange names."""
    return list(_VALID_NAMES)


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
        ``coinbase``, ``deribit``, ``derive``, ``okx``, ``base_onchain``,
        ``gmx_synthetix``, ``superchain``.
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
        ``region="us"`` for OKX, ``rpc_url`` / ``viewer_address`` for Derive).

    Raises
    ------
    ValueError
        If *exchange* is not a recognised name.  The error message lists all
        valid names.
    """
    entry = _REGISTRY.get(exchange)
    if entry is None:
        raise ValueError(
            f"Unknown exchange {exchange!r}. Valid names: {_VALID_NAMES}"
        )
    module_path, class_name = entry
    cls: type[Connector] = getattr(import_module(module_path), class_name)
    return cls(
        symbols=symbols,
        channels=channels,
        out=out,
        registry=registry,
        **kw,
    )

from __future__ import annotations

from typing import Any

from crocodile.core.sink.base import Sink
from crocodile.equity.providers.alpaca.connector import AlpacaProvider
from crocodile.equity.providers.base import Provider
from crocodile.equity.providers.finnhub.connector import FinnhubProvider
from crocodile.equity.providers.google_finance.connector import GoogleFinanceProvider
from crocodile.equity.providers.msn_money.connector import MsnMoneyProvider
from crocodile.equity.providers.stooq.connector import StooqProvider
from crocodile.equity.reference.registry import InstrumentRegistry

_REGISTRY: dict[str, type[Provider]] = {
    "alpaca": AlpacaProvider,
    "finnhub": FinnhubProvider,
    "stooq": StooqProvider,
    "google_finance": GoogleFinanceProvider,
    "msn_money": MsnMoneyProvider,
}

_VALID_NAMES = sorted(_REGISTRY)


def make_provider(
    provider: str,
    symbols: list[str],
    channels: list[str],
    out: Sink,
    registry: InstrumentRegistry,
    **kw: Any,
) -> Provider:
    """Instantiate and return the correct Provider subclass.

    Parameters
    ----------
    provider:
        Lowercase provider name. Valid values: ``alpaca``, ``finnhub``.
    symbols:
        List of symbol strings to subscribe to.
    channels:
        List of canonical channel names (e.g. ``"trade"``, ``"quote"``, ``"ohlcv"``).
        ``"bar"`` is still accepted by the connectors that predate the struct
        collapse, but it is a retired tag, not a canonical one, and nothing
        writes it any more.
    out:
        Sink to receive normalised records.
    registry:
        Instrument registry for symbol resolution.
    **kw:
        Extra keyword arguments forwarded verbatim to the provider constructor.

    Raises
    ------
    ValueError
        If *provider* is not a recognised name.
    """
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(f"Unknown provider {provider!r}. Valid names: {_VALID_NAMES}")
    return cls(
        symbols=symbols,
        channels=channels,
        out=out,
        registry=registry,
        **kw,
    )

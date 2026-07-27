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


def list_providers() -> list[str]:
    """Sorted names of the registered equity providers.

    The counterpart of :func:`crocodile.crypto.exchanges.factory.list_exchanges`, and it
    exists for the same reason that one does: something outside this module needs to ask
    which sources this asset class serves. Two callers do — the ``list-exchanges``
    capability, which needs both halves to answer one question about the whole build, and
    the surface projection, which uses it to decide which market a canonical
    ``source:SYMBOL`` belongs to. Both would otherwise read ``_REGISTRY`` across a package
    boundary, which is how a rename becomes a caller's problem.

    Note this cannot be answered with :func:`supported_channels`, which returns ``None``
    both for an unknown provider and for a registered one that declares no channels.
    """
    return list(_VALID_NAMES)


def supported_channels(provider: str) -> frozenset[str] | None:
    """Return the channels ``provider`` declares it can serve, or ``None`` if it has not.

    ``None`` is "not declared", never "all" — see
    :attr:`crocodile.equity.providers.base.Provider.supported_channels`. Callers that
    offer a channel menu should narrow it with this and fall back to the full
    vocabulary when it answers ``None``, so an undeclared connector keeps working
    exactly as it did.

    The CLI needed this because its picker offered all four channels *after* the
    provider was chosen, with no cross-check: `[3] google_finance` then `[2] quote` is a
    dead channel the tool walked you into.
    """
    cls = _REGISTRY.get(provider)
    return None if cls is None else cls.supported_channels


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
        List of canonical channel names (e.g. ``"trade"``, ``"ohlcv"``). ``"bar"`` is
        still accepted by the connectors that predate the struct collapse, but it is a
        retired tag, not a canonical one, and nothing writes it any more. A connector
        that declares :attr:`~crocodile.equity.providers.base.Provider.supported_channels`
        warns for each name outside its set and refuses the run when none survives, so
        ``google_finance`` with ``["quote"]`` raises here rather than polling forever
        and returning nothing; :func:`supported_channels` answers the same question
        without constructing anything.
    out:
        Sink to receive normalised records.
    registry:
        Instrument registry for symbol resolution.
    **kw:
        Extra keyword arguments forwarded verbatim to the provider constructor.

    Raises
    ------
    ValueError
        If *provider* is not a recognised name, or if the connector declares its
        servable channels and none of *channels* is among them.
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

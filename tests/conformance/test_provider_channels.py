"""A channel a connector will not serve is a decision, and decisions are declared.

``google_finance`` stopped emitting ``Quote`` records because the page it scrapes
publishes a last price and no bid/ask — the record it had been building was a
two-sided quote of zero width, at a price nobody quoted, in sizes nobody posted,
labelled ``prov=native``. Removing it was right. Removing it *silently* was not: the
capability appeared on no list, the CLI went on offering the channel after the
provider had been chosen, and ``--channels quote`` polled forever at four fetches per
symbol per cycle and returned nothing.

So the drop is recorded where it happened, on the connector, with the argument — and
these are the gates that keep the record honest. The discipline is
:data:`crocodile.core.capability.IRREDUCIBLE`'s: an exemption without an argument is
a place obligations go to be forgotten, and an exemption for something that is no
longer true is worse.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from crocodile.core.schema.enums import CHANNEL_SUCCESSORS, Channel
from crocodile.equity.providers.base import Provider
from crocodile.equity.providers.factory import (
    _REGISTRY,
    VALID_CHANNELS,
    channels_for_provider,
    supported_channels,
)


def _declaring_providers() -> list[str]:
    return sorted(name for name, cls in _REGISTRY.items() if cls.supported_channels is not None)


def test_at_least_one_connector_declares_what_it_can_serve() -> None:
    """Otherwise every gate below is vacuous."""
    assert _declaring_providers()


def test_every_registered_provider_declares_what_it_can_serve() -> None:
    """``None`` used to be safe here and is not any more.

    It means "has not been through the exercise", and
    :func:`~crocodile.equity.providers.factory.channels_for_provider` answers it with the
    whole vocabulary. That was harmless while the vocabulary was four market-data channels
    every equity source plausibly served. It is now *derived* from these declarations and
    carries ``options_chain``, ``macro_series``, ``insider`` and ``holding_13f`` — so one
    undeclared connector would be offered a menu of channels it has never heard of, which is
    the dead-channel defect this file exists to prevent, reintroduced through the fix for a
    different one.

    So the derived vocabulary and the mandatory declaration are one change: neither is safe
    without the other, and this is the half a reviewer would otherwise have to remember.
    """
    undeclared = sorted(
        name for name, cls in _REGISTRY.items() if cls.supported_channels is None
    )
    assert not undeclared, (
        f"{undeclared} are registered and declare no supported_channels, so the CLI would "
        f"offer each of them every channel any *other* provider serves; declare the set on "
        f"the class"
    )


@pytest.mark.parametrize("provider", sorted(_REGISTRY))
def test_a_declared_channel_is_a_channel_the_schema_knows(provider: str) -> None:
    """A typo in a declaration is a channel nothing can ever write.

    The declarations now feed :data:`~crocodile.equity.providers.factory.VALID_CHANNELS`,
    which the surfaces offer and ``tests/conformance/test_channel_writability.py`` treats as
    the definition of what an equity ingest path can write. A misspelt member would put a
    name into all three that no record carries.
    """
    known = {channel.value for channel in Channel}
    declared = _REGISTRY[provider].supported_channels or frozenset()
    assert declared <= known, f"{provider} declares {sorted(declared - known)}, not a Channel"
    retired = declared & set(CHANNEL_SUCCESSORS)
    assert not retired, (
        f"{provider} declares the retired tag(s) {sorted(retired)}; declare the successor "
        f"and let channels_for_provider widen back to the old spelling, or the set that "
        f"decides the menu holds two names for one channel"
    )


@pytest.mark.parametrize("provider", sorted(_REGISTRY))
def test_a_dropped_channel_carries_the_argument_for_dropping_it(provider: str) -> None:
    """An entry with no reason is a capability that went missing rather than was decided."""
    cls: type[Provider] = _REGISTRY[provider]
    for channel, why in cls.unservable_channels.items():
        assert why.strip(), (
            f"{provider} lists {channel!r} as unservable with no argument; say what the "
            f"connector would have to invent in order to serve it"
        )


@pytest.mark.parametrize("provider", sorted(_REGISTRY))
def test_the_dropped_list_holds_nothing_the_connector_actually_serves(provider: str) -> None:
    """Self-cleaning: a channel that came back must leave the list."""
    cls: type[Provider] = _REGISTRY[provider]
    servable = cls.supported_channels
    if servable is None:
        assert not cls.unservable_channels, (
            f"{provider} explains channels it will not serve but never says which it will; "
            f"declare supported_channels or the explanation guards nothing"
        )
        return
    resurrected = sorted(set(cls.unservable_channels) & servable)
    assert not resurrected, (
        f"{provider} serves {resurrected} and still explains why it does not; "
        f"drop the entry rather than leave a stale excuse lying around"
    )


@pytest.mark.parametrize("provider", _declaring_providers())
def test_the_cli_does_not_offer_a_channel_the_chosen_provider_cannot_serve(
    provider: str,
) -> None:
    """The picker offered all four channels *after* the provider was chosen.

    ``[3] google_finance`` then ``[2] quote`` was a dead channel the tool walked you
    into. The menu is narrowed by what the connector declares, with a retired tag riding
    along behind its successor because the two are one channel under two spellings.
    """
    servable = supported_channels(provider)
    assert servable is not None
    offered = channels_for_provider(provider)

    assert offered, f"{provider} would be offered an empty channel menu"
    for channel in offered:
        assert CHANNEL_SUCCESSORS.get(channel, channel) in servable, (
            f"the CLI offers {channel!r} for {provider}, which declares it cannot serve it"
        )
    assert set(offered) <= set(VALID_CHANNELS)


def test_the_vocabulary_is_exactly_what_the_providers_between_them_declare() -> None:
    """``VALID_CHANNELS`` is derived, and this is what "derived" has to mean.

    A hand-written list agrees with the connectors by inspection until it does not:
    ``index_value`` and ``corp_action`` were written by ``stooq`` and ``msn_money`` and
    typeable by nobody, because the list held four market-data names and had outlived two
    connectors. Equality in both directions — nothing offered that nothing serves, nothing
    served that cannot be asked for.
    """
    served = {
        channel
        for cls in _REGISTRY.values()
        for channel in (cls.supported_channels or frozenset())
    }
    offered = set(VALID_CHANNELS)
    retired_on_offer = {
        retired for retired, successor in CHANNEL_SUCCESSORS.items() if successor in served
    }
    assert offered == served | retired_on_offer


def test_an_undeclared_connector_keeps_the_whole_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` means "not declared", never "serves nothing".

    Narrowing on a guess would hide working channels from a connector that has not been
    through the exercise. Every *registered* connector has now — the gate above requires
    it — so the fallback is exercised against a stand-in rather than against a real
    provider, which is the honest way to keep testing a branch that must go on working for
    the next connector somebody adds before they have finished thinking about it.
    """

    class Undeclared(Provider):
        name = "undeclared"
        supported_channels: ClassVar[frozenset[str] | None] = None

        def normalize(self, msg: object, local_ts: int) -> tuple[()]:  # pragma: no cover
            return ()

        async def list_instruments(self) -> list[object]:  # pragma: no cover
            return []

        async def _subscribe(self, transport: object) -> None:  # pragma: no cover
            return None

    monkeypatch.setitem(_REGISTRY, "undeclared", Undeclared)
    assert channels_for_provider("undeclared") == VALID_CHANNELS
    assert channels_for_provider(None) == VALID_CHANNELS
    assert channels_for_provider("not-a-provider") == VALID_CHANNELS

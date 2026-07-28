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

import pytest

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
    into. The menu is narrowed by what the connector declares, with ``bar`` riding along
    with ``ohlcv`` because it is the retired spelling of the same channel.
    """
    servable = supported_channels(provider)
    assert servable is not None
    offered = channels_for_provider(provider)

    assert offered, f"{provider} would be offered an empty channel menu"
    for channel in offered:
        assert channel in servable or channel == "bar", (
            f"the CLI offers {channel!r} for {provider}, which declares it cannot serve it"
        )
    assert set(offered) <= set(VALID_CHANNELS)


def test_an_undeclared_connector_keeps_the_whole_vocabulary() -> None:
    """``None`` means "not declared", never "serves nothing".

    Narrowing on a guess would hide working channels from every connector that has not
    been through the exercise yet, which is most of them.
    """
    assert channels_for_provider("alpaca") == VALID_CHANNELS
    assert channels_for_provider(None) == VALID_CHANNELS
    assert channels_for_provider("not-a-provider") == VALID_CHANNELS

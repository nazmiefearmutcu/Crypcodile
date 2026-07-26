"""Canonical record types.

Every record leads with the ten fields of :class:`_Header`: the four that identify an
observation, ``asset_class``, ``source_ts``, and the four-field provenance tail. All ten
live on the base and no record can restate or reorder them.

``kw_only=True`` on each record is what makes that possible. msgspec forbids a required
field after a defaulted one, which would otherwise force every struct to repeat the
defaulted tail after its own required fields; keyword-only fields are exempt, and the
base's fields stay positional so they keep leading. The pairing is load-bearing in both
directions: ``kw_only`` on the base instead would order the subclass's fields first and
silently break the header. ``tests/conformance/test_gates.py`` enforces both halves.
"""

from __future__ import annotations

import msgspec

from crocodile.core.schema.enums import AssetClass, Channel, Side, Tape
from crocodile.core.schema.provenance import Provenance

Level = tuple[float, float]
"""(price, size). A size of 0.0 means REMOVE this level."""


class _Header(msgspec.Struct, frozen=True):
    """The ten fields every record carries.

    Never mark this ``kw_only``: msgspec orders positional fields ahead of keyword-only
    ones, so doing so would move each record's own fields in front of the header.
    """

    source: str
    """Venue (crypto) or data provider (equity)."""

    symbol: str
    """Canonical symbol, e.g. ``deribit:BTC-PERPETUAL`` or ``AAPL``."""

    symbol_raw: str
    """The symbol exactly as the source spelled it."""

    local_ts: int
    """UTC epoch nanoseconds at which we observed the record."""

    asset_class: AssetClass
    """Which market this came from, so an absent field reads as normal or as a defect."""

    source_ts: int | None
    """UTC epoch nanoseconds the source stamped, or ``None`` if it stamped nothing.

    Required, with no default. An adapter must state which of those two happened; a
    default would let one of 108 adapters forget and report a silent ``None`` instead.
    """

    prov: Provenance = Provenance.NATIVE
    """How this record came to exist. Never ``UNAVAILABLE`` — see :class:`Provenance`."""

    prov_basis: str = "native"
    """The registered basis name, a key into the provenance registry."""

    prov_confidence: float = 1.0
    """Sampling adequacy within ``prov``'s level, from the basis's registered formula."""

    prov_inputs: list[str] = []
    """The channels the basis consumed.

    A mutable default is safe here and only here: msgspec copies it per instance rather
    than sharing one list across every record, which a conformance test pins down.
    """


class Trade(_Header, frozen=True, kw_only=True, tag=Channel.TRADE.value, tag_field="channel"):
    id: str
    price: float
    amount: float
    side: Side
    liquidation: str | None = None
    l1_gas_fee: float | None = None
    l2_gas_fee: float | None = None
    gas_price: float | None = None
    sender: str | None = None
    is_smart_wallet: bool | None = None
    conditions: list[str] | None = None
    tape: Tape | None = None
    venue: str | None = None


Record = Trade

"""The in-memory identity of a listed security, as the provider layer passes it around.

Its own module and its own name because ``Instrument`` meant two things at once.
:class:`crocodile.core.schema.records.Instrument` is a *record*: tagged ``instrument``,
a member of the canonical union, flattened by ``to_row`` and written into a
``channel=instrument/`` partition. This is not a record and never becomes one — no tag,
no union, no row — it is the object :class:`~crocodile.equity.reference.registry.
InstrumentRegistry` hands a connector so it can turn a venue's raw symbol into a
canonical one and look up what kind of security it is.

Two frozen msgspec structs sharing a name in one package is a wrong import that type-checks:
both have ``symbol``, ``symbol_raw``, ``exchange`` and a ``SecurityType``, so a
``list_instruments()`` annotated against the wrong one is caught by nothing until a
connector tries to construct a record with no header. The record's own docstring promised
this separation; this is it.
"""

from __future__ import annotations

import msgspec

from crocodile.core.schema.enums import SecurityType


class InstrumentIdentity(msgspec.Struct, frozen=True):
    """What one data provider calls one security, plus the identifiers that pin it down."""

    symbol: str
    """Canonical symbol, e.g. ``AAPL``."""

    source: str
    """Who serves the data: ``alpaca``, ``stooq``, ``finnhub``.

    Spelled ``provider`` until the union merge, and renamed on the same argument the
    record header was renamed on — crypto's ``exchange`` and equity's ``provider`` named
    one thing, and ``source`` is the spelling that presumes no market. Keeping the old
    word here would leave this struct and the record it feeds disagreeing about the name
    of the same value across a single assignment, which is where a rename gets skipped.
    """

    symbol_raw: str
    """The symbol exactly as ``source`` spells it, and half of the registry's key."""

    security_type: SecurityType

    name: str | None = None

    exchange: str | None = None
    """Where the security is *listed*, which is not :attr:`source`.

    The two now sit one field apart and that is the hazard this struct is closest to.
    ``crocodile.core.store.rows`` carries the live version of it: reading ``exchange``
    ahead of the provider name filed an Alpaca-served instrument under ``source=NASDAQ``
    — no exception, just the wrong partition directory. The rename above puts the two
    words side by side; it does not merge them, and nothing here may.
    """

    cik: str | None = None
    figi: str | None = None
    cusip: str | None = None

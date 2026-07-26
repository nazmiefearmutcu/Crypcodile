"""Canonical record types.

Every record leads with ``source, symbol, symbol_raw, local_ts`` and ends with
``source_ts, prov, prov_basis, prov_confidence, prov_inputs``. msgspec forbids a
required field after a defaulted one, so the trailing block is repeated on each
struct rather than inherited; ``tests/conformance/test_gates.py`` enforces it.
"""

from __future__ import annotations

import msgspec

from crocodile.core.schema.enums import Side, Tape
from crocodile.core.schema.provenance import Provenance

Level = tuple[float, float]
"""(price, size). A size of 0.0 means REMOVE this level."""


class _Header(msgspec.Struct, frozen=True):
    """The four fields every record leads with."""

    source: str
    """Venue (crypto) or data provider (equity)."""

    symbol: str
    """Canonical symbol, e.g. ``deribit:BTC-PERPETUAL`` or ``AAPL``."""

    symbol_raw: str
    """The symbol exactly as the source spelled it."""

    local_ts: int
    """UTC epoch nanoseconds at which we observed the record."""


class Trade(_Header, frozen=True, tag="trade", tag_field="channel"):
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
    source_ts: int | None = None
    prov: Provenance = Provenance.NATIVE
    prov_basis: str | None = None
    prov_confidence: float | None = None
    prov_inputs: list[str] | None = None


Record = Trade

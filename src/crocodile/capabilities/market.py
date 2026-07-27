"""Capabilities describing market structure and reference data.

Owns ``markets``, ``universe``, ``census``, ``list-exchanges``, ``open-interest``,
``whale-alerts`` and the instrument-level lookups — what exists to trade, where, and in
what size, as opposed to what the lake happens to hold about it.

Empty until the port fills it; see :mod:`crocodile.capabilities.catalog` for why an empty
batch module is still load-bearing.

The equity halves of this family are where :data:`SPEC_METHODS
<crocodile.core.capability.SPEC_METHODS>` M2 and M3 land. A crypto-only declaration
belongs on :data:`PENDING_SYMMETRY <crocodile.core.capability.PENDING_SYMMETRY>` against
the method that closes it — never on :data:`IRREDUCIBLE
<crocodile.core.capability.IRREDUCIBLE>`, which claims no equity analogue can exist and is
permanent.
"""

from __future__ import annotations

__all__: list[str] = []

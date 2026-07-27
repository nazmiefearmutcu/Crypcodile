"""Capabilities that move data, plus the six names no equity analogue can have.

Owns ``collect``, ``backfill``, ``replay``, ``export`` and ``migrate-lake``, and the six
entries of :data:`IRREDUCIBLE <crocodile.core.capability.IRREDUCIBLE>` — ``gas-tracker``,
``gas-vol``, ``mev-sandwich``, ``sequencer-latency``, ``peg-deviation``,
``lending-stress``. Those six are declared here so the exemption list and the declarations
it exempts can be read side by side; the justification stays on ``IRREDUCIBLE``, where the
gate looks for it.

Empty until the port fills it; see :mod:`crocodile.capabilities.catalog` for why an empty
batch module is still load-bearing.

What does *not* belong here, or anywhere in this package: infrastructure. ``health``,
``ready``, ``version``, ``metrics``, ``docs``, ``/``, ``/api/events``, the x402 payment
routes, and the launchers ``mcp``, ``api``, ``update``, ``shell``, ``flowmap`` and
``gas-tracker``'s live TUI are hand-written on the surfaces that have them. A capability is
something with an asset class, a parameter schema and a provenance; a readiness probe has
none of the three, and registering one would put it in the symmetry gate's subject list
where it can only ever be answered with an exemption.
"""

from __future__ import annotations

__all__: list[str] = []

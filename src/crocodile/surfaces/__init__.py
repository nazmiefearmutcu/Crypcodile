"""The three surfaces, as projections of the capability registry.

Crypcodile said its capability list three times — 5 499 lines of Typer, 3 273 of FastAPI,
2 600 of MCP handlers — and Stockodile said a smaller one three times more. Six stacks,
11 411 and 4 302 lines, one list. This package is what replaces them: three modules that
each walk :data:`~crocodile.core.capability.REGISTRY` once and derive their commands,
routes and tools from it.

The test of whether that has actually happened is mechanical, and it is stated here so it
can be applied while reading a diff: **a projector contains no per-capability branch.** A
surface that says ``if cap.name == "query"`` has stopped being a projection and become a
copy with a loop around it, and every reason the six stacks drifted apart is back.

What the surfaces *do* differ on is trust, and that difference is deliberate:
:mod:`crocodile.surfaces.dispatch` builds the
:class:`~crocodile.core.capability.CapabilityContext`, and each surface tells it how far
the caller is to be believed. A local CLI on an operator's own machine and a public HTTP
endpoint are not the same threat model, and the legacy stacks encoded that by accident —
one ``query`` capability shipped three different SQL policies — rather than on purpose.

Infrastructure is not projected and is not in the registry: ``health``, ``ready``,
``version``, ``metrics``, ``docs``, ``/``, ``/api/events``, the x402 payment routes, and
the launchers ``mcp``, ``api``, ``update``, ``shell``, ``flowmap`` and ``gas-tracker``
stay hand-written on whichever surface has them. A capability has an asset class, a
parameter schema and a provenance; a readiness probe has none of the three.
"""

from __future__ import annotations

__all__: list[str] = []

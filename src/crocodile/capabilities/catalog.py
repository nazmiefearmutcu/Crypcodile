"""Capabilities that answer questions about the lake itself.

Owns ``query``, ``search``, the ``catalog-*`` family (``catalog-channels``,
``catalog-dates``, ``catalog-summary``, ``catalog-stats``, ``catalog-inventory``,
``catalog-scan``, ``catalog-symbols``), ``data-coverage`` and ``resolve-symbols``.

Empty until the port fills it. That is not the same as unused: :data:`BATCHES
<crocodile.capabilities.BATCHES>` names this module, :func:`load_all
<crocodile.capabilities.load_all>` imports it, and a conformance gate asserts both — so
the file being importable and listed is what makes a declaration added here take effect
without anybody wiring anything up.

One note for whoever ports ``query``: its SQL policy does **not** belong in its params
struct. The three legacy stacks each decided at the call site — the crypto CLI had no
guard, REST rejected mutating SQL and wrapped a ``LIMIT``, MCP passed ``readonly=True`` —
and that divergence is why one capability had three behaviours. The implementation reads
:meth:`CapabilityContext.query <crocodile.core.capability.CapabilityContext.query>`, and
each surface sets the policy when it builds the context.
"""

from __future__ import annotations

__all__: list[str] = []

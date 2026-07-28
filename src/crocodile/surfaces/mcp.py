"""The MCP surface, projected: one tool per capability.

Replaces 2 600 lines of hand-written crypto tool handlers and 800 of equity ones, and the
``_CAPABILITIES_MCP_TOOLS_HINT`` list that was a third hand-maintained copy of the same
names sitting inside the REST server.

Deliberately transport-free. This module produces the two things a JSON-RPC server needs —
a ``tools/list`` payload and a ``tools/call`` implementation — and nothing that speaks a
socket. A projection whose only entry point is a running server can only be tested by
starting one, which is how the legacy MCP handlers ended up with almost no coverage.

Trust posture: MCP is a network surface and is treated exactly as REST is — raw SQL vetted,
reads capped, cap published. The two agreeing is the point; they disagreed before, with
MCP passing ``readonly=True`` and REST wrapping a ``LIMIT``, each having independently
solved half the problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec

from crocodile.core.capability import REGISTRY, AssetClass, Capability
from crocodile.core.config import Settings
from crocodile.core.store.catalog import Catalog
from crocodile.surfaces import dispatch

__all__ = ["call_tool", "tool_definitions", "tool_names"]


def _definition(cap: Capability, wire: str) -> dict[str, Any]:
    """One MCP tool declaration.

    ``inputSchema`` is ``msgspec.json.schema(cap.params)`` verbatim — the ``$ref``/``$defs``
    form, which is valid JSON Schema and is what the params struct *is*. Inlining or
    rewriting it here would be a second description of the parameters, which is precisely
    what the three surfaces are being collapsed to avoid.

    ``asset_class`` is added on top, because it is the one input a caller may supply that
    is not a capability parameter: it selects the implementation rather than describing the
    request.
    """
    schema: dict[str, Any] = dict(msgspec.json.schema(cap.params))
    schema.setdefault("properties", {})
    return {
        "name": wire,
        "description": cap.summary if wire == cap.name else f"{cap.summary} (alias of {cap.name})",
        "inputSchema": schema,
        "assetClasses": sorted(a.value for a in cap.impls),
    }


def tool_definitions() -> list[dict[str, Any]]:
    """The ``tools/list`` payload: one entry per capability name and per alias."""
    return [
        _definition(REGISTRY[name], wire) for wire, name in sorted(dispatch.wire_names().items())
    ]


def tool_names() -> set[str]:
    """The tool names this projection actually publishes."""
    return {tool["name"] for tool in tool_definitions()}


def call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Run one tool and return its response body.

    The body carries the same ``provenance`` block REST serves and, when the
    implementation's ceiling is not :attr:`Provenance.NATIVE`, the same human-readable
    ``warning``. That warning generalises the synthetic-depth banner the equity REST server
    already shipped for exactly one route: an agent reading a modelled answer is told so,
    for every capability that can produce one, without anyone writing fifty banners.

    Raises:
        KeyError: no tool of that name.
        ValueError: the arguments do not fit the capability's schema, or the
            implementation refused them.
        CapabilityUnavailable: no implementation for the resolved asset class.
    """
    cap = dispatch.resolve(name)
    supplied = dict(arguments or {})
    explicit_raw = supplied.pop("asset_class", None)
    explicit = AssetClass(explicit_raw) if explicit_raw else None

    params = dispatch.build_params(cap, supplied)
    resolved = dispatch.resolve_asset_class(cap, explicit=explicit, symbol=supplied.get("symbol"))
    resolved_settings = settings if settings is not None else Settings.from_env()

    with Catalog(dispatch.data_dir_for(resolved_settings, data_dir)) as catalog:
        ctx = dispatch.build_context(
            catalog,
            resolved,
            settings=resolved_settings,
            readonly=True,
            row_limit=dispatch.NETWORK_ROW_LIMIT,
        )
        result = dispatch.drive(dispatch.invoke(cap, ctx, params), row_limit=ctx.row_limit)
        body = dispatch.payload(cap, result)
        body["provenance"] = dispatch.provenance_block(cap, ctx)
        warning = dispatch.warning_for(cap, ctx)
        if warning:
            body["warning"] = warning
        return body

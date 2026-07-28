"""The REST surface, projected: ``GET /api/v1/{name}`` per capability.

Replaces 3 273 lines of hand-written crypto routes and 1 500 of equity ones. It also
replaces ``GET /api/v1/capabilities``, which was a hand-copied list of route strings
sitting beside the routes it described — fifty-odd names maintained by remembering to.
Here the same route is the registry, so it cannot be stale.

FastAPI is imported inside :func:`build_app`, not at module scope. It lives in the ``web``
extra, and a module that cannot be imported on the base install would break the import gate
in ``tests/conformance/test_import_safety.py`` — an optional dependency is allowed to be
absent, but not at import time.

Trust posture: this is the network surface. Raw SQL is vetted and reads are capped at
:data:`~crocodile.surfaces.dispatch.NETWORK_ROW_LIMIT`, and the cap is published in the
provenance block so a truncated answer reads as a ceiling rather than as the whole lake.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from crocodile.core.capability import REGISTRY, AssetClass, Capability
from crocodile.core.config import Settings
from crocodile.core.store.catalog import Catalog
from crocodile.surfaces import dispatch

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from collections.abc import Callable, Coroutine

    from fastapi import FastAPI

__all__ = ["API_PREFIX", "build_app", "route_paths"]

API_PREFIX = "/api/v1"
"""The prefix both forks already served on. Kept so existing callers do not move."""


def path_for(wire: str) -> str:
    """The route a capability answers on. One rule, no table."""
    return f"{API_PREFIX}/{wire}"


def _openapi_parameters(cap: Capability) -> list[dict[str, Any]]:
    """Describe the query string from ``cap.params``, for the generated OpenAPI document.

    The same schema MCP publishes as its ``inputSchema``, reshaped into OpenAPI's parameter
    list. Two surfaces, one description of what a caller may send — which is half of what
    "full API symmetry" means and the half that is easiest to lose by hand.
    """
    schema = dispatch.params_schema(cap)
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    parameters: list[dict[str, Any]] = [
        {"name": name, "in": "query", "required": name in required, "schema": subschema}
        for name, subschema in properties.items()
    ]
    parameters.append(
        {
            "name": "asset_class",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "enum": dispatch.asset_class_option_values()},
        }
    )
    return parameters


def _handler(
    cap: Capability, settings: Settings, data_dir: Path | None
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Return the endpoint for one capability. The whole of the per-capability code."""
    from fastapi import HTTPException, Request
    from starlette.concurrency import run_in_threadpool

    def serve(supplied: dict[str, Any]) -> dict[str, Any]:
        explicit_raw = supplied.pop("asset_class", None)
        try:
            asset_class = AssetClass(explicit_raw) if explicit_raw else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            params = dispatch.build_params(cap, supplied)
            resolved = dispatch.resolve_asset_class(
                cap, explicit=asset_class, symbols=dispatch.symbol_hints(params)
            )
        except dispatch.UNAVAILABLE as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except dispatch.BAD_REQUEST as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        with Catalog(dispatch.data_dir_for(settings, data_dir)) as catalog:
            ctx = dispatch.build_context(
                catalog,
                resolved,
                settings=settings,
                readonly=True,
                row_limit=dispatch.NETWORK_ROW_LIMIT,
            )
            try:
                result = dispatch.drive(
                    dispatch.invoke(cap, ctx, params), row_limit=ctx.row_limit
                )
            except dispatch.UNAVAILABLE as exc:
                raise HTTPException(status_code=501, detail=str(exc)) from exc
            except dispatch.REFUSED as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except dispatch.BAD_REQUEST as exc:
                # An unknown indicator, a symbol with no stored book, SQL that does not
                # compile against this lake: a bad request, not a broken server. The legacy
                # routes mapped these the same way.
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            body = dispatch.payload(cap, result)
            body["provenance"] = dispatch.provenance_block(cap, ctx)
            warning = dispatch.warning_for(cap, ctx)
            if warning:
                body["warning"] = warning
            return body

    async def endpoint(request: Request) -> dict[str, Any]:
        supplied = dict(request.query_params)
        # Off the event loop, for two reasons that are really one. Every capability here is
        # blocking — DuckDB, Parquet, HTTP — so serving it inline stalls every other request
        # on the process; and `dispatch.drive` calls `asyncio.run` for a capability that
        # handed back an unstarted coroutine, which raises outright inside a running loop.
        # A worker thread has no loop of its own, so both stop being true at once.
        return await run_in_threadpool(serve, supplied)

    # FastAPI resolves a route's parameters through ``get_type_hints`` against the module
    # globals. ``from __future__ import annotations`` turns ``request: Request`` into the
    # string ``"Request"``, and ``Request`` is imported inside this function rather than at
    # module scope (see the module docstring on the ``web`` extra) — so the lookup fails,
    # FastAPI falls back to treating it as an ordinary query parameter, and every route
    # answers 422 "Field required: query.request". Binding the live class here is what
    # makes the deferred import and the annotation coexist.
    endpoint.__annotations__ = {"request": Request, "return": dict[str, Any]}
    return endpoint


def build_app(*, settings: Settings | None = None, data_dir: Path | None = None) -> FastAPI:
    """Return a FastAPI app holding one route per capability and per alias.

    Infrastructure routes — ``/health``, ``/ready``, ``/version``, ``/metrics``, ``/docs``,
    ``/``, ``/api/events``, the x402 payment routes — are deliberately absent. They are not
    capabilities: they have no asset class, no parameter schema and no provenance, so
    registering them would put them in front of the symmetry gate where the only possible
    answer is an exemption. They stay hand-written on the server that mounts this app.
    """
    from fastapi import FastAPI

    resolved_settings = settings if settings is not None else Settings.from_env()
    app = FastAPI(
        title="Crocodile",
        description="Every capability, projected from one registry.",
    )
    for wire, name in sorted(dispatch.wire_names().items()):
        cap = REGISTRY[name]
        summary = cap.summary if wire == name else f"{cap.summary}  [alias of {name}]"
        app.add_api_route(
            path_for(wire),
            _handler(cap, resolved_settings, data_dir),
            methods=["GET"],
            name=wire,
            summary=summary,
            openapi_extra={"parameters": _openapi_parameters(cap)},
        )
    return app


def route_paths() -> set[str]:
    """The paths this projection actually serves.

    Read off the built app, so the parity gate measures the surface rather than re-deriving
    the answer it is supposed to be checking.
    """
    paths = {str(getattr(route, "path", "")) for route in build_app().routes}
    return {path for path in paths if path.startswith(f"{API_PREFIX}/")}

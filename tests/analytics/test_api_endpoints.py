"""The operational routes of the deployable server: probes, telemetry, discovery.

What is left of a 6 236-line file that tested ``crocodile/crypto/legacy/api_server.py``
route by route. That module is gone, and with it every hand-written capability route it
served. :mod:`crocodile.surfaces.rest` projects those from the registry now, and
``tests/surfaces/test_end_to_end.py`` and ``tests/conformance/test_surfaces.py`` own what
they used to assert here — rows come back, a bad parameter is a 400, the row ceiling is
published, and every capability reaches all three surfaces under exactly one name.

The routes below are the ones that were never capabilities: they have no asset class, no
parameter schema and no provenance, so they could not be projected and stayed hand-written.
They moved to :mod:`crocodile.surfaces.server`, which mounts the projection's routes and
adds these beside them. Two things they used to do are gone rather than moved and are not
tested here: ``GET /api/v1/capabilities`` no longer answers from two hand-copied Python
lists — it reads the route table and the tool table it is describing, which is the whole
reason it stopped being able to drift — and the x402 on-chain verification path went with
the one route it gated.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

import pytest

from crocodile import __version__
from crocodile.surfaces import mcp, rest, server


def _lake(root: pathlib.Path, *channels: str) -> pathlib.Path:
    """Write a lake whose only content is its partition layout.

    ``Catalog.list_channels`` walks ``source=*/channel=*`` on the filesystem rather than
    scanning Parquet, so a directory tree is enough to make the count below a real read of
    a real lake instead of a mock returning a list it was handed.
    """
    for index, channel in enumerate(channels):
        (root / f"source=venue{index}" / f"channel={channel}").mkdir(parents=True)
    return root


class _BrokenCatalog:
    """A lake that opens and then fails on the read, which is how a disk fails."""

    def __init__(self, data_dir: pathlib.Path) -> None:
        self._data_dir = data_dir

    def __enter__(self) -> _BrokenCatalog:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def list_channels(self) -> list[str]:
        raise RuntimeError("disk failed")


@pytest.fixture
def _broken_lake(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_health_body`` imports ``Catalog`` at call time, so the module attribute is live."""
    monkeypatch.setattr("crocodile.core.store.catalog.Catalog", _BrokenCatalog)


@pytest.fixture(scope="module")
def routes() -> set[tuple[str | None, tuple[str, ...]]]:
    """``(path, methods)`` for everything ``crocodile api`` serves."""
    return {
        (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()) or [])))
        for route in server.build_server().routes
    }


# ---------------------------------------------------------------------------
# GET /api/v1/health and /api/v1/status — lightweight probe
# ---------------------------------------------------------------------------


def test_health_empty_lake(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty / missing lake still reports ok with lake_channels=0."""
    monkeypatch.setenv("CROCODILE_DATA_DIR", str(tmp_path))

    assert asyncio.run(server.health()) == {
        "ok": True,
        "version": __version__,
        "lake_channels": 0,
    }


def test_health_returns_channel_count(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CROCODILE_DATA_DIR", str(_lake(tmp_path, "book_snapshot", "trade")))

    result = asyncio.run(server.health())

    assert result["ok"] is True
    assert result["version"] == __version__
    assert result["lake_channels"] == 2


def test_status_alias_matches_health(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/v1/status is the same payload as /api/v1/health."""
    monkeypatch.setenv("CROCODILE_DATA_DIR", str(_lake(tmp_path, "trade")))

    health = asyncio.run(server.health())
    status = asyncio.run(server.status())

    assert health == status
    assert status["ok"] is True
    assert status["lake_channels"] == 1


def test_health_lake_failure_reports_not_ok(_broken_lake: None) -> None:
    result = asyncio.run(server.health())

    assert result["ok"] is False
    assert result["version"] == __version__
    assert result["lake_channels"] == 0
    assert result["error"] == "lake_unavailable"


def test_health_and_status_routes_registered(
    routes: set[tuple[str | None, tuple[str, ...]]],
) -> None:
    assert ("/api/v1/health", ("GET",)) in routes
    assert ("/api/v1/status", ("GET",)) in routes


# ---------------------------------------------------------------------------
# GET /api/v1/ready — k8s-style readiness (200 when health.ok, else 503)
# ---------------------------------------------------------------------------


def test_ready_returns_200_when_ok(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ready when lake is available: same payload as health, HTTP 200."""
    from fastapi import Response

    monkeypatch.setenv("CROCODILE_DATA_DIR", str(_lake(tmp_path, "trade")))

    response = Response()
    result = asyncio.run(server.ready(response))

    assert result == asyncio.run(server.health())
    assert result["ok"] is True
    assert result["version"] == __version__
    assert result["lake_channels"] == 1
    # Default Response status is 200; ready does not downgrade when ok.
    assert response.status_code == 200


def test_ready_returns_503_when_not_ok(_broken_lake: None) -> None:
    """Lake failure → readiness fails with HTTP 503; health body still ok=False."""
    from fastapi import Response

    response = Response()
    result = asyncio.run(server.ready(response))

    assert result == asyncio.run(server.health())
    assert result["ok"] is False
    assert result["error"] == "lake_unavailable"
    assert result["version"] == __version__
    assert response.status_code == 503


def test_ready_empty_lake_is_ready(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty lake is still ready (ok=true, lake_channels=0) — same as health."""
    from fastapi import Response

    monkeypatch.setenv("CROCODILE_DATA_DIR", str(tmp_path))

    response = Response()
    result = asyncio.run(server.ready(response))

    assert result == {"ok": True, "version": __version__, "lake_channels": 0}
    assert response.status_code == 200


def test_ready_route_registered(routes: set[tuple[str | None, tuple[str, ...]]]) -> None:
    assert ("/api/v1/ready", ("GET",)) in routes
    # metrics stays at /metrics; readiness is the separate k8s probe
    assert ("/metrics", ("GET",)) in routes


def test_ready_separate_from_health_status_semantics(_broken_lake: None) -> None:
    """health/status always return body (liveness); ready sets 503 when not ok.

    Direct handler calls: health ignores Response status; ready mutates it.
    """
    from fastapi import Response

    ready_response = Response()
    ready_body = asyncio.run(server.ready(ready_response))
    health_body = asyncio.run(server.health())
    status_body = asyncio.run(server.status())

    assert ready_body == health_body == status_body
    assert ready_body["ok"] is False
    assert ready_response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v1/capabilities — agent discovery, read off what is mounted
# ---------------------------------------------------------------------------


def test_capabilities_shape_and_contents(
    routes: set[tuple[str | None, tuple[str, ...]]], _broken_lake: None
) -> None:
    """Returns {rest, mcp_tools_hint}, and every name in it is a name the server answers to.

    The old route answered from two hardcoded module-level lists sitting beside the routes
    they claimed to describe, and they had drifted: the MCP hint named 36 tools while the
    tool table declared 37. The assertion that matters is therefore no longer "it contains
    the strings we remembered to put in it" but "it agrees with the route table", so the
    two cannot come apart again.
    """
    result: dict[str, Any] = asyncio.run(server.capabilities())

    assert set(result.keys()) == {"rest", "mcp_tools_hint"}
    assert isinstance(result["rest"], list)
    assert isinstance(result["mcp_tools_hint"], list)

    served = {
        f"{method} {path}"
        for path, methods in routes
        for method in methods
        if method in {"GET", "POST"}
    }
    listed = set(result["rest"])
    assert listed <= served, f"advertised but not served: {sorted(listed - served)}"
    assert served - listed == {
        # Pages rather than queries, and FastAPI's own schema plumbing behind them.
        "GET /",
        "GET /docs",
        "GET /docs/oauth2-redirect",
        "GET /openapi.json",
        "GET /redoc",
        # Ledger administration behind a key, deliberately not in free discovery.
        "POST /api/v1/simulate-payment",
        "GET /api/v1/admin/payments",
    }

    assert result["mcp_tools_hint"] == sorted(mcp.tool_names())
    assert {f"GET {path}" for path in rest.route_paths()} <= listed

    # Core free meta / catalog / analytics routes agents should discover. Every entry is a
    # capability's wire name now, so the hierarchical spellings the hand-written routes
    # used — /api/v1/catalog/channels — are gone; one rule, one segment.
    for route in (
        "GET /api/v1/health",
        "GET /api/v1/ready",
        "GET /api/v1/status",
        "GET /api/v1/version",
        "GET /api/v1/capabilities",
        "GET /metrics",
        "GET /api/v1/catalog-channels",
        "GET /api/v1/catalog-inventory",
        "GET /api/v1/catalog-symbols",
        "GET /api/v1/catalog-dates",
        "GET /api/v1/catalog-exchanges",
        "GET /api/v1/catalog-summary",
        "GET /api/v1/catalog-stats",
        "GET /api/v1/catalog-scan",
        "GET /api/v1/open-interest",
        "GET /api/v1/perp-basis",
        "GET /api/v1/lending-stress",
        # Every capability is a GET with query parameters; these two were POSTs.
        "GET /api/v1/query",
        "GET /api/v1/simulate-price-impact",
    ):
        assert route in listed

    # Paid/admin routes must not appear in free discovery.
    for route in (
        "GET /api/v1/market-data",
        "POST /api/v1/simulate-payment",
        "GET /api/v1/admin/payments",
    ):
        assert route not in listed

    for tool in (
        "list_data_channels",
        "list_dates",
        "list_exchanges_on_disk",
        "list-exchanges",
        "catalog-summary",
        "catalog-stats",
        "search_symbols",
        "list_symbols",
        "resolve-symbols",
        "indicators",
        "spot-future-basis",
        "lending-stress",
        "label-transfers",
        "chaos-score",
        "funding-predict",
    ):
        assert tool in result["mcp_tools_hint"]

    # All REST entries are METHOD + path; no duplicates.
    for entry in listed:
        assert entry.startswith("GET ") or entry.startswith("POST ")
        assert entry.split(" ", 1)[1].startswith("/")
    assert len(result["rest"]) == len(set(result["rest"]))
    assert len(result["rest"]) >= 30
    assert len(result["mcp_tools_hint"]) >= 8


def test_capabilities_returns_defensive_copies() -> None:
    """Mutating the response lists must not corrupt subsequent calls."""
    first = asyncio.run(server.capabilities())
    first["rest"].append("GET /api/v1/not-real")
    first["mcp_tools_hint"].append("not_a_tool")

    second = asyncio.run(server.capabilities())

    assert "GET /api/v1/not-real" not in second["rest"]
    assert "not_a_tool" not in second["mcp_tools_hint"]


def test_capabilities_route_registered(routes: set[tuple[str | None, tuple[str, ...]]]) -> None:
    assert ("/api/v1/capabilities", ("GET",)) in routes


# ---------------------------------------------------------------------------
# GET /api/v1/version — package version only (no lake)
# ---------------------------------------------------------------------------


def test_version_returns_package_version_only(_broken_lake: None) -> None:
    """Endpoint returns exactly {"version": __version__}, from a build with a dead lake."""
    result = asyncio.run(server.version())

    assert result == {"version": __version__}
    assert set(result.keys()) == {"version"}
    assert isinstance(result["version"], str)
    assert result["version"]


def test_version_route_registered(routes: set[tuple[str | None, tuple[str, ...]]]) -> None:
    assert ("/api/v1/version", ("GET",)) in routes

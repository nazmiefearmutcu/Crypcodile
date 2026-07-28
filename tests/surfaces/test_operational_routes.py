"""The routes that describe and operate the process, which no capability declares.

``tests/conformance/test_surfaces.py`` measures the projection against the registry, and by
construction it cannot see these: they are hand-written in
:mod:`crocodile.surfaces.server`, deliberately outside :func:`crocodile.surfaces.rest.build_app`
so that Gate 4 does not read them as invented capabilities. That leaves them measured by
nothing, which is how the route they replace went wrong in the first place.

The file exists because of one exemption in particular. ``capabilities`` sits on
``_INFRASTRUCTURE`` in ``tests/conformance/test_phase2_surface_parity.py``, and the note it
replaced objected that classifying it that way "would license deleting the one route whose
whole job is saying what exists". The answer to that objection is here: the entry points at
this file, and the assertions below are what make the licence not exist.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from crocodile.core.config import Settings
from crocodile.surfaces import dispatch, mcp, rest, server


@pytest.fixture
def client(lake: pathlib.Path) -> TestClient:
    return TestClient(server.build_server(settings=Settings(data_dir=lake)))


# ---------------------------------------------------------------------------
# The route whose job is saying what exists
# ---------------------------------------------------------------------------


def test_the_capabilities_route_lists_exactly_what_is_mounted(client: TestClient) -> None:
    """The drift that made the old route wrong, made impossible rather than unlikely.

    ``_CAPABILITIES_MCP_TOOLS_HINT`` named 36 tools while ``TOOLS`` declared 37, so
    ``list_all_exchanges`` existed and the self-description did not mention it. Nothing
    compared the two, because they were two hand-maintained Python lists sitting beside the
    routes and tools they claimed to describe.
    """
    body = client.get("/api/v1/capabilities").json()

    # Method *and* path, because four capabilities take parameters a query string cannot
    # carry and answer only on POST. Describing them as GET would be a self-description that
    # sends a caller to a 405.
    mounted = {
        f"{method} {route.path}"
        for route in client.app.routes  # type: ignore[attr-defined]
        for method in (getattr(route, "methods", None) or ())
        if method not in {"HEAD", "OPTIONS"}
    }
    missing = set(body["rest"]) - mounted
    assert not missing, f"described but not mounted: {sorted(missing)}"

    # Everything mounted and not described has to be one of the documented omissions,
    # or the route is under-reporting the build — the direction the old one failed in.
    undescribed = mounted - set(body["rest"])
    assert undescribed == {
        "GET /",
        "GET /docs",
        "GET /docs/oauth2-redirect",
        "GET /openapi.json",
        "GET /redoc",
        "GET /api/v1/admin/payments",
        "POST /api/v1/simulate-payment",
    }, sorted(undescribed)


def test_the_capabilities_route_names_every_tool_the_mcp_surface_publishes(
    client: TestClient,
) -> None:
    """The half that actually drifted, asserted against the surface rather than a list."""
    body = client.get("/api/v1/capabilities").json()
    assert body["mcp_tools_hint"] == sorted(mcp.tool_names())
    assert len(body["mcp_tools_hint"]) == len(dispatch.wire_names())


def test_every_capability_route_is_described_by_the_capabilities_route(
    client: TestClient,
) -> None:
    """A capability added tomorrow appears here without anyone editing this module."""
    described = set(client.get("/api/v1/capabilities").json()["rest"])
    for path, methods in rest.route_methods().items():
        assert methods, f"{path} answers on no method at all"
        for method in methods:
            assert f"{method} {path}" in described, f"{method} {path}"


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def test_health_and_status_answer_the_same_thing(client: TestClient) -> None:
    """Both forks served both spellings; ``status`` was documented as an alias of ``health``."""
    health = client.get("/api/v1/health")
    status = client.get("/api/v1/status")
    assert health.status_code == status.status_code == 200
    assert health.json() == status.json()
    assert health.json()["ok"] is True


def test_ready_is_200_over_a_lake_it_can_open(client: TestClient) -> None:
    assert client.get("/api/v1/ready").status_code == 200


def test_ready_is_503_when_the_lake_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason ``ready`` is not an alias of ``health``.

    Liveness is "is this process alive", which it plainly is if it replied. Readiness is
    "should traffic be routed here", and it should not be while the lake cannot be read.

    Driven by making ``Catalog`` raise rather than by pointing at a missing directory: an
    absent lake opens cleanly and reports zero channels, which is a healthy empty lake and
    not an unreachable one. Distinguishing those two is the point of the branch.
    """

    def unreachable(*_args: object, **_kwargs: object) -> object:
        raise OSError("lake is on fire")

    monkeypatch.setattr("crocodile.core.store.catalog.Catalog", unreachable)
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["error"] == "lake_unavailable"
    assert client.get("/api/v1/health").status_code == 200, "liveness must not follow the lake"


def test_an_absent_lake_is_healthy_and_empty_rather_than_unreachable(
    tmp_path: pathlib.Path,
) -> None:
    """"No data" and "no answer" are different, and this pair is where they are decided."""
    probe = TestClient(server.build_server(settings=Settings(data_dir=tmp_path / "absent")))
    body = probe.get("/api/v1/health").json()
    assert body["ok"] is True
    assert body["lake_channels"] == 0
    assert probe.get("/api/v1/ready").status_code == 200


def test_version_answers_without_opening_the_lake(tmp_path: pathlib.Path) -> None:
    from crocodile import __version__

    probe = TestClient(server.build_server(settings=Settings(data_dir=tmp_path / "absent")))
    assert probe.get("/api/v1/version").json() == {"version": __version__}


def test_the_landing_page_points_at_the_two_machine_readable_answers(
    client: TestClient,
) -> None:
    body = client.get("/").text
    assert "/docs" in body
    assert "/api/v1/capabilities" in body


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def test_metrics_counts_every_route_rather_than_three(client: TestClient) -> None:
    """Both forks bumped three globals inside three handlers.

    The other forty-five routes were invisible to ``/metrics`` and nothing said so, which is
    the same shape as a capability nobody projected: present, plausible, and silently short.
    """
    def counted(path: str, body: str) -> int:
        prefix = f'crocodile_api_requests_total{{path="{path}"}} '
        line = next(row for row in body.splitlines() if row.startswith(prefix))
        return int(line.removeprefix(prefix))

    before = client.get("/metrics").text
    client.get("/api/v1/health")
    client.get("/api/v1/health")
    client.get("/api/v1/version")
    body = client.get("/metrics").text

    # Counted as a delta rather than an absolute: `_REQUESTS` is process state, so an
    # absolute assertion would pass or fail on test ordering rather than on the counter.
    assert counted("/api/v1/health", body) - counted("/api/v1/health", before) == 2
    assert counted("/api/v1/version", body) - counted("/api/v1/version", before) == 1
    for series in (
        "process_cpu_seconds_total",
        "process_resident_memory_peak_bytes",
        "crocodile_uptime_seconds",
    ):
        assert f"# TYPE {series} " in body, series


def test_metrics_reports_the_statuses_the_ledger_actually_holds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The forks hardcoded ``pending`` and ``verified``.

    Nothing in either tree ever wrote ``verified``, so that series read zero forever while
    the ``paid`` and ``spent`` rows it should have been counting went unreported.
    """
    import json

    ledger = tmp_path / "payments.json"
    ledger.write_text(json.dumps({"a": {"status": "paid"}, "b": {"status": "paid"}}))
    monkeypatch.setenv("PAYMENTS_FILE", str(ledger))

    body = client.get("/metrics").text
    assert 'crocodile_payments_total{status="paid"} 2' in body
    assert 'status="verified"' not in body


# ---------------------------------------------------------------------------
# Serialising what a capability actually returns
# ---------------------------------------------------------------------------


def test_a_capability_returning_a_struct_serialises_on_the_network_surfaces() -> None:
    """``depth`` returns a ``DepthProfile``, and msgspec is not the encoder here.

    A Struct is this codebase's wire type for *msgspec*; FastAPI serialises with pydantic
    and refuses an unknown type, so this route answered 500 with ``Unable to serialize
    unknown type: DepthProfile`` while the CLI printed the same value happily. One
    capability working on one surface and failing on another is precisely the divergence the
    projection exists to end, so the conversion belongs to ``dispatch.payload`` — shared by
    all three — rather than to the REST handler.
    """
    import msgspec

    from crocodile.core.capability import REGISTRY

    class _Profile(msgspec.Struct, frozen=True):
        symbol: str
        bids: list[tuple[float, float]]

    shaped = dispatch.payload(REGISTRY["depth"], _Profile(symbol="AAPL", bids=[(1.0, 2.0)]))
    assert shaped == {"result": {"symbol": "AAPL", "bids": [(1.0, 2.0)]}}
    # Plain data all the way down, which is what both network encoders need.
    import json

    json.dumps(shaped)


def test_a_non_finite_number_leaves_the_surface_as_null(lake: pathlib.Path) -> None:
    """JSON has no ``NaN``; ordinary analytics produce one.

    ``json_safe_float`` is unit-tested, but nothing drove a non-finite cell through
    ``payload`` — so a projection that stopped calling it would have left every test green
    and emitted a bare ``NaN`` token most clients reject as malformed.
    """
    import json

    import polars as pl

    from crocodile.core.capability import REGISTRY

    frame = pl.DataFrame({"value": [float("nan"), float("inf"), 1.5]})
    shaped = dispatch.payload(REGISTRY["indicators"], frame)
    assert [row["value"] for row in shaped["rows"]] == [None, None, 1.5]
    assert "NaN" not in json.dumps(shaped)


# ---------------------------------------------------------------------------
# The x402 ledger, which is all that is left of the payment gate
# ---------------------------------------------------------------------------


def test_the_admin_dump_hides_itself_when_no_key_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 rather than 401: a 401 tells an unauthenticated caller the route is worth guessing."""
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert client.get("/api/v1/admin/payments").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Admin-Key": "wrong"},
        {"Authorization": "Bearer wrong"},
    ],
    ids=["none", "header", "bearer"],
)
def test_the_admin_dump_refuses_a_key_that_does_not_match(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "sekrit")
    assert client.get("/api/v1/admin/payments", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [{"X-Admin-Key": "sekrit"}, {"Authorization": "Bearer sekrit"}],
    ids=["header", "bearer"],
)
def test_the_admin_dump_accepts_either_spelling_of_the_right_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "sekrit")
    response = client.get("/api/v1/admin/payments", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_simulation_is_off_unless_it_is_asked_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It marks a payment paid without one having been made, so it is not a default."""
    monkeypatch.delenv("ALLOW_SIMULATION", raising=False)
    response = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "p", "tx_hash": "t", "signature": "0x" + "ab" * 65},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Simulation mode is disabled."


def test_the_simulation_gate_is_checked_before_the_ledger(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order is the contract: a caller with simulation off learns nothing about what exists.

    Checked with a payment id that is definitely absent — if the ledger were consulted first
    the answer would be 404, which is a existence oracle for anyone who can spell an id.
    """
    monkeypatch.delenv("ALLOW_SIMULATION", raising=False)
    response = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "definitely-not-there", "tx_hash": "t", "signature": "0x00"},
    )
    assert response.status_code == 400, response.json()


def test_a_malformed_body_is_refused_before_a_signature_is_recovered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``payment_id`` is the message the signature recovers against.

    A body missing it would recover a signer for the empty string, which is a valid address
    — so the struct conversion has to come first.
    """
    monkeypatch.setenv("ALLOW_SIMULATION", "true")
    assert client.post("/api/v1/simulate-payment", json={"tx_hash": "t"}).status_code == 400

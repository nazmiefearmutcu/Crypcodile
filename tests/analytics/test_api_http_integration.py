"""Real HTTP tests for the FastAPI app, over a genuine ASGI transport.

``tests/analytics/test_api_endpoints.py`` is ~6,200 lines and ~339 tests, but it
calls endpoint *functions* directly through a hand-rolled ``MockTestClient``.
Routing, middleware, dependency injection, status-code generation and JSON
serialization are all bypassed there — see that file's module docstring.

This file is deliberately small and covers only what that harness structurally
cannot: the layers between a socket and a handler. Every test below asserts on
something the mock client is incapable of producing.

Requires ``starlette.testclient``, i.e. the ``httpx2`` dev dependency.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip(
    "httpx2",
    reason="starlette.testclient needs httpx2; install the dev dependency group",
)

from starlette.testclient import TestClient  # noqa: E402

from crypcodile.api_server import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health_is_reachable_over_http_and_serializes_to_json(client: TestClient) -> None:
    """A 200 with a real Content-Type and a real JSON body.

    The mock client returns the handler's Python object untouched, so it can
    never catch a value FastAPI would fail to encode, and never sees a header.
    """
    resp = client.get("/api/v1/health")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")

    # Decode the wire bytes, not a MagicMock attribute.
    body = json.loads(resp.content)
    assert body["ok"] is True
    assert isinstance(body["version"], str)
    assert isinstance(body["lake_channels"], int)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/version",
        "/api/v1/status",
        "/api/v1/ready",
        "/api/v1/capabilities",
        "/api/v1/exchanges",
        "/api/v1/catalog/channels",
    ],
)
def test_get_endpoints_answer_200_with_json_bodies(client: TestClient, path: str) -> None:
    """MockTestClient implements only ``post``; every GET route is unreached by it."""
    resp = client.get(path)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    json.loads(resp.content)  # must be real, encodable JSON


def test_unknown_path_is_404_not_a_harness_error(client: TestClient) -> None:
    """The mock client raises ``ValueError('Route ... not found')`` instead."""
    resp = client.get("/api/v1/definitely-not-a-route")
    assert resp.status_code == 404


def test_wrong_method_is_405(client: TestClient) -> None:
    """Method mismatch is invisible to a harness that never consults the router."""
    resp = client.request("DELETE", "/api/v1/health")
    assert resp.status_code == 405


def test_market_data_is_payment_gated_at_402(client: TestClient) -> None:
    """The x402 gate is middleware/handler behaviour a direct call never reaches.

    This is the sharpest example of the gap: the same endpoint that looks like a
    plain 200 in the handler tests answers 402 with a payment challenge to an
    actual caller.
    """
    resp = client.get("/api/v1/market-data", params={"symbol": "cbBTC-USDC"})

    assert resp.status_code == 402
    body = resp.json()
    assert body["status"] == "payment_required"
    assert "payment_id" in body
    assert body["payment_required"]["currency"] == "USDC"


def test_missing_query_parameter_is_422_from_fastapi_validation(client: TestClient) -> None:
    """Query-parameter validation lives in the request layer, not the handler body."""
    resp = client.get("/api/v1/market-data")

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(item["loc"] == ["query", "symbol"] for item in detail)


def test_query_endpoint_round_trips_a_real_json_body(client: TestClient) -> None:
    """POST with a real encoded body, a real response body, real status."""
    resp = client.post("/api/v1/query", json={"sql": "SELECT 1"})

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert json.loads(resp.content) == [{"1": 1}]


def test_query_endpoint_rejects_mutating_sql_with_a_real_error_status(
    client: TestClient,
) -> None:
    """One error path, end to end.

    The mock client's ``except Exception`` fallback stamps 400 on *anything*
    unexpected, so a 400 asserted there does not prove the guard fired. Here the
    status and the message both come off the wire.
    """
    resp = client.post("/api/v1/query", json={"sql": "DROP TABLE records"})

    assert resp.status_code == 400
    assert "Mutating SQL is not allowed" in resp.json()["detail"]


def test_malformed_post_body_is_422_before_the_handler_runs(client: TestClient) -> None:
    resp = client.post("/api/v1/query", json={})

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(item["loc"] == ["body", "sql"] for item in detail)


def test_cors_middleware_is_actually_installed(client: TestClient) -> None:
    """Middleware is the layer a direct handler call is structurally blind to."""
    simple = client.get("/api/v1/health", headers={"Origin": "https://example.com"})
    assert simple.status_code == 200
    assert simple.headers["access-control-allow-origin"] == "*"

    preflight = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert "GET" in preflight.headers["access-control-allow-methods"]


def test_openapi_schema_is_generated_and_valid_json(client: TestClient) -> None:
    """If any route's models were unserializable, this is where it surfaces."""
    resp = client.get("/openapi.json")

    assert resp.status_code == 200
    schema = json.loads(resp.content)
    assert schema["openapi"].startswith("3.")
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/query" in schema["paths"]

"""One capability, invoked through all three projections, against a real lake.

Gate 4 checks that the names line up. These check that following one of those names reaches
an implementation, reads Parquet off disk, and returns the numbers — because a surface that
lists a capability and answers nothing is exactly what this merge shipped once already.

The same request is sent through each surface and the row counts are compared at the end,
which is the property "full API symmetry" is actually a claim about.
"""

from __future__ import annotations

import pathlib

import pytest
from typer.testing import CliRunner

from crocodile.core.capability import AssetClass, CapabilityContext
from crocodile.core.config import Settings
from crocodile.core.store.catalog import Catalog
from crocodile.surfaces import cli, dispatch, mcp, rest
from tests.surfaces.conftest import END_NS, START_NS, SYMBOL


def _settings(lake: pathlib.Path) -> Settings:
    return Settings(data_dir=lake)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_the_cli_computes_indicators_against_a_real_lake(
    lake: pathlib.Path, indicator_query: dict[str, str]
) -> None:
    result = CliRunner().invoke(
        cli.build_app(),
        [
            "indicators",
            "--symbol",
            indicator_query["symbol"],
            "--start-ns",
            indicator_query["start_ns"],
            "--end-ns",
            indicator_query["end_ns"],
            "--interval",
            indicator_query["interval"],
            "--indicator",
            indicator_query["indicator"],
            "--period",
            indicator_query["period"],
            "--data-dir",
            str(lake),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rsi" in result.output, result.output
    assert "No data found" not in result.output


def test_the_cli_reports_a_bad_parameter_instead_of_a_traceback(lake: pathlib.Path) -> None:
    """``--indicator stochastic`` is a user error, and the implementation says so."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["indicators", "--symbol", SYMBOL, "--start-ns", str(START_NS), "--end-ns", str(END_NS),
         "--indicator", "stochastic", "--data-dir", str(lake)],
    )
    assert result.exit_code == 1
    assert "Unknown indicator" in result.output


def test_the_cli_answers_to_a_retired_spelling(lake: pathlib.Path) -> None:
    """``simulate-price-impact`` was the only name equity ever exposed for ``slippage``."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["simulate-price-impact", "--symbol", SYMBOL, "--side", "buy", "--size", "1.0",
         "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "expected_price" in result.output


def test_the_cli_warns_when_the_implementation_is_not_native(lake: pathlib.Path) -> None:
    """Equity slippage rests on a modelled book, and the surface says so before the number.

    The banner generalises the one the equity REST depth route shipped by hand for a single
    endpoint. Here it comes from the basis registration, so every modelled answer carries
    one.
    """
    runner = CliRunner()
    result = runner.invoke(
        cli.build_app(),
        ["slippage", "--symbol", SYMBOL, "--side", "buy", "--size", "1.0",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "SYNTHETIC" in result.output
    assert "yahoo_1m_vap" in result.output


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


def _client(lake: pathlib.Path):  # starlette's TestClient, imported lazily
    from starlette.testclient import TestClient

    return TestClient(rest.build_app(settings=_settings(lake)))


def test_rest_returns_rows_and_a_provenance_block(
    lake: pathlib.Path, indicator_query: dict[str, str]
) -> None:
    response = _client(lake).get("/api/v1/indicators", params=indicator_query)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"], "a lake with forty trades must produce bars"
    assert "rsi" in body["rows"][0]
    assert body["provenance"] == {
        "capability": "indicators",
        "asset_class": "crypto",
        "prov": "derived",
        "prov_basis": "native",
        "method": body["provenance"]["method"],
        "row_limit": dispatch.NETWORK_ROW_LIMIT,
    }


def test_rest_publishes_the_row_ceiling_it_applied(
    lake: pathlib.Path, indicator_query: dict[str, str]
) -> None:
    """A cap nobody is told about turns a truncated answer into a wrong one."""
    body = _client(lake).get("/api/v1/indicators", params=indicator_query).json()
    assert body["provenance"]["row_limit"] == dispatch.NETWORK_ROW_LIMIT


def test_rest_maps_a_bad_parameter_to_400(lake: pathlib.Path) -> None:
    response = _client(lake).get(
        "/api/v1/indicators",
        params={"symbol": SYMBOL, "start_ns": START_NS, "end_ns": END_NS, "indicator": "nope"},
    )
    assert response.status_code == 400
    assert "Unknown indicator" in response.json()["detail"]


def test_rest_maps_a_missing_required_parameter_to_400(lake: pathlib.Path) -> None:
    response = _client(lake).get("/api/v1/indicators", params={"symbol": SYMBOL})
    assert response.status_code == 400


def test_rest_serves_the_alias_and_the_synthetic_warning(lake: pathlib.Path) -> None:
    response = _client(lake).get(
        "/api/v1/simulate-price-impact",
        params={"symbol": SYMBOL, "side": "buy", "size": "1.0", "asset_class": "equity"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["expected_price"] > 0
    assert "SYNTHETIC" in body["warning"]


def test_rest_describes_its_query_parameters_in_openapi(lake: pathlib.Path) -> None:
    """The published schema and the MCP inputSchema come from the same params struct."""
    document = _client(lake).get("/openapi.json").json()
    parameters = document["paths"]["/api/v1/indicators"]["get"]["parameters"]
    names = {parameter["name"] for parameter in parameters}
    assert {"symbol", "start_ns", "end_ns", "interval", "indicator", "period"} <= names
    assert "asset_class" in names


def test_rest_serves_no_route_for_something_that_is_not_a_capability(lake: pathlib.Path) -> None:
    """``health`` is infrastructure. It is hand-written elsewhere and not projected here."""
    assert _client(lake).get("/api/v1/health").status_code == 404


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def test_mcp_calls_a_tool_and_returns_the_same_envelope(
    lake: pathlib.Path, indicator_query: dict[str, str]
) -> None:
    body = mcp.call_tool("indicators", dict(indicator_query), settings=_settings(lake))
    assert body["rows"]
    assert "rsi" in body["rows"][0]
    assert body["provenance"]["capability"] == "indicators"
    assert body["provenance"]["row_limit"] == dispatch.NETWORK_ROW_LIMIT
    # DERIVED, not NATIVE: an indicator is computed, never reported by a venue. The banner
    # must say that without also claiming the *inputs* were computed — they were native.
    assert body["warning"].startswith("DERIVED — indicators for crypto")
    assert "inputs rest on 'native'" in body["warning"]


def test_mcp_publishes_the_params_struct_as_its_input_schema() -> None:
    tool = next(t for t in mcp.tool_definitions() if t["name"] == "indicators")
    assert tool["inputSchema"]["$ref"].endswith("IndicatorParams")
    properties = tool["inputSchema"]["$defs"]["IndicatorParams"]["properties"]
    assert set(properties) == {"symbol", "start_ns", "end_ns", "interval", "indicator", "period"}
    assert tool["assetClasses"] == ["crypto", "equity"]


def test_mcp_warns_on_a_modelled_answer(lake: pathlib.Path) -> None:
    body = mcp.call_tool(
        "simulate-price-impact",
        {"symbol": SYMBOL, "side": "buy", "size": 1.0, "asset_class": "equity"},
        settings=_settings(lake),
    )
    assert "SYNTHETIC" in body["warning"]
    assert body["provenance"]["prov_basis"] == "yahoo_1m_vap"


def test_mcp_refuses_a_tool_it_does_not_have() -> None:
    with pytest.raises(KeyError, match="no-such-tool"):
        mcp.call_tool("no-such-tool", {})


# ---------------------------------------------------------------------------
# The three together
# ---------------------------------------------------------------------------


def test_all_three_surfaces_return_the_same_answer(
    lake: pathlib.Path, indicator_query: dict[str, str]
) -> None:
    """The claim the projection actually makes, checked rather than assumed."""
    from_rest = _client(lake).get("/api/v1/indicators", params=indicator_query).json()["rows"]
    from_mcp = mcp.call_tool("indicators", dict(indicator_query), settings=_settings(lake))["rows"]
    assert from_rest == from_mcp

    result = CliRunner().invoke(
        cli.build_app(),
        ["indicators", "--symbol", SYMBOL, "--start-ns", str(START_NS), "--end-ns", str(END_NS),
         "--interval", "1m", "--indicator", "rsi", "--period", "5", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    # Polars prints the row count in its frame header; the CLI renders the frame directly.
    assert f"({len(from_rest)}," in result.output


# ---------------------------------------------------------------------------
# `readonly` is a property of the surface
# ---------------------------------------------------------------------------

_REJECTED_BY_THE_GUARD = "SELECT 'delete me' AS note"
"""Valid, harmless SQL that :func:`assert_readonly_sql` refuses.

Deliberately not an actual mutation. The guard is keyword-level and has no lexer, so a
banned word inside a string literal trips it — which its own docstring documents — and that
makes this the one probe that tells the two postures apart without a statement that would
damage a lake if the guard were missing.
"""


def _context(
    lake: pathlib.Path, *, readonly: bool, row_limit: int | None
) -> CapabilityContext:
    catalog = Catalog(lake)
    return dispatch.build_context(
        catalog,
        AssetClass.CRYPTO,
        settings=_settings(lake),
        readonly=readonly,
        row_limit=row_limit,
    )


def test_the_local_cli_posture_does_not_vet_sql(lake: pathlib.Path) -> None:
    """The crypto CLI reaches ``Catalog.query`` with SQL this guard rejects, by design."""
    ctx = _context(lake, readonly=False, row_limit=None)
    assert ctx.query(_REJECTED_BY_THE_GUARD)["note"][0] == "delete me"


@pytest.mark.parametrize("row_limit", [None, dispatch.NETWORK_ROW_LIMIT])
def test_the_network_posture_vets_sql(lake: pathlib.Path, row_limit: int | None) -> None:
    ctx = _context(lake, readonly=True, row_limit=row_limit)
    with pytest.raises(ValueError, match="disallowed keywords"):
        ctx.query(_REJECTED_BY_THE_GUARD)


def test_the_network_posture_caps_the_rows_it_materialises(lake: pathlib.Path) -> None:
    ctx = _context(lake, readonly=True, row_limit=2)
    rows = ctx.query("SELECT * FROM trade")
    assert len(rows) == 2

    uncapped = _context(lake, readonly=False, row_limit=None)
    assert len(uncapped.query("SELECT * FROM trade")) > 2


def test_each_surface_declares_its_own_posture(lake: pathlib.Path) -> None:
    """Read off the surfaces rather than restated, so a change of posture fails here.

    The CLI is permissive because it runs on the machine that owns the lake; REST and MCP
    are the network and are identical to each other, which is the half of the old
    divergence that mattered — the two had independently solved different halves of the
    same problem.
    """
    captured: dict[str, tuple[bool, int | None]] = {}
    real_build = dispatch.build_context

    def _spy(catalog, asset_class, *, settings=None, readonly, row_limit):
        captured[str(len(captured))] = (readonly, row_limit)
        return real_build(
            catalog, asset_class, settings=settings, readonly=readonly, row_limit=row_limit
        )

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(dispatch, "build_context", _spy)
        CliRunner().invoke(
            cli.build_app(),
            ["indicators", "--symbol", SYMBOL, "--start-ns", str(START_NS), "--end-ns",
             str(END_NS), "--data-dir", str(lake)],
        )
        cli_posture = captured.pop("0")

        captured.clear()
        mcp.call_tool(
            "indicators",
            {"symbol": SYMBOL, "start_ns": START_NS, "end_ns": END_NS},
            settings=_settings(lake),
        )
        mcp_posture = captured.pop("0")

        captured.clear()
        _client(lake).get(
            "/api/v1/indicators",
            params={"symbol": SYMBOL, "start_ns": START_NS, "end_ns": END_NS},
        )
        rest_posture = captured.pop("0")
    finally:
        monkey.undo()

    assert cli_posture == (False, None)
    assert rest_posture == (True, dispatch.NETWORK_ROW_LIMIT)
    assert rest_posture == mcp_posture

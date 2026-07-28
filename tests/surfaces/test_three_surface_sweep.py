"""Six capabilities of six shapes, driven through all three surfaces against a real lake.

Gate 4 proves the three projections carry the same *names*. ``test_end_to_end`` proves one
capability of one shape reaches an implementation. Neither of those is the property the
projection actually claims, which is that following a name on any surface returns the same
answer — and the exit review found seven ways it did not, every one of them by taking a
capability of an unusual shape and pushing it through all three.

So this is that sweep, kept. The shapes are chosen to be different from each other rather
than representative, because every finding lived in a shape nothing else exercised:

``slippage``          ``SCALAR``: one object, not a one-row table.
``simulate-price-impact``  the same capability under a retired spelling.
``catalog-scan``      a sequence parameter, and the ``date`` partition cell on every row.
``replay``            a lazy iterator the surface has to consume.
``query``             raw SQL, so the result shape is entirely the caller's.
``collect``           ``STREAM``: unbounded, and a write the network surfaces refuse.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from typer.testing import CliRunner

from crocodile.core.config import Settings
from crocodile.surfaces import cli, dispatch, mcp, rest, stdio
from tests.surfaces.conftest import END_NS, START_NS, SYMBOL


def _settings(lake: pathlib.Path) -> Settings:
    return Settings(data_dir=lake)


def _client(lake: pathlib.Path):  # starlette's TestClient, imported lazily
    from starlette.testclient import TestClient

    return TestClient(rest.build_app(settings=_settings(lake)), raise_server_exceptions=False)


def _cli(lake: pathlib.Path, *arguments: str):
    return CliRunner().invoke(cli.build_app(), [*arguments, "--data-dir", str(lake)])


# ---------------------------------------------------------------------------
# SCALAR, and the same capability under its retired spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wire", ["slippage", "simulate-price-impact"])
def test_a_scalar_capability_answers_the_same_on_all_three(
    lake: pathlib.Path, wire: str
) -> None:
    arguments = {"symbol": SYMBOL, "side": "buy", "size": "1.0"}

    from_rest = _client(lake).get(f"/api/v1/{wire}", params=arguments)
    assert from_rest.status_code == 200, from_rest.text
    from_mcp = mcp.call_tool(wire, dict(arguments), settings=_settings(lake))
    from_cli = _cli(lake, wire, "--symbol", SYMBOL, "--side", "buy", "--size", "1.0")

    assert from_cli.exit_code == 0, from_cli.output
    assert from_rest.json()["result"] == from_mcp["result"] == json.loads(from_cli.stdout)
    assert from_rest.json()["provenance"] == from_mcp["provenance"]
    # A one-object answer, not a one-row table: the distinction is read off the declaration
    # and it is what stops one surface serving a frame where another serves an object.
    assert from_mcp["result"]["expected_price"] > 0
    assert "rows" not in from_mcp


# ---------------------------------------------------------------------------
# A sequence parameter, and the date cell every stored row carries
# ---------------------------------------------------------------------------


def test_a_sequence_parameter_capability_answers_the_same_on_all_three(
    lake: pathlib.Path,
) -> None:
    arguments = {
        "channel": "trade",
        "symbols": SYMBOL,
        "start_ns": str(START_NS),
        "end_ns": str(END_NS),
        "limit": "3",
    }

    from_rest = _client(lake).get("/api/v1/catalog-scan", params=arguments)
    assert from_rest.status_code == 200, from_rest.text
    from_mcp = mcp.call_tool("catalog-scan", dict(arguments), settings=_settings(lake))
    from_cli = _cli(
        lake, "catalog-scan", "--channel", "trade", "--symbols", SYMBOL,
        "--start-ns", str(START_NS), "--end-ns", str(END_NS), "--limit", "3",
    )

    assert from_cli.exit_code == 0, from_cli.output
    assert len(from_rest.json()["rows"]) == 3
    assert from_rest.json()["rows"] == from_mcp["rows"]
    # Neither surface was told which market and both answered, which is the claim; the
    # symbol settled it for `resolve_asset_class` either way. The envelope reports `any`
    # because `catalog-scan` reads the lake as a lake — one implementation for both classes,
    # never consulting `ctx.asset_class` — so stamping the resolved value would report an
    # input as a property of the answer.
    assert from_rest.json()["provenance"]["asset_class"] == "any"
    assert dispatch.resolve("catalog-scan").cross_market is True
    # And the result crosses both wires, which the `date` partition column stopped it doing.
    json.dumps(from_mcp)


# ---------------------------------------------------------------------------
# A lazy iterator
# ---------------------------------------------------------------------------


def test_a_lazily_returned_capability_answers_the_same_on_all_three(
    lake: pathlib.Path,
) -> None:
    arguments = {
        "channels": "trade",
        "symbols": SYMBOL,
        "start_ns": str(START_NS),
        "end_ns": str(END_NS),
        "limit": "4",
    }

    from_rest = _client(lake).get("/api/v1/replay", params=arguments)
    assert from_rest.status_code == 200, from_rest.text
    from_mcp = mcp.call_tool("replay", dict(arguments), settings=_settings(lake))
    from_cli = _cli(
        lake, "replay", "--channels", "trade", "--symbols", SYMBOL,
        "--start-ns", str(START_NS), "--end-ns", str(END_NS), "--limit", "4",
    )

    assert from_cli.exit_code == 0, from_cli.output
    assert [row["id"] for row in from_rest.json()["rows"]] == ["0", "1", "2", "3"]
    assert from_rest.json()["rows"] == from_mcp["rows"] == json.loads(from_cli.stdout)


# ---------------------------------------------------------------------------
# Raw SQL, where the result shape is the caller's
# ---------------------------------------------------------------------------


def test_a_raw_sql_capability_answers_the_same_on_all_three(lake: pathlib.Path) -> None:
    sql = "SELECT symbol, count(*) AS n, min(local_ts) AS first_ts FROM trade GROUP BY symbol"

    from_get = _client(lake).get("/api/v1/query", params={"sql": sql, "asset_class": "crypto"})
    from_post = _client(lake).post("/api/v1/query", json={"sql": sql, "asset_class": "crypto"})
    from_mcp = mcp.call_tool("query", {"sql": sql, "asset_class": "crypto"},
                             settings=_settings(lake))
    from_cli = _cli(lake, "query", sql, "--asset-class", "crypto")

    assert from_get.status_code == from_post.status_code == 200, from_get.text
    assert from_cli.exit_code == 0, from_cli.output
    assert from_get.json() == from_post.json()
    assert from_get.json()["rows"] == from_mcp["rows"] == [{"symbol": SYMBOL, "n": 40,
                                                            "first_ts": START_NS}]
    assert str(START_NS) in from_cli.stdout


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) AS n FROM trade -- how many",
        "SELECT count(*) AS n FROM trade --",
        "SELECT count(*) AS n FROM trade -- trailing ) AS x LIMIT 1",
    ],
    ids=["comment", "bare-marker", "comment-containing-sql"],
)
def test_a_trailing_line_comment_is_not_a_syntax_error_on_the_network_surfaces(
    lake: pathlib.Path, sql: str
) -> None:
    """The surface's own LIMIT wrapper must not be swallowed by the caller's comment.

    ``--`` runs to the end of the line, and the wrapper folded the statement onto one line —
    so the closing parenthesis, the alias and the LIMIT all landed *inside* the comment.
    Before: the CLI (no cap, no wrapper) answered, and REST and MCP answered
    ``400 Parser Error: syntax error at end of input``, blaming the caller for SQL the
    surface broke. A commented statement is what anyone pastes out of an editor.
    """
    from_rest = _client(lake).get("/api/v1/query", params={"sql": sql, "asset_class": "crypto"})
    assert from_rest.status_code == 200, from_rest.text
    from_mcp = mcp.call_tool("query", {"sql": sql, "asset_class": "crypto"},
                             settings=_settings(lake))
    from_cli = _cli(lake, "query", sql, "--asset-class", "crypto")
    assert from_cli.exit_code == 0, from_cli.output
    assert from_rest.json()["rows"] == from_mcp["rows"] == [{"n": 40}]


def test_the_network_posture_holds_across_the_sweep(lake: pathlib.Path) -> None:
    """Same statement, same lake, and only the local surface is allowed to run it."""
    guarded = "SELECT 'delete me' AS note"
    assert _client(lake).get(
        "/api/v1/query", params={"sql": guarded, "asset_class": "crypto"}
    ).status_code == 400
    with pytest.raises(ValueError, match="disallowed keywords"):
        mcp.call_tool("query", {"sql": guarded, "asset_class": "crypto"},
                      settings=_settings(lake))
    assert _cli(lake, "query", guarded, "--asset-class", "crypto").exit_code == 0


# ---------------------------------------------------------------------------
# STREAM: unbounded, and a write
# ---------------------------------------------------------------------------


_COLLECT = {
    "sources": "deribit",
    "symbols": SYMBOL,
    "channels": "trade",
    "duration_seconds": "1",
}


def test_a_stream_capability_runs_locally_and_is_refused_on_the_network(
    lake: pathlib.Path, collecting_nothing: None
) -> None:
    """Three different right answers, which is what makes this shape worth sweeping.

    The CLI owns a process and a terminal, so it runs the subscription and reports what it
    collected. REST and MCP are read-only and refuse the write before a socket exists — 403
    on one and a tool error on the other, both saying the same thing, and neither of them a
    500 that reads as a crash.
    """
    from_cli = _cli(
        lake, "collect", "--sources", "deribit", "--symbols", SYMBOL,
        "--channels", "trade", "--duration-seconds", "1",
    )
    assert from_cli.exit_code == 0, from_cli.output
    assert "trade" in from_cli.stdout and "deribit" in from_cli.stdout

    refused = _client(lake).post("/api/v1/collect", json=dict(_COLLECT))
    assert refused.status_code == 403, refused.text
    assert "read-only" in refused.json()["detail"]

    reported = stdio.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "collect", "arguments": dict(_COLLECT)}},
        data_dir=lake,
    )
    assert "error" not in reported
    assert "read-only" in reported["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# The property all of it is about
# ---------------------------------------------------------------------------


_SWEPT = ["slippage", "simulate-price-impact", "catalog-scan", "replay", "query", "collect"]


@pytest.mark.parametrize("wire", _SWEPT)
def test_every_swept_capability_is_reachable_on_every_surface(wire: str) -> None:
    """The name is on all three, before any of the above asks what it answers."""
    assert wire in cli.command_names()
    assert wire in mcp.tool_names()
    assert rest.path_for(wire) in rest.route_paths()
    assert rest.methods_for(dispatch.resolve(wire)), wire


def test_the_swept_shapes_are_actually_different(lake: pathlib.Path) -> None:
    """Guard the guard: six capabilities of one shape would prove one thing six times."""
    from crocodile.core.capability import ReturnKind

    shapes: set[tuple[Any, ...]] = set()
    for wire in _SWEPT:
        cap = dispatch.resolve(wire)
        shapes.add(
            (
                cap.returns,
                bool(dispatch.structured_fields(cap)),
                cap.returns is ReturnKind.TABLE,
                tuple(sorted(dispatch.symbol_hints(_probe(cap)))),
            )
        )
    assert len(shapes) >= 3, shapes
    assert {dispatch.resolve(w).returns for w in _SWEPT} == {
        ReturnKind.SCALAR,
        ReturnKind.TABLE,
        ReturnKind.STREAM,
    }


def _probe(cap: Any) -> Any:
    """A params instance with only its required scalar fields filled, for shape questions."""
    import msgspec

    values: dict[str, Any] = {}
    for field in msgspec.structs.fields(cap.params):
        if field.required:
            values[field.name] = SYMBOL if "symbol" in field.name else "1"
    return dispatch.build_params(cap, values)

"""Nothing a capability hands back unstarted or unconsumed may reach a caller.

Three capabilities return work rather than an answer — ``backfill`` an unstarted coroutine,
``collect`` an unstarted :class:`~crocodile.capabilities.ops.Subscription`, ``replay`` a lazy
iterator — because an implementation cannot know whether its caller already owns an event
loop. The surface is what finishes them, and when it does not the failure is silent: the CLI
printed ``<itertools.islice object at 0x…>`` and exited **0**.

The first two were fixed inside the CLI, which is why the third one shipped: a hazard that
belongs to all three surfaces cannot be answered in one of them. These tests drive the third
through every surface, which is the only shape that would have caught it.
"""

from __future__ import annotations

import json
import pathlib

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


_REPLAY_ARGUMENTS = {
    "channels": "trade",
    "symbols": SYMBOL,
    "start_ns": str(START_NS),
    "end_ns": str(END_NS),
    "limit": "3",
    "asset_class": "crypto",
}
"""The same replay request, spelled for each transport."""


def test_the_cli_prints_replayed_records_instead_of_the_iterator_object(
    lake: pathlib.Path,
) -> None:
    """``exit 0`` with an ``islice`` repr on stdout is the quietest possible failure."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["replay", "--channels", "trade", "--symbols", SYMBOL, "--start-ns", str(START_NS),
         "--end-ns", str(END_NS), "--limit", "3", "--asset-class", "crypto",
         "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "islice" not in result.output, result.output
    assert "object at 0x" not in result.output, result.output
    assert SYMBOL in result.output, result.output


def test_rest_answers_replay_with_rows_rather_than_500(lake: pathlib.Path) -> None:
    response = _client(lake).get("/api/v1/replay", params=_REPLAY_ARGUMENTS)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert len(rows) == 3, rows
    assert rows[0]["symbol"] == SYMBOL


def test_mcp_replay_survives_json_dumps(lake: pathlib.Path) -> None:
    """The MCP transport is ``json.dumps``, and a generator is not serialisable by it."""
    body = mcp.call_tool("replay", dict(_REPLAY_ARGUMENTS), settings=_settings(lake))
    assert len(body["rows"]) == 3
    json.dumps(body)


def test_all_three_surfaces_replay_the_same_records(lake: pathlib.Path) -> None:
    """The claim the projection makes, for the return shape that had it on zero surfaces."""
    from_rest = _client(lake).get("/api/v1/replay", params=_REPLAY_ARGUMENTS).json()["rows"]
    from_mcp = mcp.call_tool("replay", dict(_REPLAY_ARGUMENTS), settings=_settings(lake))["rows"]
    assert from_rest == from_mcp
    assert [row["id"] for row in from_rest] == ["0", "1", "2"]


def test_a_network_surface_stops_materialising_at_its_own_row_ceiling(
    lake: pathlib.Path,
) -> None:
    """A lazy result is only safe on the network while somebody bounds it.

    ``replay`` reads through ``Catalog.scan`` rather than through
    :meth:`CapabilityContext.query <crocodile.core.capability.CapabilityContext.query>`, so
    the ``LIMIT`` wrapper that caps raw SQL never sees it. Draining an unbounded iterator
    into a JSON response is how one request materialises a lake.
    """
    unbounded = dict(_REPLAY_ARGUMENTS)
    del unbounded["limit"]
    body = mcp.call_tool("replay", unbounded, settings=_settings(lake))
    assert len(body["rows"]) <= dispatch.NETWORK_ROW_LIMIT

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(dispatch, "NETWORK_ROW_LIMIT", 5)
        capped = mcp.call_tool("replay", unbounded, settings=_settings(lake))
    finally:
        monkey.undo()
    assert len(capped["rows"]) == 5
    assert capped["provenance"]["row_limit"] == 5


def _returning_a_coroutine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``catalog-summary`` with an implementation that hands back unstarted work."""
    from crocodile.core.capability import REGISTRY, Capability, Impl

    original = dispatch.resolve("catalog-summary")

    async def _work(ctx: object, params: object) -> dict[str, str]:
        return {"awaited": "yes"}

    monkeypatch.setitem(
        REGISTRY,
        "catalog-summary",
        Capability(
            name=original.name,
            summary=original.summary,
            params=original.params,
            returns=original.returns,
            aliases=original.aliases,
            impls={
                asset_class: Impl(fn=_work, prov=impl.prov, basis=impl.basis)
                for asset_class, impl in original.impls.items()
            },
        ),
    )


def test_an_unstarted_coroutine_is_awaited_by_the_network_surface_too(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other unstarted shape, on the surface that could not previously start one.

    ``backfill`` returns its coroutine unstarted precisely because ``asyncio.run`` inside a
    running event loop raises, and a FastAPI route is inside one — so an ``async def``
    endpoint could only ever have answered this with ``RuntimeError``. The route does its
    work in a worker thread, which owns no loop, and that is what makes one ``drive`` serve
    all three surfaces instead of the CLI keeping its own.
    """
    _returning_a_coroutine(monkeypatch)
    response = _client(lake).get("/api/v1/catalog-summary", params={"asset_class": "crypto"})
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"awaited": "yes"}

    from_mcp = mcp.call_tool("catalog-summary", {"asset_class": "crypto"},
                             settings=_settings(lake))
    assert from_mcp["result"] == {"awaited": "yes"}


async def test_mcp_drives_unstarted_work_from_inside_the_loop_it_actually_runs_in(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same claim from the one posture where it can fail, which is MCP's real one.

    ``async def``, and that is the entire point of this test existing beside the one above.
    ``drive`` called a bare ``asyncio.run``, which raises ``RuntimeError`` inside a running
    event loop; REST is protected because its endpoint hands the work to
    ``run_in_threadpool``, and MCP is not — ``operate.mcp`` opens a loop with ``asyncio.run``
    and ``stdio.serve_stdio`` calls ``handle_request`` inline on it. Nothing stands between
    them.

    The gate that was meant to catch this asserted MCP from a **synchronous** body, which is
    the one posture where no loop is running and the bug therefore cannot appear — and every
    MCP-touching test body in the tree was ``def``, despite ``asyncio_mode = "auto"`` making
    an ``async def`` one free. A capability returning a coroutine is latent rather than
    impossible: the four that do today are all writes, so ``_refuse_readonly`` fires first,
    and nothing in the registry forbids a read-only one.

    Both entry points are driven: ``call_tool`` directly, and the JSON-RPC handler the
    transport actually calls, because it is the second that runs on the loop in production.
    """
    import asyncio

    assert asyncio.get_running_loop() is not None, "this test is worthless without a loop"
    _returning_a_coroutine(monkeypatch)

    body = mcp.call_tool("catalog-summary", {"asset_class": "crypto"}, settings=_settings(lake))
    assert body["result"] == {"awaited": "yes"}

    response = stdio.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "catalog-summary", "arguments": {"asset_class": "crypto"}},
        },
        data_dir=lake,
    )
    assert "error" not in response, response
    assert '"awaited": "yes"' in response["result"]["content"][0]["text"]


def test_a_result_no_surface_can_encode_is_refused_rather_than_handed_back() -> None:
    """``_encodable`` swallowed the ``TypeError`` and returned the object unchanged.

    That is what turned a lazy ``replay`` into an ``islice`` on stdout, a 500 on REST and a
    protocol error on MCP instead of one legible failure. A projection that cannot encode a
    result has a bug in it, and a bug must say so where it happens.
    """

    class _Unencodable:
        pass

    with pytest.raises(TypeError, match="depth"):
        dispatch.payload(dispatch.resolve("depth"), _Unencodable())

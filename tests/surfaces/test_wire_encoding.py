"""What one surface can encode, every surface must encode.

The three projections do not share an encoder — FastAPI serialises with pydantic, MCP with
``json.dumps``, the CLI prints — so the only thing they can share is what they are *asked* to
encode. When they were not, the divergence was invisible from either side: every lake read
carries a ``date`` partition cell, which FastAPI turns into ``"2023-11-14"`` and
``json.dumps`` refuses outright, so ``catalog-scan`` and ``query`` answered 200 on REST and
raised ``Object of type date is not JSON serializable`` on MCP.

``query`` is the most-used capability in the product and the one whose result shape is
entirely the caller's, which makes it the worst place to have a type the wire cannot carry.
"""

from __future__ import annotations

import datetime
import decimal
import json
import pathlib
import uuid

import pytest

from crocodile.core.capability import REGISTRY
from crocodile.core.config import Settings
from crocodile.surfaces import dispatch, mcp, rest, stdio
from tests.surfaces.conftest import END_NS, START_NS, SYMBOL


def _settings(lake: pathlib.Path) -> Settings:
    return Settings(data_dir=lake)


def _client(lake: pathlib.Path):  # starlette's TestClient, imported lazily
    from starlette.testclient import TestClient

    return TestClient(rest.build_app(settings=_settings(lake)), raise_server_exceptions=False)


_DATE_SQL = "SELECT DATE '2023-11-14' AS d"


def test_a_date_cell_reaches_mcp_the_way_it_reaches_rest(lake: pathlib.Path) -> None:
    """The measured divergence: REST 200, MCP ``TypeError``, same capability, same lake."""
    arguments = {"sql": _DATE_SQL, "asset_class": "crypto"}
    from_rest = _client(lake).get("/api/v1/query", params=arguments)
    assert from_rest.status_code == 200, from_rest.text

    body = mcp.call_tool("query", dict(arguments), settings=_settings(lake))
    json.dumps(body)
    assert body["rows"] == from_rest.json()["rows"] == [{"d": "2023-11-14"}]


def test_the_partition_column_of_every_lake_read_survives_mcp(lake: pathlib.Path) -> None:
    """``catalog-scan`` returns the ``date`` column the lake is partitioned by.

    Not a corner case reachable only by writing ``DATE`` into SQL by hand: it is on every
    row of every stored channel, so this failure covered every lake-reading capability.
    """
    body = mcp.call_tool(
        "catalog-scan",
        {"channel": "trade", "symbols": SYMBOL, "start_ns": str(START_NS),
         "end_ns": str(END_NS), "limit": "2", "asset_class": "crypto"},
        settings=_settings(lake),
    )
    json.dumps(body)
    assert body["rows"], "the fixture lake holds trades in this range"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime.date(2023, 11, 14), "2023-11-14"),
        (datetime.datetime(2023, 11, 14, 9, 30), "2023-11-14T09:30:00"),
        (decimal.Decimal("1.5"), "1.5"),
        (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_a_cell_the_wire_cannot_carry_is_converted_rather_than_passed_through(
    value: object, expected: object
) -> None:
    """One rule for every type a capability may legitimately return.

    Narrowing to ``float`` covered the non-finite case and nothing else, which is how a
    ``date`` got through. Which types a given transport happens to understand is not
    something a caller should have to know.
    """
    assert dispatch._jsonable(value) == expected


def test_a_non_finite_number_nested_inside_a_struct_is_still_caught() -> None:
    """The walk continues into what ``to_builtins`` produced, not just over the top level."""
    import msgspec

    class _Nested(msgspec.Struct, frozen=True):
        readings: list[float]
        stamped: datetime.date

    shaped = dispatch.payload(
        REGISTRY["depth"], _Nested(readings=[float("nan"), 1.5], stamped=datetime.date(2020, 1, 1))
    )
    assert shaped == {"result": {"readings": [None, 1.5], "stamped": "2020-01-01"}}
    assert "NaN" not in json.dumps(shaped)


def test_an_unencodable_tool_result_is_reported_as_a_tool_error(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``json.dumps`` sat *outside* the try, so an encoding failure was not even reported.

    The handler catches what ``call_tool`` raises and turns it into a result an agent can
    read; serialising that result afterwards meant a ``TypeError`` there escaped to the read
    loop as ``-32603 Internal error``, which tells the agent the *call* failed rather than
    what about its request could not be answered.
    """
    monkeypatch.setattr(mcp, "call_tool", lambda *a, **k: {"rows": [{"d": object()}]})
    response = stdio.handle_request(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "query", "arguments": {"sql": "SELECT 1"}}},
        data_dir=lake,
    )
    assert "error" not in response, response
    assert response["id"] == 7
    assert "query" in response["result"]["content"][0]["text"]

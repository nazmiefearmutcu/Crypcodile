"""A parameter nobody understood is a question nobody answered.

``GET /api/v1/catalog-inventory?source=nope`` answered **200 with the full inventory**,
because the field is spelled ``exchange`` and ``msgspec.convert`` drops what it does not
recognise. A filter that is silently ignored does not narrow anything and does not say it
did not: the caller reads the whole lake as the answer to a question about one exchange,
which is the exact failure shape this projection was built to end.
"""

from __future__ import annotations

import pathlib

import pytest
from typer.testing import CliRunner

from crocodile.surfaces import cli, dispatch, mcp, rest, stdio
from tests.surfaces.conftest import SYMBOL


def _client(lake: pathlib.Path):  # starlette's TestClient, imported lazily
    from starlette.testclient import TestClient

    from crocodile.core.config import Settings

    return TestClient(
        rest.build_app(settings=Settings(data_dir=lake)), raise_server_exceptions=False
    )


_MISSPELLED = "exchagne"
"""A near miss for a real filter, and a name no capability declares under any spelling.

The measured case was `?source=nope` on `catalog-inventory`, which takes `exchange`:
`source` is the lake's own partition key, so it is the obvious guess, and guessing it
returned the whole inventory. A transposition is used here instead so this test keeps
measuring the *rule* rather than which of the two spellings the registry settles on.
"""


def _filter_field(name: str) -> str:
    """A field this capability really declares, read off the registry rather than typed."""
    import msgspec

    fields = [
        field.name
        for field in msgspec.structs.fields(dispatch.resolve(name).params)
        if field.name != "channel"
    ]
    assert fields, name
    return fields[0]


def test_a_misspelled_filter_is_refused_rather_than_ignored(lake: pathlib.Path) -> None:
    """A filter that is silently ignored narrows nothing and does not say it did not."""
    response = _client(lake).get(
        "/api/v1/catalog-inventory", params={_MISSPELLED: "nope", "asset_class": "crypto"}
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert _MISSPELLED in detail
    assert _filter_field("catalog-inventory") in detail, (
        "the refusal has to name what the caller could have meant"
    )


def test_the_refusal_reaches_the_other_two_surfaces(lake: pathlib.Path) -> None:
    with pytest.raises(ValueError, match=_MISSPELLED):
        mcp.call_tool("catalog-inventory", {_MISSPELLED: "nope", "asset_class": "crypto"})

    reported = stdio.handle_request(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "catalog-inventory",
                    "arguments": {_MISSPELLED: "nope", "asset_class": "crypto"}}},
        data_dir=lake,
    )
    assert _MISSPELLED in reported["result"]["content"][0]["text"]


def test_a_body_parameter_that_is_not_a_parameter_is_refused_too(lake: pathlib.Path) -> None:
    """A POST body is the same request in a richer encoding, and gets the same reading."""
    response = _client(lake).post(
        "/api/v1/query", json={"sql": "SELECT 1", "limit": 5, "asset_class": "crypto"}
    )
    assert response.status_code == 400, response.text
    assert "limit" in response.json()["detail"]


def test_the_surfaces_own_parameters_are_not_mistaken_for_unknown_ones(
    lake: pathlib.Path,
) -> None:
    """``asset_class`` selects the implementation; it is not a capability parameter."""
    field = _filter_field("catalog-inventory")
    response = _client(lake).get(
        "/api/v1/catalog-inventory", params={field: "deribit", "asset_class": "crypto"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"], "deribit is in the fixture lake"

    result = CliRunner().invoke(
        cli.build_app(),
        ["catalog-inventory", f"--{field.replace('_', '-')}", "deribit",
         "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output


def test_the_refusal_lists_what_the_capability_does_accept() -> None:
    with pytest.raises(ValueError) as raised:
        dispatch.build_params(dispatch.resolve("slippage"), {"symbol": SYMBOL, "sied": "buy"})
    message = str(raised.value)
    assert "sied" in message
    assert "side" in message
    assert "slippage" in message

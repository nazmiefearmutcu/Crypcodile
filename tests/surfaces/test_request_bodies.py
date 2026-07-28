"""Some requests do not fit in a URL, and the projection has to know which.

Six routes were ``POST`` before the merge and every route the projection builds is
``GET``-only, so all six answer 405. The frozen parity fixture records them —
``POST /api/v1/query``, ``/simulate-price-impact`` on both forks, ``/smart-money``,
``/gas-vol``, ``/mev-sandwich``, ``/label-transfers`` — and the scanner strips the method
before comparing, which is why no gate saw it.

Retrying ``query`` as a GET is not a workaround: a SQL statement is routinely kilobytes,
nginx's default ``large_client_header_buffers`` is 8 k so it 414s, and what does fit lands in
access logs and browser history. Four of the six cannot be spelled in a query string at all —
their parameters are arrays of objects.

The rule is read off ``cap.params`` and there is no list of capability names anywhere in
``rest.py``; ``test_the_method_rule_is_derived_and_not_a_list`` is what says so.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from typer.testing import CliRunner

from crocodile.core.config import Settings
from crocodile.surfaces import cli, dispatch, rest
from tests.surfaces.conftest import SYMBOL

_LEGACY_POST_ROUTES = [
    "query",
    "simulate-price-impact",
    "smart-money",
    "gas-vol",
    "mev-sandwich",
    "label-transfers",
]
"""Every ``POST`` path in ``premerge_phase2_surface.json`` that is a capability."""

_STRUCTURED = ["gas-vol", "mev-sandwich", "smart-money", "label-transfers"]
"""The four whose parameters are arrays of objects, so a query string cannot carry them."""


def _settings(lake: pathlib.Path) -> Settings:
    return Settings(data_dir=lake)


def _client(lake: pathlib.Path):  # starlette's TestClient, imported lazily
    from starlette.testclient import TestClient

    return TestClient(rest.build_app(settings=_settings(lake)), raise_server_exceptions=False)


@pytest.mark.parametrize("name", _LEGACY_POST_ROUTES)
def test_every_route_the_forks_served_on_post_still_answers_post(name: str) -> None:
    assert "POST" in rest.methods_for(dispatch.resolve(name)), name


def test_a_capability_whose_parameters_fit_in_a_url_answers_both(lake: pathlib.Path) -> None:
    """A caller with a 20 kB statement uses the body; one with ``SELECT 1`` uses the URL."""
    client = _client(lake)
    arguments = {"sql": "SELECT 1 AS one", "asset_class": "crypto"}

    from_get = client.get("/api/v1/query", params=arguments)
    from_post = client.post("/api/v1/query", json=arguments)
    assert from_get.status_code == 200, from_get.text
    assert from_post.status_code == 200, from_post.text
    assert from_get.json() == from_post.json()


@pytest.mark.parametrize("name", _STRUCTURED)
def test_a_capability_whose_parameters_cannot_be_spelled_in_a_url_is_post_only(
    name: str,
) -> None:
    """405 with an ``Allow`` header, rather than a 400 riddle about a missing field.

    ``?gas=[{...}]`` is not a thing a query string can carry, so a GET here can only ever
    fail; saying which method to use is the honest answer and is what the forks served.
    """
    assert rest.methods_for(dispatch.resolve(name)) == ["POST"]


def test_a_structured_body_reaches_the_implementation(lake: pathlib.Path) -> None:
    """The end of the round trip: an array of objects, posted, computed, answered."""
    response = _client(lake).post(
        "/api/v1/mev-sandwich",
        json={"trades": [], "asset_class": "crypto"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []


def test_a_get_on_a_post_only_route_says_which_method_to_use(lake: pathlib.Path) -> None:
    response = _client(lake).get("/api/v1/gas-vol", params={"asset_class": "crypto"})
    assert response.status_code == 405, response.text


def test_the_body_and_the_query_string_are_read_into_one_request(lake: pathlib.Path) -> None:
    """``asset_class`` is a query parameter on every surface; the body carries the rest."""
    response = _client(lake).post(
        "/api/v1/query?asset_class=crypto", json={"sql": "SELECT 2 AS two"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [{"two": 2}]


def test_a_body_that_is_not_a_json_object_is_a_bad_request(lake: pathlib.Path) -> None:
    client = _client(lake)
    malformed = client.post(
        "/api/v1/query", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert malformed.status_code == 400, malformed.text
    not_an_object = client.post("/api/v1/query", json=["SELECT 1"])
    assert not_an_object.status_code == 400, not_an_object.text


def test_the_method_rule_is_derived_and_not_a_list() -> None:
    """A table of capability names in ``rest.py`` would undo the point of a projection.

    The whole file is 3 273 + 1 500 hand-written lines shorter than what it replaced because
    it holds no per-capability knowledge. One name in a method table is the first of fifty.

    Driven with capabilities registered by this test rather than by reading the source: no
    line of ``rest.py`` mentions either of them, and nothing was edited to add them, so if
    the rule were a table this is the assertion that could not pass.
    """
    import msgspec

    from crocodile.core.capability import AssetClass, Capability, Impl, ReturnKind
    from crocodile.core.schema.provenance import Provenance

    class _Spellable(msgspec.Struct, frozen=True):
        symbol: str
        channels: tuple[str, ...] = ()

    class _Structured(msgspec.Struct, frozen=True):
        rows: list[dict[str, Any]]

    def _impls(params: type[msgspec.Struct]) -> Capability:
        impl = Impl(fn=lambda ctx, p: None, prov=Provenance.NATIVE, basis="native")
        return Capability(
            name="fixture-method-rule",
            summary="Registered by a test.",
            params=params,
            returns=ReturnKind.TABLE,
            impls={AssetClass.CRYPTO: impl},
        )

    assert rest.methods_for(_impls(_Spellable)) == ["GET", "POST"]
    assert rest.methods_for(_impls(_Structured)) == ["POST"]


def test_the_openapi_document_says_a_body_is_accepted(lake: pathlib.Path) -> None:
    document = _client(lake).get("/openapi.json").json()
    posted = document["paths"]["/api/v1/gas-vol"]["post"]
    schema = posted["requestBody"]["content"]["application/json"]["schema"]
    assert "gas" in schema["properties"], schema
    # The fields a URL cannot carry are not advertised as query parameters, because that is
    # a promise the transport cannot keep.
    assert {p["name"] for p in posted.get("parameters", [])} == {"asset_class"}


def test_each_method_gets_its_own_operation_id(lake: pathlib.Path) -> None:
    """OpenAPI requires ``operationId`` to be unique across every operation in a document.

    FastAPI derives one per *route* and a route here serves two methods, so it wrote the
    same id under ``get`` and ``post`` for all fifty-odd paths — and warned about each one.
    A generated client would have produced two methods of the same name, or silently kept
    whichever it read last.
    """
    document = _client(lake).get("/openapi.json").json()
    ids = [
        operation["operationId"]
        for item in document["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})

    query = document["paths"]["/api/v1/query"]
    assert query["get"]["operationId"] != query["post"]["operationId"]
    assert query["get"]["operationId"].endswith("_get")
    assert query["post"]["operationId"].endswith("_post")


def test_generating_the_document_warns_about_nothing(lake: pathlib.Path) -> None:
    """The warning is FastAPI telling us the document it just wrote is invalid."""
    import warnings

    app = rest.build_app(settings=_settings(lake))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.openapi()
    assert [str(warning.message) for warning in caught] == []


def test_the_openapi_document_describes_the_response_envelope(lake: pathlib.Path) -> None:
    """Where a caller is told the shape changed.

    The legacy ``GET /api/v1/slippage`` returned a bare one-element array, so
    ``resp.json()[0]["slippage_pct"]`` now raises ``KeyError: 0``. The envelope stays — it
    is where ``provenance`` and the SYNTHETIC warning live, and a bare array has nowhere to
    put them — so the published schema has to say so rather than leaving it to be
    discovered.
    """
    document = _client(lake).get("/openapi.json").json()
    scalar = document["paths"]["/api/v1/slippage"]["get"]["responses"]["200"]
    table = document["paths"]["/api/v1/indicators"]["get"]["responses"]["200"]
    scalar_schema = scalar["content"]["application/json"]["schema"]
    table_schema = table["content"]["application/json"]["schema"]
    assert set(scalar_schema["properties"]) == {"result", "provenance", "warning"}
    # ``truncated`` joined the table envelope when the published row ceiling stopped being a
    # claim and started being applied: a caller who receives exactly ``row_limit`` rows
    # otherwise cannot tell a full answer of that size from a cut one.
    assert set(table_schema["properties"]) == {"rows", "truncated", "provenance", "warning"}
    assert table_schema["properties"]["rows"]["type"] == "array"
    assert table_schema["properties"]["truncated"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# The same parameters, on a command line
# ---------------------------------------------------------------------------


def test_a_structured_parameter_is_reachable_from_the_command_line(
    lake: pathlib.Path,
) -> None:
    """A JSON document is not a comma-separated list, and splitting one produces garbage.

    ``build_params`` splits a string into a sequence field on commas, which is right for
    ``--symbols BTC,ETH`` and destroys ``--trades '[{"a": 1}, {"b": 2}]'``. These four
    capabilities were declared, projected, counted by Gate 4 and impossible to call here.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["mev-sandwich", "--trades", "[]", "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output

    labelled = CliRunner().invoke(
        cli.build_app(),
        ["label-transfers", "--transfers", "[]", "--watchlist", '{"0xabc": "treasury"}',
         "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert labelled.exit_code == 0, labelled.output


def test_a_comma_separated_sequence_of_scalars_still_splits(lake: pathlib.Path) -> None:
    """The other half of the rule, which fifteen capabilities depend on."""
    params = dispatch.build_params(
        dispatch.resolve("resolve-symbols"), {"symbols": f"{SYMBOL},{SYMBOL}"}
    )
    assert params.symbols == (SYMBOL, SYMBOL)


# ---------------------------------------------------------------------------
# The array form a client reads off the schema
# ---------------------------------------------------------------------------


def _array_parameters(document: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every published query parameter whose schema is a sequence, with its path."""
    found: list[tuple[str, dict[str, Any]]] = []
    for path, item in document["paths"].items():
        operation = item.get("get")
        if operation is None:
            continue
        for parameter in operation.get("parameters", []):
            if rest._is_array(parameter.get("schema", {})):
                found.append((path, parameter))
    return found


def test_every_published_array_parameter_is_parsed_the_way_it_is_published(
    lake: pathlib.Path,
) -> None:
    """The schema and the wire, compared rather than assumed to match.

    Before: the document said ``{"type": "array"}``, whose OpenAPI serialisation is repeated
    keys, and the handler did ``dict(request.query_params)``, which keeps only the last. Every
    array parameter on every route was published in a form the surface silently truncated.

    Read off the generated document rather than off a list, so a capability that gains a
    sequence parameter is covered the day it is declared.
    """
    client = _client(lake)
    published = _array_parameters(client.get("/openapi.json").json())
    assert published, "no array parameter is published; this gate would prove nothing"

    for path, parameter in published:
        assert parameter["explode"] is True, (path, parameter["name"])
        assert parameter["style"] == "form", (path, parameter["name"])

    seen: dict[str, Any] = {}
    real = dispatch.build_params

    def _spy(cap: Any, values: dict[str, Any]) -> Any:
        seen[cap.name] = dict(values)
        return real(cap, values)

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(dispatch, "build_params", _spy)
        for path, parameter in published:
            name = parameter["name"]
            wire = path.removeprefix(f"{rest.API_PREFIX}/")
            seen.clear()
            client.get(f"{path}?{name}=1&{name}=2")
            capability = dispatch.resolve(wire).name
            assert seen.get(capability, {}).get(name) == ["1", "2"], (
                f"{wire} published {name} as an exploded array and kept "
                f"{seen.get(capability, {}).get(name)!r}"
            )
    finally:
        monkey.undo()


def test_the_repeated_key_form_and_the_comma_form_give_the_same_answer(
    lake: pathlib.Path,
) -> None:
    """The defect as a caller met it: a wrong number, in range, with a 200 on it.

    ``funding-predict`` averages the rates it is handed, so the two spellings of the same
    four numbers have to produce one prediction. Before, the repeated-key form — the one the
    published schema describes — arrived as a single rate: ``predicted_funding_rate: 0.004``
    over ``n_history: 1`` against the comma form's ``0.0025`` over ``n_history: 4``.
    """
    client = _client(lake)
    rates = ["0.001", "0.002", "0.003", "0.004"]
    exploded = client.get(
        "/api/v1/funding-predict?" + "&".join(f"rates={rate}" for rate in rates)
        + "&asset_class=crypto"
    )
    comma = client.get(
        "/api/v1/funding-predict",
        params={"rates": ",".join(rates), "asset_class": "crypto"},
    )
    assert exploded.status_code == comma.status_code == 200, exploded.text
    assert exploded.json()["result"] == comma.json()["result"]
    assert exploded.json()["result"]["n_history"] == len(rates)


def test_a_single_value_is_still_a_single_value(lake: pathlib.Path) -> None:
    """The half that keeps the two forks' callers working.

    A key that appears once must stay a *string* through the collection step, because that is
    what makes ``build_params`` split it on commas. Turning every query parameter into a list
    would have fixed the exploded form and broken ``?symbols=BTC,ETH`` in the same commit.
    """
    seen: dict[str, Any] = {}
    real = dispatch.build_params

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(
            dispatch,
            "build_params",
            lambda cap, values: seen.setdefault(cap.name, real(cap, values)),
        )
        response = _client(lake).get(
            "/api/v1/catalog-scan",
            params={
                "channel": "trade",
                "symbols": f"{SYMBOL},deribit:ETH-PERPETUAL",
                "start_ns": "0",
                "end_ns": "9" * 18,
            },
        )
    finally:
        monkey.undo()
    assert response.status_code == 200, response.text
    assert seen["catalog-scan"].symbols == (SYMBOL, "deribit:ETH-PERPETUAL")

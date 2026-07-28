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

from crocodile.core.capability import REGISTRY, AssetClass, CapabilityContext
from crocodile.core.config import Settings
from crocodile.core.schema.provenance import Provenance
from crocodile.core.store.catalog import Catalog
from crocodile.surfaces import cli, dispatch, mcp, rest
from tests.surfaces.conftest import END_NS, START_NS, SYMBOL


def _settings(lake: pathlib.Path) -> Settings:
    return Settings(data_dir=lake)


_ALPACA_ENV = (
    "CROCODILE_ALPACA_API_KEY",
    "CROCODILE_ALPACA_API_SECRET",
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
)
"""Every spelling ``alpaca_is_keyed`` consults, so "keyless" is a fact rather than a hope.

``Settings`` reads the prefixed pair and ``select_depth_source`` has always read the bare
one; both are honoured so an existing keyed deployment does not go dark. A test asserting
what the *default* deployment announces therefore has to clear all four, or it passes or
fails according to whose laptop it runs on.
"""


def _unkey(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALPACA_ENV:
        monkeypatch.delenv(name, raising=False)


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


def test_the_cli_does_not_banner_a_derived_answer_but_the_payload_still_says_so(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrated: this asserted ``DERIVED`` on the terminal, and the rule narrowed under it.

    Two changes landed on the same behaviour from different branches. The equity half of
    ``slippage`` used to be the crypto function — reading the crypto ``book_snapshot`` this
    lake writes while declaring a VAP basis it never touched — and now walks the ladder
    ``depth`` serves, declaring the same ``DERIVED``/``alpaca_l1`` ceiling because it is the
    same book. Independently, ``banner_for`` stopped announcing ``DERIVED`` on stderr: a
    derived answer is computed from native inputs, so being told an RSI was computed is not
    news, and printing it on every successful call broke scripts asserting empty stderr
    *and* taught operators to skip the channel the ``SYNTHETIC`` banner arrives on.

    So the subject survives and the assertion moves: the terminal stays quiet, and the
    provenance is still there for a reader that wants it. ``warning_for`` is unchanged and
    REST and MCP carry every non-native answer in the payload.

    Migrated a second time, and this is why the keys are set. ``DERIVED`` is the *ceiling*
    and it is only what happens when the deployment can reach it: with no Alpaca keys
    ``select_depth_source`` returns the modelled Yahoo ladder, so the answer is ``SYNTHETIC``
    and now says so. The claim under test — a derived answer does not shout — is a claim
    about a derived answer, so the deployment is keyed here and
    ``test_the_keyless_deployment_announces_the_ladder_it_actually_returns`` owns the other
    branch. Before that split, this test passed *because* the surface named a method that
    never ran.
    """
    monkeypatch.setenv("CROCODILE_ALPACA_API_KEY", "key")
    monkeypatch.setenv("CROCODILE_ALPACA_API_SECRET", "secret")

    runner = CliRunner()
    result = runner.invoke(
        cli.build_app(),
        ["slippage", "--symbol", SYMBOL, "--side", "buy", "--size", "1.0",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "DERIVED" not in result.output

    cap = REGISTRY["slippage"]
    impl = cap.impls[AssetClass.EQUITY]
    assert impl.prov is Provenance.DERIVED
    assert impl.basis == "alpaca_l1"

    catalog = Catalog(lake)
    try:
        ctx = CapabilityContext(
            catalog=catalog,
            settings=Settings(data_dir=lake, alpaca_api_key="key", alpaca_api_secret="secret"),
            asset_class=AssetClass.EQUITY,
        )
        warning = dispatch.warning_for(cap, ctx) or ""
    finally:
        catalog.close()
    assert "DERIVED" in warning and "alpaca_l1" in warning


@pytest.mark.parametrize("keyed", [False, True], ids=["keyless", "keyed"])
def test_the_keyless_deployment_announces_the_ladder_it_actually_returns(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch, keyed: bool
) -> None:
    """The one hand-written banner this design generalises, in the deployment that has none.

    ``depth``, ``slippage`` and ``liquidity-depth`` all declare ``DERIVED``/``alpaca_l1``
    over ``select_depth_source``, which returns the **synthetic** Yahoo ladder whenever the
    Alpaca keys are unset — the default. ``banner_for`` suppresses ``DERIVED``, so the CLI's
    stderr was empty and REST and MCP named ``alpaca_l1``, a method that had not run, over a
    modelled answer. That is exactly what ``warning_for``'s docstring says it exists to
    prevent, on the endpoint it was generalised from.

    Both branches are driven, because a fix that announced ``SYNTHETIC`` unconditionally
    would be the same defect pointed the other way: a keyed deployment would be told its real
    quoted ladder was modelled.
    """
    if keyed:
        monkeypatch.setenv("CROCODILE_ALPACA_API_KEY", "key")
        monkeypatch.setenv("CROCODILE_ALPACA_API_SECRET", "secret")
    else:
        _unkey(monkeypatch)

    expected = ("DERIVED", "alpaca_l1") if keyed else ("SYNTHETIC", "yahoo_1m_vap")
    # The network surfaces take their settings from the caller that mounts them, so a test
    # that only edits the environment would leave them keyless whatever the CLI saw. This is
    # the same object `dispatch.build_context` hands the implementation.
    settings = Settings(
        data_dir=lake,
        **({"alpaca_api_key": "key", "alpaca_api_secret": "secret"} if keyed else {}),
    )

    result = CliRunner().invoke(
        cli.build_app(),
        ["slippage", "--symbol", SYMBOL, "--side", "buy", "--size", "1.0",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert (expected[0] in result.stderr) is not keyed, result.stderr

    from starlette.testclient import TestClient

    for body in (
        TestClient(rest.build_app(settings=settings)).get(
            "/api/v1/slippage",
            params={"symbol": SYMBOL, "side": "buy", "size": "1.0", "asset_class": "equity"},
        ).json(),
        mcp.call_tool(
            "slippage",
            {"symbol": SYMBOL, "side": "buy", "size": 1.0, "asset_class": "equity"},
            settings=settings,
        ),
    ):
        assert body["provenance"]["prov"] == expected[0].lower()
        assert body["provenance"]["prov_basis"] == expected[1]
        assert expected[0] in body["warning"]
        # The ceiling is still published, and only where it differs from what ran — which is
        # what tells a caller that setting a key would upgrade this exact answer.
        assert ("prov_ceiling" in body["provenance"]) is not keyed


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
    """A cap nobody is told about turns a truncated answer into a wrong one.

    Kept as the *statement* of the claim; ``test_the_published_row_ceiling_is_the_one_the
    _answer_got`` below is what measures it. This one asserted the published number equalled
    the constant, on a capability that applied no ceiling, against a forty-row lake — three
    ways of being unable to fail at once — and the exit review found the number was false for
    30 of the 33 ``TABLE`` capabilities while this stayed green.
    """
    body = _client(lake).get("/api/v1/indicators", params=indicator_query).json()
    assert body["provenance"]["row_limit"] == dispatch.NETWORK_ROW_LIMIT
    assert "truncated" not in body, "forty trades cannot reach a ten-thousand-row ceiling"


@pytest.mark.parametrize("surface", ["rest", "mcp"])
def test_the_published_row_ceiling_is_the_one_the_answer_got(
    oversized_lake: pathlib.Path, surface: str
) -> None:
    """Driven over a lake bigger than the cap, which is the only way this can fail.

    ``resample`` is the probe because it reads through ``Catalog.scan`` and therefore never
    meets :meth:`CapabilityContext.query <crocodile.core.capability.CapabilityContext.query>`'s
    LIMIT wrapper — the same shape as ``indicators``, ``ofi``, ``funding-apr``,
    ``spot-future-basis``, ``open-interest`` and ``whale-alerts``. Before the fix this
    answered 12 000 rows and 3.5 MB under ``provenance.row_limit: 10000``.
    """
    from tests.surfaces.conftest import OVERSIZED_ROWS

    request = {
        "symbol": SYMBOL,
        "start_ns": str(START_NS),
        "end_ns": str(START_NS + OVERSIZED_ROWS * 60 * 1_000_000_000),
        "interval": "1m",
        "asset_class": "crypto",
    }
    body = (
        _client(oversized_lake).get("/api/v1/resample", params=request).json()
        if surface == "rest"
        else mcp.call_tool("resample", dict(request), settings=_settings(oversized_lake))
    )
    ceiling = body["provenance"]["row_limit"]
    assert ceiling == dispatch.NETWORK_ROW_LIMIT
    assert len(body["rows"]) == ceiling, (
        f"{surface} published a ceiling of {ceiling} and returned {len(body['rows'])} rows"
    )
    assert body["truncated"] is True, "a cut answer has to say it was cut"


def test_every_table_capability_is_subject_to_the_ceiling_not_only_the_three_that_read_sql(
    oversized_lake: pathlib.Path,
) -> None:
    """The projection applies the cap, so no implementation can be the one that forgets.

    Stated against ``dispatch.payload`` directly and over every ``TABLE`` capability in the
    registry, because the defect was never about one capability: it was that the ceiling
    lived in three implementations instead of in the one place every answer passes through.
    A frame is manufactured here rather than read, so the assertion is about the projection
    and stays true for a capability that has not been written yet.
    """
    import polars as pl

    from crocodile.core.capability import REGISTRY, ReturnKind

    dispatch.wire_names()
    oversized = pl.DataFrame({"n": list(range(dispatch.NETWORK_ROW_LIMIT + 500))})
    tables = [cap for cap in REGISTRY.values() if cap.returns is ReturnKind.TABLE]
    assert len(tables) > 1, "nothing declares TABLE; this gate would prove nothing"
    for cap in tables:
        body = dispatch.payload(cap, oversized, row_limit=dispatch.NETWORK_ROW_LIMIT)
        assert len(body["rows"]) == dispatch.NETWORK_ROW_LIMIT, cap.name
        assert body["truncated"] is True, cap.name


def test_the_cli_is_not_capped_and_does_not_claim_to_be(
    oversized_lake: pathlib.Path,
) -> None:
    """The ceiling is a property of the network surfaces, and stays one.

    The CLI runs on the machine that owns the lake, so it returns everything — and publishes
    no ``row_limit``, because a ceiling nobody applied is exactly the false claim being
    fixed, pointed the other way.
    """
    from tests.surfaces.conftest import OVERSIZED_ROWS

    result = CliRunner().invoke(
        cli.build_app(),
        ["resample", "--symbol", SYMBOL, "--start-ns", str(START_NS), "--end-ns",
         str(START_NS + OVERSIZED_ROWS * 60 * 1_000_000_000), "--interval", "1m",
         "--asset-class", "crypto", "--data-dir", str(oversized_lake)],
    )
    assert result.exit_code == 0, result.output
    # Polars groups the row count in its frame header: ``shape: (12_000, 15)``.
    assert f"({OVERSIZED_ROWS:_}," in result.output, result.output[:400]


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


def test_rest_serves_the_alias_and_the_synthetic_warning(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unkey(monkeypatch)
    response = _client(lake).get(
        "/api/v1/simulate-price-impact",
        params={"symbol": SYMBOL, "side": "buy", "size": "1.0", "asset_class": "equity"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["expected_price"] > 0
    # SYNTHETIC, and the name of this test is accurate again. `DERIVED`/`alpaca_l1` is the
    # ceiling; a keyless deployment gets the modelled Yahoo ladder, and the envelope now
    # names the branch that ran rather than the one it could have taken. The ceiling is
    # alongside it so a caller can see that a key would upgrade this answer.
    assert "SYNTHETIC" in body["warning"]
    assert body["provenance"]["prov_basis"] == "yahoo_1m_vap"
    assert body["provenance"]["prov_ceiling_basis"] == "alpaca_l1"


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
    """Re-pinned: the schema is inlined now, because a root ``$ref`` published no inputs.

    This asserted the ``$ref``/``$defs`` form and read the properties out of ``$defs``, which
    is where they were — and not where an MCP client looks. Clients build a tool's arguments
    from ``inputSchema.properties``, and the projection wrote ``setdefault("properties", {})``
    beside the reference, so every one of the 57 tools published an *empty* property set. The
    assertion moves to the object a client actually reads.
    """
    tool = next(t for t in mcp.tool_definitions() if t["name"] == "indicators")
    properties = tool["inputSchema"]["properties"]
    # `fill_empty` joined the struct when the equity CLI's flag came back — it had been
    # dropped and its default silently flipped False to True. Listed here because the
    # projection publishes whatever the struct holds, which is the property being asserted.
    assert set(properties) == {
        "symbol",
        "start_ns",
        "end_ns",
        "interval",
        "indicator",
        "period",
        "fill_empty",
        # The one input that is not a capability parameter: it selects the implementation.
        "asset_class",
    }
    assert tool["assetClasses"] == ["crypto", "equity"]
    # `indicators` takes a symbol, so the class is inferable and the option is not required.
    assert "asset_class" not in tool["inputSchema"].get("required", [])


def test_mcp_warns_on_a_modelled_answer(
    lake: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent reading a modelled number has to be told, and this is the default deployment."""
    _unkey(monkeypatch)
    body = mcp.call_tool(
        "simulate-price-impact",
        {"symbol": SYMBOL, "side": "buy", "size": 1.0, "asset_class": "equity"},
        settings=_settings(lake),
    )
    assert "SYNTHETIC" in body["warning"]
    assert body["provenance"]["prov_basis"] == "yahoo_1m_vap"
    assert body["provenance"]["prov_ceiling_basis"] == "alpaca_l1"


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


# ---------------------------------------------------------------------------
# The published contract, read the way a generated client reads it
# ---------------------------------------------------------------------------


def test_no_mcp_tool_publishes_an_empty_input_schema() -> None:
    """A tool whose ``properties`` is ``{}`` cannot be called correctly from its contract.

    Before: all 57. ``setdefault("properties", {})`` inserted an empty object beside the
    root ``$ref`` instead of resolving it, and a client builds arguments from
    ``properties`` — so every parameter of every capability was invisible on this surface.
    """
    import msgspec

    from crocodile.core.capability import REGISTRY

    tools = mcp.tool_definitions()
    assert len(tools) > 50, "the registry is too small for this to prove anything"
    for tool in tools:
        schema = tool["inputSchema"]
        cap = dispatch.resolve(tool["name"])
        declared = {field.name for field in msgspec.structs.fields(cap.params)}
        published = set(schema["properties"])
        assert schema.get("type") == "object", tool["name"]
        assert "$ref" not in schema, f"{tool['name']} publishes a reference, not a schema"
        assert declared <= published, (
            f"{tool['name']} declares {sorted(declared - published)} and publishes neither"
        )
        assert published == declared | {"asset_class"}, tool["name"]
    assert REGISTRY, "no capabilities loaded"


def test_every_tool_that_cannot_infer_its_market_says_asset_class_is_required() -> None:
    """32 of the 57 wire names have two implementations and no symbol to resolve from.

    For those, ``asset_class`` is the only thing that makes the call answerable — and it
    appeared in no schema, no description and no error text. A client following the contract
    could not call them; it found out with a 400.
    """
    mandatory = [
        tool
        for tool in mcp.tool_definitions()
        if dispatch.requires_explicit_asset_class(dispatch.resolve(tool["name"]))
    ]
    assert len(mandatory) > 20, "this gate is measuring the wrong population"
    for tool in mandatory:
        schema = tool["inputSchema"]
        assert "asset_class" in schema["required"], tool["name"]
        assert schema["properties"]["asset_class"]["enum"] == tool["assetClasses"]

    inferable = [
        tool
        for tool in mcp.tool_definitions()
        if not dispatch.requires_explicit_asset_class(dispatch.resolve(tool["name"]))
    ]
    assert inferable, "every tool requires it; the distinction is not being drawn"
    for tool in inferable:
        assert "asset_class" not in tool["inputSchema"].get("required", []), tool["name"]


def test_rest_publishes_asset_class_as_required_exactly_where_it_is(
    lake: pathlib.Path,
) -> None:
    """The same claim on the other network surface, off the generated document.

    ``required: false`` on a route where ``resolve_asset_class`` refuses outright describes a
    request that can only answer 400. The enum is narrowed for the same reason: a route
    offering a class it has no implementation for describes a 501.
    """
    document = _client(lake).get("/openapi.json").json()
    checked = 0
    for path, item in document["paths"].items():
        operation = item.get("get") or item.get("post")
        parameter = next(
            p for p in operation["parameters"] if p["name"] == "asset_class"
        )
        cap = dispatch.resolve(path.removeprefix(f"{rest.API_PREFIX}/"))
        assert parameter["required"] is dispatch.requires_explicit_asset_class(cap), path
        assert parameter["schema"]["enum"] == dispatch.asset_class_option_values(cap), path
        checked += 1
    assert checked > 50, checked


def test_the_two_network_surfaces_publish_the_same_parameters(lake: pathlib.Path) -> None:
    """Half of what "full API symmetry" means, measured off both published documents.

    Both are derived from one params struct, so this can only fail if a projector starts
    describing the schema in its own words — which is what each of these fixes was.
    """
    document = _client(lake).get("/openapi.json").json()
    for tool in mcp.tool_definitions():
        cap = dispatch.resolve(tool["name"])
        operation = document["paths"][f"{rest.API_PREFIX}/{tool['name']}"]
        published = {p["name"] for p in (operation.get("get") or operation["post"])["parameters"]}
        # REST omits the fields a URL cannot carry; they live in the request body instead.
        expected = set(tool["inputSchema"]["properties"]) - dispatch.structured_fields(cap)
        assert published == expected, tool["name"]

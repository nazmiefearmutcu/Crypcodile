"""A canonical symbol says which market it is in, however the parameter is spelled.

``resolve_asset_class`` reads the source out of ``source:RAW`` and asks the two source
registries which market claims it — evidence rather than a guess, and the reason a caller
does not have to name the asset class twice. The surfaces then handed it exactly one field,
the one literally named ``symbol``, and six two-implementation capabilities spell it
``symbols``: ``catalog-scan``, ``resolve-symbols``, ``replay``, ``export``, ``backfill`` and
``collect``.

So ``?symbol=deribit:BTC-PERPETUAL`` answered 200 and ``?symbols=deribit:BTC-PERPETUAL``
answered 400 *cannot tell which market*, for the same symbol, on the same lake. Loud, but it
made six capabilities unreachable without an out-of-band parameter the symbol already
determines.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from typer.testing import CliRunner

from crocodile.capabilities import load_all
from crocodile.core.capability import IRREDUCIBLE, REGISTRY, AssetClass
from crocodile.core.config import Settings
from crocodile.core.errors import CapabilityUnavailable
from crocodile.surfaces import cli, dispatch, mcp, rest
from tests.surfaces.conftest import END_NS, START_NS, SYMBOL

_EQUITY_SYMBOL = "stooq:AAPL"
_CLAIMED_BY_BOTH = "alpaca:AAPL"
"""``alpaca`` is registered as a crypto exchange *and* as an equity provider."""


def _settings(lake: pathlib.Path) -> Settings:
    return Settings(data_dir=lake)


def _client(lake: pathlib.Path):  # starlette's TestClient, imported lazily
    from starlette.testclient import TestClient

    return TestClient(rest.build_app(settings=_settings(lake)), raise_server_exceptions=False)


_ARGUMENTS: dict[str, dict[str, Any]] = {
    "catalog-scan": {"channel": "trade", "symbols": SYMBOL, "start_ns": START_NS,
                     "end_ns": END_NS},
    "resolve-symbols": {"symbols": SYMBOL},
    "replay": {"channels": "trade", "symbols": SYMBOL, "start_ns": START_NS, "end_ns": END_NS},
    "export": {"channel": "trade", "symbols": SYMBOL, "start_ns": START_NS, "end_ns": END_NS,
               "dest": "/dev/null"},
    "backfill": {"source": "deribit", "channel": "trade", "symbols": SYMBOL,
                 "start_ns": START_NS, "end_ns": END_NS},
    "collect": {"sources": "deribit", "symbols": SYMBOL, "channels": "trade"},
}
"""The six capabilities that spell it ``symbols``, with enough of a request to build one."""


@pytest.mark.parametrize("name", sorted(_ARGUMENTS))
def test_the_market_is_inferred_from_a_sequence_of_symbols(name: str) -> None:
    """The whole path a surface takes: build the params, read the symbols off them, resolve.

    Reading the built struct rather than the raw request is what makes ``symbols`` work
    without a second guess about spelling: by then a sequence is a sequence, whether the
    transport delivered it as a list, a repeated flag or a comma-separated string.
    """
    cap = dispatch.resolve(name)
    assert len(cap.impls) > 1, f"{name} would have nothing to infer"
    params = dispatch.build_params(cap, dict(_ARGUMENTS[name]))
    assert dispatch.symbol_hints(params) == (SYMBOL,)
    assert dispatch.resolve_asset_class(cap, symbols=dispatch.symbol_hints(params)) is (
        AssetClass.CRYPTO
    )


def test_rest_serves_a_symbols_request_without_being_told_the_market(lake: pathlib.Path) -> None:
    """Answering at all is the claim; the envelope's class is asserted where it is a fact.

    ``catalog-scan`` reads the lake as a lake, so its envelope now reports ``any`` — see
    ``Capability.cross_market``. Reading the resolution off the payload was never the
    strongest way to observe it, and ``test_a_symbol_settles_the_market_for_a_sequence_field``
    above asserts the resolution itself.
    """
    response = _client(lake).get(
        "/api/v1/catalog-scan",
        params={"channel": "trade", "symbols": SYMBOL, "start_ns": START_NS, "end_ns": END_NS},
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["asset_class"] == "any"


def test_the_cli_serves_a_symbols_request_without_being_told_the_market(
    lake: pathlib.Path,
) -> None:
    result = CliRunner().invoke(
        cli.build_app(),
        ["replay", "--channels", "trade", "--symbols", SYMBOL, "--start-ns", str(START_NS),
         "--end-ns", str(END_NS), "--limit", "2", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert SYMBOL in result.output


def test_mcp_serves_a_symbols_request_without_being_told_the_market(lake: pathlib.Path) -> None:
    """Same as above: the call succeeds unnamed, and the envelope does not echo an input."""
    body = mcp.call_tool("resolve-symbols", {"symbols": [SYMBOL]}, settings=_settings(lake))
    assert body["rows"]
    assert body["provenance"]["asset_class"] == "any"


def test_a_single_symbol_still_settles_it(lake: pathlib.Path) -> None:
    """``slippage`` spells it ``symbol``, and the singular case must not regress."""
    response = _client(lake).get(
        "/api/v1/slippage", params={"symbol": SYMBOL, "side": "buy", "size": "1.0"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["asset_class"] == "crypto"


# ---------------------------------------------------------------------------
# What is still refused, and must be
# ---------------------------------------------------------------------------


def test_symbols_from_two_markets_are_refused_rather_than_resolved_by_position() -> None:
    """One request cannot be served by two implementations, and the first is not the answer.

    Picking the first symbol's market would send the whole request — including the equity
    symbols — into the crypto implementation, which is how a query comes back plausible and
    empty. The refusal names both.
    """
    with pytest.raises(ValueError, match="two markets"):
        dispatch.resolve_asset_class(dispatch.resolve("replay"), symbols=(SYMBOL, _EQUITY_SYMBOL))


def test_a_source_both_registries_claim_settles_nothing() -> None:
    """``alpaca`` is a crypto exchange and an equity provider; an overlap is an ambiguity."""
    with pytest.raises(ValueError, match="cannot tell which market"):
        dispatch.resolve_asset_class(dispatch.resolve("replay"), symbols=(_CLAIMED_BY_BOTH,))


def test_a_symbol_with_no_registered_source_settles_nothing() -> None:
    with pytest.raises(ValueError, match="cannot tell which market"):
        dispatch.resolve_asset_class(dispatch.resolve("replay"), symbols=("nosuchvenue:BTC",))


def test_an_explicit_asset_class_is_never_overridden_by_a_symbol() -> None:
    """Step 1 of the order, and the symbol does not get a vote against it.

    The second half needs a capability that genuinely has no equity implementation, and this
    fixture has now been chased twice: it was ``collect-market`` until M3 gave that an equity
    half, then ``open-interest`` until M1 gave that one — and the agent that made the second
    swap could not see the first, because both landed in the same phase from different
    branches. A third name would be chased too. The population it was drawn from is
    *scheduled asymmetry*, and emptying that population is exactly what Phase 3 was, so no
    member of it is a durable fixture by construction.

    So the fixture is derived rather than named, and drawn from :data:`IRREDUCIBLE` — the
    other kind of asymmetry, which is a claim about the market and therefore does not expire.
    The guard below is the part that matters: deriving a subject from a set means the test
    quietly stops testing anything if the set empties, which is the vacuous-green shape this
    codebase keeps finding. If ``IRREDUCIBLE`` is ever empty, this fails and says why rather
    than passing over nothing.
    """
    assert dispatch.resolve_asset_class(
        dispatch.resolve("replay"), explicit=AssetClass.EQUITY, symbols=(SYMBOL,)
    ) is AssetClass.EQUITY

    load_all()
    crypto_only = sorted(
        name
        for name in IRREDUCIBLE
        if (cap := REGISTRY.get(name)) is not None and set(cap.impls) == {AssetClass.CRYPTO}
    )
    assert crypto_only, (
        "no capability is crypto-only, so this test has no subject and would pass over "
        "nothing. IRREDUCIBLE is what guarantees one exists; an empty one means either the "
        "list was emptied or its entries stopped being asymmetric, and both are decisions "
        "somebody should have to make deliberately."
    )
    for name in crypto_only:
        with pytest.raises(CapabilityUnavailable):
            dispatch.resolve_asset_class(
                dispatch.resolve(name), explicit=AssetClass.EQUITY, symbols=(SYMBOL,)
            )


def test_a_field_that_is_not_a_symbol_is_not_read_as_one() -> None:
    """``sql`` may contain a colon, and free text is not evidence about a market.

    The fields consulted are named, not sniffed: taking any string with a colon in it would
    let ``SELECT * FROM t WHERE note = 'binance:x'`` decide which implementation runs.
    """
    query = dispatch.resolve("query")
    params = dispatch.build_params(query, {"sql": "SELECT 'binance:BTC' AS note"})
    assert dispatch.symbol_hints(params) == ()


# ---------------------------------------------------------------------------
# A cross-market answer says so instead of echoing what it was told
# ---------------------------------------------------------------------------


_CROSS_MARKET_REQUESTS: dict[str, dict[str, str]] = {
    # The three the exit review drove, each one returning symbols from both markets while
    # the envelope reported whichever class the caller had been forced to name.
    "search": {"q": "BTC"},
    "catalog-symbols": {},
    "catalog-exchanges": {},
}


def test_a_cross_market_capability_is_one_implementation_and_says_so() -> None:
    """The declaration is checkable, so it is checked rather than believed.

    An answer that does not depend on the market cannot have two implementations, two
    provenance ceilings or two bases — so a capability claiming ``cross_market`` and holding
    any of those is claiming something its own declaration contradicts.
    """
    from crocodile.core.capability import REGISTRY

    dispatch.wire_names()
    declared = [cap for cap in REGISTRY.values() if cap.cross_market]
    assert len(declared) > 5, "nothing claims to be cross-market; this gate proves nothing"
    for cap in declared:
        assert len({impl.fn for impl in cap.impls.values()}) == 1, cap.name
        assert len({(impl.prov, impl.basis) for impl in cap.impls.values()}) == 1, cap.name
        assert len(cap.impls) > 1, f"{cap.name} serves one market; it is not cross-market"


@pytest.mark.parametrize("wire", sorted(_CROSS_MARKET_REQUESTS))
def test_naming_either_market_gives_the_identical_answer(
    lake: pathlib.Path, wire: str
) -> None:
    """Measured, not declared: the two classes are driven and the rows compared.

    ``search?q=EQ&asset_class=crypto`` answered 200 with three ``stooq:`` equity symbols and
    stamped ``provenance.asset_class: "crypto"``. The caller could not omit the class — a
    hard 400 — any value was accepted, and the answer ignored it. Echoing an input back as a
    property of the answer is the part a caller can be misled by: filtering results on that
    field would partition a cross-market answer by an argument.
    """
    arguments = _CROSS_MARKET_REQUESTS[wire]
    answers = [
        mcp.call_tool(wire, {**arguments, "asset_class": value}, settings=_settings(lake))
        for value in ("crypto", "equity")
    ]
    assert answers[0]["rows"] == answers[1]["rows"], wire
    for answer in answers:
        assert answer["provenance"]["asset_class"] == "any", wire


def test_a_capability_that_does_serve_one_market_still_reports_which(
    lake: pathlib.Path,
) -> None:
    """The other side of the line, so ``any`` stays a statement rather than a blanket."""
    body = mcp.call_tool(
        "slippage",
        {"symbol": SYMBOL, "side": "buy", "size": 1.0, "asset_class": "crypto"},
        settings=_settings(lake),
    )
    assert body["provenance"]["asset_class"] == "crypto"
    assert dispatch.resolve("slippage").cross_market is False

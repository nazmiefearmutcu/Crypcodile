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

from crocodile.core.capability import AssetClass
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
    response = _client(lake).get(
        "/api/v1/catalog-scan",
        params={"channel": "trade", "symbols": SYMBOL, "start_ns": START_NS, "end_ns": END_NS},
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["asset_class"] == "crypto"


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
    body = mcp.call_tool("resolve-symbols", {"symbols": [SYMBOL]}, settings=_settings(lake))
    assert body["provenance"]["asset_class"] == "crypto"


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
    """Step 1 of the order, and the symbol does not get a vote against it."""
    assert dispatch.resolve_asset_class(
        dispatch.resolve("replay"), explicit=AssetClass.EQUITY, symbols=(SYMBOL,)
    ) is AssetClass.EQUITY
    with pytest.raises(CapabilityUnavailable):
        dispatch.resolve_asset_class(
            dispatch.resolve("collect-market"), explicit=AssetClass.EQUITY, symbols=(SYMBOL,)
        )


def test_a_field_that_is_not_a_symbol_is_not_read_as_one() -> None:
    """``sql`` may contain a colon, and free text is not evidence about a market.

    The fields consulted are named, not sniffed: taking any string with a colon in it would
    let ``SELECT * FROM t WHERE note = 'binance:x'`` decide which implementation runs.
    """
    query = dispatch.resolve("query")
    params = dispatch.build_params(query, {"sql": "SELECT 'binance:BTC' AS note"})
    assert dispatch.symbol_hints(params) == ()

"""The onchain batch: two capabilities that arrived from a gate rather than from the port.

Deliberately network-free. Both implementations open an RPC socket to Base mainnet and read
the head block, so exercising them for real would make this suite depend on a chain being up
and on an answer that is different one block later. What is asserted instead is everything
that can be wrong *without* a network: that they are declared, that they say which market
they serve and why no other one can, that their provenance is a claim someone can check, and
that the surfaces project them.

Why this file exists at all is the part worth reading. The surface-parity gate found, at
Phase 2's exit, that three wire names the six deleted stacks had exposed —
``get_onchain_price`` and ``get_base_market_data`` on both forks' MCP servers, and equity's
``GET /api/v1/market-data`` — were served by nothing in the 47 ported capabilities. The
alternative to declaring them was writing "never a capability" on an exemption list about
something that takes a parameter, measures a market and has a provenance. These tests are
what makes the declaration checkable rather than merely present.
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
from typing import cast

import msgspec
import pytest

import crocodile.crypto.exchanges.base_onchain.price
from crocodile.capabilities import onchain
from crocodile.core.capability import (
    IRREDUCIBLE,
    PENDING_SYMMETRY,
    REGISTRY,
    AssetClass,
    CapabilityContext,
    ReturnKind,
)
from crocodile.core.schema.provenance import Provenance, registered_bases
from crocodile.surfaces import cli, mcp, rest

_NAMES = ("onchain-price", "base-market-data")

_CTX = cast(CapabilityContext, None)
"""These two never touch ``ctx`` — see
:func:`test_neither_implementation_reads_the_lake`, which asserts it — so the tests
below pass nothing rather than build a lake that would not be read."""


@pytest.mark.parametrize("name", _NAMES)
def test_the_capability_is_registered(name: str) -> None:
    assert name in REGISTRY, f"{name} is declared in capabilities/onchain.py but not registered"


@pytest.mark.parametrize("name", _NAMES)
def test_an_amm_pool_price_is_argued_irreducible_rather_than_scheduled(name: str) -> None:
    """``IRREDUCIBLE`` is a claim about the market and ``PENDING_SYMMETRY`` about the schedule.

    These two are the first kind. An automated market maker prices from two pooled reserves
    and there is no equity instrument that does, so there is no equity half to build later —
    which is exactly the distinction the two ledgers exist to keep, and the reason
    ``PENDING_SYMMETRY`` must not be where a permanent asymmetry goes to be forgotten.
    """
    assert set(REGISTRY[name].impls) == {AssetClass.CRYPTO}
    assert name in IRREDUCIBLE
    assert name not in PENDING_SYMMETRY
    assert IRREDUCIBLE[name].strip(), f"{name} is on IRREDUCIBLE with no argument"


@pytest.mark.parametrize("name", _NAMES)
def test_the_provenance_is_a_claim_about_a_contract_call(name: str) -> None:
    """``NATIVE`` here means the pool published its own state, which it did.

    ``slot0`` on a Uniswap V3 pool and ``getReserves`` on an Aerodrome one are the venue
    reporting itself. The arithmetic on top — sqrtPriceX96 to a price, liquidity to virtual
    reserves — is the pool's own definition of those quantities and not a model of them,
    which is the line that keeps this off ``DERIVED``.
    """
    impl = REGISTRY[name].impls[AssetClass.CRYPTO]
    assert impl.prov is Provenance.NATIVE
    assert impl.basis in registered_bases()


@pytest.mark.parametrize("name", _NAMES)
def test_the_result_is_one_object_rather_than_a_row_set(name: str) -> None:
    """One pool, one reading. ``SCALAR`` is what makes the CLI print it as an object."""
    assert REGISTRY[name].returns is ReturnKind.SCALAR


def test_the_two_capabilities_name_a_pool_in_the_spelling_each_tool_took() -> None:
    """``symbol`` and ``token_pair`` are the frozen wire names, not a slip.

    ``get_onchain_price`` took ``WETH-USDC`` and ``get_base_market_data`` took ``WETH/USDC``;
    the surface-parity fixture holds both. Renaming either to match the other would be a
    rename this port did not make, and the parameter gate would say so.
    """
    assert msgspec.structs.fields(onchain.OnchainPriceParams)[0].name == "symbol"
    assert msgspec.structs.fields(onchain.BaseMarketDataParams)[0].name == "token_pair"


def test_neither_implementation_reads_the_lake() -> None:
    """The honest shape, asserted so it stays honest.

    These are the only capabilities in the registry whose ``ctx`` is unused, because there is
    no stored row behind the answer. If one of them grows a ``ctx.query`` it has become a
    lake capability and its provenance needs revisiting — a stored reading is not the head
    block.
    """
    import ast
    import textwrap

    for fn in (onchain.onchain_price, onchain.base_market_data):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        body = tree.body[0]
        assert isinstance(body, ast.FunctionDef)
        # The docstring is stripped before scanning, because both of these *say* they do not
        # call `ctx.query` — a scan over the raw source would fail on the explanation.
        statements = body.body[1:] if ast.get_docstring(body) else body.body
        reads = [
            node.attr
            for statement in statements
            for node in ast.walk(statement)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ctx"
        ]
        assert not reads, f"{fn.__name__} now reaches the lake through ctx.{reads}"


@pytest.mark.parametrize("name", _NAMES)
def test_all_three_surfaces_project_the_restored_capability(name: str) -> None:
    """The point of restoring them rather than exempting them.

    An exemption would have left the names off every surface while the gate stayed green.
    This is the assertion that says a caller can reach them again — and it holds because
    they are declared, not because anything in the three projectors mentions them.
    """
    assert name in cli.command_names()
    assert f"{rest.API_PREFIX}/{name}" in rest.route_paths()
    assert name in mcp.tool_names()


# ---------------------------------------------------------------------------
# Callable from a loop, and honest when the chain says no
# ---------------------------------------------------------------------------


async def test_the_pool_readers_answer_from_inside_a_running_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two of the three surfaces could not call these at all, and the comment said why not.

    ``_run`` was a bare ``asyncio.run``, justified in the source by the claim that "both
    network surfaces call ``dispatch.invoke`` from a worker thread rather than from the
    event loop". They do not: ``surfaces/rest.py``'s endpoint is ``async def`` and
    ``surfaces/stdio.py`` calls ``handle_request`` inside an async body. Measured on the
    shipped build — CLI worked, REST returned 500, MCP returned ``RuntimeError:
    asyncio.run() cannot be called from a running event loop``.

    The shape that serves all three already existed one batch over, in
    ``capabilities/market.py``, with a docstring explaining precisely this case. It is in
    ``core/capability.py`` now so a third copy is never the easy option.
    """

    async def _fake_price(symbol: str) -> dict[str, object]:
        return {"symbol": symbol, "price": 2500.0}

    monkeypatch.setattr(
        "crocodile.crypto.exchanges.base_onchain.price.get_onchain_price", _fake_price
    )

    assert asyncio.get_running_loop() is not None
    answer = onchain.onchain_price(
        _CTX, onchain.OnchainPriceParams(symbol="WETH-USDC")
    )
    assert answer == {"symbol": "WETH-USDC", "price": 2500.0}


async def test_a_pool_read_that_failed_raises_instead_of_answering_with_an_error_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An RPC failure was reported as a successful ``NATIVE`` reading.

    ``crocodile onchain-price --symbol WETH/USDC`` exited 0 with ``{"error": …}`` printed as
    the answer, and ``GET /api/v1/base-market-data`` answered ``200`` with
    ``{"result":{"error":"403 Forbidden"},"provenance":{"prov":"native"}}``. A script
    checking ``$?`` sees success; ``warning_for`` stays silent because the declared ``prov``
    is ``NATIVE``; and the provenance block describes a reading that does not exist.

    A failure has to leave by the path failures leave by, which is the exception the three
    surfaces already know how to render.
    """
    from crocodile.core.errors import ConnectorError

    async def _boom(symbol: str) -> dict[str, object]:
        raise ConnectorError("403 Forbidden")

    monkeypatch.setattr(
        "crocodile.crypto.exchanges.base_onchain.price.get_onchain_price", _boom
    )

    with pytest.raises(ConnectorError, match="403 Forbidden"):
        onchain.onchain_price(_CTX, onchain.OnchainPriceParams(symbol="WETH-USDC"))


def test_no_pool_reader_reports_a_failure_as_a_result() -> None:
    """The source rule, scanned, because the dicts were built five places deep.

    ``price.py`` returned ``{"error": …}`` at five sites — an unsupported symbol, a missing
    pool, a failed state read, an unsupported pair and a failed volume read. Each one looks
    like an answer to every caller above it, and the two capabilities here declare
    ``prov=NATIVE`` over whatever comes back.
    """
    import ast

    source = pathlib.Path(
        crocodile.crypto.exchanges.base_onchain.price.__file__
    ).read_text()
    returned_errors = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Dict)
        and any(
            isinstance(key, ast.Constant) and key.value == "error" for key in node.value.keys
        )
    ]
    assert not returned_errors, (
        f"base_onchain/price.py returns an error dict at lines {returned_errors}; raise "
        f"instead, so a failure cannot be served as a NATIVE reading"
    )


def test_a_pool_this_build_does_not_serve_is_the_callers_mistake_not_ours() -> None:
    """Refusing a symbol and failing to reach the chain are different answers.

    Both used to be ``{"error": …}`` and both then became ``FatalConnectorError``, which is
    a ``CrocodileError`` the surfaces do not classify — so an unknown pool name answered
    **500** on REST and a traceback on the CLI. That is the same defect as the one that was
    just fixed in the other direction: the caller's bad parameter reported as our fault, and
    reported as retryable, when the supported list is right there in the message.

    ``ValueError`` is what this codebase already means by "your parameters are wrong" —
    ``_refuse_readonly`` chose ``PermissionError`` over it on exactly that reading — and
    ``surfaces/rest.py`` maps it to 400 at the invoke site. So an unserved pool raises
    ``ValueError`` and a chain that would not answer keeps ``ConnectorError``. The split is
    not cosmetic: one of the two is worth retrying and the other never is.
    """
    from crocodile.core.errors import CrocodileError
    from crocodile.crypto.exchanges.base_onchain.price import get_onchain_price

    with pytest.raises(ValueError, match="not supported") as caught:
        await_result = get_onchain_price("NOT-A-POOL")
        asyncio.run(await_result)

    assert not isinstance(caught.value, CrocodileError), (
        "a bad parameter must not arrive as a connector failure; the surfaces classify the "
        "two differently and only one of them is the caller's to fix"
    )

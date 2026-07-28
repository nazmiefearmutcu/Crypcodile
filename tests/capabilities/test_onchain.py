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

import inspect

import msgspec
import pytest

from crocodile.capabilities import onchain
from crocodile.core.capability import (
    IRREDUCIBLE,
    PENDING_SYMMETRY,
    REGISTRY,
    AssetClass,
    ReturnKind,
)
from crocodile.core.schema.provenance import Provenance, registered_bases
from crocodile.surfaces import cli, mcp, rest

_NAMES = ("onchain-price", "base-market-data")


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

"""The registry's own mechanics, and the capabilities declared so far.

The gates in ``test_gates.py`` ask whether the *contents* of the registry are symmetric
and provenanced. These ask whether the registry itself behaves: that a duplicate name is
rejected, that the import-time seeding survives being run twice, and that the capability's
implementation does what its declaration claims.
"""

from collections.abc import Iterator

import msgspec
import polars as pl
import pytest

from crocodile.capabilities import analytics
from crocodile.core import capability
from crocodile.core.analytics.indicators import (
    INDICATOR_NAMES,
    apply_indicators,
    calculate_ema,
    calculate_rsi,
    calculate_sma,
)
from crocodile.core.capability import (
    REGISTRY,
    AssetClass,
    Capability,
    Impl,
    ReturnKind,
    register,
)
from crocodile.core.schema.provenance import Provenance, level_for


@pytest.fixture
def _isolate_registry() -> Iterator[None]:
    """Snapshot the capability registry around a test that registers into it.

    The same reasoning as the provenance fixture in ``conftest.py``: registration mutates
    module-level state, and a leaked name would make the symmetry gate depend on
    collection order. Both the registry and the set naming which entries this module
    installed have to be restored, or a later ``_install`` would silently take the
    replace-in-place branch.
    """
    registry = dict(REGISTRY)
    builtins = set(capability._DECLARED_NAMES)
    try:
        yield
    finally:
        REGISTRY.clear()
        REGISTRY.update(registry)
        capability._DECLARED_NAMES.clear()
        capability._DECLARED_NAMES.update(builtins)


class _Params(msgspec.Struct, frozen=True):
    symbol: str


def _a_capability(name: str = "fixture-cap") -> Capability:
    return Capability(
        name=name,
        summary="A capability that exists only for this test.",
        params=_Params,
        returns=ReturnKind.SCALAR,
        impls={
            AssetClass.CRYPTO: Impl(fn=len, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(fn=len, prov=Provenance.NATIVE, basis="native"),
        },
    )


# ---------------------------------------------------------------------------
# Registry mechanics
# ---------------------------------------------------------------------------


def test_register_rejects_a_duplicate_name(_isolate_registry: None) -> None:
    """Two modules claiming one name would let the three surfaces project different
    things under the same command."""
    register(_a_capability())
    with pytest.raises(ValueError, match="already registered"):
        register(_a_capability())


def test_register_returns_the_capability(_isolate_registry: None) -> None:
    cap = _a_capability()
    assert register(cap) is cap
    assert REGISTRY["fixture-cap"] is cap


def test_declaring_the_same_capability_twice_is_idempotent(_isolate_registry: None) -> None:
    """A batch module declares at import time, and ``load_all_bases()`` swallows errors.

    A ``ValueError`` from a re-run of a batch module's body would therefore not fail
    loudly; it would leave a registry quietly missing everything declared after it.
    """
    capability.declare(_a_capability())
    capability.declare(_a_capability())
    assert sorted(REGISTRY) == sorted({*REGISTRY} | {"fixture-cap"})
    assert REGISTRY["fixture-cap"].name == "fixture-cap"


def test_a_foreign_duplicate_still_fails_after_a_capability_is_declared(
    _isolate_registry: None,
) -> None:
    """Idempotency is scoped to names already declared, not a blanket amnesty."""
    capability.declare(_a_capability())
    with pytest.raises(ValueError, match="already registered"):
        register(_a_capability())


def test_the_seeded_registry_holds_indicators() -> None:
    cap = REGISTRY["indicators"]
    assert cap.returns is ReturnKind.TABLE
    assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    assert all(impl.fn is analytics.indicators for impl in cap.impls.values())


def test_the_seeded_registry_holds_slippage_under_one_name() -> None:
    """One capability was on the wire under two names; the registry may only hold one.

    ``slippage`` (crypto CLI, crypto REST GET, MCP ``estimate_slippage``) and
    ``simulate-price-impact`` (REST POST, both asset classes) both called the same
    estimator. The measurement is the name; the retired spelling is an alias.
    """
    cap = REGISTRY["slippage"]
    assert cap.returns is ReturnKind.SCALAR
    assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    assert all(impl.fn is analytics.slippage for impl in cap.impls.values())
    assert cap.aliases == ("simulate-price-impact",)
    assert "simulate-price-impact" not in REGISTRY, (
        "an alias that is also a registered name is two capabilities again"
    )


def test_slippage_carries_size_unit_so_one_struct_covers_both_arities() -> None:
    """The crypto estimator took a unit and the equity one did not; the struct keeps it.

    Optional, so an equity caller sizing in shares omits it and gets a walk by quantity.
    Dropping it would have deleted the only path that can size an order by notional.
    """
    fields = {f.name: f for f in msgspec.structs.fields(REGISTRY["slippage"].params)}
    assert set(fields) == {"symbol", "side", "size", "size_unit"}
    assert fields["size_unit"].default is None


def test_no_alias_collides_with_a_capability_name_or_another_alias() -> None:
    """An alias is a redirect. Two things answering to one string is the original bug."""
    seen: dict[str, str] = {}
    for cap in REGISTRY.values():
        for alias in cap.aliases:
            assert alias not in REGISTRY, f"{alias!r} is both an alias and a capability name"
            assert alias not in seen, f"{alias!r} aliases both {seen[alias]!r} and {cap.name!r}"
            seen[alias] = cap.name


def test_slippage_rests_on_a_modelled_book_for_equities_and_a_native_one_for_crypto() -> None:
    """``basis`` names the inputs, and the two asset classes genuinely differ here.

    A crypto venue streams its book. An equity book is modelled from volume bars unless an
    Alpaca key upgrades it to L1, so the estimate is SYNTHETIC on its best day — declaring
    the keyed ceiling would let a keyless deployment claim a level it never reaches.
    """
    crypto = REGISTRY["slippage"].impls[AssetClass.CRYPTO]
    assert crypto.basis == "native"
    assert level_for(crypto.basis) is Provenance.NATIVE
    assert crypto.prov is Provenance.DERIVED

    equity = REGISTRY["slippage"].impls[AssetClass.EQUITY]
    assert equity.basis == "yahoo_1m_vap"
    assert level_for(equity.basis) is Provenance.SYNTHETIC
    assert equity.prov is Provenance.SYNTHETIC


def test_indicators_declares_a_native_input_basis() -> None:
    """``basis`` names where the *inputs* came from, which is why ``native`` is right here.

    Both asset classes report OHLCV natively, so the capability rests on no modelling;
    what it returns is computed, which is what ``prov`` says.
    """
    for impl in REGISTRY["indicators"].impls.values():
        assert impl.basis == "native"
        assert level_for(impl.basis) is Provenance.NATIVE
        assert impl.prov is Provenance.DERIVED


def test_every_irreducible_justification_names_a_market_property() -> None:
    """A guard on the bar, not just on emptiness.

    "Not built yet" and "no free data source" are scheduling facts; the promise is that a
    synthetic method fills an absent source while saying so, so neither can buy an
    exemption from the symmetry gate.
    """
    excuses = ("not built", "not yet", "todo", "later", "no free", "no data source")
    for name, why in capability.IRREDUCIBLE.items():
        lowered = why.lower()
        assert not any(e in lowered for e in excuses), (
            f"{name} is exempted with a scheduling excuse, not a market property: {why!r}"
        )


# ---------------------------------------------------------------------------
# The capability's implementation
# ---------------------------------------------------------------------------


def _bars(n: int = 30) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "bar": list(range(n)),
            "close": [10.0 + (i % 5) for i in range(n)],
        }
    )


def test_apply_indicators_appends_every_column_by_default() -> None:
    out = apply_indicators(_bars())
    assert out.columns == [
        "bar",
        "close",
        "sma",
        "ema",
        "rsi",
        "macd",
        "signal",
        "hist",
        "bb_upper",
        "bb_middle",
        "bb_lower",
    ]
    assert out.height == 30


def test_apply_indicators_none_means_all() -> None:
    assert apply_indicators(_bars(), None).columns == apply_indicators(_bars(), "all").columns


@pytest.mark.parametrize(
    ("name", "added"),
    [
        ("sma", ["sma"]),
        ("ema", ["ema"]),
        ("rsi", ["rsi"]),
        ("macd", ["macd", "signal", "hist"]),
        ("bb", ["bb_upper", "bb_middle", "bb_lower"]),
    ],
)
def test_apply_indicators_adds_only_what_was_asked_for(name: str, added: list[str]) -> None:
    out = apply_indicators(_bars(), name)
    assert out.columns == ["bar", "close", *added]


def test_apply_indicators_is_case_insensitive() -> None:
    assert apply_indicators(_bars(), "RSI").columns == ["bar", "close", "rsi"]


def test_apply_indicators_matches_the_primitives_it_wraps() -> None:
    """The wrapper must not quietly become a second implementation."""
    bars = _bars()
    out = apply_indicators(bars, "all", period=7)
    close = bars["close"]
    assert out["sma"].to_list() == calculate_sma(close, 7).to_list()
    assert out["ema"].to_list() == calculate_ema(close, 7).to_list()
    assert out["rsi"].to_list() == calculate_rsi(close, 7).to_list()


def test_apply_indicators_returns_an_empty_frame_unchanged() -> None:
    """No rows means no indicators, which is an answer rather than an error."""
    empty = pl.DataFrame({"bar": [], "close": []})
    assert apply_indicators(empty).columns == ["bar", "close"]


def test_apply_indicators_rejects_an_unknown_name() -> None:
    """Returning the frame unchanged would hide a typo in a surface's parameter."""
    with pytest.raises(ValueError, match="Unknown indicator"):
        apply_indicators(_bars(), "stochastic")


def test_apply_indicators_rejects_a_non_positive_period() -> None:
    with pytest.raises(ValueError, match="positive"):
        apply_indicators(_bars(), "sma", period=0)


def test_indicator_names_are_what_apply_indicators_accepts() -> None:
    """The advertised list and the accepted list are one list."""
    for name in INDICATOR_NAMES:
        apply_indicators(_bars(), name)

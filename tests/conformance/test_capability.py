"""The registry's own mechanics, and the one capability Phase 1 declares.

The gates in ``test_gates.py`` ask whether the *contents* of the registry are symmetric
and provenanced. These ask whether the registry itself behaves: that a duplicate name is
rejected, that the import-time seeding survives being run twice, and that the capability's
implementation does what its declaration claims.
"""

from collections.abc import Iterator

import msgspec
import polars as pl
import pytest

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
    builtins = set(capability._BUILTIN_NAMES)
    try:
        yield
    finally:
        REGISTRY.clear()
        REGISTRY.update(registry)
        capability._BUILTIN_NAMES.clear()
        capability._BUILTIN_NAMES.update(builtins)


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


def test_installing_a_builtin_twice_is_idempotent(_isolate_registry: None) -> None:
    """The seeding runs at import time, and ``load_all_bases()`` swallows import errors.

    A ``ValueError`` from a re-run of this module's body would therefore not fail loudly;
    it would leave a registry quietly missing everything declared after it.
    """
    capability._install(_a_capability())
    capability._install(_a_capability())
    assert sorted(REGISTRY) == sorted({*REGISTRY} | {"fixture-cap"})
    assert REGISTRY["fixture-cap"].name == "fixture-cap"


def test_a_foreign_duplicate_still_fails_after_a_builtin_is_installed(
    _isolate_registry: None,
) -> None:
    """Idempotency is scoped to names this module installed, not a blanket amnesty."""
    capability._install(_a_capability())
    with pytest.raises(ValueError, match="already registered"):
        register(_a_capability())


def test_the_seeded_registry_holds_indicators() -> None:
    cap = REGISTRY["indicators"]
    assert cap.returns is ReturnKind.TABLE
    assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    assert all(impl.fn is apply_indicators for impl in cap.impls.values())


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

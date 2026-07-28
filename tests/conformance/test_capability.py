"""The registry's own mechanics, and the capabilities declared so far.

The gates in ``test_gates.py`` ask whether the *contents* of the registry are symmetric
and provenanced. These ask whether the registry itself behaves: that a duplicate name is
rejected, that the import-time seeding survives being run twice, and that the capability's
implementation does what its declaration claims.
"""

import re
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

    One name, two adapters. This used to assert one *function* for both asset classes, and
    that reading was the defect rather than the invariant: the shared function read
    ``book_snapshot``, which no equity provider writes, so every equity call raised while
    the declaration advertised a ``yahoo_1m_vap`` ladder it never opened. What has to be
    shared is the name, the params struct and the arithmetic — not the store the ladder
    comes out of.
    """
    cap = REGISTRY["slippage"]
    assert cap.returns is ReturnKind.SCALAR
    assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    assert cap.impls[AssetClass.CRYPTO].fn is analytics.slippage
    assert cap.impls[AssetClass.EQUITY].fn is analytics.slippage_equities
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


def test_slippage_rests_on_a_rebuilt_ladder_for_equities_and_a_streamed_book_for_crypto() -> None:
    """``basis`` names the inputs, and the two asset classes genuinely differ here.

    A crypto venue streams its book. An equity ladder is built by ``select_depth_source`` —
    Alpaca L1 when keyed, a synthetic Yahoo VAP profile when not — which is the same book
    ``depth`` reads, so the two declare the same ceiling.

    Renamed and re-pinned. The old assertion was ``SYNTHETIC``/``yahoo_1m_vap``, argued as
    the deliberate *floor* so that a keyless deployment could not claim a level it never
    reaches. ``Impl.prov`` is documented as a ceiling, so the floor argument was answering a
    question the field does not ask — and the worry behind it is handled where it belongs,
    on the returned profile's own tail. The basis was also naming a code path that could not
    execute, which is the part that made it not merely pessimistic but false.
    """
    crypto = REGISTRY["slippage"].impls[AssetClass.CRYPTO]
    assert crypto.basis == "native"
    assert level_for(crypto.basis) is Provenance.NATIVE
    assert crypto.prov is Provenance.DERIVED

    equity = REGISTRY["slippage"].impls[AssetClass.EQUITY]
    assert (equity.basis, equity.prov) == ("alpaca_l1", Provenance.DERIVED)
    assert level_for(equity.basis) is Provenance.DERIVED

    depth_equity = REGISTRY["depth"].impls[AssetClass.EQUITY]
    assert (equity.basis, equity.prov) == (depth_equity.basis, depth_equity.prov), (
        "one book cannot have two ceilings; slippage and depth both read select_depth_source"
    )


def test_indicators_declares_a_native_input_basis() -> None:
    """``basis`` names where the *inputs* came from, which is why ``native`` is right here.

    Both asset classes report OHLCV natively, so the capability rests on no modelling;
    what it returns is computed, which is what ``prov`` says.
    """
    for impl in REGISTRY["indicators"].impls.values():
        assert impl.basis == "native"
        assert level_for(impl.basis) is Provenance.NATIVE
        assert impl.prov is Provenance.DERIVED


_SCHEDULING_EXCUSES = ("not built", "not yet", "todo", "later", "no free", "no data source")
"""Phrases that describe a plan rather than a market. Six words, and six words is a floor."""

_CLAIM_VOCABULARY = frozenset(
    """
    a an and are as at be because been but by can cannot chain class could data do does
    either exist exists for from half has have here in is it its native no none not of on
    onchain one only or other side so that the their there these this those to two version
    was way which will with analog analogue analogues counterpart crypto cryptocurrency
    equities equity asset capability market markets
    """.split()
)
"""Every word the exemption itself supplies, which is why a justification made only of them
says nothing.

``IRREDUCIBLE`` already means "no equity analogue can exist for this crypto capability". A
justification is the *argument* for that claim, so a sentence assembled entirely out of the
claim's own vocabulary has restated the conclusion and stopped. This set is the claim's
vocabulary, taken from how :data:`~crocodile.core.capability.IRREDUCIBLE`'s own docstring
states it, plus ordinary grammatical glue — not a list of words somebody dislikes.
"""


def test_every_irreducible_justification_names_a_market_property() -> None:
    """A guard on the bar, not just on emptiness — and an honest account of what a bar can do.

    "Not built yet" and "no free data source" are scheduling facts; the promise is that a
    synthetic method fills an absent source while saying so, so neither can buy an
    exemption from the symmetry gate. That was the whole rule, and a referee walked through
    it: *"No equity analogue can exist; this is chain-native."* names no schedule, so it
    passed, and it bought a deletion.

    Two things are wrong with a blacklist and only one is fixable. The unfixable one is that
    it grades words, and there are always more sentences than words — no list makes a
    convincing-sounding excuse fail. What *is* fixable is the specific sentence the referee
    reached for, because it is not merely unconvincing: it is the conclusion restated. "No
    equity analogue can exist" is what an entry on this mapping already asserts, and
    "chain-native" is the category the mapping is named for. A justification built only from
    the claim's own vocabulary carries no information at all, and that is checkable without
    grading anybody's prose.

    So there are two rules, and neither is the mechanism. The mechanism is the census in
    ``tests/conformance/test_pending_symmetry.py``, which asserts that this dict holds
    exactly the seven entries a reviewer agreed to and that each one still carries the
    argument it was pinned with. Prose is graded by people; what a gate can require is that
    people were given the diff.
    """
    for name, why in capability.IRREDUCIBLE.items():
        lowered = why.lower()
        assert not any(e in lowered for e in _SCHEDULING_EXCUSES), (
            f"{name} is exempted with a scheduling excuse, not a market property: {why!r}"
        )
        substantive = {
            word
            for word in re.findall(r"[a-z0-9]+", lowered)
            if word not in _CLAIM_VOCABULARY
        }
        assert len(substantive) >= 2, (
            f"{name} is exempted by restating the exemption: {why!r}. Every word of that is "
            f"one IRREDUCIBLE already supplies — it means "
            f"'no equity analogue can exist' by itself. Name the thing the market has on one "
            f"side and not the other: a mempool, a sequencer, a pooled reserve, a peg."
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


@pytest.mark.parametrize(
    "why",
    [
        "No equity analogue can exist; this is chain-native.",
        "Crypto-only; there is no equity counterpart.",
        "This capability cannot exist for the equity asset class.",
        "Chain-native data.",
    ],
)
def test_a_justification_that_only_restates_the_exemption_is_rejected(
    why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first is the referee's, verbatim; the rest are the same move spelled differently.

    Kept as a parametrised negative pin rather than as one case because the finding was not
    that *this sentence* got through — it was that the bar graded a six-word blacklist, and
    every sentence here clears that blacklist while asserting nothing an entry on
    ``IRREDUCIBLE`` does not already assert.
    """
    monkeypatch.setitem(capability.IRREDUCIBLE, "fixture-laundered", why)
    with pytest.raises(AssertionError, match="restating the exemption"):
        test_every_irreducible_justification_names_a_market_property()


@pytest.mark.parametrize(
    "why",
    [
        "Requires a public mempool and atomic transaction ordering.",
        "Measures an L2 sequencer; equities have no sequencer.",
        "Stablecoin peg mechanics; no equity instrument behaves this way.",
    ],
)
def test_a_justification_that_names_a_mechanism_is_accepted(
    why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule is only worth having if the seven live entries would survive being re-argued.

    These are three of them, re-declared under a fixture name so the acceptance is asserted
    rather than inferred from the suite being green. Each names something the market has on
    one side and not the other, which is the property the mapping claims.
    """
    monkeypatch.setitem(capability.IRREDUCIBLE, "fixture-argued", why)
    test_every_irreducible_justification_names_a_market_property()

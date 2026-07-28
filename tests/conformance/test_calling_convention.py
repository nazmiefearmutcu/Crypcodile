"""Every implementation in the registry takes ``(ctx, params)`` and nothing else.

A surface projects a capability by calling it. If implementations disagree about how they
want to be called, the projector has to know each one individually, and at that point the
three surfaces are hand-written again with extra steps.

The convention could have been enforced by convention. It is enforced by this gate instead
because the alternative — a projector that introspects each signature and injects
dependencies by name — fails *silently*: a renamed parameter becomes a ``TypeError`` at
call time, on whichever surface calls it first, for whichever asset class drifted. That is
the exact shape of the failure the merge exists to end, so the check happens at build time
over the whole registry rather than at call time over one entry.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Iterator

import msgspec
import pytest

from crocodile import capabilities
from crocodile.capabilities import analytics
from crocodile.core import capability
from crocodile.core.analytics.slippage import estimate_slippage
from crocodile.core.capability import (
    REGISTRY,
    AssetClass,
    Capability,
    CapabilityContext,
    Impl,
    ReturnKind,
    declare,
)
from crocodile.core.schema.provenance import Provenance

_ACCEPTS_POSITIONALLY = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


class _Params(msgspec.Struct, frozen=True):
    symbol: str


@pytest.fixture
def _isolate() -> Iterator[None]:
    """The registry is module state; a leak would order-couple every other gate."""
    registry = dict(REGISTRY)
    declared = set(capability._DECLARED_NAMES)
    try:
        yield
    finally:
        REGISTRY.clear()
        REGISTRY.update(registry)
        capability._DECLARED_NAMES.clear()
        capability._DECLARED_NAMES.update(declared)


def _conforming(ctx: CapabilityContext, params: _Params) -> str:
    return params.symbol


_FIXTURE = "fixture-convention"


def _with(fn: object, name: str = _FIXTURE) -> Capability:
    """A capability whose only interesting property is the shape of its implementation."""
    impl = Impl(fn=fn, prov=Provenance.NATIVE, basis="native")  # type: ignore[arg-type]
    return Capability(
        name=name,
        summary="Exists only to drive the calling-convention gate.",
        params=_Params,
        returns=ReturnKind.SCALAR,
        impls={AssetClass.CRYPTO: impl, AssetClass.EQUITY: impl},
    )


def _offences(only: str | None = None) -> list[str]:
    """Return one line per implementation that does not take exactly ``(ctx, params)``.

    ``only`` narrows the walk to one capability, which is what the fixture-driven tests
    below use. Without it a single real offender fails every probe in this file at once and
    the accepting cases stop being able to say anything.

    Four callable shapes need a decision rather than a rule of thumb, and each is decided
    on whether the projector can still call it correctly:

    * A **bound method** is accepted on whatever it looks like after ``self`` is dropped,
      which is what ``inspect.signature`` already reports and what the projector will
      actually call.
    * A :func:`functools.partial` is likewise judged on its *remaining* parameters. A
      partial with the catalog already bound is a legitimate adapter; one that leaves three
      parameters open is not, and the difference is exactly what ``signature`` reports.
    * ``*args`` / ``**kwargs`` is **rejected** even though such a callable can be handed two
      positional arguments. It accepts every call and describes none, which makes it
      indistinguishable from a drifted signature — the failure this gate exists to catch,
      wearing a passing build.
    * A **C builtin with no introspectable signature** raises ``ValueError`` from
      ``inspect.signature``. That is reported as an offence, not skipped. A skip would mean
      the one implementation nobody can check is also the one nobody is told about, and
      "unverifiable" is not the same claim as "fine".
    """
    offences: list[str] = []
    for cap in REGISTRY.values():
        if only is not None and cap.name != only:
            continue
        for asset_class, impl in cap.impls.items():
            where = f"{cap.name}/{asset_class.value}"
            try:
                sig = inspect.signature(impl.fn)
            except (ValueError, TypeError) as exc:
                offences.append(
                    f"{where}: {impl.fn!r} has no introspectable signature ({exc}); "
                    f"wrap it in a module-level adapter that declares (ctx, params)"
                )
                continue
            params = list(sig.parameters.values())
            variadic = [p.name for p in params if p.kind is inspect.Parameter.VAR_POSITIONAL]
            variadic += [p.name for p in params if p.kind is inspect.Parameter.VAR_KEYWORD]
            if variadic:
                offences.append(
                    f"{where}: {impl.fn!r} is variadic ({', '.join(variadic)}); "
                    f"it accepts every call and describes none"
                )
                continue
            positional = [p.name for p in params if p.kind in _ACCEPTS_POSITIONALLY]
            if len(positional) != 2 or len(positional) != len(params):
                offences.append(
                    f"{where}: {impl.fn!r} takes {[p.name for p in params]}, "
                    f"not exactly two positional parameters (ctx, params)"
                )
    return offences


def test_the_gate_has_a_registry_to_check() -> None:
    """An empty registry would make every assertion below vacuously true."""
    capabilities.load_all()
    assert REGISTRY, "nothing declared; crocodile.capabilities.load_all() did not populate"


def test_every_implementation_takes_exactly_a_context_and_a_params_struct() -> None:
    capabilities.load_all()
    assert not _offences(), "\n".join(_offences())


def test_a_conforming_implementation_passes(_isolate: None) -> None:
    """The accepting branch, so the gate is a filter rather than a ban."""
    declare(_with(_conforming))
    assert not _offences(_FIXTURE)


def test_a_bound_method_is_judged_after_self_is_dropped(_isolate: None) -> None:
    class _Adapter:
        def run(self, ctx: CapabilityContext, params: _Params) -> str:
            return params.symbol

    declare(_with(_Adapter().run))
    assert not _offences(_FIXTURE)


def test_a_partial_that_closes_over_its_extra_arguments_passes(_isolate: None) -> None:
    def _three(catalog: object, ctx: CapabilityContext, params: _Params) -> str:
        return params.symbol

    declare(_with(functools.partial(_three, object())))
    assert not _offences(_FIXTURE)


_NON_CONFORMING: dict[str, object] = {
    "the analytics signature used directly": estimate_slippage,
    "one parameter": lambda ctx: None,
    "three parameters": lambda ctx, params, extra: None,
    "two positional plus a defaulted third": lambda ctx, params, extra=1: None,
    "a required keyword-only parameter": lambda ctx, params, *, extra: None,
    "star-args": lambda *args: None,
    "star-kwargs": lambda ctx, params, **kw: None,
    "a partial that leaves three open": functools.partial(lambda a, b, c, d: None, 1),
}
"""Every way of not being ``(ctx, params)`` that the port could plausibly write.

The first row is the one that matters: ``estimate_slippage(catalog, symbol, side, size,
size_unit)`` is a real function that a declaration could name directly, and did until the
adapter was written. It has to be rejected here or the gate does not cover its own subject.
"""


@pytest.mark.parametrize("label", sorted(_NON_CONFORMING), ids=lambda k: k.replace(" ", "_"))
def test_a_non_conforming_implementation_is_rejected(label: str, _isolate: None) -> None:
    declare(_with(_NON_CONFORMING[label]))
    assert _offences(_FIXTURE), f"{label} was accepted"


def test_an_uninstrospectable_callable_is_reported_rather_than_skipped(_isolate: None) -> None:
    """The branch a ``pytest.skip`` would have hidden.

    Not every C builtin refuses introspection — ``len`` reports ``(obj, /)`` quite happily
    — so the case is forced with a callable that raises from ``__signature__``, which is
    what an unintrospectable extension type looks like from here.
    """

    class _Opaque:
        def __call__(self, *args: object) -> None: ...

        @property
        def __signature__(self) -> inspect.Signature:
            raise ValueError("no signature for builtin")

    declare(_with(_Opaque()))
    offences = _offences(_FIXTURE)
    assert offences and "no introspectable signature" in offences[0]


def test_the_declared_capabilities_call_through_to_their_analytics_functions() -> None:
    """The adapters are adapters, not second implementations.

    Named here rather than in ``test_capability.py`` because it is the convention that
    makes the indirection necessary: the analytics functions keep the signatures their own
    domain wants, and the registry only ever sees the two-argument wrapper.
    """
    capabilities.load_all()
    assert REGISTRY["indicators"].impls[AssetClass.CRYPTO].fn is analytics.indicators
    # One adapter per (capability, asset class), not one per capability. This asserted
    # `analytics.slippage` for the *equity* impl, which was the shape of the defect: one
    # function bound twice, reading `book_snapshot`, which no equity provider writes.
    assert REGISTRY["slippage"].impls[AssetClass.CRYPTO].fn is analytics.slippage
    assert REGISTRY["slippage"].impls[AssetClass.EQUITY].fn is analytics.slippage_equities

"""Provenance levels and the registry of confidence formulas.

Every record Crocodile writes declares how it came to exist. Where a value is
not reported natively by a venue, the method that produced it is named by
``prov_basis``, and that name is a key into this registry.

The registry exists to make one rule enforceable: a confidence number must be
a pure function of observable inputs, documented alongside its formula. Constants
chosen by feel are how honest provenance decays into decoration.

Confidence measures **sampling adequacy within a level**, not truthfulness
across levels. A synthetic profile built from a full session scores 1.0 because
it is fully sampled *as a synthetic profile*; ``prov`` is what says it is not a
real order book. The two fields answer different questions and neither
substitutes for the other. This is also why a confidence of 0.0 is not the same
claim as :attr:`Provenance.UNAVAILABLE`: an empty synthetic profile and an
absent one are distinguished by ``prov``, never by the number alone.

:func:`provenance_fields` is the only supported way to populate a record's four
``prov_*`` fields. Hand-assembling them bypasses the confidence formula, which
is exactly what this module exists to prevent.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
import warnings
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Final, NamedTuple

__all__ = [
    "ConfidenceFn",
    "ConfidenceFormulaError",
    "ConfidenceInputError",
    "Provenance",
    "ProvenanceFields",
    "UnregisteredBasisError",
    "confidence_for",
    "describe",
    "level_for",
    "load_all_bases",
    "provenance_fields",
    "register_basis",
    "registered_bases",
]


class Provenance(StrEnum):
    """How a record came to exist."""

    NATIVE = "native"
    """The venue reported this value directly."""

    DERIVED = "derived"
    """Computed from native records already in the lake."""

    SYNTHETIC = "synthetic"
    """Modelled from a different data class than the one being reported."""

    UNAVAILABLE = "unavailable"
    """Obtainable from no configured source. A typed hole, not a zero."""


ConfidenceFn = Callable[[Mapping[str, Any]], float]


# TODO(task-3): re-parent under CrocodileError
class UnregisteredBasisError(LookupError):
    """A ``prov_basis`` was used that has no registered confidence formula."""


# TODO(task-3): re-parent under CrocodileError
class ConfidenceInputError(ValueError):
    """Caller fault: the inputs handed to a confidence formula are missing or malformed."""


# TODO(task-3): re-parent under CrocodileError
class ConfidenceFormulaError(RuntimeError):
    """Formula fault: a registered formula raised, or returned a value outside ``[0, 1]``.

    Distinct from :class:`ConfidenceInputError` on purpose. A caller that degrades to
    :attr:`Provenance.UNAVAILABLE` on bad input must not silently swallow a broken formula.
    """


class _Registered(NamedTuple):
    """One basis's registration, inserted into the registry as a single atomic value."""

    fn: ConfidenceFn
    level: Provenance
    inputs: tuple[str, ...]
    doc: str


class ProvenanceFields(NamedTuple):
    """The four-field provenance tail every record carries."""

    prov: Provenance
    prov_basis: str
    prov_confidence: float
    prov_inputs: list[str]


_REGISTRY: Final[dict[str, _Registered]] = {}

# One regular US trading session in 1-minute bars, scoped to the one basis that
# uses it: a crypto volume-at-price basis would have a 1440-minute day and no
# session at all, so this is not a shared-core constant.
_YAHOO_1M_VAP_SESSION_BARS: Final[int] = 390


def register_basis(
    basis: str, *, level: Provenance, inputs: Sequence[str], doc: str | None = None
) -> Callable[[ConfidenceFn], ConfidenceFn]:
    """Register the confidence formula for ``basis``.

    Args:
        basis: The ``prov_basis`` name this formula answers for.
        level: The provenance level records built on this basis carry.
        inputs: The data channels the method consumes (``["bar"]``, ``[]``, ...). This is a
            property of the method rather than of any call site, which is why it is declared
            here; it becomes the record's ``prov_inputs``. Note this is a different thing
            from the ``inputs`` mapping passed to :func:`confidence_for`, which carries the
            observable measurements a specific call has to hand.
        doc: Optional description, preferred by :func:`describe` over the formula's
            docstring. The docstring is the normal path and the rule below still applies to
            it; this exists because ``python -OO`` strips docstrings, which would otherwise
            leave :func:`describe` empty and blank the warning body the REST and MCP
            surfaces must emit for every non-native record. A basis that supplies neither a
            docstring nor ``doc`` yields an empty description under ``-OO``, so ``-OO`` is
            not a supported mode for those surfaces.

    Raises:
        ValueError: if ``basis`` is already registered, or the formula has no docstring.
            The docstring is the only place a constant like ``native``'s 1.0 can be
            justified, so an undocumented formula is a rejected formula. The docstring
            half of that check is skipped under ``python -OO``, which strips docstrings at
            compile time and so leaves it nothing to inspect.
    """

    def decorate(fn: ConfidenceFn) -> ConfidenceFn:
        if basis in _REGISTRY:
            raise ValueError(f"provenance basis {basis!r} is already registered")
        docstring = (fn.__doc__ or "").strip()
        # Under -OO the interpreter strips docstrings at compile time, so this check has
        # nothing to inspect and would turn every registration into an import-time crash.
        # The rule is a development and CI discipline; neither runs with -OO.
        if sys.flags.optimize < 2 and not docstring:
            raise ValueError(
                f"confidence formula for {basis!r} has no docstring; "
                f"a confidence number must be documented alongside its formula"
            )
        _REGISTRY[basis] = _Registered(
            fn=fn,
            level=level,
            inputs=tuple(inputs),
            doc=(doc or "").strip() or docstring,
        )
        return fn

    return decorate


def _lookup(basis: str) -> _Registered:
    """Return the registration for ``basis``, or raise :class:`UnregisteredBasisError`."""
    try:
        return _REGISTRY[basis]
    except KeyError:
        raise UnregisteredBasisError(
            f"provenance basis {basis!r} has no registered confidence formula; "
            f"register one with @register_basis({basis!r}, level=..., inputs=[...])"
        ) from None


def confidence_for(basis: str, inputs: Mapping[str, Any]) -> float:
    """Return the confidence for ``basis`` given its observable ``inputs``.

    Raises:
        UnregisteredBasisError: if ``basis`` has no formula.
        ConfidenceInputError: if ``inputs`` is missing a key the formula needs, or holds a
            value of the wrong type. Caller fault.
        ConfidenceFormulaError: if the formula raised, or returned a value outside
            ``[0.0, 1.0]``. Formula fault.
    """
    registered = _lookup(basis)
    try:
        value = float(registered.fn(inputs))
    except ConfidenceInputError:
        raise
    except KeyError as exc:
        raise ConfidenceInputError(
            f"confidence formula for {basis!r} requires input {exc}; given: {sorted(inputs)}"
        ) from exc
    except Exception as exc:
        raise ConfidenceFormulaError(
            f"confidence formula for {basis!r} raised {type(exc).__name__}: {exc}"
        ) from exc
    if not 0.0 <= value <= 1.0:
        raise ConfidenceFormulaError(
            f"confidence formula for {basis!r} returned {value!r}, outside [0, 1]"
        )
    return value


def level_for(basis: str) -> Provenance:
    """Return the provenance level ``basis`` was registered under."""
    return _lookup(basis).level


def describe(basis: str) -> str:
    """Return the description of the formula registered for ``basis``.

    This is the ``doc`` passed to :func:`register_basis` if there was one, and the formula's
    docstring otherwise. The REST and MCP surfaces use it as the body of the warning they
    are required to emit whenever they serve a record whose ``prov`` is not
    :attr:`Provenance.NATIVE`. It is empty under ``python -OO`` for any basis that relied on
    its docstring, since ``-OO`` strips docstrings.
    """
    return _lookup(basis).doc


def registered_bases() -> frozenset[str]:
    """Every basis name registered *in this process so far*.

    This reflects only what has been imported. A conformance gate that compares call sites
    against this set must call :func:`load_all_bases` first, or a basis living in a module
    nothing happened to import reads as a false offender. That import is deliberately not
    done here: an accessor that imports the world is a trap.
    """
    return frozenset(_REGISTRY)


def load_all_bases() -> None:
    """Import every ``crocodile`` submodule so that every ``@register_basis`` has run.

    Call this explicitly before treating :func:`registered_bases` as the complete set.
    A submodule that fails to import — a missing optional extra, most likely — is skipped
    rather than allowed to crash the caller, since a gate must still be able to run on a
    partial install. The cost is that a basis inside such a module stays invisible. Any
    other failure is warned about rather than swallowed, so a genuinely broken module is
    not mistaken for an absent optional dependency.
    """
    import crocodile

    for module in pkgutil.walk_packages(crocodile.__path__, prefix=f"{crocodile.__name__}."):
        try:
            # The names come from walk_packages over the package's own __path__, so no
            # caller-supplied value reaches this import; and a dynamic import is the entire
            # point of the function, so there is no literal form to prefer.
            # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
            importlib.import_module(module.name)
        except ImportError:
            continue
        except Exception as exc:
            warnings.warn(
                f"crocodile: could not load provenance bases from {module.name!r}: "
                f"{type(exc).__name__}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue


def provenance_fields(basis: str, inputs: Mapping[str, Any] | None = None) -> ProvenanceFields:
    """Build a record's provenance tail from a registered basis.

    This is the only supported way to populate the four ``prov_*`` fields.
    Hand-assembling them bypasses the confidence formula, which is exactly what
    the registry exists to prevent.

    ``prov`` and ``prov_inputs`` come from the registration; ``prov_confidence`` is
    computed from ``inputs`` by the registered formula.

    Raises:
        UnregisteredBasisError: if ``basis`` has no formula.
        ConfidenceInputError: if ``inputs`` does not satisfy the formula.
        ConfidenceFormulaError: if the formula misbehaves.
    """
    registered = _lookup(basis)
    return ProvenanceFields(
        prov=registered.level,
        prov_basis=basis,
        prov_confidence=confidence_for(basis, inputs if inputs is not None else {}),
        prov_inputs=list(registered.inputs),
    )


def _require_int(inputs: Mapping[str, Any], key: str) -> int:
    """Read ``key`` from ``inputs`` as an ``int``, refusing to coerce.

    A definitional count arriving as ``3.9`` or ``"390"`` is a caller bug, and coercing it
    would silently turn a malformed input into a plausible-looking confidence.
    """
    try:
        value = inputs[key]
    except KeyError:
        raise ConfidenceInputError(
            f"missing required input {key!r}; given: {sorted(inputs)}"
        ) from None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfidenceInputError(
            f"input {key!r} must be an int, got {type(value).__name__}: {value!r}"
        )
    return value


@register_basis("native", level=Provenance.NATIVE, inputs=[])
def _native(_: Mapping[str, Any]) -> float:
    """A venue-reported value is certain by definition.

    The constant 1.0 is not a judgement about the venue's accuracy; it says the value was
    fully sampled at its own level, having been read rather than reconstructed.
    """
    return 1.0


@register_basis("unavailable", level=Provenance.UNAVAILABLE, inputs=[])
def _unavailable(_: Mapping[str, Any]) -> float:
    """A hole carries no information, so it is fully unsampled at any level."""
    return 0.0


@register_basis("yahoo_1m_vap", level=Provenance.SYNTHETIC, inputs=["bar"])
def _yahoo_1m_vap(inputs: Mapping[str, Any]) -> float:
    """Volume-at-price depth: confidence is the session coverage of the profile.

    ``min(n / 390, 1.0)``, where ``n`` counts volume-bearing 1-minute bars and 390 is one
    full regular US session in such bars. The number reads as how many bars the profile
    holds against that reference: 1.0 means at least as many volume-bearing bars as one
    full session contains. That is not the same as having covered a session — 390 bars
    drawn from three partial sessions also scores 1.0. A profile built from three bars is
    not a book.

    Saturating at a full session says the profile is as well sampled as this method can
    make it — not that it has become a real order book. That claim is ``prov``'s job, and
    it stays :attr:`Provenance.SYNTHETIC` at every value.
    """
    n = _require_int(inputs, "n_volume_bars")
    if n < 0:
        raise ConfidenceInputError(f"input 'n_volume_bars' must be non-negative, got {n}")
    return min(n / _YAHOO_1M_VAP_SESSION_BARS, 1.0)

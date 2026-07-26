"""Provenance levels and the registry of confidence formulas.

Every record Crocodile writes declares how it came to exist. Where a value is
not reported natively by a venue, the method that produced it is named by
``prov_basis``, and that name is a key into this registry.

The registry exists to make one rule enforceable: a confidence number must be
a pure function of observable inputs, documented alongside its formula. Constants
chosen by feel are how honest provenance decays into decoration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "ConfidenceFn",
    "Provenance",
    "UnregisteredBasisError",
    "confidence_for",
    "level_for",
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


class UnregisteredBasisError(LookupError):
    """A ``prov_basis`` was used that has no registered confidence formula."""


_FORMULAS: Final[dict[str, ConfidenceFn]] = {}
_LEVELS: Final[dict[str, Provenance]] = {}

# One regular US trading session in 1-minute bars. Used as the saturation
# reference for volume-at-price synthesis: a full session of volume-bearing
# bars is the point at which the profile is considered half-confident.
_RTH_MINUTES: Final[int] = 390


def register_basis(basis: str, *, level: Provenance) -> Callable[[ConfidenceFn], ConfidenceFn]:
    """Register the confidence formula for ``basis``.

    Raises:
        ValueError: if ``basis`` is already registered.
    """

    def decorate(fn: ConfidenceFn) -> ConfidenceFn:
        if basis in _FORMULAS:
            raise ValueError(f"provenance basis {basis!r} is already registered")
        _FORMULAS[basis] = fn
        _LEVELS[basis] = level
        return fn

    return decorate


def confidence_for(basis: str, inputs: Mapping[str, Any]) -> float:
    """Return the confidence for ``basis`` given its observable ``inputs``.

    Raises:
        UnregisteredBasisError: if ``basis`` has no formula.
        ValueError: if the formula returns a value outside ``[0.0, 1.0]``.
    """
    try:
        fn = _FORMULAS[basis]
    except KeyError:
        raise UnregisteredBasisError(
            f"provenance basis {basis!r} has no registered confidence formula; "
            f"register one with @register_basis({basis!r}, level=...)"
        ) from None
    value = float(fn(inputs))
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"confidence formula for {basis!r} returned {value!r}, outside [0, 1]")
    return value


def level_for(basis: str) -> Provenance:
    """Return the provenance level ``basis`` was registered under."""
    try:
        return _LEVELS[basis]
    except KeyError:
        raise UnregisteredBasisError(f"provenance basis {basis!r} is not registered") from None


def registered_bases() -> frozenset[str]:
    """Every registered basis name."""
    return frozenset(_FORMULAS)


@register_basis("native", level=Provenance.NATIVE)
def _native(_: Mapping[str, Any]) -> float:
    """A venue-reported value is certain by definition."""
    return 1.0


@register_basis("unavailable", level=Provenance.UNAVAILABLE)
def _unavailable(_: Mapping[str, Any]) -> float:
    """A hole carries no information."""
    return 0.0


@register_basis("yahoo_1m_vap", level=Provenance.SYNTHETIC)
def _yahoo_1m_vap(inputs: Mapping[str, Any]) -> float:
    """Volume-at-price depth: confidence saturates with volume-bearing bar count.

    ``n / (n + 390)`` — half-confident at one full regular session, asymptotic
    to 1.0, never reaching it. A profile built from three bars is not a book.
    """
    n = int(inputs["n_volume_bars"])
    if n < 0:
        raise ValueError(f"n_volume_bars must be non-negative, got {n}")
    return n / (n + _RTH_MINUTES)

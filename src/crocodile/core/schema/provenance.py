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
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Final, NamedTuple

from crocodile.core.errors import ProvenanceError

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
    "worst_provenance",
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
    """Obtainable from no configured source. A typed hole, not a zero.

    This is a capability-envelope state, never a record state. A record's required fields
    are the observation itself — a ``Trade`` cannot exist without an id, a price and an
    amount — so an "unavailable record" could only be built by inventing them. A capability
    with nothing to return for an asset class returns an empty result set whose envelope
    carries this level; it never fabricates hole-records. :class:`CapabilityUnavailable` is
    the raised form of the same fact, and a conformance gate asserts no record type defaults
    ``prov`` to this value.
    """


ConfidenceFn = Callable[[Mapping[str, Any]], float]


class UnregisteredBasisError(ProvenanceError, LookupError):
    """A ``prov_basis`` was used that has no registered confidence formula."""


class ConfidenceInputError(ProvenanceError, ValueError):
    """Caller fault: the inputs handed to a confidence formula are missing or malformed."""


class ConfidenceFormulaError(ProvenanceError, RuntimeError):
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

# The two sides a top of book has. Scoped the same way: it is the denominator of
# one basis and a fact about quotes, not about depth in general.
_L1_QUOTED_SIDES: Final[int] = 2


def register_basis(
    basis: str, *, level: Provenance, inputs: Sequence[str], doc: str | None = None
) -> Callable[[ConfidenceFn], ConfidenceFn]:
    """Register the confidence formula for ``basis``.

    Args:
        basis: The ``prov_basis`` name this formula answers for.
        level: The provenance level records built on this basis carry.
        inputs: The data channels the method consumes (``["ohlcv"]``, ``[]``, ...). This is a
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


@register_basis("yahoo_1m_vap", level=Provenance.SYNTHETIC, inputs=["ohlcv"])
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


@register_basis("alpaca_l1", level=Provenance.DERIVED, inputs=["quote"])
def _alpaca_l1(inputs: Mapping[str, Any]) -> float:
    """Top of book reshaped as a depth profile: confidence is how much of the top is quoted.

    ``n_quoted_sides / 2``, where ``n_quoted_sides`` counts the sides Alpaca's latest-quote
    endpoint returned a price for. It is the only thing about this method that varies: the
    source has exactly one level per side and no notion of a requested depth, so two sides is
    the whole of what one call can observe. A one-sided quote describes half a top of book,
    an empty one describes none, and both are states the endpoint really returns.

    :attr:`Provenance.DERIVED` rather than :attr:`Provenance.NATIVE`: every number in the
    profile was reported by the venue, but the venue reported a *quote*. Nothing here is
    modelled — which is what separates it from ``yahoo_1m_vap``, where traded volume stands
    in for resting size — so it is not :attr:`Provenance.SYNTHETIC` either, and
    ``DepthProfile.is_synthetic`` reads ``False`` for it exactly as the equity fork's
    hand-written ``is_synthetic=False`` did.

    Saturating at both sides says the profile is as well sampled as this method can make it,
    not that one price level has become a book. That claim is ``prov``'s, and it stays
    ``DERIVED`` at every value.
    """
    n = _require_int(inputs, "n_quoted_sides")
    if not 0 <= n <= _L1_QUOTED_SIDES:
        raise ConfidenceInputError(
            f"input 'n_quoted_sides' must be between 0 and {_L1_QUOTED_SIDES}, got {n}"
        )
    return n / _L1_QUOTED_SIDES


def _aggregate_of_an_undeclared_stream(_: Mapping[str, Any]) -> float:
    """Shared body for the two bases whose input stream declares no size: 1.0, argued.

    A bar is a *function* of the records handed to the resampler — every print in the
    bucket is in the open, high, low, close and volume, and none of them is estimated.
    What the bar might be missing is records the caller never supplied, and a resampler
    cannot observe a stream it was not given.

    The obvious counter-argument, and why it does not land here. ``high`` and ``low`` are
    order statistics over the supplied sample, so a one-quote minute yields
    ``open == high == low == close`` with ``volume = 0.0`` and still scores 1.0 — which is
    precisely the claim ``yahoo_1m_vap`` refuses for a three-bar profile. The difference is
    the denominator, not the sparsity. ``yahoo_1m_vap`` divides by 390 because a regular US
    session *contains* 390 one-minute bars whether or not any were fetched: the reference
    exists independently of the input, so "how much of it did we get" is answerable. A trade
    or quote stream has no such reference. How many prints a minute should hold is not a
    property of the market, it is whatever traded, and dividing by a number invented to
    stand for it would be the constant this registry exists to refuse, one indirection
    deeper.

    That is an argument for refusing to measure, not a claim that the sparsity does not
    matter — so the sparsity is reported where it can be reported honestly, as a count
    rather than a ratio: the bar carries ``num_trades``, which is the sample size, and for
    a quote bar it is the quote count. ``ohlcv_from_ohlcv`` is the case where the
    denominator *does* exist, because every input bar declares its own width, and that one
    is measured. The contrast is the argument: measure when the input declares a
    denominator, refuse to invent one when it does not.

    The claim that the bar was not reported by a venue is ``prov``'s, and it is the level
    each registration below states — not this number.
    """
    return 1.0


register_basis(
    "ohlcv_from_trades",
    level=Provenance.DERIVED,
    inputs=["trade"],
    doc=(
        "Bars aggregated from trade prints. DERIVED, not NATIVE: the venue reported the "
        "prints, not the bar. Confidence is a constant 1.0, and the argument for it — "
        "including why it is not the claim yahoo_1m_vap refuses — is in "
        "_aggregate_of_an_undeclared_stream."
    ),
)(_aggregate_of_an_undeclared_stream)

register_basis(
    "ohlcv_from_quotes",
    level=Provenance.SYNTHETIC,
    inputs=["quote"],
    doc=(
        "Bars whose prices are quotes. SYNTHETIC rather than DERIVED because a quote is "
        "a different data class from the traded prices a bar reports: nothing here was "
        "ever transacted, and `volume` is a structural 0.0 rather than a measured one — "
        "quotes carry no size that belongs in a bar. Confidence is a constant 1.0 on the "
        "argument in _aggregate_of_an_undeclared_stream; the sample size a one-quote "
        "bucket has is on the record, as num_trades."
    ),
)(_aggregate_of_an_undeclared_stream)


@register_basis("ohlcv_from_ohlcv", level=Provenance.DERIVED, inputs=["ohlcv"])
def _ohlcv_from_ohlcv(inputs: Mapping[str, Any]) -> float:
    """Re-bucketed bars: confidence is how much of the wider bucket the inputs cover.

    ``min(covered_ns / interval_ns, 1.0)``, where ``interval_ns`` is the width of the
    emitted bar and ``covered_ns`` is the summed width of the input bars that fell in it,
    each weighted by its own ``prov_confidence``.

    The denominator is observable, which is the whole reason this basis measures where
    ``ohlcv_from_trades`` does not. The resampler holds ``interval_ns`` from parsing the
    interval it was asked for, and every input bar declares its own ``interval``, so the
    duration a complete bucket would hold is exactly as computable as ``yahoo_1m_vap``'s
    390. It was reported as 1.0 while holding both numbers: a 1d bar built from three 1m
    bars scored 1.0, where ``yahoo_1m_vap`` on the same three scored 0.0077.

    Weighting by the input's own confidence is what keeps the derivation from outranking
    what it derives from. Twenty-four 1h bars each covering half their hour re-bucket into
    a day that is half covered, not a whole one; summing declared widths alone would have
    reported 1.0 for it. The same rule in the other dimension is ``prov``'s: the emitted
    bar carries the worst level among its inputs, so re-bucketing synthetic bars yields
    synthetic ones rather than laundering them into DERIVED.

    Saturating at 1.0 says the bucket is as covered as this method can make it. A caller
    re-bucketing wide bars into narrower ones passes more coverage than the bucket holds,
    and that is a full bucket, not an over-full one.
    """
    covered_ns = _require_int(inputs, "covered_ns")
    interval_ns = _require_int(inputs, "interval_ns")
    if interval_ns <= 0:
        raise ConfidenceInputError(f"input 'interval_ns' must be positive, got {interval_ns}")
    if covered_ns < 0:
        raise ConfidenceInputError(f"input 'covered_ns' must be non-negative, got {covered_ns}")
    return min(covered_ns / interval_ns, 1.0)


@register_basis("scraped_last_price", level=Provenance.SYNTHETIC, inputs=[])
def _scraped_last_price(_: Mapping[str, Any]) -> float:
    """A last price lifted off a web page, reported as a trade: 0.0, by definition.

    ``google_finance`` scrapes one number — the last traded price — and the record it
    fills is a :class:`~crocodile.core.schema.records.Trade`, which cannot exist without a
    quantity. The page publishes no per-print size at any time, for any symbol, so the
    measurement that makes a trade a trade is unsampled at every call. There is nothing
    that varies, which is why this is a constant and why it is declared as one rather than
    left to look like a formula.

    0.0 is the same reading ``unavailable`` carries: no sampling evidence. It is not the
    claim that the price is wrong — the price was really the last trade — and ``prov``
    stays :attr:`Provenance.SYNTHETIC` to say the record's shape is modelled rather than
    observed. What this refuses to be is the 1.0 the header defaults to, which said the
    venue reported a one-share print directly and left a consumer filtering on
    ``prov != NATIVE`` silent.
    """
    return 0.0


_TRUST_ORDER: Final[tuple[Provenance, ...]] = (
    Provenance.NATIVE,
    Provenance.DERIVED,
    Provenance.SYNTHETIC,
    Provenance.UNAVAILABLE,
)
"""The levels from most to least trustworthy, which is what makes "worst" well defined."""


def worst_provenance(levels: Iterable[Provenance]) -> Provenance:
    """Return the least trustworthy level in ``levels``.

    A derivation can never be more trustworthy than its worst input, and the level a basis
    registers is a ceiling rather than a measurement — ``Impl.prov`` says the same thing
    about implementations. Without this, re-bucketing quote-derived bars produced
    ``prov=derived`` over prices that were never transacted, and a caller filtering
    ``WHERE prov != 'synthetic'`` got them back with nothing to notice.

    Raises:
        ValueError: if ``levels`` is empty. There is no worst of nothing, and returning
            NATIVE for it would be the laundering this function exists to stop.
    """
    materialised = list(levels)
    if not materialised:
        raise ValueError("worst_provenance() needs at least one level; there is no worst of none")
    return max(materialised, key=_TRUST_ORDER.index)


@register_basis("book_resample", level=Provenance.DERIVED, inputs=["book_snapshot", "book_delta"])
def _book_resample(inputs: Mapping[str, Any]) -> float:
    """Resampled book depth: confidence is how much of its own timestamp the capture earns.

    ``max(1 - lookahead_ns / interval_ns, 0.0)``, where ``lookahead_ns`` is how far past
    the emitted boundary the newest applied update lies.

    A resampled snapshot claims to be the book at a bucket boundary, but the resampler
    applies the boundary-crossing record *first* and captures afterwards — so the state it
    reports can include updates stamped after the timestamp it carries. That distance is
    the error, it is directly observable at the capture site, and it is the whole of what
    can go wrong with this method: the reconstruction itself is exact, since the engine
    replays absolute levels from a real venue snapshot and raises ``BookGap`` rather than
    guessing across a sequence break.

    ``1.0`` means the newest update landed on the boundary, so the capture describes the
    instant it is stamped with. ``0.0`` means it is a whole interval or more ahead, which
    is what a run of boundaries dragged along by one late record looks like: an interval
    with no updates emits the state that arrives after it, not the state that held during
    it. Between the two the score falls linearly, because the error is a duration and
    nothing about a book makes half an interval of lookahead better than linear.

    A quiet interval is not penalised. If no update arrives between two boundaries the
    lookahead is zero and the book genuinely did not change, so the reconstruction is
    exact and says so — absence of updates is not absence of sampling.

    Saturating at zero says the capture has stopped earning its timestamp, not that the
    book is unknown; ``prov`` stays :attr:`Provenance.DERIVED` at every value.
    """
    lookahead_ns = _require_int(inputs, "lookahead_ns")
    interval_ns = _require_int(inputs, "interval_ns")
    if interval_ns <= 0:
        raise ConfidenceInputError(f"input 'interval_ns' must be positive, got {interval_ns}")
    if lookahead_ns < 0:
        raise ConfidenceInputError(
            f"input 'lookahead_ns' must be non-negative, got {lookahead_ns}; "
            f"a capture cannot precede the record that triggered it"
        )
    return max(1.0 - lookahead_ns / interval_ns, 0.0)

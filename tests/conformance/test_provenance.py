from itertools import pairwise

import pytest

from crocodile.core.schema.provenance import (
    ConfidenceFormulaError,
    ConfidenceInputError,
    Provenance,
    ProvenanceFields,
    UnregisteredBasisError,
    confidence_for,
    describe,
    level_for,
    load_all_bases,
    provenance_fields,
    register_basis,
    registered_bases,
    worst_provenance,
)

_EXPECTED_BASES = frozenset(
    {
        "alpaca_l1",
        "book_resample",
        "native",
        "ohlcv_from_ohlcv",
        "ohlcv_from_quotes",
        "ohlcv_from_trades",
        "scraped_last_price",
        "unavailable",
        "yahoo_1m_vap",
    }
)
_SESSION_BARS = 390


def test_native_basis_is_certain():
    assert confidence_for("native", {}) == 1.0
    assert level_for("native") is Provenance.NATIVE


def test_unavailable_basis_is_zero():
    assert confidence_for("unavailable", {}) == 0.0
    assert level_for("unavailable") is Provenance.UNAVAILABLE


def test_vap_confidence_is_session_coverage():
    counts = [0, 10, 97, 195, 389, _SESSION_BARS, 780, 3900]
    values = [confidence_for("yahoo_1m_vap", {"n_volume_bars": n}) for n in counts]

    assert values[0] == 0.0
    # Monotone non-decreasing: a two-point check would pass a curve that rises then falls.
    assert all(a <= b for a, b in pairwise(values))
    # Coverage has a reading: half a session's bars is half a session covered.
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": 195}) == pytest.approx(0.5)
    # A full session saturates, and stays saturated beyond it.
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": _SESSION_BARS}) == 1.0
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": 3900}) == 1.0


def test_unregistered_basis_is_an_error_not_a_default():
    with pytest.raises(UnregisteredBasisError):
        confidence_for("something_invented", {})


def test_level_for_unregistered_basis_is_an_error():
    with pytest.raises(UnregisteredBasisError):
        level_for("something_invented")


def test_duplicate_registration_is_rejected():
    def formula(_):
        """A duplicate registration of an already-registered basis."""
        return 1.0

    with pytest.raises(ValueError):
        register_basis("native", level=Provenance.NATIVE, inputs=[])(formula)


def test_missing_docstring_is_rejected():
    with pytest.raises(ValueError, match="docstring"):
        register_basis("_test_no_doc", level=Provenance.DERIVED, inputs=[])(lambda _: 0.5)


def test_the_package_imports_under_docstring_stripping():
    """-OO strips docstrings; the mandatory-docstring rule must not become a crash.

    This is a subprocess test on purpose: the running interpreter's optimize flag
    cannot be changed, and monkeypatching sys.flags would test the mock, not the
    behaviour that breaks a container build.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-OO", "-c", "import crocodile.core.schema.provenance"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_every_registered_basis_is_documented():
    load_all_bases()
    for basis in registered_bases():
        assert describe(basis).strip(), f"basis {basis!r} carries no docstring"


@pytest.mark.parametrize("bad", [1.5, -0.5])
def test_formula_returning_out_of_range_is_rejected(bad):
    def formula(_):
        """A deliberately broken formula, for the range guard."""
        return bad

    register_basis("_test_bad_range", level=Provenance.DERIVED, inputs=[])(formula)
    with pytest.raises(ConfidenceFormulaError):
        confidence_for("_test_bad_range", {})


def test_formula_raising_unexpectedly_is_a_formula_error():
    def formula(_):
        """A formula that blows up, for the wrapping guard."""
        raise ZeroDivisionError("boom")

    register_basis("_test_raises", level=Provenance.DERIVED, inputs=[])(formula)
    with pytest.raises(ConfidenceFormulaError) as excinfo:
        confidence_for("_test_raises", {})
    assert "_test_raises" in str(excinfo.value)


def test_missing_input_is_a_caller_error_not_a_formula_error():
    with pytest.raises(ConfidenceInputError):
        confidence_for("yahoo_1m_vap", {})


def test_bare_keyerror_from_a_formula_becomes_an_input_error():
    def formula(inputs):
        """Indexes an input directly, without the strict helper."""
        return float(inputs["absent"])

    register_basis("_test_bare_key", level=Provenance.DERIVED, inputs=[])(formula)
    with pytest.raises(ConfidenceInputError):
        confidence_for("_test_bare_key", {})


@pytest.mark.parametrize("bad", [3.9, "390", True, None])
def test_vap_rejects_a_non_int_count(bad):
    with pytest.raises(ConfidenceInputError):
        confidence_for("yahoo_1m_vap", {"n_volume_bars": bad})


def test_vap_rejects_a_negative_count():
    with pytest.raises(ConfidenceInputError):
        confidence_for("yahoo_1m_vap", {"n_volume_bars": -1})


def test_registered_bases_is_a_frozenset():
    bases = registered_bases()
    assert isinstance(bases, frozenset)
    assert _EXPECTED_BASES <= bases

    def formula(_):
        """A basis registered after the snapshot was taken."""
        return 1.0

    register_basis("_test_later", level=Provenance.DERIVED, inputs=[])(formula)
    assert "_test_later" not in bases, "an earlier result must not track later registrations"
    assert "_test_later" in registered_bases()


def test_provenance_fields_is_the_blessed_tail():
    tail = provenance_fields("yahoo_1m_vap", {"n_volume_bars": 195})
    assert isinstance(tail, ProvenanceFields)
    assert tail.prov is Provenance.SYNTHETIC
    assert tail.prov_basis == "yahoo_1m_vap"
    assert tail.prov_confidence == pytest.approx(0.5)
    # ``ohlcv``, not ``bar``: the producer consumes canonical OHLCV records written to
    # ``channel=ohlcv/``, and declaring a retired channel made
    # ``WHERE list_contains(prov_inputs, 'ohlcv')`` return zero synthetic depth profiles.
    assert tail.prov_inputs == ["ohlcv"]

    tail.prov_inputs.append("mutated")
    assert provenance_fields("yahoo_1m_vap", {"n_volume_bars": 195}).prov_inputs == ["ohlcv"]


def test_provenance_fields_defaults_inputs_for_input_free_bases():
    tail = provenance_fields("native")
    assert tail.prov is Provenance.NATIVE
    assert tail.prov_confidence == 1.0
    assert tail.prov_inputs == []


def test_provenance_fields_rejects_an_unregistered_basis():
    with pytest.raises(UnregisteredBasisError):
        provenance_fields("something_invented")


def test_describe_returns_the_formula_docstring():
    assert "coverage" in describe("yahoo_1m_vap")
    with pytest.raises(UnregisteredBasisError):
        describe("something_invented")


def test_explicit_doc_is_preferred_over_the_docstring():
    def formula(_):
        """The docstring, which an explicit doc= must win over."""
        return 1.0

    register_basis(
        "_test_explicit_doc",
        level=Provenance.DERIVED,
        inputs=[],
        doc="Survives -OO, which strips docstrings.",
    )(formula)
    assert describe("_test_explicit_doc") == "Survives -OO, which strips docstrings."


def test_load_all_bases_is_idempotent_and_survives_bad_imports():
    load_all_bases()
    assert _EXPECTED_BASES <= registered_bases()
    load_all_bases()
    assert _EXPECTED_BASES <= registered_bases()


# ---------------------------------------------------------------------------
# book_resample — the basis a reconstructed BookSnapshot rests on
# ---------------------------------------------------------------------------


def test_book_resample_is_certain_because_a_capture_cannot_contain_the_future():
    """Migrated from ``test_book_resample_scores_a_capture_that_earns_its_timestamp``.

    Same assertion, different reason, and the reason is the whole change. It used to be
    the ``lookahead_ns = 0`` corner of a formula; the two book resamplers have since
    collapsed onto the ordering that flushes every boundary below a record *before*
    applying it, so zero lookahead is the only state a capture can be in and 1.0 is the
    only answer the basis has. It is declared a constant in ``CONSTANT_BY_DEFINITION``
    with the argument, which is what keeps Gate 3c green for the right reason.
    """
    assert confidence_for("book_resample", {}) == 1.0
    assert level_for("book_resample") is Provenance.DERIVED
    assert provenance_fields("book_resample").prov_inputs == ["book_snapshot", "book_delta"]


def test_book_resample_no_longer_falls_off_because_there_is_no_lookahead_to_score():
    """Migrated from ``test_book_resample_falls_off_linearly_and_saturates_at_zero``.

    The same three input maps, and the answers they used to produce are worth writing
    down rather than deleting: 250ns into a 1µs bucket scored 0.75, a full bucket scored
    0.0, and 90 buckets of drag — a run of boundaries pulled along by one late record —
    also scored 0.0 and was emitted anyway. Every one of those states was reachable only
    because the resampler applied before it emitted. None of them is reachable now, so
    the ratio that described them is inert: a caller still passing the old measurement
    map gets the constant.

    That inertness is the point of the test. If the ordering is ever reverted, this stays
    green while the lake silently refills with biased bars — which is precisely why the
    refusal moved to the capture site instead, see below.
    """
    assert confidence_for("book_resample", {"lookahead_ns": 250, "interval_ns": 1_000}) == 1.0
    assert confidence_for("book_resample", {"lookahead_ns": 1_000, "interval_ns": 1_000}) == 1.0
    assert confidence_for("book_resample", {"lookahead_ns": 90_000, "interval_ns": 1_000}) == 1.0


def test_the_book_resample_refusal_moved_from_the_formula_to_the_capture_site():
    """Migrated from ``test_book_resample_refuses_inputs_that_could_not_have_been_measured``.

    Its subject was that a lookahead nobody could have measured is refused rather than
    turned into a plausible-looking number, and that subject outlived the formula that
    used to enforce it. The registry cannot enforce it any more — with no inputs to read
    there is nothing to validate, so the three maps that used to raise now return the
    constant. The refusal lives where the quantity does: ``_capture_snapshot`` will not
    build a ``BookSnapshot`` stamped at a boundary the book has already moved past.

    Both halves are asserted here, because "we deleted the validation" and "we moved the
    validation" look identical from the registry alone.
    """
    from crocodile.core.errors import ProvenanceError
    from crocodile.core.replay.orderbook import OrderBook
    from crocodile.core.resample.book import _capture_snapshot
    from crocodile.core.schema.enums import AssetClass
    from crocodile.core.schema.records import BookSnapshot

    assert confidence_for("book_resample", {"lookahead_ns": -1, "interval_ns": 1_000}) == 1.0
    assert confidence_for("book_resample", {"lookahead_ns": 0, "interval_ns": 0}) == 1.0
    assert confidence_for("book_resample", {"lookahead_ns": 0}) == 1.0

    snapshot = BookSnapshot(
        source="test",
        symbol="test:SYM",
        symbol_raw="SYM",
        source_ts=None,
        local_ts=0,
        asset_class=AssetClass.CRYPTO,
        bids=[(100.0, 5.0)],
        asks=[(101.0, 4.0)],
        depth=2,
    )
    book = OrderBook()
    book.apply(snapshot)

    with pytest.raises(ProvenanceError, match="lookahead"):
        _capture_snapshot(book, snapshot, 1_000, 1_250, None)


# ---------------------------------------------------------------------------
# alpaca_l1 — a top of book reshaped into a depth profile
# ---------------------------------------------------------------------------


def test_alpaca_l1_scores_the_sides_the_venue_actually_quoted():
    """The whole range of the formula, which is the whole range of the endpoint.

    A latest-quote call returns two sides, one, or none, and there is no requested depth
    to compare against — so the two sides are the entire observable. The equity fork
    wrote no confidence at all here, which meant the header default: 1.0, for a one-sided
    quote as readily as for a two-sided one.
    """
    assert confidence_for("alpaca_l1", {"n_quoted_sides": 0}) == 0.0
    assert confidence_for("alpaca_l1", {"n_quoted_sides": 1}) == 0.5
    assert confidence_for("alpaca_l1", {"n_quoted_sides": 2}) == 1.0


def test_alpaca_l1_is_derived_not_native_and_not_synthetic():
    """The level is the claim, and it is a different claim from the number.

    DERIVED: every price and size came from the venue, but the venue reported a quote,
    not a depth profile. Not SYNTHETIC, because nothing is modelled — which is what keeps
    ``DepthProfile.is_synthetic`` False for a real L1 snapshot, as the fork's hand-written
    ``is_synthetic=False`` had it.
    """
    assert level_for("alpaca_l1") is Provenance.DERIVED
    tail = provenance_fields("alpaca_l1", {"n_quoted_sides": 2})
    assert tail.prov is Provenance.DERIVED
    assert tail.prov_inputs == ["quote"]


def test_alpaca_l1_refuses_a_side_count_that_could_not_have_been_measured():
    with pytest.raises(ConfidenceInputError, match="n_quoted_sides"):
        confidence_for("alpaca_l1", {"n_quoted_sides": 3})
    with pytest.raises(ConfidenceInputError, match="n_quoted_sides"):
        confidence_for("alpaca_l1", {"n_quoted_sides": -1})
    with pytest.raises(ConfidenceInputError, match="n_quoted_sides"):
        confidence_for("alpaca_l1", {})


# ---------------------------------------------------------------------------
# The three bar aggregations
# ---------------------------------------------------------------------------


def test_a_quote_bar_is_synthetic_and_a_trade_bar_is_not():
    """The three aggregations differ in level, which is where the difference lives.

    Trades and narrower bars aggregate into a wider bar exactly; quotes do not aggregate
    into a bar at all — they stand in for traded prices, and the ``volume`` such a bar
    reports is a structural zero. Confidence cannot express that difference and does not
    try to; ``prov`` does.
    """
    assert level_for("ohlcv_from_trades") is Provenance.DERIVED
    assert level_for("ohlcv_from_ohlcv") is Provenance.DERIVED
    assert level_for("ohlcv_from_quotes") is Provenance.SYNTHETIC

    assert provenance_fields("ohlcv_from_trades").prov_inputs == ["trade"]
    assert provenance_fields("ohlcv_from_quotes").prov_inputs == ["quote"]
    assert (
        provenance_fields("ohlcv_from_ohlcv", _FULL_BUCKET).prov_inputs == ["ohlcv"]
    )


def test_none_of_the_bar_aggregations_claims_a_venue_reported_it():
    """The failure they were all one edit away from: the header default says NATIVE."""
    for basis in ("ohlcv_from_trades", "ohlcv_from_ohlcv", "ohlcv_from_quotes"):
        assert level_for(basis) is not Provenance.NATIVE
        assert describe(basis).strip(), f"{basis} has to argue for its number"


_HOUR_NS = 3_600_000_000_000
_MINUTE_NS = 60_000_000_000
_FULL_BUCKET = {
    "covered_ns": _HOUR_NS,
    "sampled_ns": _HOUR_NS,
    "tradeable_ns": _HOUR_NS,
}


def test_re_bucketing_bars_measures_coverage_instead_of_asserting_it():
    """C1: the resampler holds the denominator, so 1.0 was a choice, not a limit.

    ``resample_bars_to_bars`` parses the interval it was asked for and every input bar
    declares its own width, so the duration a complete bucket holds is exactly as
    computable as ``yahoo_1m_vap``'s 390 — and a 1d bar built from three 1m bars used to
    score 1.0 while the same three bars scored 0.0077 through the vap formula.
    """
    assert confidence_for("ohlcv_from_ohlcv", _FULL_BUCKET) == 1.0
    three_minutes = {
        "covered_ns": 3 * _MINUTE_NS,
        "sampled_ns": 3 * _MINUTE_NS,
        "tradeable_ns": _HOUR_NS,
    }
    assert confidence_for("ohlcv_from_ohlcv", three_minutes) == pytest.approx(0.05 * 0.05)
    empty = {"covered_ns": 0, "sampled_ns": 0, "tradeable_ns": _HOUR_NS}
    assert confidence_for("ohlcv_from_ohlcv", empty) == 0.0
    # Re-bucketing wide bars into narrow ones is a full bucket, not an over-full one.
    overfull = {
        "covered_ns": 4 * _HOUR_NS,
        "sampled_ns": 4 * _HOUR_NS,
        "tradeable_ns": _HOUR_NS,
    }
    assert confidence_for("ohlcv_from_ohlcv", overfull) == 1.0


def test_a_complete_us_session_re_bucketed_to_a_day_is_fully_covered():
    """I1: the denominator was wall-clock, so a complete session scored 0.2708.

    390 one-minute bars *are* a regular US trading day. Dividing them by a 1440-minute
    calendar day made every complete equity daily bar fail a ``prov_confidence >= 0.5``
    filter, while ``yahoo_1m_vap`` in the same registry already treats 390 as the
    session reference for this market.
    """
    session_ns = _SESSION_BARS * _MINUTE_NS
    complete = {
        "covered_ns": session_ns,
        "sampled_ns": session_ns,
        "tradeable_ns": session_ns,
    }

    assert confidence_for("ohlcv_from_ohlcv", complete) == 1.0
    assert session_ns / (24 * 60 * _MINUTE_NS) == pytest.approx(0.2708, abs=1e-4)


def test_coverage_and_adequacy_are_not_interchangeable():
    """I1: 390 bars at 0.5 and 195 bars at 1.0 used to return the identical number.

    They are different states. Half-sampled inputs across a whole session still observed
    every minute's own high and low; a missing half-session observed nothing there, so
    the day's high may be absent from the bar entirely. A gap fails both terms — extent
    and adequacy — where dilution fails only the second.
    """
    session_ns = _SESSION_BARS * _MINUTE_NS
    diluted = {
        "covered_ns": session_ns,
        "sampled_ns": session_ns // 2,
        "tradeable_ns": session_ns,
    }
    truncated = {
        "covered_ns": session_ns // 2,
        "sampled_ns": session_ns // 2,
        "tradeable_ns": session_ns,
    }

    assert confidence_for("ohlcv_from_ohlcv", diluted) == pytest.approx(0.5)
    assert confidence_for("ohlcv_from_ohlcv", truncated) == pytest.approx(0.25)


def test_re_bucketing_rejects_a_bucket_with_no_width():
    with pytest.raises(ConfidenceInputError):
        confidence_for(
            "ohlcv_from_ohlcv", {"covered_ns": 1, "sampled_ns": 1, "tradeable_ns": 0}
        )
    with pytest.raises(ConfidenceInputError):
        confidence_for(
            "ohlcv_from_ohlcv", {"covered_ns": -1, "sampled_ns": 0, "tradeable_ns": 1}
        )


def test_re_bucketing_rejects_an_instant_sampled_better_than_it_is_covered():
    """``sampled_ns`` weights the union ``covered_ns`` measures; it cannot exceed it."""
    with pytest.raises(ConfidenceInputError, match="exceeds"):
        confidence_for(
            "ohlcv_from_ohlcv",
            {"covered_ns": _MINUTE_NS, "sampled_ns": _HOUR_NS, "tradeable_ns": _HOUR_NS},
        )


def test_worst_provenance_orders_the_levels_by_trust():
    assert worst_provenance([Provenance.NATIVE]) is Provenance.NATIVE
    assert worst_provenance([Provenance.NATIVE, Provenance.DERIVED]) is Provenance.DERIVED
    assert (
        worst_provenance([Provenance.DERIVED, Provenance.SYNTHETIC, Provenance.NATIVE])
        is Provenance.SYNTHETIC
    )
    assert (
        worst_provenance([Provenance.SYNTHETIC, Provenance.UNAVAILABLE])
        is Provenance.UNAVAILABLE
    )


def test_worst_provenance_refuses_an_empty_run():
    """Returning NATIVE for "no inputs" would be the laundering it exists to stop."""
    with pytest.raises(ValueError, match="no worst of none"):
        worst_provenance([])


def test_a_scraped_last_price_is_not_a_venue_reported_print():
    """C7: every google_finance last price shipped prov=native, prov_confidence=1.0."""
    assert level_for("scraped_last_price") is Provenance.SYNTHETIC
    assert confidence_for("scraped_last_price", {}) == 0.0
    assert "no per-print size" in describe("scraped_last_price")


_CALLER_SUPPLIED_CAPABILITIES = frozenset(
    {
        "chaos-score",
        "funding-predict",
        "gas-vol",
        "label-transfers",
        "lending-stress",
        "mev-sandwich",
        "peg-deviation",
        "smart-money",
    }
)
"""The eight capabilities whose numbers arrive in ``params`` rather than out of the lake.

Named in ``_caller_supplied``'s own docstring, one file over, as the reason it exists. The
set is pinned here because a basis is only worth registering if something declares it, and
this one was registered, argued at length, and then declared by nobody: all eight kept
``basis="native"`` and ``crocodile.capabilities.ops`` kept the comment asking for the basis
that by then existed. ``WHERE prov_basis = 'caller_supplied'`` — the query the registration
says is the whole point — returned nothing.
"""


def test_the_capabilities_that_compute_over_caller_supplied_numbers_declare_it():
    """A registered basis nothing declares is a decision that was made and not applied.

    What shipped, live, from ``chaos-score``::

        {"prov": "synthetic", "prov_basis": "native",
         "method": "A venue-reported value is certain by definition. …"}

    ``native`` means *a venue reported this*, and the four readings behind that score were
    typed in by the caller. The sentence the basis carries contradicts the request that
    produced it, in one line, on a surface that publishes both.
    """
    from crocodile.capabilities import load_all
    from crocodile.core.capability import REGISTRY

    load_all()

    wrong = sorted(
        f"{name}/{asset_class.value}={impl.basis}"
        for name in _CALLER_SUPPLIED_CAPABILITIES
        for asset_class, impl in REGISTRY[name].impls.items()
        if impl.basis != "caller_supplied"
    )
    assert not wrong, (
        f"{wrong} compute over numbers the caller handed in and claim a venue reported "
        f"them. `caller_supplied` is registered for exactly this and says the true, narrower "
        f"thing: a pure function is exact over what it was given, and the sampling story of "
        f"the inputs belongs to whoever produced them."
    )


def test_no_capability_reading_the_lake_claims_its_inputs_were_handed_in():
    """The other direction, so the basis stays a filter rather than a habit.

    ``caller_supplied`` abstains from grading inputs this engine did not observe. Declaring
    it where the lake *is* the input throws away a real ``native`` reading and makes the
    query in the registration docstring stop separating anything.
    """
    from crocodile.capabilities import load_all
    from crocodile.core.capability import REGISTRY

    load_all()

    unexpected = sorted(
        name
        for name, cap in REGISTRY.items()
        if any(impl.basis == "caller_supplied" for impl in cap.impls.values())
        and name not in _CALLER_SUPPLIED_CAPABILITIES
    )
    assert not unexpected, (
        f"{unexpected} declare caller_supplied and are not on the list that argues for it; "
        f"add them there with the argument, or declare what their inputs really rest on"
    )

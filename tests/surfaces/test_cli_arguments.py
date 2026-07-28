"""How a command line hands over a value too long to type, and what it says on stderr.

Two things the legacy CLI did that the projection stopped doing, both of them about the
shape of a shell rather than about any one capability:

* ``crypcodile query "SELECT 1"`` took its statement positionally, and
  ``echo "SELECT …" | crypcodile query`` took it off a pipe. The projection synthesises
  keyword-only options exclusively, so both answered ``Missing option '--sql'`` and exit 2.
* Every successful non-``NATIVE`` call wrote a ``DERIVED — …`` banner to stderr, which is
  most of the registry, so a script that asserts empty stderr broke on a working command
  and an operator learned to ignore the line that matters.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from typer.testing import CliRunner

from crocodile.surfaces import cli, dispatch
from tests.surfaces.conftest import SYMBOL

# ---------------------------------------------------------------------------
# The value that does not fit on a command line
# ---------------------------------------------------------------------------


def test_a_single_required_parameter_can_be_given_positionally(lake: pathlib.Path) -> None:
    """``crocodile query "SELECT 1"`` is how every SQL tool is invoked."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["query", "SELECT 1 AS one", "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "one" in result.output


def test_the_option_still_works_and_wins(lake: pathlib.Path) -> None:
    """The positional is added, not substituted: no existing invocation changes meaning."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["query", "SELECT 1 AS ignored", "--sql", "SELECT 2 AS chosen", "--asset-class",
         "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "chosen" in result.output
    assert "ignored" not in result.output


def test_a_piped_value_is_read_when_nothing_else_supplied_it(lake: pathlib.Path) -> None:
    """``echo "SELECT …" | crocodile query`` — how a shell hands over a document."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["query", "--asset-class", "crypto", "--data-dir", str(lake)],
        input="SELECT 3 AS piped\n",
    )
    assert result.exit_code == 0, result.output
    assert "piped" in result.output


def test_a_pipe_that_carries_nothing_is_a_missing_argument_and_not_a_hang(
    lake: pathlib.Path,
) -> None:
    result = CliRunner().invoke(
        cli.build_app(),
        ["query", "--asset-class", "crypto", "--data-dir", str(lake)],
        input="",
    )
    assert result.exit_code == 1, result.output
    assert "sql" in result.output


def test_the_positional_is_offered_only_where_it_is_unambiguous() -> None:
    """One required parameter, or none: two would make the order a thing to memorise.

    ``indicators`` requires a symbol and both time bounds, and
    ``crocodile indicators deribit:BTC-PERPETUAL 1700000000000000000 …`` is a line nobody
    can read back. ``mev-sandwich`` requires an array of objects, which is a document rather
    than a word.
    """
    assert cli.positional_field(dispatch.resolve("query")) == "sql"
    assert cli.positional_field(dispatch.resolve("search")) == "q"
    assert cli.positional_field(dispatch.resolve("indicators")) is None
    assert cli.positional_field(dispatch.resolve("catalog")) is None
    assert cli.positional_field(dispatch.resolve("mev-sandwich")) is None


def test_a_positional_sequence_is_split_the_way_the_option_is(lake: pathlib.Path) -> None:
    """``resolve-symbols`` took a comma-separated positional on the legacy CLI."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["resolve-symbols", f"{SYMBOL},{SYMBOL}", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output


def test_the_help_text_names_the_positional(lake: pathlib.Path) -> None:
    result = CliRunner().invoke(cli.build_app(), ["query", "--help"])
    assert result.exit_code == 0, result.output
    assert "SQL" in result.output


# ---------------------------------------------------------------------------
# What goes on stderr
# ---------------------------------------------------------------------------


def test_a_computed_answer_does_not_print_a_banner(lake: pathlib.Path) -> None:
    """``indicators`` is DERIVED, which is what an indicator *is*.

    A banner on every successful call is a banner nobody reads, and it is the same channel
    the SYNTHETIC one has to arrive on. The caller asked for ``--indicator rsi`` by name;
    being told that an RSI was computed is not news.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["indicators", "--symbol", SYMBOL, "--indicator", "rsi", "--start-ns", "0",
         "--end-ns", "9" * 18, "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert result.stderr == "", result.stderr


def test_a_modelled_answer_still_announces_itself(lake: pathlib.Path) -> None:
    """A chaos score is an index no market publishes. This is the line that must survive.

    ``chaos-score`` is chosen because it is ``SYNTHETIC`` by declaration and computes over
    four numbers the caller supplies, so the assertion is about the banner rather than about
    whichever data source a modelled implementation happens to read this month. The set
    comparison below is why it keeps working after Phase 3: the equity half is SYNTHETIC too,
    so the banner is owed whichever market answers.

    ``--asset-class`` is named because it now has to be. This capability has no symbol
    parameter, so ``resolve_asset_class`` has nothing to infer from, and until its equity
    half landed the answer fell out of there being only one implementation. Two
    implementations and no symbol is exactly the ambiguity that function refuses rather than
    guesses at — defaulting to crypto would send an equity request into the crypto
    composite, which answers plausibly and with the wrong terms.
    """
    from crocodile.core.schema.provenance import Provenance

    cap = dispatch.resolve("chaos-score")
    assert {impl.prov for impl in cap.impls.values()} == {Provenance.SYNTHETIC}

    result = CliRunner().invoke(
        cli.build_app(),
        ["chaos-score", "--volatility", "0.4", "--stablecoin-deviation", "0.01",
         "--orderbook-imbalance", "0.2", "--sequencer-delay", "1.5",
         "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "SYNTHETIC" in result.stderr
    assert "chaos-score" in result.stderr


def test_a_symmetric_capability_with_no_symbol_asks_which_market_it_serves(
    lake: pathlib.Path,
) -> None:
    """The other side of the line above, kept so the refusal is a behaviour and not a gap.

    ``chaos-score`` gained an equity half in Phase 3 and has no symbol to resolve by, which
    makes it the one capability where omitting ``--asset-class`` cannot be answered. The
    failure has to be a refusal that names the option: silently picking one market would give
    a caller a number built from four terms they did not mean, on the same scale and under
    the same name as the one they did.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["chaos-score", "--volatility", "0.4", "--stablecoin-deviation", "0.01",
         "--orderbook-imbalance", "0.2", "--sequencer-delay", "1.5",
         "--data-dir", str(lake)],
    )
    assert result.exit_code == 1
    assert "cannot tell which market" in result.stderr
    assert "crypto" in result.stderr and "equity" in result.stderr


def test_the_network_surfaces_still_carry_every_warning(lake: pathlib.Path) -> None:
    """The banner is narrowed on stderr only. In a payload it is a field, not noise.

    A REST or MCP caller reads ``warning`` when it looks, and a machine reader has no
    equivalent of learning to ignore a line — so ``warning_for`` keeps announcing every
    implementation whose ceiling is not NATIVE.
    """
    from crocodile.core.capability import AssetClass
    from crocodile.core.config import Settings
    from crocodile.core.store.catalog import Catalog

    indicators = dispatch.resolve("indicators")
    with Catalog(lake) as catalog:
        ctx = dispatch.build_context(
            catalog,
            AssetClass.CRYPTO,
            settings=Settings(data_dir=lake),
            readonly=True,
            row_limit=dispatch.NETWORK_ROW_LIMIT,
        )
        assert dispatch.warning_for(indicators, ctx).startswith("DERIVED")
        assert dispatch.banner_for(indicators, ctx) is None


def test_stdout_stays_machine_readable_while_a_banner_is_printed(lake: pathlib.Path) -> None:
    """The reason the banner is on stderr at all: ``| jq`` must keep working.

    Pinned on the crypto half, which returns the index alone. The equity half returns an
    object carrying the same index plus the weight each term received, because its weights
    depend on how many readings were finite — see ``crocodile.equity.analytics.chaos``. Both
    are JSON on stdout and a banner on stderr, which is the property this test is about; the
    scalar is asserted here rather than the shape, so the market has to be named.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["chaos-score", "--volatility", "0.4", "--stablecoin-deviation", "0.01",
         "--orderbook-imbalance", "0.2", "--sequencer-delay", "1.5",
         "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.stderr, "this capability is modelled and must say so"
    assert 0.0 <= json.loads(result.stdout) <= 100.0


def test_the_equity_composite_reaches_stdout_as_json_with_its_weights(
    lake: pathlib.Path,
) -> None:
    """The equity half's answer has to survive the projection, not only the unit test.

    A capability returning a mapping under ``ReturnKind.SCALAR`` is the shape
    ``risk-reversal`` already ships, and this asserts the CLI renders it the same way: the
    banner on stderr, one JSON document on stdout, weights included — which is the whole
    reason the equity half returns an object rather than a number.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["chaos-score", "--volatility", "0.4", "--stablecoin-deviation", "0.01",
         "--orderbook-imbalance", "0.2", "--sequencer-delay", "1.5",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "SYNTHETIC" in result.stderr
    payload = json.loads(result.stdout)
    assert 0.0 <= payload["chaos_score"] <= 100.0
    assert sum(term["weight"] for term in payload["terms"].values()) == pytest.approx(1.0)


def test_a_dropped_equity_term_crosses_the_wire_as_null_rather_than_as_a_nan_token(
    lake: pathlib.Path,
) -> None:
    """The equity composite's "no reading" state has to survive the projection's encoder.

    JSON has no NaN, and ``json.dumps`` emits a bare ``NaN`` token that most clients reject
    as malformed — which is what the projection's ``_jsonable`` walk exists to stop. A term
    that was excluded therefore reaches a caller as ``null`` with a zero weight, and the
    three that were read carry a third of the index each. Asserting it here rather than only
    in the unit test is the difference between the behaviour existing and the behaviour being
    reachable: a caller who cannot parse the answer has not been told anything.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["chaos-score", "--volatility", "nan", "--stablecoin-deviation", "0.01",
         "--orderbook-imbalance", "1.0", "--sequencer-delay", "5.0",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "NaN" not in result.stdout
    payload = json.loads(result.stdout)
    volatility = payload["terms"]["volatility"]
    assert volatility["supplied"] is None
    assert volatility["normalised"] is None
    assert volatility["weight"] == 0.0
    assert payload["terms"]["sequencer_delay"]["weight"] == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# The parameter whose units are not in its type
# ---------------------------------------------------------------------------


_RATE_CAPABILITIES = ["iv-surface", "term-structure", "vol-skew", "risk-reversal"]


@pytest.mark.parametrize("name", _RATE_CAPABILITIES)
def test_the_options_rate_says_which_compounding_convention_it_wants(name: str) -> None:
    """``rate: float = 0.0`` is the same annotation for two incompatible conventions.

    M1's options family is continuous — ``equity/analytics/options.py`` discounts with
    ``exp(-r*t)`` — and M5's carry family is simple, which is also the convention of the
    only rate this engine *publishes*: the Treasury par yield emitted as ``risk_free_rate``.
    Both are decimal fractions, so there is no percent bug to catch; the hazard is an
    operator reading one out of ``spot-future-basis`` and passing it into ``--rate``, which
    is a one-line pipeline that answers plausibly and wrongly — 15 bp of discount factor at
    a year, 62 bp at two.

    The generated ``--rate`` shipped with no help text at all, so the field itself now
    carries the convention and the conversion.
    """
    schema = dispatch.params_schema(dispatch.resolve(name))
    description = schema["properties"]["rate"]["description"]
    assert "CONTINUOUSLY" in description
    assert "risk_free_rate" in description
    assert "ln(1 + r_simple)" in description


@pytest.mark.parametrize("name", _RATE_CAPABILITIES)
def test_that_convention_reaches_the_command_line(name: str) -> None:
    """A description in a schema nobody renders is a docstring with extra steps.

    All three projections read ``params_schema``, so pinning the CLI — the surface that
    would silently fall back to ``rate (float)`` — pins that the description is projected
    rather than merely declared.
    """
    result = CliRunner().invoke(cli.build_app(), [name, "--help"])
    assert result.exit_code == 0, result.output
    rendered = " ".join(result.output.split())
    assert "CONTINUOUSLY compounded" in rendered
    assert "ln(1 + r_simple)" in rendered

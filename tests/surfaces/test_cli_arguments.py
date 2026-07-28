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
    """Equity slippage rests on a modelled book. This is the line that must survive."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["slippage", "--symbol", SYMBOL, "--side", "buy", "--size", "1.0",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "SYNTHETIC" in result.stderr
    assert "yahoo_1m_vap" in result.stderr


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
    """The reason the banner is on stderr at all: ``| jq`` must keep working."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["slippage", "--symbol", SYMBOL, "--side", "buy", "--size", "1.0",
         "--asset-class", "equity", "--data-dir", str(lake)],
    )
    assert json.loads(result.stdout)["expected_price"] > 0

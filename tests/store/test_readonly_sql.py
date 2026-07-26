"""Tests for the read-only SQL guard promoted from the equity fork.

The guard is keyword-level, not a parse, so these tests pin the exact shape of
that approximation — including where it over-rejects — rather than the shape a
real SQL lexer would give. Anything asserted here was verified against the
equity original at ``src/_incoming_stockodile/src/stockodile/store/catalog.py``.
"""

from __future__ import annotations

import pathlib

import pytest

from crocodile.core.store.catalog import Catalog, assert_readonly_sql

# ---------------------------------------------------------------------------
# Accepted statements
# ---------------------------------------------------------------------------


def test_plain_select_passes() -> None:
    """A plain SELECT is the canonical accepted case."""
    assert_readonly_sql("SELECT 1 AS x")


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",  # verb matching is case-insensitive
        "  SELECT 1  ",  # surrounding whitespace is stripped first
        "SELECT 1;",  # one trailing semicolon is not "multi-statement"
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "DESCRIBE trade",
        "SHOW TABLES",
        "EXPLAIN SELECT 1",
    ],
)
def test_readonly_verbs_pass(sql: str) -> None:
    """Every verb on the allowlist is accepted, in any case, semicolon or not."""
    assert_readonly_sql(sql)


def test_relative_read_parquet_passes() -> None:
    """``read_parquet`` is only blocked on an absolute path.

    The catalog builds its own views out of ``read_parquet``, so a blanket ban
    would forbid the lake's normal access pattern; only a caller reaching for
    the filesystem root is refused.
    """
    assert_readonly_sql("SELECT * FROM read_parquet('rel/path.parquet')")


# ---------------------------------------------------------------------------
# Rejected statements
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM trade",
        "DROP TABLE trade",
        "INSERT INTO trade VALUES (1)",
        "UPDATE trade SET price = 1",
        "TRUNCATE trade",
        "ALTER TABLE trade ADD COLUMN x INT",
        "CREATE OR REPLACE TABLE trade AS SELECT 1",
        "COPY (SELECT 1) TO '/tmp/x.csv'",
        "ATTACH '/tmp/other.db'",
        "INSTALL httpfs",
        "PRAGMA database_list",
        "GRANT SELECT ON trade TO bob",
    ],
)
def test_mutating_and_external_sql_rejected(sql: str) -> None:
    """Mutating statements and extension/attach verbs never reach DuckDB."""
    with pytest.raises(ValueError):
        assert_readonly_sql(sql)


def test_absolute_path_read_parquet_rejected() -> None:
    """A caller-named absolute path is the file-read escape the guard exists for."""
    with pytest.raises(ValueError, match="disallowed keywords or external readers"):
        assert_readonly_sql("SELECT * FROM read_parquet('/etc/passwd')")


def test_multi_statement_rejected() -> None:
    """A second statement smuggled in behind a SELECT is refused."""
    with pytest.raises(ValueError, match="Multi-statement"):
        assert_readonly_sql("SELECT 1; DROP TABLE trade")


@pytest.mark.parametrize("sql", ["", "   ", ";", "  ;  "])
def test_empty_sql_rejected(sql: str) -> None:
    """Empty input — including a bare semicolon — is rejected, not passed through."""
    with pytest.raises(ValueError, match="Empty SQL"):
        assert_readonly_sql(sql)


def test_non_readonly_verb_rejected() -> None:
    """A statement starting with an unlisted verb is refused on the verb check."""
    with pytest.raises(ValueError, match="Only SELECT/WITH/DESCRIBE/SHOW/EXPLAIN"):
        assert_readonly_sql("VACUUM")


# ---------------------------------------------------------------------------
# Where the keyword-level approximation shows through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'delete me' AS msg",  # string literal
        'SELECT "delete" FROM trade',  # double-quoted identifier
    ],
)
def test_banned_word_in_literal_or_quoted_identifier_is_rejected(sql: str) -> None:
    """A banned word inside quotes still trips the guard.

    This is a false positive, and it is the equity original's behaviour: the
    pattern is a regex over the whole statement with no notion of string
    literals or quoted identifiers. Pinned so a future rewrite has to decide
    the change deliberately rather than drift into it.
    """
    with pytest.raises(ValueError, match="disallowed keywords or external readers"):
        assert_readonly_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT deleted_at FROM trade",
        "SELECT symbol AS delete_flag FROM trade",
        "SELECT dropped_count FROM trade",
        "SELECT updated_ts FROM trade",
    ],
)
def test_banned_word_as_identifier_substring_is_allowed(sql: str) -> None:
    """Ordinary column names that merely contain a banned word are allowed.

    ``\\b`` word boundaries carry this: ``deleted_at`` and ``delete_flag`` both
    continue with a word character, so ``\\bDELETE\\b`` does not match.
    """
    assert_readonly_sql(sql)


# ---------------------------------------------------------------------------
# Catalog.query integration
# ---------------------------------------------------------------------------


def test_query_readonly_blocks_mutating_sql(tmp_path: pathlib.Path) -> None:
    """``readonly=True`` applies the guard before touching DuckDB."""
    cat = Catalog(tmp_path)
    with pytest.raises(ValueError):
        cat.query("DROP TABLE IF EXISTS trade", readonly=True)


def test_query_readonly_allows_select(tmp_path: pathlib.Path) -> None:
    """``readonly=True`` still lets an ordinary SELECT through."""
    cat = Catalog(tmp_path)
    df = cat.query("SELECT 1 AS x", readonly=True)
    assert df["x"][0] == 1


def test_query_defaults_to_unguarded(tmp_path: pathlib.Path) -> None:
    """The default stays unguarded, preserving both forks' local-caller access.

    ``CREATE OR REPLACE TABLE`` is on the guard's blocklist yet executes fine
    here, which is what proves the guard is opt-in rather than always-on.
    """
    cat = Catalog(tmp_path)
    cat.query("CREATE OR REPLACE TABLE scratch AS SELECT 1 AS x")
    assert cat.query("SELECT x FROM scratch")["x"][0] == 1

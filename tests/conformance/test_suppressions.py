"""A suppression that suppresses nothing is worse than no suppression at all.

``core/store/catalog.py`` carried two ``# nosemgrep:`` comments, and neither of them
silenced anything. Both sat on the *closing paren* of a multi-line call::

    result = self._conn.execute(
        sql, params
    )  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query...

Semgrep reports a finding at the line its match *starts* on and honours a ``nosemgrep``
comment on that line or on the line immediately above it. The match here starts at
``result = self._conn.execute(`` and the comment is two lines below it, so the rule went
on firing. Measured with semgrep 1.157.0 against the two named rules: with suppressions
enabled and with ``--disable-nosem`` the file reported the identical set of lines, which
is the definition of a comment that does nothing.

That leaves the worst of both. The finding is still noise in every scan, so nobody reads
the output; and the comment reads, to anybody maintaining the file, as a decision that
was made and recorded. Reformatting the call onto one line — which is what a formatter
would eventually do — would then have activated a suppression nobody re-argued.

Semgrep is not available offline and its rules live in a network registry, so this gate
cannot re-run the scanner. It pins the structural property instead, which is the half
that actually broke: a ``nosemgrep`` comment must be attached to the beginning of a
statement, either as that statement's own preceding line or as a trailing comment on its
first line. Every placement semgrep honours has that shape, and the two broken ones did
not.
"""

from __future__ import annotations

import ast
import io
import pathlib
import tokenize

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCANNED = ("src/crocodile", "tests")

_MARKER = "nosemgrep"


def _suppression_lines(source: str) -> list[int]:
    """The lines carrying a suppression, found by tokenising rather than by substring.

    A substring search reads this file's own docstring — which quotes the broken form in
    order to explain it — as an offence, and would read any future prose about the
    marker the same way. Only a ``COMMENT`` token is a suppression; the same characters
    inside a string literal are documentation.
    """
    lines: list[int] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and _MARKER in token.string:
            lines.append(token.start[0])
    return lines


def _annotated_files() -> list[pathlib.Path]:
    """Every Python file under the scanned roots that carries a suppression."""
    found: list[pathlib.Path] = []
    for root in _SCANNED:
        for path in sorted((_ROOT / root).rglob("*.py")):
            if _suppression_lines(path.read_text()):
                found.append(path)
    return found


def _statement_start_lines(tree: ast.AST) -> frozenset[int]:
    """The line every statement in ``tree`` begins on.

    A decorated function begins at its first decorator as far as a reader is concerned,
    so those count too — ``@decorator  # nosemgrep`` is a placement semgrep honours and
    ``ast`` reports the ``def`` line as ``lineno``.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            lines.add(node.lineno)
            for decorator in getattr(node, "decorator_list", []):
                lines.add(decorator.lineno)
    return frozenset(lines)


def _misplaced(path: pathlib.Path) -> list[str]:
    """Return one message per suppression in ``path`` that annotates no statement."""
    source = path.read_text()
    starts = _statement_start_lines(ast.parse(source))
    physical = source.splitlines()
    offences: list[str] = []
    for index in _suppression_lines(source):
        stripped = physical[index - 1].lstrip()
        if stripped.startswith("#"):
            # A comment on its own line applies to the line below it.
            target, shape = index + 1, "the line below it is not the start of a statement"
        else:
            # A trailing comment applies to its own line.
            target, shape = index, "its own line is not the start of a statement"
        if target not in starts:
            offences.append(f"{path.relative_to(_ROOT).as_posix()}:{index} — {shape}")
    return offences


def test_there_is_something_to_check() -> None:
    """Otherwise the gate below is a loop over nothing, quietly."""
    assert _annotated_files(), "no suppression anywhere; this gate guards an empty set"


@pytest.mark.parametrize(
    "path", _annotated_files(), ids=lambda p: p.relative_to(_ROOT).as_posix()
)
def test_every_suppression_is_attached_to_a_statement(path: pathlib.Path) -> None:
    """A comment on a continuation line is a decision recorded where nothing reads it."""
    offences = _misplaced(path)
    assert not offences, (
        f"{offences}. Semgrep honours a nosemgrep comment on the line a finding starts "
        f"on, or on the line immediately above it — never on the closing paren of the "
        f"call it belongs to. Move the comment above the statement, or delete it if the "
        f"finding it names no longer fires."
    )


def test_the_catalogs_two_suppressions_sit_where_they_were_meant_to() -> None:
    """The specific pair this gate was written for, named rather than merely covered.

    Both name DuckDB calls, and both rules they name are about *SQLAlchemy* or about
    f-string SQL generally. The interpolated parts at these two sites are a validated
    ``LIMIT`` integer and a double-quote-escaped identifier respectively; every
    caller-supplied value is a ``?`` parameter. The argument for suppressing was already
    in the file. Only the placement was wrong.
    """
    path = _ROOT / "src/crocodile/core/store/catalog.py"
    source = path.read_text().splitlines()
    starts = _statement_start_lines(ast.parse(path.read_text()))

    suppressed = [i for i, text in enumerate(source, start=1) if _MARKER in text]
    assert len(suppressed) == 2, f"catalog.py carries {len(suppressed)} suppressions, not 2"
    for line in suppressed:
        assert source[line - 1].lstrip().startswith("# nosemgrep:"), (
            f"catalog.py:{line} annotates a rule from a continuation line"
        )
        assert line + 1 in starts
        assert "self._conn.execute(" in source[line], (
            f"catalog.py:{line} suppresses something other than the execute it names"
        )

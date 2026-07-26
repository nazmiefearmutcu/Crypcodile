# Transient directory

Stockodile's tree, imported with `git subtree add` so its 53 commits stay
reachable. Phase 1 dissolves this directory: shared modules move to
`src/crocodile/core/`, equity-specific modules to `src/crocodile/equity/`,
and the fork residue listed in spec §13.1 is deleted. This directory MUST
NOT exist when Phase 1 completes — Task 16 asserts that.

## Baseline

Captured before any code moved. No later task may reduce either number.

| Suite | Collected | Command |
|---|---|---|
| crypto | 1817 | `./.venv/bin/python -m pytest tests/ --collect-only -q --ignore=tests/gui/test_flowmap_window.py` |
| equity | 389 | `./.venv/bin/python -m pytest tests/ --collect-only -q` (run in the pre-merge stockodile checkout) |

Both suites are collected with each repo's own virtualenv. The system
`python3` cannot collect either one: the crypto suite errors on missing
extras, and the equity suite raises 29 collection errors from missing
`mmh3` / `msgspec`. `tests/gui/test_flowmap_window.py` is ignored on the
crypto side because it needs the external FlowMap renderer.

Neither total is printed as a `N tests collected` line — this pytest
configuration emits only per-file counts under `--collect-only -q`, so the
numbers above are the sum of those per-file counts (crypto: 140 files;
equity: 44 files).

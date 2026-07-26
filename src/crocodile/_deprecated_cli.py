"""The `crypcodile` and `stockodile` commands, kept alive for one minor version.

The two distributions became one, and their console scripts became aliases. An
alias that says nothing is a promise nobody hears: the module shims warn on
import, but a console script points straight at the merged CLI and never
imports the old package, so someone typing `crypcodile` today would get no
notice at all before the command disappears in 0.4.

These wrappers exist to make the deprecation audible exactly where it is met —
on stderr, once per invocation, before the real CLI runs — without changing
what the command does.
"""

from __future__ import annotations

import sys


def _warn(old: str, new: str, package: str) -> None:
    print(
        f"warning: `{old}` is deprecated and will be removed in 0.4. "
        f"Use `{new}` instead; the Python package is now `{package}`.",
        file=sys.stderr,
    )


def crypcodile_main() -> None:
    """Deprecated alias for the `crocodile` command."""
    from crocodile.crypto.legacy.cli import main

    _warn("crypcodile", "crocodile", "crocodile.crypto")
    main()


def stockodile_main() -> None:
    """Deprecated alias for the `crocodile-equity` command."""
    from crocodile.equity.legacy.cli import main

    _warn("stockodile", "crocodile-equity", "crocodile.equity")
    main()

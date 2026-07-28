"""The ``crocodile`` command: the projection's commands, plus the ones that operate it.

One console script now covers both asset classes. ``crocodile-equity`` is gone — it existed
because there were two engines, and a command whose whole meaning was "the other fork" has
nothing left to name. Every capability that serves equities is reachable here under the same
name it has for crypto, with ``--asset-class equity`` when the symbol does not say.

The two lists are assembled here rather than in either module because they are measured
differently. :func:`crocodile.surfaces.cli.build_app` is generated from
:data:`~crocodile.core.capability.REGISTRY` and Gate 4 asserts it holds exactly the
registry's names; :func:`crocodile.surfaces.operate.build_app` is hand-written and must not
appear in front of that gate. Merging them in a third place is what keeps both true.
"""

from __future__ import annotations

import sys

import typer

from crocodile.surfaces import cli, operate

__all__ = ["build_app", "main"]


def build_app() -> typer.Typer:
    """Return the full command table: one command per capability, plus the operator's."""
    app = cli.build_app()
    app.registered_commands.extend(operate.build_app().registered_commands)
    return app


def main() -> None:
    """Entry point for the ``crocodile`` console script.

    A bare invocation prints help rather than starting the REPL. Both forks declared
    ``no_args_is_help=True`` on the Typer app and then defeated it in ``main`` by appending
    ``shell`` to ``sys.argv``, so the declared behaviour was unreachable and a bare
    ``crocodile`` in a pipeline blocked on a prompt nobody was there to answer. The shell is
    one word away.
    """
    app = build_app()
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    app()


if __name__ == "__main__":  # pragma: no cover - console-script parity
    main()

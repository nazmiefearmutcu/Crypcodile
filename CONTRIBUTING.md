# Contributing

Thanks for looking. This file describes what CI actually enforces, so you never
have to guess which checks are real.

## Setup

```bash
uv sync --all-extras
uv run pytest --ignore=tests/gui
```

`tests/gui` is excluded because it drives real PyQt6 windows, and its FlowMap
tests import a package this project does not install — see the FlowMap section
of the [README](README.md#flowmap). Those tests skip cleanly rather than error,
so running the full `uv run pytest` locally is fine too.

## What CI enforces

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs on every push
to `main` and every pull request.

| Check | Command | Blocking? |
|---|---|---|
| Test suite | `uv run pytest --ignore=tests/gui` | **yes** |
| Undefined names | `uv run ruff check src --select F821` | **yes** |
| Full lint | `uv run ruff check src` | no — advisory |
| Type check | `uv run mypy` | no — advisory |

### Why ruff and mypy are advisory

They are not green. As of the commit that added this file, `ruff check src`
reports **432** findings and `mypy` reports **258** errors across **36** of 130
source files. Claiming a gate that does not run is worse than admitting the
debt, so CI runs both with `continue-on-error` to keep the numbers visible and
prevent them from growing unnoticed. If you clean a module up, say so in the PR
and we can start ratcheting.

`F821` (undefined name) is carved out and *does* block. It is the one lint class
that is never stylistic — every finding is a latent `NameError`. `src/` is
currently at zero and must stay there.

## Pull requests

- Keep the test suite green, and add a test for anything you fix. A bug that
  survived because no test looked for it deserves a test that looks for it.
- Conventional Commit subject lines (`fix(store): …`, `feat(exchanges): …`).
- Don't add new `F821`s. Everything else in the lint report is pre-existing; you
  are not expected to fix unrelated debt to land a change.
- Skim [`CHANGELOG.md`](CHANGELOG.md) for direction before starting something
  large.

Apache-2.0 — see [LICENSE](LICENSE).

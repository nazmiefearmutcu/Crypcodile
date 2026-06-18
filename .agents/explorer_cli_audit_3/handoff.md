# Handoff Report: CLI Terminal Commands Audit

This report contains findings and recommendations from the scan and audit of the CLI terminal commands defined in `src/crypcodile/cli.py`.

---

## 1. Findings Table

| # | Command Name | Description of the Issue | File and Line Reference | Recommendation for Fix |
|---|---|---|---|---|
| **1** | **All commands** (`query`, `export`, `replay`, `collect`, `funding-apr`, `basis`, `iv-surface`, `term-structure`) | **Silent Success (Exit 0) on Missing Options**: When required options are missing in a non-interactive shell (e.g., in a cron job or script where stdin is redirected), the CLI attempts to prompt the user. This raises a `KeyboardInterrupt` internally when reading EOF from stdin, which is caught and forces a clean exit with code 0 instead of a validation error (code 1 or 2). | `src/crypcodile/cli.py`:<br>• Lines 158–161 (in `_prompt_with_esc`) <br>• Lines 213–221 (in `_prompt_with_esc`) | Before calling `typer.prompt()`, check if `is_interactive_stdin()` is False. If it is False, immediately check if all required options are present; if they are not, raise `typer.BadParameter` or `typer.Exit(code=1)` with a descriptive error message. |
| **2** | `shell` | **Crash on Non-TTY Stdin**: If the `shell` command is run with redirected stdin/stdout (e.g. `echo "help" \| crypcodile shell`), and it is not running in pytest (`is_pytest = False`), it instantiates `PromptSession` and calls `session.prompt()`. This throws terminal control errors (e.g. `OSError: [Errno 25] Inappropriate ioctl for device` or `NoRunningAppError`) because `prompt_toolkit` cannot initialize raw mode. | `src/crypcodile/cli.py`:<br>• Lines 1930–1943 <br>• Lines 1976–1979 | Check `is_interactive_stdin()`. If it returns False, fallback to using `input()` instead of `PromptSession` (exactly like `is_pytest` behaves), allowing the shell to process redirected inputs cleanly. |
| **3** | `update` | **Incorrect Pre-release Version Comparison**: The version comparison parses all digit characters and compares the resulting lists. A pre-release tag like `v1.0.0-beta.1` returns `[1, 0, 0, 1]`, while a stable tag `v1.0.0` returns `[1, 0, 0]`. Since `[1, 0, 0, 1] > [1, 0, 0]` is True, the CLI incorrectly assumes a beta release is newer than a stable release. | `src/crypcodile/cli.py`:<br>• Lines 1860–1867 | Use `packaging.version.Version` from the `packaging` library to safely compare semantic versions. |
| **4** | `basis` | **Confusing Basis Mode Prompts**: If the user provides only `--future` (e.g., `crypcodile basis --future deribit:BTC-FUTURE`), the validation condition `perp is None and (future is None or spot is None)` is True. This prompts for "Basis mode" (defaulting to `perp`). If chosen, it discards the `--future` argument and asks for a perp symbol. | `src/crypcodile/cli.py`:<br>• Lines 1456–1480 | If `--future` or `--spot` is specified, skip prompting for the basis mode. Only prompt for mode if all three options (`--perp`, `--future`, `--spot`) are unspecified. If both perp and futures/spots are specified, raise a validation error. |
| **5** | `query` | **Traceback Leak on Database Exceptions**: Database exceptions (e.g. syntax errors or missing tables) are not caught inside the command, causing a raw DuckDB traceback leak. | `src/crypcodile/cli.py`:<br>• Lines 691–693 | Wrap `client.query(sql)` in a `try...except Exception` block. Catch database errors, print them cleanly to stderr, and raise `typer.Exit(code=1)`. |

---

## 2. Logic Chain

1. **Missing Option Silent Success**:
   - *Observation*: `_prompt_with_esc` reads via `sys.stdin.readline()`. If stdin has no TTY (Observation 1), it reads `""` and raises `KeyboardInterrupt`.
   - *Reasoning*: `KeyboardInterrupt` is caught by the prompt loop, raising `typer.Exit(code=0)` (success).
   - *Conclusion*: Missing options inside scripts lead to silent exit code 0 rather than exit code 1/2.

2. **Shell Non-TTY Crash**:
   - *Observation*: `shell` executes `session.prompt()` when not in pytest, regardless of whether stdin is a TTY (Observation 2).
   - *Reasoning*: `prompt_toolkit` requires a TTY to query terminal capabilities and enter raw mode.
   - *Conclusion*: Redirecting stdin to `crypcodile shell` crashes instead of processing inputs.

3. **Incorrect Version Comparisons**:
   - *Observation*: `parse_version` extracts all digits (Observation 3).
   - *Reasoning*: `1.0.0-beta.1` maps to list `[1, 0, 0, 1]` which is sorted higher than stable `1.0.0` `[1, 0, 0]`.
   - *Conclusion*: Stable builds will prompt to "upgrade" to older pre-releases.

4. **Basis Mode Logic**:
   - *Observation*: If only `--future` is provided, `spot` is None. Thus, `perp is None and (future is None or spot is None)` evaluates to `True` (Observation 4).
   - *Reasoning*: The CLI incorrectly forces a prompt to select a basis mode, ignoring the input leg and defaulting to perpetual basis mode.
   - *Conclusion*: Partial arguments trigger confusing prompts and input loss.

5. **DuckDB Tracebacks**:
   - *Observation*: `client.query(sql)` directly executes raw SQL on the DuckDB connection (Observation 5).
   - *Reasoning*: DuckDB throws `CatalogException` or `ParserException` if the query is syntactically invalid or targets a missing view.
   - *Conclusion*: Unhandled database errors bubble up and leak internal code tracebacks to the user.

---

## 3. Caveats

- **No Caveats**: The audit covers all 12 requested commands inside `src/crypcodile/cli.py` by static code tracing.

---

## 4. Conclusion

The CLI is generally well-structured and uses modern styling, but possesses several validation and interactive prompt safety bugs. Crucially, missing required options in automated scripts results in false successes (exit code 0), and running the interactive shell in non-TTY environments triggers unhandled library crashes. Applying the recommended validations will resolve these issues.

---

## 5. Verification Method

To independently verify these findings, run the following commands in the project directory:

1. **Verify silent success on missing options**:
   ```bash
   # Run with empty input redirected
   poetry run crypcodile export --fmt csv --dest /tmp/out.csv --data-dir test_data < /dev/null
   echo "Exit code: $?"
   # Expect: "Exit code: 0" (should be 1)
   ```

2. **Verify shell crash on non-TTY**:
   ```bash
   echo "help" | poetry run crypcodile shell
   # Expect: OSError or prompt_toolkit error traceback
   ```

3. **Verify version comparison failure**:
   Execute this Python snippet:
   ```python
   import re
   def parse_version(v: str) -> list[int]:
       return [int(x) for x in re.findall(r"\d+", v)]
   print(parse_version("1.0.0-beta.1") > parse_version("1.0.0"))
   # Expect: True (incorrectly saying beta.1 is newer than stable 1.0.0)
   ```

4. **Verify basis command mode prompt**:
   ```bash
   poetry run crypcodile basis --future deribit:BTC-FUTURE
   # Expect: Prompts for mode instead of prompting directly for the missing spot symbol
   ```

5. **Verify raw DuckDB traceback**:
   ```bash
   poetry run crypcodile query "SELECT * FROM invalid_table_name" --data-dir test_data
   # Expect: duckdb.CatalogException traceback printed to screen
   ```

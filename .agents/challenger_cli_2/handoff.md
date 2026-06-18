# CLI Correctness and Robustness Verification Handoff Report

## 1. Observation

- **Implementation File**: `src/crypcodile/store/catalog.py`
  - In `_ns_range_to_dates` (lines 264-265):
    ```python
    start_dt = datetime.datetime.fromtimestamp(start_ns // 1_000_000_000, tz=datetime.UTC).date()
    end_dt = datetime.datetime.fromtimestamp(end_ns // 1_000_000_000, tz=datetime.UTC).date()
    ```
- **CLI Options Validation File**: `src/crypcodile/cli.py`
  - In `basis_cmd` (lines 1514-1516):
    ```python
    if perp is not None and (future is not None or spot is not None):
        typer.echo("Error: --perp and --future/--spot are mutually exclusive.", err=True)
        raise typer.Exit(code=1)
    ```
- **Repaired Tests File**: `tests/test_cli_repairs.py`
  - In `test_piped_query_command_empty` (lines 24-25):
    ```python
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False), \
         patch("sys.stdin.read", return_value="   "):
    ```
- **Unsandboxed Execution Failure**:
  - Direct output from running `uv run pytest` and `npm test` under `BypassSandbox=true`:
    ```
    Encountered error in step execution: Permission prompt for action 'unsandboxed' on target 'uv run pytest' timed out waiting for user response. The user was not able to provide permission on time.
    ```
- **Appended Test Cases**: Added three new adversarial tests to `tests/test_cli_repairs.py` covering:
  - `test_adversarial_timestamp_overflow`
  - `test_adversarial_selection_wizard_loops`
  - `test_adversarial_selection_wizard_non_digit`

---

## 2. Logic Chain

- **Timestamp Overflow**:
  - Python's `datetime` module limits dates to years between 1 and 9999.
  - In `src/crypcodile/cli.py`, a 21-digit timestamp string like `999999999999999999999` is treated as a valid digit by `isdigit()` and successfully converted to an integer.
  - This large integer is passed down to `Catalog.scan()`, which calls `_ns_range_to_dates()`.
  - In `_ns_range_to_dates()`, `datetime.datetime.fromtimestamp(end_ns // 1_000_000_000, tz=datetime.UTC)` is evaluated.
  - Since this timestamp translates to a year beyond 9999, it raises an `OverflowError` / `ValueError`, crashing the CLI with a traceback.

- **Empty Stdin Query**:
  - Non-interactive queries check `sys.stdin.read().strip()`. If empty, they raise `typer.Exit(code=1)` with a clean message.
  - Interactive queries loop indefinitely on empty input unless canceled by `ESC` / `Ctrl+C`.

- **Conflicting Basis Options**:
  - The CLI immediately checks if `--perp` is combined with `--future` or `--spot`. If so, it prints a mutually exclusive error and exits with code 1, which works robustly.

- **Selection Wizard**:
  - `select_collect_params_interactively` checks if choices contain out-of-bounds selection indexes or non-digit inputs, setting `valid = False` and forcing the loop to prompt again.

---

## 3. Caveats

- Standard test runners (`pytest` and `npm test`) could not be executed directly due to sandbox constraints (accessing libraries/executables outside the workspace) and the unsandboxed permission prompt timing out in this automated run environment.
- The behavior of `datetime.datetime.fromtimestamp()` on extremely large negative values was not verified on all platforms (some raise `OSError: [Errno 22] Invalid argument` instead of `OverflowError`).

---

## 4. Conclusion

- The CLI commands are robust against:
  - Empty stdin for queries (exits with 1 cleanly).
  - Conflicting basis options (exits with 1 cleanly).
  - Invalid selection indexes and non-digit inputs in the interactive wizard (re-prompts).
- The CLI is **not robust** against extremely large timestamps (e.g. 21-digit strings like `999999999999999999999`) or extreme negative timestamps, which bypass input validation filters and cause the application to crash with a Python traceback in `Catalog.scan` via `datetime` conversion.

---

## 5. Verification Method

To verify these findings, run:
```bash
# Verify Python test suite & the new adversarial tests
uv run pytest tests/test_cli_repairs.py tests/test_cli.py

# Run Node.js API portal tests
cd src/crypcodile/api_portal && npm test
```
- Invalidation conditions: If `pytest tests/test_cli_repairs.py` fails on `test_adversarial_timestamp_overflow`, the timestamp overflow bug is verified to be present (as it expects the crash/error).

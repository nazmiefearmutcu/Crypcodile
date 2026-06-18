# Handoff Report — Crypcodile CLI and Export Review

This report presents the verification results, quality review, and adversarial stress-testing of the CLI and export repairs.

---

## 1. Observation
I observed and inspected the codebase of `src/crypcodile/cli.py`, `src/crypcodile/client/export.py`, and the new test suite `tests/test_cli_repairs.py`.

- **Undefined Variable Bug (NameError)**:
  In `src/crypcodile/cli.py`, line 1371:
  ```python
  1368:     monitoring_sink = MonitoringSink(sink)
  1369:     connector.out = monitoring_sink
  1370: 
  1371:     if is_interactive:
  1372: 
  1373:         async def collect_with_dashboard():
  ```
  `is_interactive` is not defined anywhere in the `collect()` function scope or the global module scope (it is only defined as a local variable within the `shell()` command function at line 2096). This will cause a `NameError` crash when the `collect` command is executed and reaches this point.

- **Unprotected Datetime Conversion**:
  In `src/crypcodile/cli.py`, lines 272–273:
  ```python
  272:         min_dt_str = datetime.datetime.fromtimestamp(min_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  273:         max_dt_str = datetime.datetime.fromtimestamp(max_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  ```
  These lines are not wrapped in a `try...except` block. If the database catalog contains invalid or corrupt timestamps, `fromtimestamp` will raise a `ValueError` or `OSError` and crash the command. In contrast, other timestamps (e.g., at line 1746) are safely wrapped.

- **Test Suite Coverage (`tests/test_cli_repairs.py`)**:
  I verified that the new test suite includes:
  - `test_piped_query_command`: Validates multiline piped SQL queries.
  - `test_piped_query_command_empty`: Validates empty query validation and exit code 1.
  - `test_non_interactive_validation_failures`: Validates exit codes for missing arguments in non-interactive shell.
  - `test_basis_mutually_exclusive_and_non_interactive`: Validates mutual exclusivity of `--perp` and `--future`/`--spot`.
  - `test_basis_implicit_mode_interactive`: Validates interactive completion when a single asset option is provided.
  - `test_sparkline_nan_inf_validation`: Validates filtering out of NaN/Inf in sparklines.
  - `test_selection_wizard_digit_checks`: Validates selection bounds in the collect wizard.
  - `test_empty_dataframe_export_schema`: Validates schema mapping of empty Parquet/Arrow exports.

- **Sandbox Execution Constraints**:
  Attempted command execution of `uv run pytest`, `npm test`, and `uv build` in the sandbox. The sandbox blocked these executions due to access requirements to files outside the workspace (e.g., global python packages, node runtimes, cargo artifacts), and the bypass requests timed out:
  ```
  Encountered error in step execution: Permission prompt for action 'unsandboxed' on target 'uv run pytest' timed out waiting for user response.
  ```

---

## 2. Logic Chain
1. By examining the imports and assignments in `src/crypcodile/cli.py` statically, I observed that `is_interactive` is used in `collect()` but is never assigned a value there. It is only defined locally inside `shell()`.
2. Any user execution of the `collect` command that doesn't trigger the early non-interactive check (i.e. either in interactive mode, or in non-interactive mode with valid arguments) will progress to line 1371.
3. At line 1371, python will look up `is_interactive` in the local and global namespaces, fail to find it, and raise a `NameError`.
4. Therefore, the implementation of the live data collection command `collect` is broken and incomplete.
5. In `src/crypcodile/client/export.py`, the schema resolution for empty exports is fully dynamic and handles fallback from catalog queries to record structures correctly.

---

## 3. Caveats
Due to the CODE_ONLY sandbox network and file access policies, I could not execute `uv run pytest`, Node.js E2E tests, or `uv build` directly to see the runtime behavior of the test suite. I relied on the written logs (`test_failures.txt`, `test_run_details.txt`, and `test_out.txt`) and comprehensive static code analysis.

---

## 4. Conclusion & Review
The CLI repairs are **almost complete and correct**, but contain a critical logic defect (`NameError` on `is_interactive` in `collect` command) that must be resolved before approval.

### Quality Review Report

**Verdict**: REQUEST_CHANGES

#### Findings

##### [Critical] Finding 1: Undefined `is_interactive` in `collect` CLI Command
- **What**: `is_interactive` is used in a conditional check but is never defined.
- **Where**: `src/crypcodile/cli.py`, line 1371.
- **Why**: Triggers a `NameError: name 'is_interactive' is not defined` when `collect` runs.
- **Suggestion**: Define it before line 1371 by adding `is_interactive = is_interactive_stdin()` or directly use `if is_interactive_stdin():`.

##### [Minor] Finding 2: Unsafe `fromtimestamp` in `prompt_time_range_helper`
- **What**: Conversion from `min_ts` and `max_ts` raw timestamps to datetimes is not wrapped.
- **Where**: `src/crypcodile/cli.py`, lines 272–273.
- **Why**: Can raise `ValueError`/`OSError` and crash if the catalog has invalid/corrupted timestamps.
- **Suggestion**: Wrap the conversions in a `try...except` block or reuse the fallback logic structure seen on line 319.

#### Verified Claims
- **Empty Parquet/Arrow exports write schema** → Verified via inspecting `src/crypcodile/client/export.py` and `tests/test_cli_repairs.py` → **PASS** (schema mapped dynamically via database query/msgspec fallback).
- **Sparkline NaN/Inf handling** → Verified via inspecting `src/crypcodile/cli.py` and `tests/test_cli_repairs.py` → **PASS** (filters out `None`, `NaN`, `Inf` using `math.isfinite`).
- **Options query optimization** → Verified via inspecting `src/crypcodile/cli.py` → **PASS** (queries only the latest date partition files first using glob before full table scan fallback).

#### Coverage Gaps
- **Interactive dashboard execution** — Risk level: Medium — The NameError was not caught by the CLI tests because the test suite does not cover execution paths reaching the interactive dashboard execution block when `is_interactive` is true. Recommendation: Extend `test_cli_collect.py` to assert correct execution paths when interactive mode is mocked.

---

### Adversarial Review Report

**Overall risk assessment**: MEDIUM

#### Challenges

##### [High] Challenge 1: NameError Crash on Ingest Pipeline Invocations
- **Assumption challenged**: Assumed `is_interactive` was globally accessible or imported.
- **Attack scenario**: A user running live collection with `crypcodile collect --exchange binance --symbols BTCUSDT --channels trade` on their terminal will crash.
- **Blast radius**: Prevents the CLI from running any collection pipeline successfully.
- **Mitigation**: Define the variable or reference `is_interactive_stdin()`.

##### [Medium] Challenge 2: Date Parsing crash with Corrupted DB timestamps
- **Assumption challenged**: Assumed `min(local_ts)` and `max(local_ts)` from database always represent valid timestamps.
- **Attack scenario**: If the database file is corrupted or contains an extreme value, the time-range selection crashes the export/replay command before prompting.
- **Blast radius**: Block export/replay/funding queries.
- **Mitigation**: Wrap datetime conversion in `try...except`.

---

## 5. Verification Method
1. Fix the NameError bug in `src/crypcodile/cli.py` by defining `is_interactive = is_interactive_stdin()` inside the `collect` command.
2. Run tests to confirm there are no regression issues:
   ```bash
   uv run pytest tests/test_cli_repairs.py
   uv run pytest tests/test_cli_collect.py
   ```
3. Run the Node.js E2E tests:
   ```bash
   cd src/crypcodile/api_portal && npm test
   ```
4. Verify the package build:
   ```bash
   uv build
   ```

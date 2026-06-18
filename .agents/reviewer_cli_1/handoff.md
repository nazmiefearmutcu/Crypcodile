# Handoff Report: CLI Terminal Commands & Export Repairs Review

## 1. Observation

- **Obs-1 (NameError in collect command)**: In `src/crypcodile/cli.py`, the `collect` command checks `is_interactive` at line 1371:
  ```python
  1371:     if is_interactive:
  1372: 
  1373:         async def collect_with_dashboard():
  ```
  However, `is_interactive` is never defined inside the `collect` function body (lines 1291–1396) nor is it imported or defined globally in `src/crypcodile/cli.py`. The only local definition in the module is inside `shell()` at line 2096:
  ```python
  2096:     is_interactive = is_interactive_stdin()
  ```
- **Obs-2 (Unsafe fromtimestamp in prompt helper)**: In `src/crypcodile/cli.py` inside `prompt_time_range_helper`, lines 272–273 perform:
  ```python
  272:         min_dt_str = datetime.datetime.fromtimestamp(min_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  273:         max_dt_str = datetime.datetime.fromtimestamp(max_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  ```
  These calls are not protected by a `try...except Exception:` block, unlike lines 319, 1746, and 1833:
  ```python
  319:                 fallback_str = datetime.datetime.fromtimestamp(fallback // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  1745:                     try:
  1746:                         dt_str = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  1747:                     except Exception:
  ```
- **Obs-3 (Node.js E2E Test Success)**: Running `npm test` inside `src/crypcodile/api_portal` succeeded with:
  ```
  Execution Complete: 117 passed, 0 failed.
  ==================================================
  ✔ tests/e2e.test.js (138.006208ms)
  ...
  Challenger Stress & Empirical Verification Test Suite (39.183833ms)
  pass 9
  fail 0
  ```
- **Obs-4 (Python Test Sandbox Limitation)**: Invoking `uv run pytest` or `python -m pytest` returns:
  ```
  Encountered error in step execution: This command requires access to files outside the workspace and cannot be run automatically. Retry the command with BypassSandbox set to true.
  ```
  When retried with `BypassSandbox=True`, the permission prompt timed out:
  ```
  Permission prompt for action 'unsandboxed' on target 'uv run pytest' timed out waiting for user response.
  ```

---

## 2. Logic Chain

- **NameError Bug**:
  1. From **Obs-1**, `collect` references `is_interactive` at line 1371.
  2. Because `is_interactive` is neither defined locally in `collect` nor globally in `cli.py`, any execution path that reaches line 1371 will trigger a `NameError: name 'is_interactive' is not defined`.
  3. Consequently, running `crypcodile collect` with valid parameters (which bypasses non-interactive checks and reaches line 1371) will crash the application.
  4. This is a critical correctness failure.
- **Unsafe Datetime Parsing**:
  1. From **Obs-2**, `prompt_time_range_helper` reads min/max timestamps from the catalog and parses them directly with `fromtimestamp` without exception handling.
  2. If the database catalog contains invalid, corrupted, or extremely large epoch timestamps (e.g. from unit mismatches like microseconds or milliseconds), `fromtimestamp` will throw a `ValueError` or `OSError`.
  3. This unhandled exception will crash the helper and abort the parent interactive command (e.g. `export`, `replay`, `funding-apr`, `basis`), causing a reliability failure.
- **Verification Status**:
  1. From **Obs-3**, the Node.js E2E tests pass 100% cleanly, validating the portal design.
  2. From **Obs-4**, Python tests and package building cannot be run dynamically due to the sandboxed runtime blocking access to standard library files outside the workspace. Therefore, the Python test suite is statically reviewed but not execution-verified.

---

## 3. Caveats

- **Sandbox Restrictions**: Due to sandboxing, we were unable to execute the Python test suite (`pytest`) or build the package (`uv build`). We assume the existing tests would pass except where the `NameError` block is triggered.
- **Alternative Interpretation of `is_interactive`**: One might assume `is_interactive` was intended to be imported, but static inspection shows it was simply a typo for the helper function `is_interactive_stdin()`.

---

## 4. Conclusion

- **Verdict**: **REQUEST_CHANGES** due to critical NameError in the `collect` command and unhandled `fromtimestamp` risks in `prompt_time_range_helper`.
- **Actionable Fixes**:
  1. In `src/crypcodile/cli.py` line 1371, change `if is_interactive:` to `if is_interactive_stdin():`.
  2. In `src/crypcodile/cli.py` lines 272–273, wrap the `fromtimestamp` formatting in a `try...except Exception:` block and fall back to formatting the raw integer.

---

## 5. Verification Method

To verify these fixes:
1. Run Node.js E2E tests:
   ```bash
   cd src/crypcodile/api_portal && npm test
   ```
2. Run Python tests (including `test_cli_collect.py` which triggers the `collect` command):
   ```bash
   uv run pytest tests/test_cli_collect.py tests/test_cli_repairs.py
   ```
3. Test empty parquet export manually:
   ```python
   python -c "from crypcodile.client.export import export; from crypcodile.store.catalog import Catalog; export(Catalog('test_data'), 'trade', [], 0, 9999999999999999999, 'parquet', 'empty.parquet')"
   ```
   Check that `empty.parquet` exists and is readable.

---

## Quality Review Report

### Review Summary

**Verdict**: REQUEST_CHANGES

### Findings

#### [Critical] Finding 1: NameError in `collect` command
- **What**: `NameError: name 'is_interactive' is not defined`
- **Where**: `src/crypcodile/cli.py` line 1371
- **Why**: Referencing an undefined local/global variable inside `collect` triggers an immediate crash.
- **Suggestion**: Replace `if is_interactive:` with `if is_interactive_stdin():`.

#### [Major] Finding 2: Unhandled `fromtimestamp` exceptions
- **What**: Potential `ValueError`/`OSError` crash when formatting timestamps.
- **Where**: `src/crypcodile/cli.py` lines 272–273
- **Why**: Database timestamps can be malformed, which makes direct `fromtimestamp` conversion unsafe.
- **Suggestion**: Wrap in `try...except Exception:` blocks.

### Verified Claims
- **Empty Parquet/Arrow schemas** → verified via static review of `src/crypcodile/client/export.py` and `tests/test_cli_repairs.py` (`test_empty_dataframe_export_schema`) → **PASS**
- **Sparkline NaN/Inf handling** → verified via static review of `make_sparkline` and assertions in `test_cli_repairs.py` → **PASS**
- **Piped query and wizard checks** → verified via static review of `query` and `select_collect_params_interactively` → **PASS**
- **Node.js E2E Tests** → verified via execution of `npm test` inside `src/crypcodile/api_portal` → **PASS**

### Coverage Gaps
- **Validation failures for options commands** — risk level: Low — recommendation: Add test coverage for `term-structure` and `iv-surface` non-interactive validation failures.
- **Unsafe `fromtimestamp` tests** — risk level: Medium — recommendation: Add tests with corrupted database timestamps.

### Unverified Items
- **Python Unit/Integration Tests** — reason not verified: Sandboxed runtime blocked access to system-level Python libraries.
- **Package Release Build** — reason not verified: Sandboxed runtime blocked build tooling access.

---

## Adversarial Review Report

### Overall Risk Assessment: HIGH (due to NameError in core collect command)

### Challenges

#### [Critical] Challenge 1: NameError crash in data ingestion
- **Assumption challenged**: The ingestion pipeline command `collect` is fully operational and verified by tests.
- **Attack scenario**: A user runs `crypcodile collect --exchange deribit --symbols BTC-PERPETUAL --channels trade`.
- **Blast radius**: The command crashes immediately upon start, preventing any data collection.
- **Mitigation**: Change to `if is_interactive_stdin():`.

#### [High] Challenge 2: Corrupted DB range prompts crash
- **Assumption challenged**: Database timestamps are always valid.
- **Attack scenario**: Database contains a corrupted record with a massive `local_ts` value (e.g. `9999999999999999999`).
- **Blast radius**: Running `export`, `replay`, or other interactive wizard commands crashes the CLI because `prompt_time_range_helper` raises `ValueError`.
- **Mitigation**: Wrap `fromtimestamp` in a robust try-except wrapper.

#### [Medium] Challenge 3: List of Struct types written as Null lists
- **Assumption challenged**: Empty schema exports are type-compatible with populated ones.
- **Attack scenario**: Downstream tooling parses exported empty parquet files expecting a list of structs (e.g. for `bids`), but reads `list[null]`.
- **Blast radius**: Downstream tool might reject the file or fail schema validation checks.
- **Mitigation**: Infer nested schema types from msgspec models more precisely.

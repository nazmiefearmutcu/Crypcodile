# Forensic Audit Report & Handoff

## Forensic Audit Report

**Work Product**: CLI commands and export implementation (Version 0.1.039)
**Profile**: General Project
**Verdict**: INTEGRITY VIOLATION

### Phase Results
- **Hardcoded output detection**: PASS — No hardcoded test outputs or fake logic bypasses were detected in `src/crypcodile/cli.py` or `src/crypcodile/client/export.py`.
- **Facade detection**: PASS — Interfaces are genuine and map to backend database querying, parquet storage, and CLI prompts.
- **Pre-populated artifact detection**: PASS — No pre-populated execution logs or test output artifacts were found.
- **Version Verification**: PASS — Version was successfully bumped to `0.1.039` in `pyproject.toml` and `src/crypcodile/__init__.py`.
- **Build and Run Verification**: FAIL — Pytest execution completed with 8 test failures in the Python unit test suite.

### Evidence
- **Node.js E2E Tests**: 117 passed, 0 failed.
- **Python Tests**: 710 passed, 8 failed.
- **Verbatim Error (test_cli_iv_surface_exits_0)**:
  ```
  FAILED tests/analytics/test_client_cli.py::test_cli_iv_surface_exits_0 - AssertionError: exit_code=1
  assert 1 == 0
   +  where 1 = <Result NameError("name 'CrypcodileClient' is not defined")>.exit_code
  ```
- **Verbatim Error (test_prompt_time_range_helper_overflow_fallback)**:
  ```
  FAILED tests/test_cli_repairs.py::test_prompt_time_range_helper_overflow_fallback - AttributeError: <module 'crypcodile.cli' from '/Users/nazmi/Crypcodile/src/crypcodile/cli.py'> does not have the attribute 'Catalog'
  ```

---

## 5-Component Handoff Report

### 1. Observation
- **Version Files**:
  - `pyproject.toml:3` has `version = "0.1.039"`.
  - `src/crypcodile/__init__.py:3` has `__version__ = "0.1.039"`.
  - `CHANGELOG.md` has details under `## [0.1.039] - 2026-06-18`.
- **Node.js E2E Test Suite**:
  - Running `npm test` inside `src/crypcodile/api_portal` completes with `117 passed, 0 failed`.
- **Python Test Suite Failures**:
  - Running `.venv/bin/pytest --ignore=tests/e2e` resulted in `8 failed, 710 passed, 1 warning`.
  - **F1: `test_cli_iv_surface_exits_0` and `test_cli_iv_surface_empty_exits_0`**:
    `NameError: name 'CrypcodileClient' is not defined` occurred inside `iv_surface_cmd` in `src/crypcodile/cli.py` (line 1778).
  - **F2: `test_invalid_selection_indexes_in_wizard`**:
    `OSError: pytest: reading from stdin while output is captured!` occurred in `_prompt_with_esc` (line 81) due to reading from `sys.stdin.readline()` during test execution.
  - **F3: `test_piped_query_command`**:
    Resulted in exit code 1 instead of 0.
  - **F4: `test_adversarial_timestamp_overflow`**:
    `Failed: DID NOT RAISE any of (<class 'OverflowError'>, <class 'OSError'>, <class 'ValueError'>)` because `client.scan` did not raise an exception when a 21-digit timestamp was supplied.
  - **F5: `test_adversarial_selection_wizard_non_digit`**:
    `AssertionError: assert ['INVALID'] == ['BTCUSDT']` because selection wizard loosely accepts non-digit input in the `else` block instead of looping.
  - **F6: `test_collect_is_interactive_nameerror_fix`**:
    `RuntimeError('asyncio.run() cannot be called from a running event loop')` because `collect` CLI command runs `asyncio.run` directly.
  - **F7: `test_prompt_time_range_helper_overflow_fallback`**:
    `AttributeError: <module 'crypcodile.cli' ...> does not have the attribute 'Catalog'` because `Catalog` is not imported at the module level in `cli.py`.

### 2. Logic Chain
1. Verification of a work product requires all build checks and behavioral checks to pass cleanly.
2. In my independent execution of pytest, 8 tests failed.
3. One failure (`test_cli_iv_surface_exits_0`) is caused by a syntax error (`NameError: name 'CrypcodileClient' is not defined`) in `src/crypcodile/cli.py`.
4. Another failure (`test_prompt_time_range_helper_overflow_fallback`) is caused by an `AttributeError` because the test attempts to patch `crypcodile.cli.Catalog` which is not imported at the module level.
5. Therefore, the implementation code contains defects and does not pass behavior/test verification, resulting in a verdict of `INTEGRITY VIOLATION`.

### 3. Caveats
- E2E python tests under `tests/e2e` could not be executed completely because the sandbox runner blocked commands from spawning server processes that touched system paths, and unsandboxed execution timed out waiting for user permission.

### 4. Conclusion
The CLI commands and export implementation do not pass behavior verification due to 8 failing Python tests, including a structural `NameError` in `iv_surface_cmd` and an `AttributeError` in mock patches. The work product is rejected as an **INTEGRITY VIOLATION**.

### 5. Verification Method
- Execute the unit tests from the workspace root:
  ```bash
  .venv/bin/pytest --ignore=tests/e2e
  ```
- To verify the `NameError`, run the `iv-surface` command:
  ```bash
  .venv/bin/python -m crypcodile.cli iv-surface --underlying BTC --at 1704067200000000000
  ```

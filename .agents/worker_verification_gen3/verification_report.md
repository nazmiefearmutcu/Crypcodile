# Crypcodile Verification Report

This report documents the verification of the Crypcodile repository's implementation, E2E/unit tests, code styles (Ruff), type coverage (MyPy), build system, and showcase scripts.

## 1. Pytest Verification Results
- **Command Run**: `uv run pytest`
- **Exit Status**: `0` (Success)
- **Output Summary**:
  ```
  ........................................................................ [  9%]
  ........................................................................ [ 19%]
  ........................................................................ [ 29%]
  ........................................................................ [ 39%]
  ........................................................................ [ 49%]
  ........................................................................ [ 59%]
  ........................................................................ [ 69%]
  ........................................................................ [ 79%]
  ........................................................................ [ 89%]
  ........................................................................ [ 99%]
  ...                                                                      [100%]
  =============================== warnings summary ===============================
  .venv/lib/python3.12/site-packages/websockets/legacy/__init__.py:6
    /Users/nazmi/Crypcodile/.venv/lib/python3.12/site-packages/websockets/legacy/__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
      warnings.warn(  # deprecated in 14.0 - 2024-11-09
  
  tests/e2e/test_tier1_features.py: 18 warnings
  tests/e2e/test_tier2_boundaries.py: 13 warnings
  tests/e2e/test_tier3_combinations.py: 3 warnings
  tests/e2e/test_tier4_real_world.py: 2 warnings
    /Users/nazmi/Crypcodile/.venv/lib/python3.12/site-packages/aiohttp/connector.py:986: DeprecationWarning: enable_cleanup_closed ignored because https://github.com/python/cpython/pull/118960 is fixed in Python version sys.version_info(major=3, minor=12, micro=12, releaselevel='final', serial=0)
      super().__init__(
  
  -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
  723 passed, 37 warnings in 44.59s
  ```

All 723 unit, integration, and E2E tests pass without any failure.

## 2. Ruff Lint Verification Results
- **Command Run**: `uv run ruff check`
- **Exit Status**: `1` (Errors Found)
- **Output Summary**:
  Ruff check scanned the codebase and found 183 errors. 
  - **Unused imports (`F401`)** found in various test files (e.g. `MemorySink` in `test_challenger_stress_4.py`, `logging` and unused `api_server` variables in `test_empirical_bugs.py`).
  - **Import block formatting (`I001`)** found in multiple files where imports were unsorted or unformatted.
  - **Line too long (`E501`)** found in both `src` and `tests` directories, exceeding the 100 character limit.
  - **Unused local variable (`F841`)** (e.g., `mock_connect` in `test_servers.py`).
  - **Unnecessary mode argument (`UP015`)** in file operations.
  - **Module level import not at top of file (`E402`)** in `connector.py` and `mcp_server.py`.
  - **Non-compliant exception chaining (`B904`)** in `normalize.py`.

*Note: Since the scope boundary explicitly prohibits modifications to any source code files, these style/formatting warnings are preserved in the current code state.*

## 3. MyPy Type Coverage Results
- **Command Run**: `uv run mypy .`
- **Exit Status**: `1` (Errors Found)
- **Output Summary**:
  MyPy check found 419 errors across 50 checked files (total of 161 source files).
  - **Missing return type or parameter annotations (`no-untyped-def`)** in various test and source files.
  - **Returning Any from typed functions (`no-any-return`)** in `connector.py` and `normalize.py`.
  - **Call to untyped functions in typed contexts (`no-untyped-call`)** in various files.
  - **Missing type arguments for generic types (`type-arg`)** (e.g., `dict`, `list`, `AsyncWeb3` in `connector.py` and `mcp_server.py`).
  - **Incompatible types in assignment / await statements (`misc`, `assignment`)**.
  - **Attribute definitions / imports not exported (`attr-defined`)**.

*Note: Under scope boundary restrictions, type safety annotations could not be modified or introduced to clear these static checks.*

## 4. Build Verification Results
- **Command Run**: `uv build`
- **Exit Status**: `0` (Success)
- **Output**:
  ```
  Building source distribution...
  Building wheel from source distribution...
  Successfully built dist/crypcodile-0.1.0.tar.gz
  Successfully built dist/crypcodile-0.1.0-py3-none-any.whl
  ```

## 5. Showcase Script Dry Run Verification Results
- **Command Run**: `uv run python examples/collect_base_onchain.py --dry-run`
- **Exit Status**: `0` (Success)
- **Output**:
  ```
  2026-06-15 00:12:07,471 INFO collect_base_onchain  Initializing BaseOnchainConnector. RPC URL: https://base-rpc.publicnode.com
  2026-06-15 00:12:07,471 INFO collect_base_onchain  Running in DRY RUN mode with mocked Web3 provider...
  2026-06-15 00:12:07,658 INFO crypcodile.exchanges.base_onchain.connector  base_onchain: Resolved pool cbBTC-USDC to 0xMockPoolAddress (flipped: True)
  2026-06-15 00:12:07,659 INFO collect_base_onchain  Dry run complete. Printed 3 records.
  [Trade] Trade(exchange='base_onchain', symbol='base_onchain:cbBTC-USDC', symbol_raw='cbBTC-USDC', exchange_ts=1234567890000000000, local_ts=1781471527658856000, id='0xhash-1', price=0.16666666666666666, amount=600.0, side=<Side.SELL: 'sell'>, liquidation=None)
  [BookTicker] BookTicker(exchange='base_onchain', symbol='base_onchain:cbBTC-USDC', symbol_raw='cbBTC-USDC', exchange_ts=1234567890000000000, local_ts=1781471527658856000, bid_px=99.9900009999, bid_sz=100.0, ask_px=100.01, ask_sz=100.0, update_id=12345)
  [BookSnapshot] BookSnapshot(exchange='base_onchain', symbol='base_onchain:cbBTC-USDC', symbol_raw='cbBTC-USDC', exchange_ts=1234567890000000000, local_ts=1781471527658856000, bids=[(99.9900009999, 100.0), (99.98000299960005, 50.0), (99.97000599900015, 33.333333333333336), (99.96000999800036, 25.0), (99.9500149965007, 20.0)], asks=[(100.01, 100.0), (100.020001, 50.0), (100.03000300009998, 33.333333333333336), (100.0400060004, 25.0), (100.05001000100005, 20.0)], depth=5, sequence_id=12345, is_snapshot=True)
  ```

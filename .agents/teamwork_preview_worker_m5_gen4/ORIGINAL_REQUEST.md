## 2026-06-15T01:45:27Z
You are worker_m5, a teamwork_preview_worker.
Your working directory is /Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m5_gen4/

Objective:
Implement Milestone 5: Extensible custom pool configuration in `src/crypcodile/exchanges/base_onchain/connector.py`, resolve codebase gaps, and verify via unit and integration tests.

Here are the detailed gaps and requirements to implement:
1. Safe and Robust IPC Persistence (IPCDict & _write_ipc_to_file):
   - Upgrade `IPCDict` to reload from disk if the file modification time (`st_mtime`) or size (`st_size`) has changed, rather than comparing static path strings.
   - Use POSIX advisory file locking (`fcntl.flock` with `LOCK_SH` for shared reads and `LOCK_EX` for exclusive writes) in `_sync` and `_write_ipc_to_file` to ensure inter-process safety.
   - Handle corrupt JSON safely. If loading fails, log a warning and use defaults/current memory dict, but DO NOT overwrite the entire file with empty dictionaries.
   - Perform writes atomically (write to `.tmp` file, flush, fsync, and `os.replace` to original path).
2. Input Validation for Custom Pools:
   - In `_register_custom_pools`, validate incoming configuration:
     - Validate that `type` is exactly `"uniswap_v3"` or `"aerodrome_v2"`. Throw `ValueError` for unsupported types.
     - Checksum and validate EVM addresses for `token0_address`, `token1_address`, and the pool `address` (if provided) using `AsyncWeb3.to_checksum_address`. Throw `ValueError` if malformed.
     - Validate `decimals0` and `decimals1` are integers between 0 and 36.
     - For Uniswap V3: Ensure `fee` is present and is a positive integer if no `address` is specified.
     - For Aerodrome V2: Ensure `stable` is present and is a boolean if no `address` is specified.
     - Pre-calculate `is_flipped = int(t1_addr, 16) < int(t0_addr, 16)` during registration and store it in the pool's spec dict for easy downstream lookup.
3. Dynamic Listing and Polling:
   - In `BaseOnchainConnector.list_instruments()`, retrieve all keys dynamically from `POOL_SPECS` on the fly.
   - In `list_instruments()`, derive the tick size for flipped pools using the quote asset's decimals (`decimals0`) rather than always using `decimals1`. Support a custom `tick_size` parameter in the custom pool config if provided.
   - In `BaseOnchainTransport._poll_loop()`, dynamically fetch keys from `POOL_SPECS` periodically to discover new custom pools registered at runtime, resolve them on the fly, and poll them.
4. Test Suite Verification:
   - Ensure the existing tests in `tests/exchanges/base_onchain/test_connector.py` pass.
   - Add new tests in `tests/exchanges/base_onchain/test_connector.py` (or a separate test file) covering:
     - Parameter validation (invalid type/decimals/missing fee/stable raising `ValueError`).
     - Modification sync reloading of `IPCDict`.
     - Flipped pool tick size validation.
     - Dynamic listing and polling validation.
   - Run tests and make sure 100% of tests pass.

MANDATORY INTEGRITY WARNING:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.

Output Requirements:
When done, write a report named `changes.md` in your working directory listing what you did, and run the tests to verify. Send a message to parent (ID: e72b6678-f50d-4a4f-9b0a-1b2f957b2a1e) summarizing the implementation and listing the test execution output.

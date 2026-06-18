# Forensic Audit Report — Milestone 5

**Work Product**: Extensible custom pool configuration in `src/crypcodile/exchanges/base_onchain/connector.py` and `tests/exchanges/base_onchain/test_connector.py`
**Profile**: General Project
**Verdict**: CLEAN

---

### Phase Results

#### Phase 1: Source Code Analysis
- **Check 1: Hardcoded test results and facade detection**: **PASS**
  - Checked `connector.py` and `test_connector.py`.
  - The implementation uses genuine Web3 RPC calls and custom calculations.
  - No shortcuts, hardcoded test results, or dummy facade implementations bypass the required calculations.
- **Check 2: File locking on `.custom_pools_ipc.json`**: **PASS**
  - Verified that shared/exclusive locks are properly implemented via `fcntl.flock`.
  - An exclusive lock (`fcntl.LOCK_EX`) is acquired on write operations.
  - A shared lock (`fcntl.LOCK_SH`) is acquired on read operations.
  - Uses a dedicated lock file (`.custom_pools_ipc.json.lock`) to avoid interfering with raw file read/write operations.
  - Implements atomic write using a `.tmp` file and `os.replace` to prevent corruption.
- **Check 3: Reloading check evaluates both modification time and size**: **PASS**
  - Verified that `IPCDict._sync` evaluates both `stat.st_mtime` and `stat.st_size` (in addition to file path changes) before triggering reload.
- **Check 4: Custom pool input validation**: **PASS**
  - Input validation is fully implemented in `_register_custom_pools`.
  - Correctly verifies pool type ("uniswap_v3" or "aerodrome_v2").
  - Validates EVM addresses via `web3.AsyncWeb3.to_checksum_address`.
  - Validates decimals to be integers between 0 and 36 (specifically discarding booleans via `isinstance(d, bool)` check).
  - Validates type-specific optional parameters: `fee` is validated for Uniswap V3 if address is not specified; `stable` is validated for Aerodrome V2 if address is not specified.
  - Raises `ValueError` for incorrect/missing inputs.
- **Check 5: Flipped pool status calculation and tick size derivation**: **PASS**
  - Flipped status is calculated at registration time: `int(str(t1_addr), 16) < int(str(t0_addr), 16)` and stored in `POOL_SPECS[sym]`.
  - In `list_instruments()`, tick size is derived from `decimals0` if `is_flipped` is `True` (since `token0` is the quote asset on-chain), and `decimals1` if `is_flipped` is `False`.
- **Check 6: Dynamic discovery and polling**: **PASS**
  - `_poll_loop` calls `_load_ipc_sync` on every polling interval which syncs the `POOL_SPECS` `IPCDict`.
  - Newly added custom pools are resolved concurrently and polled dynamically.
  - `list_instruments()` triggers dictionary sync and dynamically returns the registered custom pools.

#### Phase 2: Behavioral Verification
- **Check 7: Build and run test suite**: **PASS**
  - Successfully ran pytest. All 769 tests passed in the suite, including the 14 targeted unit/integration tests for the `base_onchain` connector.
- **Check 8: Dependency audit**: **PASS**
  - Core functionality (Web3 integration, IPC synchronization, dynamic polling, and math calculations) is implemented from scratch inside the codebase. Auxiliary dependencies are standard (e.g., `web3`, `pytest`, `asyncio`, etc.).

---

### Evidence

#### 1. File Locking and Reload Logic in `connector.py`
```python
def _write_ipc_to_file(name: str, data_dict: dict[str, Any]) -> None:
    try:
        data = {}
        ipc_file = _get_ipc_file()
        lock_file = ipc_file + ".lock"
        with open(lock_file, "a+") as lf:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                ...
                if success_reading:
                    data[name] = data_dict
                    tmp_file = ipc_file + ".tmp"
                    with open(tmp_file, "w") as tmp_f:
                        json.dump(data, tmp_f)
                        tmp_f.flush()
                        os.fsync(tmp_f.fileno())
                    os.replace(tmp_file, ipc_file)
            finally:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
...
```
```python
    def _sync(self) -> None:
        current_file = _get_ipc_file()
        try:
            if not os.path.exists(current_file):
                ...
                return
            
            stat = os.stat(current_file)
            mtime = stat.st_mtime
            size = stat.st_size
            
            if (current_file != self._last_ipc_file or 
                self._last_mtime != mtime or 
                self._last_size != size):
                
                content = ""
                lock_file = current_file + ".lock"
                with open(lock_file, "a+") as lf:
                    try:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_SH)
                        if os.path.exists(current_file):
                            with open(current_file) as f:
                                content = f.read().strip()
                    finally:
                        try:
                            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
...
```

#### 2. Input Validation and Flipped pool Logic in `connector.py`
```python
def _register_custom_pools(custom_pools: dict[str, dict[str, Any]] | None) -> None:
    if not custom_pools:
        return
    for sym, cfg in custom_pools.items():
        pool_type = cfg.get("type") or cfg.get("factory_type") or "uniswap_v3"
        if pool_type not in ("uniswap_v3", "aerodrome_v2"):
            raise ValueError(f"Unsupported pool type: {pool_type}")
            
        t0 = str(cfg.get("token0", "T0"))
        t1 = str(cfg.get("token1", "T1"))
        
        def check_address(addr: Any, name: str) -> str:
            if not addr:
                raise ValueError(f"Address for {name} is missing or empty")
            try:
                res = web3.AsyncWeb3.to_checksum_address(addr)
                ...
                return res
            except Exception as e:
                raise ValueError(f"Malformed EVM address for {name}: {addr}") from e
                
        t0_addr = check_address(cfg.get("token0_address") or cfg.get("token0"), "token0")
        t1_addr = check_address(cfg.get("token1_address") or cfg.get("token1"), "token1")
        ...
        d0 = cfg.get("decimals0", 18)
        d1 = cfg.get("decimals1", 18)
        if not isinstance(d0, int) or isinstance(d0, bool) or not (0 <= d0 <= 36):
            raise ValueError(f"decimals0 must be an integer between 0 and 36, got {d0}")
        if not isinstance(d1, int) or isinstance(d1, bool) or not (0 <= d1 <= 36):
            raise ValueError(f"decimals1 must be an integer between 0 and 36, got {d1}")
        ...
        try:
            is_flipped = int(str(t1_addr), 16) < int(str(t0_addr), 16)
        except Exception:
            is_flipped = False
...
```

#### 3. Tick Size Derivation in `connector.py`
```python
            if "tick_size" in spec:
                tick_size = float(spec["tick_size"])
            elif sym in custom_ticks:
                tick_size = custom_ticks[sym]
            else:
                is_flipped = spec.get("is_flipped", False)
                quote_decimals = int(spec["decimals0"]) if is_flipped else int(spec["decimals1"])
                tick_size = 10 ** (-quote_decimals)
```

#### 4. Test Suite Execution Output
```
769 passed, 36 warnings in 41.63s
```
All tests passed without errors.

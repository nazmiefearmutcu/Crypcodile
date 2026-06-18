# Milestone 5 Analysis: Extensible Custom Pool Configuration Gaps and Recommendations

## 1. Executive Summary
We investigated the extensible custom pool configuration implementation in `src/crypcodile/exchanges/base_onchain/connector.py` and its tests in `tests/exchanges/base_onchain/test_connector.py`. 
While standard pools and registration functionality are partially supported, the current implementation is not production-ready. We identified significant gaps in **cross-process file-locking & reloading logic**, **input validation of pool parameters**, and **dynamic listing & polling of pools registered at runtime**. This report details our findings and outlines a comprehensive implementation strategy for the worker.

---

## 2. Examination of `connector.py` and `_register_custom_pools`
Custom pools are registered using `_register_custom_pools()`. This function populates two global `IPCDict` objects:
- `TOKENS`: Maps symbol names to checksummed hex addresses.
- `POOL_SPECS`: Maps symbol names to pool specification dictionaries containing type, tokens, fee, decimals, stable flag, and/or address.

### The `IPCDict` Class
`IPCDict` subclasses `dict` and implements dynamic file sync and write-back functionality targeting `.custom_pools_ipc.json` (or the path defined by `CUSTOM_POOLS_IPC_FILE` env var). 

#### Sync Method
```python
def _sync(self) -> None:
    current_file = _get_ipc_file()
    if current_file != self._last_ipc_file:
        dict.clear(self)
        dict.update(self, self._default)
        try:
            if os.path.exists(current_file):
                with open(current_file, "r") as f:
                    content = f.read().strip()
                    if content:
                        file_data = json.loads(content)
                        if self._name in file_data:
                            dict.update(self, file_data[self._name])
        except Exception:
            pass
        self._last_ipc_file = current_file
```

---

## 3. Review of Existing Test Behaviors
The test suite in `tests/exchanges/base_onchain/test_connector.py` includes `test_custom_pool_configuration_and_dynamic_listing()`, which mocks `web3.AsyncWeb3` and instantiates a `BaseOnchainConnector` with a single custom pool passed in `custom_pools` and `symbols`.
- It verifies that the pool is registered in `POOL_SPECS`, the token is registered in `TOKENS`, and `list_instruments()` successfully returns a spot instrument with correct base/quote symbols and tick size (derived from decimals).
- **Limitation**: This test only covers a basic success path where the pool configuration is passed to the constructor. It does not test dynamic registration across processes, corrupt JSON handling, parameter omission, or dynamic listing of pools added post-init.

---

## 4. Key Gaps Identified

### Gap 1: Safe and Robust IPC Persistence (`IPCDict` Flaws)
1. **No File Locking**: Multiple processes (e.g. CLI, REST API, Connector) reading and writing concurrently will corrupt the JSON or overwrite each other's changes (lost updates).
2. **No Cache Invalidation**: `_sync()` only reloads the file if the path `_get_ipc_file()` changes. Since the path remains static during runtime, a process will *never* reload updates written to the IPC file by another process.
3. **Synchronous File I/O on Event Loop**: Doing `open().read()` synchronously inside standard dictionary access methods blocks the async event loop, causing latency spikes.
4. **Destructive Corrupt File Recovery**: If reading fails (due to invalid/corrupted JSON), it initializes an empty dict `{}` and writes it back, wiping out all other keys/sections (e.g., `TOKENS` will overwrite `POOL_SPECS` or vice versa).
5. **Out-of-Order Asynchronous Writes**: Writing via `asyncio.to_thread` without serialization can result in concurrent write tasks completing out-of-order.

### Gap 2: Validation of Custom Pool Parameters
1. **Invalid Types Default to Aerodrome**: If `type` is misspelled or invalid, it bypasses the `uniswap_v3` check and defaults to treat the pool as `aerodrome_v2` during resolution and polling:
   ```python
   elif spec["type"] == "uniswap_v3":
       ...
   else: # aerodrome_v2
       ...
   ```
2. **Missing Configuration Fields**:
   - `fee` is required for Uniswap V3 factory lookups. If missing or non-integer, `int(spec["fee"])` raises a `KeyError`/`ValueError` during address resolution.
   - `stable` is required for Aerodrome V2 factory lookups. If missing, `bool(spec["stable"])` raises a `KeyError`.
3. **Malformed Addresses**: If `token0_address` or `token1_address` are missing, it defaults to the token symbols. If these symbols are not hex strings (e.g., `"TESTCUSTOM"`), `AsyncWeb3.to_checksum_address()` raises a `Web3ValidationError` at runtime.
4. **No Type/Value Casting**: Non-integer decimals, string values, or negative fee values are not validated or normalized during registration, causing down-stream calculation failures.

### Gap 3: Instruments Listing and Polling Gaps
1. **Static Polling and Listing**: `list_instruments()` and the polling loop in `_poll_loop` only process symbols defined in the connector's initial `symbols` list. Custom pools added to `.custom_pools_ipc.json` by another process at runtime will never be polled or returned in `list_instruments()`.
2. **No Custom Tick Size**: Tick size defaults to `10 ** (-decimals1)` for custom pools. There is no mechanism to specify a custom `tick_size` in the pool configuration.

---

## 5. Proposed Implementation Strategy

### Step 1: Input Validation
Refactor `_register_custom_pools` to validate each custom pool's configuration schema before saving.
```python
import re
from web3 import AsyncWeb3

HEX_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

def _register_custom_pools(custom_pools: dict[str, dict[str, Any]] | None) -> None:
    if not custom_pools:
        return
    for sym, cfg in custom_pools.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Pool configuration for {sym} must be a dictionary.")
            
        pool_type = cfg.get("factory_type") or cfg.get("type") or "uniswap_v3"
        if pool_type not in ("uniswap_v3", "aerodrome_v2"):
            raise ValueError(f"Unsupported pool type: {pool_type} for {sym}. Must be 'uniswap_v3' or 'aerodrome_v2'.")

        t0 = str(cfg.get("token0", ""))
        t1 = str(cfg.get("token1", ""))
        if not t0 or not t1:
            raise ValueError(f"Pool {sym} must specify both token0 and token1.")

        # Resolve addresses
        t0_addr = cfg.get("token0_address") or cfg.get("token0")
        t1_addr = cfg.get("token1_address") or cfg.get("token1")
        
        # If no address is specified, we must have valid token addresses for the factory lookup
        if "address" not in cfg:
            if not t0_addr or not HEX_ADDR_RE.match(str(t0_addr)):
                raise ValueError(f"Invalid or missing token0_address for pool {sym}.")
            if not t1_addr or not HEX_ADDR_RE.match(str(t1_addr)):
                raise ValueError(f"Invalid or missing token1_address for pool {sym}.")
        
        # If address is specified directly
        if "address" in cfg:
            addr = str(cfg["address"])
            if not HEX_ADDR_RE.match(addr):
                raise ValueError(f"Invalid pool address format for {sym}: {addr}")
            cfg["address"] = AsyncWeb3.to_checksum_address(addr)

        # Decimals validation
        try:
            decimals0 = int(cfg.get("decimals0", 18))
            decimals1 = int(cfg.get("decimals1", 18))
            if not (0 <= decimals0 <= 36) or not (0 <= decimals1 <= 36):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError(f"Decimals must be integers between 0 and 36 for {sym}.")

        spec = {
            "type": pool_type,
            "token0": t0,
            "token1": t1,
            "decimals0": decimals0,
            "decimals1": decimals1,
        }

        # Type-specific validation
        if pool_type == "uniswap_v3":
            if "address" not in cfg:
                if "fee" not in cfg:
                    raise ValueError(f"Uniswap V3 pool {sym} requires a 'fee' parameter.")
                try:
                    spec["fee"] = int(cfg["fee"])
                except (ValueError, TypeError):
                    raise ValueError(f"Fee must be an integer for {sym}.")
            elif "fee" in cfg:
                spec["fee"] = int(cfg["fee"])
        else: # aerodrome_v2
            if "address" not in cfg:
                if "stable" not in cfg:
                    raise ValueError(f"Aerodrome V2 pool {sym} requires a 'stable' boolean parameter.")
                spec["stable"] = bool(cfg["stable"])
            elif "stable" in cfg:
                spec["stable"] = bool(cfg["stable"])

        if "address" in cfg:
            spec["address"] = cfg["address"]
        if "tick_size" in cfg:
            spec["tick_size"] = float(cfg["tick_size"])

        # Insert token mappings
        TOKENS[t0] = AsyncWeb3.to_checksum_address(t0_addr)
        TOKENS[t1] = AsyncWeb3.to_checksum_address(t1_addr)
        
        POOL_SPECS[sym] = spec
```

### Step 2: Thread-Safe and Lock-Guarded File Synchronization (`IPCDict` Upgrade)
Improve `IPCDict` to load and write using shared/exclusive file locks (`fcntl.flock`) and track file modifications using `mtime` and `size`.

```python
import fcntl

class IPCDict(dict[str, Any]):
    def __init__(self, name: str, default_data: dict[str, Any] | None = None) -> None:
        super().__init__(default_data or {})
        self._name = name
        self._default = default_data or {}
        self._last_mtime = 0.0
        self._last_size = -1

    def _sync(self) -> None:
        current_file = _get_ipc_file()
        try:
            if not os.path.exists(current_file):
                # If file doesn't exist, ensure we are using defaults
                if self._last_mtime != 0.0:
                    dict.clear(self)
                    dict.update(self, self._default)
                    self._last_mtime = 0.0
                    self._last_size = -1
                return

            stat = os.stat(current_file)
            mtime = stat.st_mtime
            size = stat.st_size
            
            # Reload if modified time or size has changed
            if mtime != self._last_mtime or size != self._last_size:
                with open(current_file, "r") as f:
                    # Shared read lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    content = f.read().strip()
                    if content:
                        file_data = json.loads(content)
                        dict.clear(self)
                        dict.update(self, self._default)
                        if self._name in file_data:
                            dict.update(self, file_data[self._name])
                self._last_mtime = mtime
                self._last_size = size
        except Exception as e:
            log.error(f"Error syncing IPCDict {self._name}: {e}")
```

For the writing logic:
```python
def _write_ipc_to_file(name: str, data_dict: dict[str, Any]) -> None:
    ipc_file = _get_ipc_file()
    try:
        # Open in r+ or create new file
        mode = "r+" if os.path.exists(ipc_file) else "w+"
        with open(ipc_file, mode) as f:
            # Exclusive write lock
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            
            content = f.read().strip()
            data = {}
            if content:
                try:
                    data = json.loads(content)
                except Exception:
                    # Log warning, do not destroy other fields
                    log.warning(f"Corrupt JSON found in {ipc_file}, writing defaults.")
            
            data[name] = data_dict
            
            # Write back atomically
            f.seek(0)
            json.dump(data, f)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        log.error(f"Failed to write IPC file {ipc_file}: {e}")
```

### Step 3: Dynamic listing & polling support
Update `_poll_loop` inside `BaseOnchainTransport` and `list_instruments` inside `BaseOnchainConnector` to dynamically fetch active pools from `POOL_SPECS`.

1. **In `list_instruments`**:
   ```python
   async def list_instruments(self) -> list[Instrument]:
       instruments = []
       # Support both constructor-passed symbols and dynamic pools registered in POOL_SPECS
       all_symbols = set(self.symbols) | set(POOL_SPECS.keys())
       
       for sym in all_symbols:
           spec = POOL_SPECS.get(sym)
           if not spec:
               continue
           # Support custom tick_size configuration parameter
           tick_size = spec.get("tick_size") or custom_ticks.get(sym, 10 ** (-int(spec.get("decimals1", 6))))
           instruments.append(
               Instrument(
                   canonical=f"base_onchain:{sym}",
                   exchange="base_onchain",
                   symbol_raw=sym,
                   kind=Kind.SPOT,
                   base=spec["token0"],
                   quote=spec["token1"],
                   tick_size=tick_size,
               )
           )
       return instruments
   ```

2. **In `_poll_loop`**:
   Instead of looping only over `self.symbols`, periodically retrieve keys from `POOL_SPECS` and resolve them on the fly:
   ```python
   # Inside the main polling loop while self._connected:
   # Dynamically discover newly registered pool symbols from the IPC specs dict
   active_symbols = set(self.symbols) | set(POOL_SPECS.keys())
   
   resolution_tasks = [
       resolve_single_pool(sym)
       for sym in active_symbols
       if sym not in resolved_pools
   ]
   if resolution_tasks:
       await asyncio.gather(*resolution_tasks, return_exceptions=True)
   ```

---

## 6. Implementation Verification Plan
To verify the implemented worker changes:
1. Run all existing tests to ensure no regressions:
   `uv run pytest tests/exchanges/base_onchain/test_connector.py`
2. Write unit tests targeting validation:
   - Verify that invalid pool types raise a `ValueError`.
   - Verify that missing fee parameters for `uniswap_v3` raise a `ValueError`.
   - Verify that missing stable parameters for `aerodrome_v2` raise a `ValueError`.
   - Verify that malformed hex addresses fail validation.
3. Write multi-process integration tests:
   - Spawn a subprocess that updates `.custom_pools_ipc.json` with a new custom pool.
   - Verify that the running connector process automatically picks it up via `POOL_SPECS.keys()` reload detection and starts polling/resolving it.
   - Verify that `list_instruments()` dynamically lists it.

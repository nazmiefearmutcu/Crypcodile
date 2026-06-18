# Handoff Report: Milestone 5 Extensible Custom Pool Configuration Analysis

## 1. Observation
- In `src/crypcodile/exchanges/base_onchain/connector.py` at lines 206–240:
  ```python
  def _register_custom_pools(custom_pools: dict[str, dict[str, Any]] | None) -> None:
      if not custom_pools:
          return
      for sym, cfg in custom_pools.items():
          pool_type = cfg.get("factory_type") or cfg.get("type") or "uniswap_v3"
          t0 = str(cfg.get("token0", "T0"))
          t1 = str(cfg.get("token1", "T1"))
          t0_addr = str(cfg.get("token0_address") or cfg.get("token0"))
          t1_addr = str(cfg.get("token1_address") or cfg.get("token1"))
          ...
          spec = {
              "type": pool_type,
              "token0": t0,
              "token1": t1,
              "decimals0": cfg.get("decimals0", 18),
              "decimals1": cfg.get("decimals1", 18),
          }
          if "fee" in cfg:
              spec["fee"] = cfg["fee"]
          if "stable" in cfg:
              spec["stable"] = cfg["stable"]
          if "address" in cfg:
              spec["address"] = cfg["address"]
              
          POOL_SPECS[sym] = spec
  ```
  There is no schema validation on incoming configs or parameters (e.g. `pool_type`, `fee`, `stable`, hex address format).
- In `src/crypcodile/exchanges/base_onchain/connector.py` at lines 72–88, `IPCDict._sync` is implemented as:
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
  This class has no file locking (`flock` / `fcntl`) during read/write. The reloading check only compares the path (`self._last_ipc_file`), meaning updates from other processes during runtime are ignored. Furthermore, reading file content synchronously inside standard dict accessors blocks the async event loop.
- In `src/crypcodile/exchanges/base_onchain/connector.py` at lines 37–58, `_write_ipc_to_file` is defined:
  ```python
  def _write_ipc_to_file(name: str, data_dict: dict[str, Any]) -> None:
      try:
          data = {}
          ipc_file = _get_ipc_file()
          if os.path.exists(ipc_file):
              try:
                  with open(ipc_file, "r") as f:
                      content = f.read().strip()
                      if content:
                          data = json.loads(content)
              except Exception:
                  pass
          data[name] = data_dict
          ...
  ```
  If reading the file fails or throws an exception due to corruption, it defaults to `data = {}` and overwrites the file, wiping out all other keys/sections.
- In `src/crypcodile/exchanges/base_onchain/connector.py` at lines 861–885, `list_instruments` loops over `self.symbols`:
  ```python
      async def list_instruments(self) -> list[Instrument]:
          ...
          for sym in self.symbols:
              spec = POOL_SPECS.get(sym)
              if not spec:
                  continue
  ```
- In `src/crypcodile/exchanges/base_onchain/connector.py` at lines 519–523:
  ```python
                      resolution_tasks = [
                          resolve_single_pool(sym)
                          for sym in self.symbols
                          if sym not in resolved_pools
                      ]
  ```
  Neither instrument listing nor polling loop supports processing new symbols added dynamically at runtime (they only scan the static `self.symbols` array configured at connector initialization).

---

## 2. Logic Chain
1. Since `_sync()` in `IPCDict` only reloads files when the file *path* string differs (`current_file != self._last_ipc_file`), any new custom pools registered in the file `.custom_pools_ipc.json` by other processes will not trigger a reload in the current running connector process.
2. Since `_register_custom_pools` has no input validation, malformed configurations (e.g. invalid type names, non-hex addresses, missing fields) will trigger silent defaults (e.g. invalid type treated as `"aerodrome_v2"`) or runtime errors (e.g. `KeyError` or `ValueError` inside polling and resolution tasks).
3. Since `list_instruments()` and the polling loop (`_poll_loop`) scan only the static list `self.symbols` populated during connector initialization, any dynamically added pool specs will not be listed as instruments or polled, even if they are correctly stored in `POOL_SPECS`.

---

## 3. Caveats
- We assumed that cross-process modification of the custom pools configuration is a core functional requirement (as implied by the existence of `.custom_pools_ipc.json`).
- Performance overhead of file system polling: Checking `os.stat` on every dict accessor inside `IPCDict` might add minor overhead, though it can be mitigated using a periodic async synchronization task instead of on-access sync.

---

## 4. Conclusion
The current extensible custom pool configuration implementation suffers from:
- Lack of parameter schema validation during registration.
- Suboptimal IPC mechanisms (`IPCDict`) lacking file locking, corrupt JSON protection, and modification checks (leading to lost updates and dynamic configuration ignoring).
- Static design of `list_instruments` and polling loops which ignore dynamically added pools.

A series of updates to `IPCDict` (file locking, mtime checks), `_register_custom_pools` (input schema validation), and `_poll_loop` / `list_instruments` (dynamic symbol discovery from `POOL_SPECS`) is required to make the system production-ready.

---

## 5. Verification Method
1. Run standard unit tests using:
   `uv run pytest tests/exchanges/base_onchain/test_connector.py`
2. Validate new schema validations: verify that calling `_register_custom_pools` with invalid parameters (e.g. fee tier omitted on Uniswap V3, missing token addresses) raises a `ValueError`.
3. Validate dynamic synchronization: Write custom pools directly to `.custom_pools_ipc.json` via a separate process/mock, and verify that the connector automatically reloads, lists the instrument, and starts polling without restarting.

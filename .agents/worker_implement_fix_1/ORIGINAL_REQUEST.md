## 2026-06-15T01:01:00Z
Please modify `/Users/nazmi/Crypcodile/src/crypcodile/api_server.py` to fix test state pollution:

1. Import `sys`.
2. Update the initialization of `PAYMENTS_FILE` to check if `pytest` is running. If so, use a test-specific path (e.g., `/Users/nazmi/Crypcodile/.payments_db_test.json`) and delete it if it already exists:
   ```python
   import sys
   
   PAYMENTS_FILE = "/Users/nazmi/Crypcodile/.payments_db.json"
   if "pytest" in sys.modules:
       PAYMENTS_FILE = "/Users/nazmi/Crypcodile/.payments_db_test.json"
       try:
           if os.path.exists(PAYMENTS_FILE):
               os.remove(PAYMENTS_FILE)
       except Exception:
           pass
   ```
3. Implement `PersistentDict` subclass of `dict` which overrides `clear` to also clear the disk file:
   ```python
   class PersistentDict(dict[str, Any]):
       def clear(self) -> None:
           dict.clear(self)
           _save_db_file({})
   ```
4. Set the global `PAYMENTS_DB` to an instance of `PersistentDict`:
   ```python
   PAYMENTS_DB: dict[str, dict[str, Any]] = PersistentDict(_load_db_file())
   ```
5. Replace internal calls of `PAYMENTS_DB.clear()` in `api_server.py` (e.g., around lines 107, 333, and 395) with `dict.clear(PAYMENTS_DB)` to avoid triggering the disk file overwrite during normal request handling.
6. Once the modifications are done, run `uv run pytest` in `/Users/nazmi/Crypcodile` to ensure all 758 tests pass cleanly.
7. Run `uv build` to make sure it still builds successfully.
8. Save your findings in a handoff report at `/Users/nazmi/Crypcodile/.agents/worker_implement_fix_1/handoff.md` and reply with your conversation ID and a summary of results.

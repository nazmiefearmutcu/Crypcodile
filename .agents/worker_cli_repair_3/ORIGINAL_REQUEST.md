## 2026-06-18T18:18:53Z

Your working directory is /Users/nazmi/Crypcodile/.agents/worker_cli_repair_3.
Your role: teamwork_preview_worker.
Your task is to fix the following issues in the code and test suites:

1. **Fix CLI imports in src/crypcodile/cli.py**:
   - In both `iv_surface_cmd` and `term_structure_cmd`, ensure that `from crypcodile.client.client import CrypcodileClient` is imported locally at the top of the functions, so that `CrypcodileClient` is defined even when running outside the `else:` interactive blocks.
   - In `prompt_with_autocomplete` (around line 81), change the fallback `return _prompt_with_esc(text, default=default)` to `return typer.prompt(text, default=default)`. This ensures that in tests where `typer.prompt` is mocked, it will use the mock instead of trying to read from the captured stdin (avoiding OSErrors).
2. **Fix tests/test_cli_repairs.py**:
   - Change all `async def` test functions to normal synchronous `def` functions, removing the `@pytest.mark.asyncio` decorator.
   - For `test_piped_query_command` and `test_piped_query_command_empty`, do not patch `sys.stdin.read`. Instead, use the `input` argument of `runner.invoke` (e.g. `runner.invoke(app, ["query", "--data-dir", str(tmp_path)], input="SELECT 42 AS val")`).
   - For `test_prompt_time_range_helper_overflow_fallback`, patch `crypcodile.store.catalog.Catalog` instead of `crypcodile.cli.Catalog`.
   - Update `test_adversarial_timestamp_overflow(tmp_path)` to first create the channel directory on disk: `(tmp_path / "exchange=deribit" / "channel=trade").mkdir(parents=True)`. This bypasses the empty-database check and allows `fromtimestamp` to trigger the expected datetime exception.
3. **Fix tests/test_cli_adversarial.py**:
   - Change all `async def` test functions to normal synchronous `def` functions, removing the `@pytest.mark.asyncio` decorator.
   - For `test_invalid_selection_indexes_in_wizard()`, adjust the mocked inputs so that the custom symbol input is correctly processed by the mocked `typer.prompt` without raising stdin capture OSErrors.
4. Run the full test suites:
   - Python tests: `uv run pytest` (with BypassSandbox=True to allow access outside workspace).
   - Node.js tests: `npm test` in src/crypcodile/api_portal (with BypassSandbox=True if sandboxing blocks).
5. Verify package build: `uv build` (with BypassSandbox=True).
6. Message back when complete.

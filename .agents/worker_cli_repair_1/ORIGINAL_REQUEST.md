## 2026-06-18T17:51:13Z

<USER_REQUEST>
Your working directory is /Users/nazmi/Crypcodile/.agents/worker_cli_repair_1.
Your role: teamwork_preview_worker.
Your task is to implement the following CLI terminal commands repairs and verify they pass the tests:

1. **Query command piped multiline**: In `src/crypcodile/cli.py`, inside `query()` command, if `not sql`:
   Check `is_interactive_stdin()`.
   If interactive, call `sql = typer.prompt("SQL query")`.
   If NOT interactive, read all of `sys.stdin.read().strip()` (which handles piped multi-line queries), and if resulting `sql` is empty, print `"Error: SQL query is required and stdin is empty."` to stderr and raise `typer.Exit(code=1)`.
2. **Non-Interactive prompt bypass**: In all commands that prompt for missing required parameters (e.g. `export`, `replay`, `collect`, `funding_apr_cmd`, `basis_cmd`, `iv_surface_cmd`, `term-structure_cmd`), check `is_interactive_stdin()`. If `not is_interactive_stdin()` and a required option (like symbol, symbols, channel, channels, exchange, etc.) is missing or None, print a clear error to stderr and raise `typer.Exit(code=1)` instead of attempting to prompt.
3. **Interactive shell on non-TTY**: In `shell` command, check `is_interactive_stdin()`. If not interactive, set `is_interactive = False` and read input lines using standard Python `input()` instead of `session.prompt()` (which requires a TTY and crashes).
4. **Shell subcommand Exit crash**: In `shell` command's try-except block executing subcommands, catch `click.exceptions.Exit` explicitly and `pass` (or do nothing) to prevent the interactive shell from terminating when a subcommand exits with code 1.
5. **Sparkline NaN/Inf float validation**: In `make_sparkline()` (lines 1042-1057), filter the input `prices` list to exclude any non-finite floats using `math.isfinite(p)` (e.g. `prices = [p for p in prices if p is not None and math.isfinite(p)]`). If resulting `prices` list has length < 2, return `""`.
6. **Selection wizard channel and symbol checks**: In `select_collect_params_interactively`, check if choice contains *any* digit; if so, treat only as index selection (validate all indexes are valid; if not, print error and retry). If no digits, treat as custom string name selection and validate that custom names are valid (e.g. for channels, ensure custom channels are in `valid_channels`; for symbols, ensure custom symbols are non-empty).
7. **Upgrade output capture**: In `update` command, capture stdout and stderr of the pip upgrade subprocess via `capture_output=True`, and print `result.stderr` details if `result.returncode != 0`.
8. **Semantic version comparison**: In `update` command, use `packaging.version.Version` (with a try-except fallback to the regex comparison) to compare semantic versions cleanly so that stable builds are not upgraded to older beta pre-releases.
9. **Basis mutually exclusive options check**: In `basis_cmd`, raise a validation error and exit 1 if both `--perp` and either `--future`/`--spot` are specified simultaneously. If perp is None and only `--future` or only `--spot` is specified, skip prompting for basis mode, set mode implicitly to futures/spot, and only prompt for the missing futures/spot symbol. Also, if non-interactive, raise error if spot/futures or perp are missing.
10. **Exception wrapping**: Wrap `client.query` (in `query`), `client.funding_apr` (in `funding_apr_cmd`) in a try-except block to output a clean error to stderr and exit with code 1 instead of spilling DuckDB tracebacks.
11. **Prune option distinct scans**: Optimize `get_available_option_underlyings` and `get_available_option_snapshots` by globbing the latest date partition directory on disk (e.g., `date=*`) first and querying only that partition from DuckDB, falling back to a full database query if empty/fails.
12. **Safe timestamp formatting**: Safe-wrap the `datetime.fromtimestamp` calls in options snapshot listings (in `iv_surface_cmd` and `term-structure_cmd`) using a try-except block to avoid crashing on corrupt metadata timestamps.
13. **Uvicorn import fallback check**: Wrap `import uvicorn` inside the FastAPI fallback server block in `api` command, showing a clean error and exiting 1 if not present.
14. **Empty DataFrame export**: Fix `src/crypcodile/client/export.py` when `frames` is empty (no rows match). Instead of returning `pl.DataFrame()` with 0 columns, construct an empty DataFrame with the proper channel schema (e.g., call a helper `_get_empty_df_for_channel(catalog, channel)` that queries the catalog view with `LIMIT 0`, or parses the msgspec class fields for the channel and adds partition columns).
15. **Verify and run tests**:
    - Run the entire test suite via `uv run pytest`.
    - Run Node.js E2E tests (`npm test` in `src/crypcodile/api_portal`).
    - Add new Python unit/integration tests covering these CLI fixes and edge cases (e.g. piping queries, non-interactive validation failures, basis arg exclusivity) in `tests/test_cli.py` or new files under `tests/`.
16. **Release Packaging**:
    - Bump the package version to `0.1.039` in `pyproject.toml` and `src/crypcodile/__init__.py`.
    - Document all changes in `CHANGELOG.md` under `## [0.1.039]`.
    - Build the package using `uv build` or Hatch to ensure it compiles successfully.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Please execute and message back with handoff report including test outputs when complete.
</USER_REQUEST>

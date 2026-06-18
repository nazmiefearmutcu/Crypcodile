# Handoff Report: CLI Terminal Commands Audit

## 1. Observation
Direct observations from scanning `src/crypcodile/cli.py` and referencing related test files (`tests/test_cli.py`, `tests/analytics/test_client_cli.py`):

### Verbatim Code Snippets & References

#### Observation 1: Lack of General Exception Handling in `query` Command
* **File & Line**: `src/crypcodile/cli.py:675-694`
* **Snippet**:
  ```python
  @app.command()
  def query(
      sql: Annotated[str, typer.Argument(help="DuckDB SQL query to execute.")] = "",
      data_dir: _DataDirOpt = Path("data"),
  ) -> None:
      ...
      client = CrypcodileClient(data_dir=data_dir)
      df = client.query(sql)
      typer.echo(df)
  ```
* **Note**: No try-except wrapping `client.query(sql)` or other command actions across most commands.

#### Observation 2: Single-Line Reading on Piped Input Fallback
* **File & Line**: `src/crypcodile/cli.py:157-165` (inside `_prompt_with_esc`)
* **Snippet**:
  ```python
          # Fallback if stdin is not a TTY (e.g., tests)
          if not sys.stdin.isatty():
              line = sys.stdin.readline()
              if not line:
                  raise KeyboardInterrupt
              line = line.rstrip("\r\n")
              if not line and default is not None:
                  return str(default)
              return line
  ```
* **Note**: `readline()` only reads a single line, causing piped multi-line SQL queries to truncate.

#### Observation 3: Prompts Executed and Exiting 0 on Closed Stdin (Non-Interactive)
* **File & Line**: `src/crypcodile/cli.py:772-784` (in `export`), similar logic in other commands.
* **Snippet**:
  ```python
      if not channel:
          channel = typer.prompt("Channel (e.g. trade)")
      if not symbols:
          sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel=channel)
          symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
  ```
* **Note**: When `is_interactive` is `False`, missing arguments still trigger `typer.prompt` or `prompt_symbol`. If stdin is closed, `_prompt_with_esc` raises `KeyboardInterrupt` which is caught at line 220, printing `\nCancelled.\n` and raising `typer.Exit(code=0)`.

#### Observation 4: TTY Assumed for Interactive Shell Default
* **File & Line**: `src/crypcodile/cli.py:1934-1943`
* **Snippet**:
  ```python
      if not is_pytest:
          session = PromptSession(
              history=InMemoryHistory(),
              auto_suggest=AutoSuggestFromHistory(),
              completer=WordCompleter(
                  words=list(commands.keys()) + ["exit", "quit", "help"],
                  meta_dict={**commands, "exit": "Exit the shell", "quit": "Exit the shell", "help": "Show help"},
                  ignore_case=True
              ),
              complete_while_typing=True
          )
  ```
* **Note**: If `sys.stdin.isatty()` is `False`, `PromptSession` creation/instantiation fails or crashes.

#### Observation 5: Full Table Scans for Interactive Help
* **File & Line**: `src/crypcodile/cli.py:1524-1552`
* **Snippet**:
  ```python
  def get_available_option_underlyings(data_dir: Path) -> list[str]:
      ...
          df = cat.query("SELECT DISTINCT underlying FROM options_chain ORDER BY underlying")
      ...
  def get_available_option_snapshots(data_dir: Path, underlying: str | None = None) -> list[int]:
      ...
          sql = f"SELECT DISTINCT local_ts FROM options_chain{u_filter} ORDER BY local_ts DESC LIMIT 5"
          df = cat.query(sql)
  ```
* **Note**: No date partition pruning is performed, causing a full table distinct scan in DuckDB.

#### Observation 6: Silent Prioritization of Mutually Exclusive Options
* **File & Line**: `src/crypcodile/cli.py:1505-1515` (in `basis`)
* **Snippet**:
  ```python
      if perp is not None:
          df = client.perp_basis(perp, start, end)
      elif future is not None and spot is not None:
          df = client.spot_future_basis(future, spot, start, end, expiry_ns=expiry)
  ```
* **Note**: Specifying all three silently triggers `perp_basis` and ignores the spot/future parameters.

#### Observation 7: Unwrapped `fromtimestamp` in Snapshot Listings
* **File & Line**: `src/crypcodile/cli.py:1602` and `1680`
* **Snippet**:
  ```python
  dt_str = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
  ```
* **Note**: A corrupted timestamp will crash `fromtimestamp` with `ValueError`/`OSError`, which is not caught inside this printing loop.

#### Observation 8: Missing Output Option Validation
* **File & Line**: `src/crypcodile/cli.py:751-754` (in `export` options)
* **Snippet**:
  ```python
      fmt: Annotated[
          str,
          typer.Option("--fmt", help="Output format: parquet|csv|arrow|json|jsonl."),
      ] = "parquet",
  ```
* **Note**: Pass-through to `client.export` which will raise an unhandled `ValueError` for bad formats.

#### Observation 9: Silently Discarded Upgrade Errors
* **File & Line**: `src/crypcodile/cli.py:1889`
* **Snippet**:
  ```python
  result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  ```
* **Note**: Discarding `stderr` conceals details about pip upgrade failures.

#### Observation 10: Missing Fallback Dependency Check for Uvicorn
* **File & Line**: `src/crypcodile/cli.py:1801-1803`
* **Snippet**:
  ```python
          import uvicorn
          typer.echo(f"Starting Crypcodile x402 API server on http://{host}:{port}...", err=True)
          uvicorn.run("crypcodile.api_server:app", host=host, port=port, log_level="info")
  ```
* **Note**: If `uvicorn` is not installed, importing it throws an unhandled `ImportError`.


## 2. Logic Chain
1. **Observation 1** shows that most commands (like `query`) execute database calls directly without catching exceptions. Therefore, syntactically invalid SQL queries or queries on missing tables will cause a raw Python traceback, indicating a lack of robust error handling.
2. **Observation 2** shows that `_prompt_with_esc` reads from stdin using `.readline()` when stdin is non-interactive. Since `.readline()` returns at the first newline, any multi-line SQL query piped into `query` will be cut off, leading to invalid syntax or incomplete query execution.
3. **Observation 3** shows that when commands are executed non-interactively (e.g. scripts or CI/CD pipelines) and missing options are encountered, they prompt anyway. When stdin is closed (EOF), this raises a `KeyboardInterrupt` that is caught and translates to a clean exit (code 0). This causes argument/configuration errors to silently pass as successful execution.
4. **Observation 4** indicates that the interactive shell instantiates `PromptSession` without verifying if stdin is actually a TTY. Since `PromptSession` requires a TTY, this will crash when run in non-interactive environments without arguments (since the entry point defaults to `shell`).
5. **Observation 5** shows that helper queries `get_available_option_underlyings` and `get_available_option_snapshots` do not apply date partition filtering. This forces DuckDB to read all options parquet data on disk to get distinct values, causing performance degradation as data accumulates.
6. **Observation 6** shows that if a user provides conflicting parameters to the `basis` command (such as `--perp` along with `--spot` and `--future`), the command silently runs `perp_basis` and ignores the other parameters. This lack of validation violates standard CLI best practices.
7. **Observation 7** displays that timestamp formatting relies on `fromtimestamp` without error safety bounds. If database values are corrupted or extreme, this will raise a `ValueError` or `OSError` and crash the interactive options snapshot menu.
8. **Observation 8** shows that `--fmt` has no restricted choices, allowing arbitrary format strings. This leads to an unhandled `ValueError` at the library level when calling `client.export`.
9. **Observation 9** runs `pip install` with both stdout and stderr redirected to `/dev/null`. Thus, any environment-specific or network-related pip upgrade failures will be invisible to the user.
10. **Observation 10** imports `uvicorn` inside `api` without a try-except. If `uvicorn` is missing from the environment, it crashes with a traceback instead of prompting the user to install it.


## 3. Caveats
- Since the sandboxed environment prevented execution of unit tests directly (permission prompts timed out), we did not run `pytest`. However, the findings are based on a meticulous code audit of the source file (`src/crypcodile/cli.py`) and library functions.
- We assume that `sys.stdin` behavior conforms to standard Python library specifications on macOS/UNIX.


## 4. Conclusion
The CLI commands in `src/crypcodile/cli.py` contain several structural, validation, and safety issues that can cause tracebacks, incorrect exit codes, slowness, or unexpected hangs. Resolving these issues involves adding non-interactive checks, input validation (e.g. Typer choices, mutual exclusivity), error handling around database and dependency imports, and avoiding full-table scans in prompt helpers.


## 5. Verification Method
To verify these findings, run the following commands once sandbox permissions are granted or in a local clone:

1. **Verify Truncated SQL Query**:
   ```bash
   echo "SELECT \n count(*) \n FROM trade;" | python -m crypcodile.cli query
   # Expectation: Fails or complains about syntax due to truncation after "SELECT "
   ```

2. **Verify Missing Argument Exit Code (Non-Interactive)**:
   ```bash
   python -m crypcodile.cli export --fmt csv < /dev/null
   # Expectation: Prints "Cancelled." and exits with code 0 instead of failing due to missing channel/symbol arguments.
   ```

3. **Verify Shell on Non-TTY**:
   ```bash
   python -m crypcodile.cli shell < /dev/null
   # Expectation: Crashes with a traceback from prompt_toolkit when trying to create/use PromptSession.
   ```

4. **Verify Invalid Format Pass-Through**:
   ```bash
   python -m crypcodile.cli export --channel trade --symbols BTC --fmt invalid --data-dir test_data
   # Expectation: Unhandled ValueError traceback from crypcodile/client/export.py:78
   ```

5. **Verify Uvicorn Import Fallback**:
   Uninstall uvicorn and run:
   ```bash
   python -m crypcodile.cli api --port 8000
   # Expectation: Unhandled ImportError traceback.
   ```

"""Typer CLI for Crypcodile (Task 3.5 / 6.5 / T7b).

Commands
--------
query          -- Execute DuckDB SQL against the data lake; print result table.
catalog        -- List all channels present in the data lake with row counts.
export         -- Export a channel x symbols x time range to a file.
replay         -- Stream canonical Records from the data lake, printed to stdout.
collect        -- Run live connectors and write data to the Parquet lake.
funding-apr    -- Print per-event funding APR for a perpetual symbol.
basis          -- Print spot-future or perpetual basis.
iv-surface     -- Print the implied-vol surface snapshot.
term-structure -- Print the ATM IV term structure.

Usage examples::

    crypcodile query "SELECT count(*) FROM trade" --data-dir /data
    crypcodile catalog --data-dir /data
    crypcodile export --channel trade --symbols BTC-PERPETUAL --from 0 --to 9e18 \\
                     --fmt csv --dest out/trades.csv --data-dir /data
    crypcodile replay --channels trade --symbols deribit:BTC-PERPETUAL \\
                     --from 0 --to 9e18 --data-dir /data
    crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \\
                      --channels trade --data-dir /data
    crypcodile funding-apr --symbol deribit:BTC-PERPETUAL \\
                          --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile basis --future deribit:BTC-FUTURE --spot binance-spot:BTCUSDT \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile basis --perp deribit:BTC-PERPETUAL \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile iv-surface --underlying BTC --at 1704067200000000000 --data-dir /data
    crypcodile term-structure --underlying BTC --at 1704067200000000000 --data-dir /data
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Annotated

import typer
import typer.rich_utils

# Change help panels and borders style from cyan to dark green
typer.rich_utils.STYLE_OPTION = "bold dark_green"
typer.rich_utils.STYLE_COMMANDS_PANEL_BORDER = "dark_green"
typer.rich_utils.STYLE_OPTIONS_PANEL_BORDER = "dark_green"
typer.rich_utils.STYLE_COMMANDS_TABLE_FIRST_COLUMN = "bold dark_green"

def is_interactive_stdin() -> bool:
    import sys
    return sys.stdin.isatty() or getattr(sys.stdin, "_mock_interactive", False)


COMMON_DEFAULT_SYMBOLS = [
    "binance-spot:BTCUSDT",
    "binance-spot:ETHUSDT",
    "binance-spot:SOLUSDT",
    "deribit:BTC-PERPETUAL",
    "deribit:ETH-PERPETUAL",
    "deribit:SOL-PERPETUAL",
]


def prompt_with_autocomplete(
    text: str,
    suggestions: list[str],
    default: str = "",
    meta_dict: dict[str, str] | None = None
) -> str:
    """Prompt the user for input, with autocomplete popup, history, and shadow suggestions."""
    from prompt_toolkit import prompt
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import has_completions
    import sys

    # If stdin is not interactive (e.g., tests/pipes) or running in pytest, use fallback _prompt_with_esc
    if not is_interactive_stdin() or "pytest" in sys.modules:
        return _prompt_with_esc(text, default=default)

    completer = WordCompleter(suggestions, ignore_case=True, meta_dict=meta_dict)
    
    kb = KeyBindings()
    @kb.add('escape', filter=~has_completions)
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    prompt_text = text
    if default:
        prompt_text += f" [{default}]"
    prompt_text += ": "
    
    try:
        val = prompt(
            prompt_text,
            completer=completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=kb,
        )
        val = val.strip()
        if not val and default:
            return default
        return val
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write("\nCancelled.\n")
        sys.stderr.flush()
        raise typer.Exit(code=0)


def prompt_symbol(text: str, data_dir: Path, channel: str | None = None, default: str = "") -> str:
    """Prompt the user for a symbol using autocomplete suggestions from the database catalog."""
    from crypcodile.store.catalog import Catalog
    
    suggestions = set()
    try:
        cat = Catalog(data_dir)
        channels = [channel] if channel else list(cat._registered_channels)
        for ch in channels:
            try:
                df = cat.query(f'SELECT DISTINCT symbol FROM "{ch}"')
                for s in df["symbol"].to_list():
                    if s:
                        suggestions.add(str(s))
            except Exception:
                pass
    except Exception:
        pass
        
    suggestions_list = sorted(list(suggestions))
    if not suggestions_list:
        suggestions_list = COMMON_DEFAULT_SYMBOLS
        
    return prompt_with_autocomplete(text, suggestions_list, default=default)



# ---------------------------------------------------------------------------
# Override typer.prompt to support cancellation with the ESC key
# ---------------------------------------------------------------------------
def _prompt_with_esc(text: str, default: Any = None, type: Any = None, *args: Any, **kwargs: Any) -> Any:
    """Prompt the user for input, allowing cancellation via ESC or Ctrl+C."""
    import sys
    import tty
    import termios
    import select

    def read_line() -> str:
        sys.stdout.write(text)
        if default is not None:
            sys.stdout.write(f" [{default}]")
        sys.stdout.write(": ")
        sys.stdout.flush()

        # Fallback if stdin is not a TTY (e.g., tests)
        if not sys.stdin.isatty():
            line = sys.stdin.readline()
            if not line:
                raise KeyboardInterrupt
            line = line.rstrip("\r\n")
            if not line and default is not None:
                return str(default)
            return line

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        line = ""
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if not ch:
                    raise EOFError

                if ch == "\x1b":
                    # Check if ESC or arrow key escape sequence
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if not r:
                        raise KeyboardInterrupt
                    else:
                        # Consume arrow keys
                        sys.stdin.read(1)
                        sys.stdin.read(1)
                        continue

                if ch in ("\r", "\n"):
                    sys.stdout.write("\r\n")
                    sys.stdout.flush()
                    break

                if ch in ("\x7f", "\x08"):
                    if len(line) > 0:
                        line = line[:-1]
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue

                if ch == "\x03":
                    raise KeyboardInterrupt

                sys.stdout.write(ch)
                sys.stdout.flush()
                line += ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        if not line and default is not None:
            return str(default)
        return line

    while True:
        try:
            val_str = read_line()
        except (KeyboardInterrupt, EOFError):
            # Print newline and Cancelled, then exit cleanly
            sys.stderr.write("\nCancelled.\n")
            sys.stderr.flush()
            raise typer.Exit(code=0)

        if not val_str and default is None:
            # Re-prompt if value is required and no default is provided
            continue

        if type is not None:
            try:
                return type(val_str)
            except ValueError:
                sys.stdout.write(f"Error: Invalid value of type {type.__name__}.\r\n")
                sys.stdout.flush()
                continue
        return val_str

import typing
typing.cast(Any, typer).prompt = _prompt_with_esc


def prompt_time_range_helper(
    data_dir: Path,
    channel: str | None,
    symbols: list[str] | None,
    default_start: int = 0,
    default_end: int = 9999999999999999999
) -> tuple[int, int]:
    """Helper to show available time range from the database and prompt for Start/End times."""
    import datetime
    from crypcodile.store.catalog import Catalog
    
    min_ts, max_ts = None, None
    if channel:
        cat = Catalog(data_dir)
        if channel in cat._registered_channels:
            try:
                where_clause = ""
                if symbols:
                    clean_syms = [s for s in symbols if s]
                    if clean_syms:
                        sym_list = ", ".join(f"'{s}'" for s in clean_syms)
                        where_clause = f" WHERE symbol IN ({sym_list})"
                df = cat.query(f'SELECT min(local_ts) as min_t, max(local_ts) as max_t FROM "{channel}"{where_clause}')
                if len(df) > 0:
                    row = df.to_dicts()[0]
                    if row.get("min_t") is not None:
                        min_ts = int(row["min_t"])
                    if row.get("max_t") is not None:
                        max_ts = int(row["max_t"])
            except Exception:
                pass

    if min_ts is not None and max_ts is not None:
        min_dt_str = datetime.datetime.fromtimestamp(min_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        max_dt_str = datetime.datetime.fromtimestamp(max_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        typer.echo(f"\nAvailable database range: {min_dt_str} to {max_dt_str}")
        
        start_prompt = "Start time (default: earliest)"
        end_prompt = "End time (default: latest)"
    else:
        typer.echo("\nNo data range found in catalog. Using absolute defaults.")
        start_prompt = "Start time (default: 0)"
        end_prompt = "End time (default: infinity)"

    instructions = (
        "\n--- Time Range Filter Instruction ---\n"
        "Start and End times filter the historical market data records retrieved from the database.\n"
        "Accepted input formats:\n"
        "  - UTC date-time string: 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD'\n"
        "  - Raw 19-digit UTC nanosecond timestamp (e.g., 1718540000000000000)\n"
        "  - Leave blank (press Enter) to use the default values shown below."
    )
    typer.echo(instructions)

    # Helper function to parse user input
    def parse_time(val: str, fallback: int) -> int:
        val = val.strip()
        if not val:
            return fallback
        if val.isdigit():
            try:
                return int(val)
            except ValueError:
                pass
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.datetime.strptime(val, fmt).replace(tzinfo=datetime.UTC)
                return int(dt.timestamp() * 1_000_000_000)
            except ValueError:
                continue
                
        # Format a human-readable fallback for the warning message
        fallback_str = str(fallback)
        if 0 < fallback < 9999999999999999999:
            try:
                fallback_str = datetime.datetime.fromtimestamp(fallback // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                pass
        elif fallback == 0:
            fallback_str = "earliest / 1970-01-01"
        elif fallback == 9999999999999999999:
            fallback_str = "latest / infinity"
            
        typer.echo(f"⚠️  Invalid date format '{val}'. Using default: {fallback_str}", err=True)
        return fallback

    start_input = typer.prompt(start_prompt, default="").strip()
    resolved_start = parse_time(start_input, min_ts if min_ts is not None else default_start)
    
    end_input = typer.prompt(end_prompt, default="").strip()
    resolved_end = parse_time(end_input, max_ts if max_ts is not None else default_end)
    
    return resolved_start, resolved_end

# ---------------------------------------------------------------------------
# Top-level imports used by collect (kept at module scope so tests can patch
# them without reloading the module).
# ---------------------------------------------------------------------------
from crypcodile.client.collect import collect as collect_live
from crypcodile.exchanges.factory import make_connector
from crypcodile.ingest.transport import AiohttpWsTransport
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.store.parquet_sink import ParquetSink

app = typer.Typer(
    name="crypcodile",
    help="Crypcodile -- open-source crypto market-data engine.",
    add_completion=False,
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# Shared option helpers
# ---------------------------------------------------------------------------

_DataDirOpt = Annotated[
    Path,
    typer.Option(
        "--data-dir",
        help="Root directory of the Parquet data lake.",
        show_default=False,
    ),
]


def resolve_data_dir(data_dir: Path) -> Path:
    """Resolve the data directory, falling back to test_data or prompting the user if empty."""
    import sys
    from pathlib import Path
    from crypcodile.store.catalog import Catalog

    cwd_test_data = Path("test_data")
    repo_root = Path(__file__).resolve().parents[2]
    repo_test_data = repo_root / "test_data"
    home_test_data = Path.home() / "Crypcodile" / "test_data"

    def has_data(d: Path) -> bool:
        if not d.exists() or not d.is_dir():
            return False
        try:
            cat = Catalog(d)
            return len(cat._registered_channels) > 0
        except Exception:
            return False

    # 1. If the requested directory already has data, use it.
    if has_data(data_dir):
        return data_dir

    # 2. Find a candidate test_data folder that actually has data.
    fallback_candidate = None
    if has_data(cwd_test_data):
        fallback_candidate = cwd_test_data
    elif has_data(repo_test_data):
        fallback_candidate = repo_test_data
    elif has_data(home_test_data):
        fallback_candidate = home_test_data

    # 3. If running in pytest:
    if "pytest" in sys.modules:
        if fallback_candidate and (data_dir == Path("data") or not data_dir.exists()):
            return fallback_candidate
        return data_dir

    # 4. If not a TTY (non-interactive / piped / IDE subprocess):
    if not is_interactive_stdin():
        if fallback_candidate and (data_dir == Path("data") or not data_dir.exists()):
            typer.echo(f"Warning: No data found in '{data_dir}', falling back to '{fallback_candidate}'.", err=True)
            return fallback_candidate
        return data_dir

    # 5. Interactive TTY:
    if fallback_candidate and (data_dir == Path("data") or not data_dir.exists()):
        use_fallback = typer.confirm(
            f"No data found in '{data_dir}', but test data was found at '{fallback_candidate}'. Use it?",
            default=True
        )
        if use_fallback:
            return fallback_candidate

    while True:
        alt_path = typer.prompt("Enter data directory", default=str(data_dir))
        alt_dir = Path(alt_path)
        if has_data(alt_dir):
            return alt_dir
        if not alt_dir.exists():
            typer.echo(f"Directory '{alt_dir}' does not exist.", err=True)
        else:
            typer.echo(f"No registered channels found in '{alt_dir}'.", err=True)
        
        if not typer.confirm("Try another path?", default=True):
            break
            
    return data_dir


def normalize_user_symbol(exchange: str, symbol: str) -> str:
    """Normalize user input symbol to the exchange's standard raw symbol format."""
    s = symbol.strip()
    if not s:
        return ""
        
    s_upper = s.upper()
    
    if exchange == "base_onchain":
        if s_upper.startswith("CBBTC"):
            return "cbBTC-USDC"
        if s_upper == "AERO":
            return "AERO-USDC"
        if s_upper == "WETH":
            return "WETH-USDC"
        if s_upper == "DEGEN":
            return "DEGEN-WETH"
        if s_upper == "WELL":
            return "WELL-WETH"
        if s_upper == "CBBTC-USDC":
            return "cbBTC-USDC"
        return s
        
    if exchange == "deribit":
        if s_upper in ("BTC", "ETH", "SOL"):
            return f"{s_upper}-PERPETUAL"
        if s_upper.endswith("-PERP"):
            return s_upper.replace("-PERP", "-PERPETUAL")
        return s_upper
        
    if exchange in ("binance", "bybit"):
        if s_upper in ("BTC", "ETH", "SOL"):
            return f"{s_upper}USDT"
        return s_upper
        
    if exchange == "okx":
        if s_upper in ("BTC", "ETH", "SOL"):
            return f"{s_upper}-USDT"
        return s_upper
        
    if exchange == "coinbase":
        if s_upper in ("BTC", "ETH", "SOL"):
            return f"{s_upper}-USD"
        return s_upper
        
    return s_upper


def resolve_input_symbols(data_dir: Path, symbols_input: list[str]) -> list[str]:
    """Resolve user entered symbols to matching catalog symbols if possible."""
    from crypcodile.store.catalog import Catalog
    try:
        cat = Catalog(data_dir)
        all_registered = set()
        for ch in cat._registered_channels:
            try:
                df = cat.query(f'SELECT DISTINCT symbol FROM "{ch}"')
                for s in df["symbol"].to_list():
                    if s:
                        all_registered.add(str(s))
            except Exception:
                pass
    except Exception:
        all_registered = set()

    resolved = []
    for sym in symbols_input:
        sym_clean = sym.strip()
        if not sym_clean:
            continue
        
        # 1. Exact match in registered symbols
        if sym_clean in all_registered:
            resolved.append(sym_clean)
            continue
            
        # 2. Case-insensitive exact match
        lower_sym = sym_clean.lower()
        matched = False
        for reg in all_registered:
            if reg.lower() == lower_sym:
                resolved.append(reg)
                matched = True
                break
        if matched:
            continue
            
        # 3. Prefix-less match (e.g., "BTC-PERPETUAL" matching "deribit:BTC-PERPETUAL")
        for reg in all_registered:
            if ":" in reg:
                parts = reg.split(":", 1)
                if parts[1].lower() == lower_sym:
                    resolved.append(reg)
                    matched = True
                    break
        if matched:
            continue

        # 4. Fuzzy substring match (e.g., "btc" matching "deribit:BTC-PERPETUAL")
        matches = []
        for reg in all_registered:
            if lower_sym in reg.lower():
                matches.append(reg)
        if len(matches) == 1:
            resolved.append(matches[0])
            continue
        elif len(matches) > 1:
            resolved.append(matches[0])
            continue

        # 5. Fallback to original
        resolved.append(sym_clean)
        
    return resolved


def select_symbols_interactively(data_dir: Path, channel: str | None = None) -> tuple[str, list[str]]:
    """Select channel and symbol(s) interactively using a search/selection wizard."""
    import sys
    from crypcodile.store.catalog import Catalog

    cat = Catalog(data_dir)
    available_channels = sorted(list(cat._registered_channels))

    if not available_channels:
        return "", []

    # 1. Resolve channel if not specified
    if not channel:
        typer.echo("\n--- Select Channel ---")
        for idx, ch in enumerate(available_channels, 1):
            typer.echo(f"  [{idx}] {ch}")
        
        while True:
            choice = typer.prompt("Select channel", default="1").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(available_channels):
                    channel = available_channels[idx]
                    break
                else:
                    typer.echo("Invalid selection. Try again.", err=True)
            elif choice:
                channel = choice
                break
            else:
                typer.echo("Channel name cannot be empty.", err=True)

    # 2. Query all unique symbols in this channel
    typer.echo(f"\nScanning symbol list for channel '{channel}'...")
    all_symbols = []
    if channel in cat._registered_channels:
        try:
            df = cat.query(f'SELECT DISTINCT symbol FROM "{channel}"')
            all_symbols = sorted([str(s) for s in df["symbol"].to_list() if s])
        except Exception:
            all_symbols = []

    if not all_symbols:
        typer.echo(f"No registered symbols found in channel '{channel}' on disk.", err=True)
        sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel=channel)
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
        return channel, symbols

    # 3. Grandma's phone filtering loop
    search_query = ""
    while True:
        # Filter symbols matching search_query
        filtered = [s for s in all_symbols if search_query.lower() in s.lower()]
        
        typer.echo(f"\n--- Symbol Search (Filter: '{search_query}') ---")
        if not filtered:
            typer.echo("No matching symbols found.")
        else:
            display_limit = 15
            for idx, sym in enumerate(filtered[:display_limit], 1):
                typer.echo(f"  [{idx}] {sym}")
            if len(filtered) > display_limit:
                typer.echo(f"  ... and {len(filtered) - display_limit} more symbols ...")
                
        typer.echo("\nOptions:")
        typer.echo("  - Type number(s) (e.g. 1 or 1,2) to select symbol(s).")
        typer.echo("  - Type letters to search/filter.")
        typer.echo("  - Type 'all' to select all currently listed symbols.")
        typer.echo("  - Press Enter with empty query to clear search.")
        typer.echo("  - Press ESC to cancel.")
        
        choice = prompt_with_autocomplete("Search/Select", filtered, default="")
        
        if not choice:
            if search_query:
                search_query = ""
                continue
            else:
                typer.echo("Please make a selection or press ESC to cancel.", err=True)
                continue

        if choice.lower() == "all":
            if filtered:
                typer.echo(f"Selected all {len(filtered)} matching symbols: {filtered}")
                return channel, filtered
            else:
                typer.echo("No symbols to select.", err=True)
                continue

        # Check if choice is a comma-separated list of numbers
        if "," in choice or (choice.isdigit() and int(choice) > 0):
            parts = [p.strip() for p in choice.split(",")]
            selected = []
            valid = True
            for p in parts:
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(filtered) and idx < 15:
                        selected.append(filtered[idx])
                    else:
                        valid = False
                        typer.echo(f"Invalid index: {p}", err=True)
                else:
                    valid = False
            if valid and selected:
                typer.echo(f"Selected: {', '.join(selected)}")
                return channel, selected
            if not valid:
                continue

        # Update search query
        search_query = choice


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@app.command()
def query(
    sql: Annotated[str, typer.Argument(help="DuckDB SQL query to execute.")] = "",
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Execute a DuckDB SQL query against the data lake and print the result."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not sql:
        sql = typer.prompt("SQL query")
    if not sql:
        typer.echo("Error: SQL query cannot be empty.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    df = client.query(sql)
    typer.echo(df)


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


@app.command()
def catalog(
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """List channels present in the data lake with their row counts."""
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.store.catalog import Catalog

    data_dir = resolve_data_dir(data_dir)

    cat: Catalog = CrypcodileClient(data_dir=data_dir)._catalog

    # Discover channels from the registered views.
    channels: list[str] = sorted(cat._registered_channels)

    if not channels:
        typer.echo("No data found in: " + str(data_dir))
        raise typer.Exit(code=0)

    typer.echo(f"{'channel':<24}  {'rows':>10}")
    typer.echo("-" * 36)
    for ch in channels:
        try:
            row_df = cat.query(f'SELECT count(*) AS n FROM "{ch}"')
            n = int(row_df["n"][0])
        except Exception:
            n = -1
        typer.echo(f"{ch:<24}  {n:>10,}")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@app.command()
def export(
    channel: Annotated[str | None, typer.Option("--channel", help="Channel name, e.g. trade.")] = None,
    symbols: Annotated[
        list[str] | None,
        typer.Option("--symbols", help="Canonical symbol(s). Repeat for multiple."),
    ] = None,
    frm: Annotated[
        int | None,
        typer.Option("--from", help="Start of time range (nanoseconds UTC)."),
    ] = None,
    to: Annotated[
        int | None,
        typer.Option("--to", help="End of time range (nanoseconds UTC)."),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option("--fmt", help="Output format: parquet|csv|arrow|json|jsonl."),
    ] = "parquet",
    dest: Annotated[
        Path,
        typer.Option("--dest", help="Destination file path."),
    ] = Path("export.parquet"),
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Export channel x symbols x time range to a file."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    is_interactive = is_interactive_stdin()
    if is_interactive and (not channel or not symbols):
        channel, selected_symbols = select_symbols_interactively(data_dir, channel)
        if selected_symbols:
            symbols = selected_symbols

    if not channel:
        channel = typer.prompt("Channel (e.g. trade)")
    if not symbols:
        sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel=channel)
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if symbols:
        symbols = resolve_input_symbols(data_dir, symbols)
    if frm is None or to is None:
        resolved_start, resolved_end = prompt_time_range_helper(data_dir, channel, symbols, default_start=0, default_end=9999999999999999999)
        if frm is None:
            frm = resolved_start
        if to is None:
            to = resolved_end

    if not channel or not symbols:
        typer.echo("Error: Channel and symbols are required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    client.export(channel, symbols, frm, to, fmt=fmt, dest=dest)  # type: ignore[arg-type]
    typer.echo(f"Exported to: {dest}")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@app.command()
def replay(
    channels: Annotated[
        list[str] | None,
        typer.Option("--channels", help="Channel name(s). Repeat for multiple."),
    ] = None,
    symbols: Annotated[
        list[str] | None,
        typer.Option("--symbols", help="Canonical symbol(s). Repeat for multiple."),
    ] = None,
    frm: Annotated[
        int | None,
        typer.Option("--from", help="Start of time range (nanoseconds UTC)."),
    ] = None,
    to: Annotated[
        int | None,
        typer.Option("--to", help="End of time range (nanoseconds UTC)."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of records to print."),
    ] = None,
) -> None:
    """Stream canonical Records from the data lake, printed to stdout."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    is_interactive = is_interactive_stdin()
    if is_interactive and (not channels or not symbols):
        wiz_channel = channels[0] if channels else None
        wiz_channel, selected_symbols = select_symbols_interactively(data_dir, wiz_channel)
        if wiz_channel and not channels:
            channels = [wiz_channel]
        if selected_symbols:
            symbols = selected_symbols

    if not channels:
        ch_input = typer.prompt("Channel (e.g. trade)")
        channels = [c.strip() for c in ch_input.split(",") if c.strip()]
    if not symbols:
        ch = channels[0] if channels else None
        sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel=ch)
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if symbols:
        symbols = resolve_input_symbols(data_dir, symbols)
    if frm is None or to is None:
        wiz_ch = channels[0] if channels else None
        resolved_start, resolved_end = prompt_time_range_helper(data_dir, wiz_ch, symbols, default_start=0, default_end=9999999999999999999)
        if frm is None:
            frm = resolved_start
        if to is None:
            to = resolved_end

    if not channels or not symbols:
        typer.echo("Error: Channels and symbols are required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    count = 0
    for record in client.replay(channels, symbols, frm, to):
        typer.echo(repr(record))
        count += 1
        if limit is not None and count >= limit:
            break
    typer.echo(f"-- {count} record(s) replayed.")


def select_collect_params_interactively(
    exchange: str | None,
    symbols: list[str] | None,
    channels: list[str] | None
) -> tuple[str, list[str], list[str]]:
    """Select exchange, channels, and symbols interactively for live data collection."""
    import sys
    
    valid_exchanges = ["binance", "bybit", "coinbase", "deribit", "okx", "base_onchain"]
    valid_channels = ["trade", "book_ticker", "book_snapshot", "book_delta"]
    
    suggested_symbols = {
        "binance": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "bybit": ["BTCUSDT", "ETHUSDT"],
        "coinbase": ["BTC-USD", "ETH-USD"],
        "deribit": ["BTC-PERPETUAL", "ETH-PERPETUAL", "SOL-PERPETUAL"],
        "okx": ["BTC-USDT", "ETH-USDT"],
        "base_onchain": ["cbBTC-USDC", "AERO-USDC", "WETH-USDC", "DEGEN-WETH", "WELL-WETH"]
    }

    # 1. Select Exchange
    if not exchange:
        typer.echo("\n--- Supported Exchanges ---")
        for idx, ex in enumerate(valid_exchanges, 1):
            typer.echo(f"  [{idx}] {ex}")
        while True:
            choice = typer.prompt("Select exchange", default="1").strip()
            if choice.isdigit():
                i = int(choice) - 1
                if 0 <= i < len(valid_exchanges):
                    exchange = valid_exchanges[i]
                    break
            elif choice in valid_exchanges:
                exchange = choice
                break
            typer.echo("Invalid selection. Try again.", err=True)

    # 2. Select Channels
    if not channels:
        typer.echo("\n--- Select Channels ---")
        for idx, ch in enumerate(valid_channels, 1):
            typer.echo(f"  [{idx}] {ch}")
        while True:
            choice = typer.prompt("Select channel(s)", default="1").strip()
            if "," in choice or (choice.isdigit() and int(choice) > 0):
                parts = [p.strip() for p in choice.split(",")]
                selected = []
                valid = True
                for p in parts:
                    if p.isdigit():
                        idx = int(p) - 1
                        if 0 <= idx < len(valid_channels):
                            selected.append(valid_channels[idx])
                        else:
                            valid = False
                    else:
                        valid = False
                if valid and selected:
                    channels = selected
                    break
            # Fallback to custom input
            custom_channels = [c.strip() for c in choice.split(",") if c.strip()]
            if custom_channels:
                channels = custom_channels
                break
            typer.echo("Invalid selection. Try again.", err=True)

    # 3. Select Symbols
    if not symbols:
        suggestions = suggested_symbols.get(exchange, ["BTC-PERPETUAL"])
        typer.echo(f"\n--- Suggested Symbols for {exchange} ---")
        for idx, sym in enumerate(suggestions, 1):
            typer.echo(f"  [{idx}] {sym}")
        typer.echo("  [C] Enter custom symbol(s)")
        
        while True:
            choice = typer.prompt("Select symbol(s)", default="1").strip()
            if choice.lower() == "c":
                custom_input = prompt_with_autocomplete("Enter symbol (e.g. BTC)", suggestions)
                custom_symbols = [s.strip() for s in custom_input.split(",") if s.strip()]
                if custom_symbols:
                    symbols = [normalize_user_symbol(exchange, s) for s in custom_symbols]
                    break
            elif "," in choice or (choice.isdigit() and int(choice) > 0):
                parts = [p.strip() for p in choice.split(",")]
                selected = []
                valid = True
                for p in parts:
                    if p.isdigit():
                        idx = int(p) - 1
                        if 0 <= idx < len(suggestions):
                            selected.append(suggestions[idx])
                        else:
                            valid = False
                    else:
                        valid = False
                if valid and selected:
                    symbols = selected
                    break
            if choice and not choice.isdigit():
                symbols = [normalize_user_symbol(exchange, s.strip()) for s in choice.split(",") if s.strip()]
                break
            typer.echo("Invalid selection. Try again.", err=True)

    return exchange, symbols, channels


# Helper to extract a representative value from any Record type
def get_record_value(rec: Any) -> float | None:
    tag = getattr(type(rec).__struct_config__, "tag", None)
    if not tag:
        # Fallback to duck typing
        if hasattr(rec, "price"):
            try:
                return float(rec.price)
            except Exception:
                pass
        if hasattr(rec, "close"):
            try:
                return float(rec.close)
            except Exception:
                pass
        return None

    try:
        if tag == "trade":
            return float(rec.price)
        elif tag == "book_ticker":
            return (float(rec.bid_px) + float(rec.ask_px)) / 2.0
        elif tag in ("book_snapshot", "book_delta"):
            if rec.bids and rec.asks:
                best_bid = rec.bids[0]
                best_ask = rec.asks[0]
                bid_px = best_bid["price"] if isinstance(best_bid, dict) else best_bid[0]
                ask_px = best_ask["price"] if isinstance(best_ask, dict) else best_ask[0]
                return (float(bid_px) + float(ask_px)) / 2.0
        elif tag == "derivative_ticker":
            if rec.last_price is not None:
                return float(rec.last_price)
            if rec.mark_price is not None:
                return float(rec.mark_price)
        elif tag == "options_chain":
            if rec.mark_iv is not None:
                return float(rec.mark_iv)
            if rec.mark_price is not None:
                return float(rec.mark_price)
        elif tag == "funding":
            return float(rec.funding_rate)
        elif tag == "open_interest":
            return float(rec.open_interest)
        elif tag == "liquidation":
            return float(rec.price)
        elif tag == "ohlcv":
            return float(rec.close)
    except Exception:
        pass
    return None


# Helper to format a record value based on its channel tag
def format_record_value(channel: str, val: float) -> str:
    if channel == "funding":
        return f"{val * 100.0:.6f}%"
    if channel == "options_chain":
        # Options implied vol (IV) is typically a decimal fraction, e.g. 0.65 -> 65.0%
        if val <= 5.0:
            return f"{val * 100.0:.2f}%"
        return f"{val:.2f}"
    if val < 0.01:
        return f"{val:.6f}"
    return f"{val:,.4f}"


def make_sparkline(prices: list[float]) -> str:
    if not prices or len(prices) < 2:
        return ""
    min_p = min(prices)
    max_p = max(prices)
    diff = max_p - min_p
    if diff == 0:
        return "█" * len(prices)
    ticks = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    res = []
    for p in prices:
        ratio = (p - min_p) / diff
        idx = int(ratio * (len(ticks) - 1))
        idx = max(0, min(len(ticks) - 1, idx))
        res.append(ticks[idx])
    return "".join(res)


import time
from collections import deque
from crypcodile.sink.base import Sink

class MonitoringSink(Sink):
    def __init__(self, target: Sink):
        self.target = target
        self.total_records = 0
        self.records_by_key = {}  # (symbol, channel) -> count
        self.values_by_key = {}  # (symbol, channel) -> deque of last values
        self.start_time = time.time()
        self.last_ts_by_key = {}  # (symbol, channel) -> float
        self.last_rec_by_key = {}  # (symbol, channel) -> record
        self.rates_deque = deque(maxlen=10)
        self.last_rate_calc_time = time.time()
        self.records_since_last_calc = 0
        self.current_rate = 0.0

    async def put(self, record: Any) -> None:
        self.total_records += 1
        self.records_since_last_calc += 1
        
        now = time.time()
        if now - self.last_rate_calc_time >= 1.0:
            elapsed = now - self.last_rate_calc_time
            self.current_rate = self.records_since_last_calc / elapsed
            self.rates_deque.append(self.current_rate)
            self.records_since_last_calc = 0
            self.last_rate_calc_time = now

        channel = getattr(type(record).__struct_config__, "tag", None) or getattr(record, "channel", "unknown")
        key = (record.symbol, channel)
        self.records_by_key[key] = self.records_by_key.get(key, 0) + 1
        self.last_ts_by_key[key] = now
        self.last_rec_by_key[key] = record

        val = get_record_value(record)
        if val is not None:
            if key not in self.values_by_key:
                self.values_by_key[key] = deque(maxlen=30)
            self.values_by_key[key].append(val)

        await self.target.put(record)

    async def flush(self) -> None:
        await self.target.flush()

    async def close(self) -> None:
        await self.target.close()



async def run_dashboard(monitoring_sink: MonitoringSink, exchange: str, symbols: list[str], channels: list[str], data_dir: Path):
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.columns import Columns
    
    console = Console()
    
    def format_elapsed(secs: int) -> str:
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        if h > 0:
            return f"{h:02d}h {m:02d}m {s:02d}s"
        return f"{m:02d}m {s:02d}s"
        
    def generate_layout() -> Panel:
        now = time.time()
        elapsed = int(now - monitoring_sink.start_time)
        
        # Title & Subtitle
        title_text = Text()
        title_text.append("🐊 CRYPCODILE LIVE DATA INGESTION PIPELINE 🐊\n", style="bold green")
        title_text.append("Real-time streaming crypto market data to local Parquet storage\n", style="dim white")
        
        # Pipeline Configuration Panel
        config_table = Table.grid(padding=(0, 2))
        config_table.add_column("Property", style="bold cyan")
        config_table.add_column("Value", style="white")
        config_table.add_row("Exchange source", f"[bold magenta]{exchange.upper()}[/]")
        config_table.add_row("Data destination", f"[bold blue]{data_dir.resolve()}[/]")
        config_table.add_row("Subscribed channels", f"[bold yellow]{', '.join(channels)}[/]")
        config_table.add_row("Session duration", format_elapsed(elapsed))
        
        # Ingestion Speed and Health calculation
        rate = monitoring_sink.current_rate
        if not monitoring_sink.rates_deque and elapsed > 0:
            rate = monitoring_sink.total_records / elapsed
            
        time_since_last_msg = 999.0
        if monitoring_sink.last_ts_by_key:
            time_since_last_msg = now - max(monitoring_sink.last_ts_by_key.values())
            
        if time_since_last_msg > 10.0:
            status_text = "[bold blink red]⚠️  STALE (Waiting for data...)[/]"
            status_border = "red"
        else:
            status_text = "[bold green]● PIPELINE ACTIVE & STREAMING[/]"
            status_border = "green"
            
        buffered_count = 0
        if hasattr(monitoring_sink.target, "_buffers"):
            buffered_count = sum(len(buf) for buf in monitoring_sink.target._buffers.values())
            
        # Stats & Performance Panel
        perf_table = Table.grid(padding=(0, 2))
        perf_table.add_column("Metric", style="bold cyan")
        perf_table.add_column("Value", style="white")
        perf_table.add_row("Pipeline status", status_text)
        perf_table.add_row("Total records saved", f"[bold green]{monitoring_sink.total_records:,}[/]")
        perf_table.add_row("Ingestion speed", f"[bold green]{rate:.1f} records/sec[/]")
        perf_table.add_row("Write-buffer queue", f"[bold red]{buffered_count:,} rows[/]")
        
        # Arrange configuration and performance side-by-side inside panels
        left_panel = Panel(config_table, title="[bold white]⚙️ Pipeline Config[/]", border_style="green")
        right_panel = Panel(perf_table, title="[bold white]📊 System Performance[/]", border_style=status_border)
        
        header_columns = Columns([left_panel, right_panel], expand=True)
        
        # Active Data Streams Table
        stats_table = Table(title="[bold white]📈 Active Market Data Streams[/]", border_style="green", expand=True)
        stats_table.add_column("Asset/Symbol", style="bold cyan", ratio=2)
        stats_table.add_column("Data Type (Channel)", style="bold yellow", ratio=2)
        stats_table.add_column("Messages Ingested", justify="right", style="green", ratio=2)
        stats_table.add_column("Latest Value/Price", justify="right", style="magenta", ratio=2)
        stats_table.add_column("Trend (since start)", justify="center", ratio=2)
        stats_table.add_column("Activity Chart (last 30 ticks)", justify="center", ratio=3)
        
        for (sym, ch), count in sorted(monitoring_sink.records_by_key.items()):
            key = (sym, ch)
            last_val_str = "-"
            trend_str = "-"
            sparkline = ""
            
            values_deque = monitoring_sink.values_by_key.get(key)
            if values_deque and len(values_deque) > 0:
                last_val = values_deque[-1]
                last_val_str = format_record_value(ch, last_val)
                
                if len(values_deque) >= 2:
                    prev_val = values_deque[-2]
                    first_val = values_deque[0]
                    pct_change = ((last_val - first_val) / first_val) * 100.0 if first_val else 0.0
                    
                    if last_val > prev_val:
                        trend_str = f"[bold green]▲ (+{pct_change:.2f}%)[/]"
                    elif last_val < prev_val:
                        trend_str = f"[bold red]▼ ({pct_change:.2f}%)[/]"
                    else:
                        trend_str = f"[bold grey]▶ ({pct_change:.2f}%)[/]"
                else:
                    trend_str = "[bold grey]▶ (0.00%)[/]"
                
                sparkline = make_sparkline(list(values_deque))
                
            stats_table.add_row(sym, ch, f"{count:,}", last_val_str, trend_str, sparkline)
            
        footer_text = Text("\n💡 To view and query your historical local data, open a new terminal window and run: ", style="dim")
        footer_text.append(f"crypcodile query \"SELECT * FROM {channels[0] if channels else 'trade'}\"", style="bold yellow")
        footer_text.append("\nPress ", style="dim")
        footer_text.append("Ctrl-C", style="bold red")
        footer_text.append(" at any time to safely stop the ingestion pipeline.", style="dim")
        
        group = Group(
            Align.center(title_text),
            header_columns,
            stats_table,
            footer_text
        )
        return Panel(group, border_style="green", expand=True)

    with Live(generate_layout(), console=console, refresh_per_second=2) as live:
        while True:
            try:
                await asyncio.sleep(0.5)
                live.update(generate_layout())
            except asyncio.CancelledError:
                break
            except Exception:
                pass


# ---------------------------------------------------------------------------
# collect  (T7b-collect — live connector wiring)
# ---------------------------------------------------------------------------
@app.command()
def collect(
    exchange: Annotated[str | None, typer.Option("--exchange", help="Exchange name, e.g. deribit.")] = None,
    symbols: Annotated[
        list[str] | None,
        typer.Option("--symbols", help="Symbol(s) to collect. Repeat for multiple."),
    ] = None,
    channels: Annotated[
        list[str] | None,
        typer.Option("--channels", help="Channel(s) to subscribe. Repeat for multiple."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Collect live market data from an exchange and write to the Parquet data lake.

    Press Ctrl-C (SIGINT) to stop gracefully — the sink is flushed before exit.

    Valid exchange names: binance, bybit, coinbase, deribit, okx.

    Example::

        crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \
                          --channels trade --channels book_delta --data-dir data
    """
    is_interactive = is_interactive_stdin()
    if is_interactive and (not exchange or not symbols or not channels):
        exchange, symbols, channels = select_collect_params_interactively(exchange, symbols, channels)

    if not exchange:
        exchange = typer.prompt("Exchange (e.g. deribit)")
    if not symbols:
        sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir)
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if symbols and exchange:
        symbols = [normalize_user_symbol(exchange, s) for s in symbols]
    if not channels:
        ch_input = typer.prompt("Channel (e.g. trade)")
        channels = [c.strip() for c in ch_input.split(",") if c.strip()]

    if not exchange or not symbols or not channels:
        typer.echo("Error: Exchange, symbols, and channels are required.", err=True)
        raise typer.Exit(code=1)

    sink = ParquetSink(
        data_dir=data_dir,
        max_buffer_rows=10_000,
        flush_interval_seconds=5.0,
    )
    registry = InstrumentRegistry()

    try:
        connector = make_connector(
            exchange=exchange,
            symbols=list(symbols),
            channels=list(channels),
            out=sink,
            registry=registry,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Wire the live WebSocket transport (may be replaced by a FakeTransport in
    # tests via monkeypatching make_connector).
    if connector.transport is None:
        connector.transport = AiohttpWsTransport(connector.ws_url)

    typer.echo(
        f"Starting collection: exchange={exchange!r} symbols={symbols} "
        f"channels={channels} data_dir={data_dir}"
    )

    monitoring_sink = MonitoringSink(sink)
    connector.out = monitoring_sink

    if is_interactive:

        async def collect_with_dashboard():
            dashboard_task = asyncio.create_task(
                run_dashboard(monitoring_sink, exchange, symbols, channels, data_dir)
            )
            try:
                await collect_live([connector], monitoring_sink)
            finally:
                dashboard_task.cancel()
                try:
                    await dashboard_task
                except asyncio.CancelledError:
                    pass

        try:
            asyncio.run(collect_with_dashboard())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    else:
        try:
            asyncio.run(collect_live([connector], monitoring_sink))
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    typer.echo("Collection stopped. Data written to: " + str(data_dir))


# ---------------------------------------------------------------------------
# funding-apr  (Task 6.5)
# ---------------------------------------------------------------------------


@app.command(name="funding-apr")
def funding_apr_cmd(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="Canonical symbol, e.g. deribit:BTC-PERPETUAL."),
    ] = None,
    start: Annotated[
        int | None,
        typer.Option("--start", help="Start of time range (nanoseconds UTC)."),
    ] = None,
    end: Annotated[
        int | None,
        typer.Option("--end", help="End of time range (nanoseconds UTC)."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print per-event funding APR and cumulative funding for a perpetual symbol."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    is_interactive = is_interactive_stdin()
    if is_interactive and not symbol:
        _, selected_symbols = select_symbols_interactively(data_dir, channel="funding")
        if selected_symbols:
            symbol = selected_symbols[0]

    if not symbol:
        symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="funding")
    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol])
        if resolved_syms:
            symbol = resolved_syms[0]
    if start is None or end is None:
        resolved_start, resolved_end = prompt_time_range_helper(data_dir, "funding", [symbol] if symbol else None, default_start=0, default_end=9999999999999999999)
        if start is None:
            start = resolved_start
        if end is None:
            end = resolved_end

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    df = client.funding_apr(symbol, start, end)
    if len(df) == 0:
        typer.echo("No funding data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# basis  (Task 6.5)
# ---------------------------------------------------------------------------


@app.command(name="basis")
def basis_cmd(
    start: Annotated[
        int | None,
        typer.Option("--start", help="Start of time range (nanoseconds UTC)."),
    ] = None,
    end: Annotated[
        int | None,
        typer.Option("--end", help="End of time range (nanoseconds UTC)."),
    ] = None,
    future: Annotated[
        str | None,
        typer.Option("--future", help="Canonical futures symbol (spot-future mode)."),
    ] = None,
    spot: Annotated[
        str | None,
        typer.Option("--spot", help="Canonical spot symbol (spot-future mode)."),
    ] = None,
    perp: Annotated[
        str | None,
        typer.Option("--perp", help="Canonical perpetual symbol (perp mode)."),
    ] = None,
    expiry: Annotated[
        int | None,
        typer.Option("--expiry", help="Contract expiry (ns UTC; spot-future mode only)."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print spot-future or perpetual basis.

    Use --future/--spot for spot-future mode, or --perp for perpetual mode.
    """
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    # We will prompt for time range after mode/symbols are resolved

    is_interactive = is_interactive_stdin()

    # If neither perp nor future/spot is specified, ask user what mode they want
    if perp is None and (future is None or spot is None):
        mode = typer.prompt("Basis mode", default="perp")
        if mode == "perp":
            if is_interactive:
                _, selected_symbols = select_symbols_interactively(data_dir, channel="derivative_ticker")
                if selected_symbols:
                    perp = selected_symbols[0]
            if not perp:
                perp = prompt_symbol("Perpetual symbol (e.g. BTC)", data_dir, channel="derivative_ticker")
        else:
            if is_interactive:
                typer.echo("\nSelect futures symbol:")
                _, selected_futures = select_symbols_interactively(data_dir, channel="trade")
                if selected_futures:
                    future = selected_futures[0]
                typer.echo("\nSelect spot symbol:")
                _, selected_spots = select_symbols_interactively(data_dir, channel="trade")
                if selected_spots:
                    spot = selected_spots[0]
            
            if not future:
                future = prompt_symbol("Futures symbol (e.g. BTC)", data_dir, channel="trade")
            if not spot:
                spot = prompt_symbol("Spot symbol (e.g. BTC)", data_dir, channel="trade")

    if perp:
        resolved = resolve_input_symbols(data_dir, [perp])
        if resolved:
            perp = resolved[0]
    if future:
        resolved = resolve_input_symbols(data_dir, [future])
        if resolved:
            future = resolved[0]
    if spot:
        resolved = resolve_input_symbols(data_dir, [spot])
        if resolved:
            spot = resolved[0]

    if start is None or end is None:
        ch = "derivative_ticker" if perp is not None else "trade"
        syms = [perp] if perp is not None else ([future, spot] if future and spot else None)
        resolved_start, resolved_end = prompt_time_range_helper(data_dir, ch, syms, default_start=0, default_end=9999999999999999999)
        if start is None:
            start = resolved_start
        if end is None:
            end = resolved_end

    client = CrypcodileClient(data_dir=data_dir)

    if perp is not None:
        df = client.perp_basis(perp, start, end)
    elif future is not None and spot is not None:
        df = client.spot_future_basis(future, spot, start, end, expiry_ns=expiry)
    else:
        typer.echo(
            "Error: provide either --perp <symbol> or both --future <symbol> and "
            "--spot <symbol>.",
            err=True,
        )
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No basis data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
def get_available_option_underlyings(data_dir: Path) -> list[str]:
    """Get list of unique underlyings in options_chain."""
    from crypcodile.store.catalog import Catalog
    cat = Catalog(data_dir)
    if "options_chain" not in cat._registered_channels:
        return []
    try:
        df = cat.query("SELECT DISTINCT underlying FROM options_chain ORDER BY underlying")
        return [str(x) for x in df["underlying"].to_list() if x]
    except Exception:
        return []


def get_available_option_snapshots(data_dir: Path, underlying: str | None = None) -> list[int]:
    """Helper to query the latest available option snapshot timestamps from the catalog."""
    from crypcodile.store.catalog import Catalog
    cat = Catalog(data_dir)
    if "options_chain" not in cat._registered_channels:
        return []
    try:
        u_filter = ""
        if underlying:
            u_filter = f" WHERE UPPER(underlying) = '{underlying.upper()}'"
        sql = f"SELECT DISTINCT local_ts FROM options_chain{u_filter} ORDER BY local_ts DESC LIMIT 5"
        df = cat.query(sql)
        return [int(x) for x in df["local_ts"].to_list() if x]
    except Exception:
        return []


# iv-surface  (Task 6.5)
# ---------------------------------------------------------------------------


@app.command(name="iv-surface")
def iv_surface_cmd(
    underlying: Annotated[
        str | None,
        typer.Option("--underlying", help="Underlying asset identifier, e.g. BTC."),
    ] = None,
    at: Annotated[
        int | None,
        typer.Option("--at", help="Snapshot instant (nanoseconds UTC)."),
    ] = None,
    rate: Annotated[
        float,
        typer.Option("--rate", help="Continuous risk-free rate (default 0.0)."),
    ] = 0.0,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print the implied-vol surface snapshot at a given instant."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not underlying:
        underlyings = get_available_option_underlyings(data_dir)
        if underlyings:
            typer.echo(f"Available option underlyings in database: {', '.join(underlyings)}")
        underlying = typer.prompt("Underlying asset (e.g. BTC)").strip()

    if at is None:
        snapshots = get_available_option_snapshots(data_dir, underlying)
        if not snapshots:
            underlyings = get_available_option_underlyings(data_dir)
            if underlyings:
                typer.echo(f"⚠️  No options snapshots found for underlying '{underlying}'.")
                typer.echo(f"Available option underlyings in database: {', '.join(underlyings)}")
                snapshots = get_available_option_snapshots(data_dir, None)
                if snapshots:
                    typer.echo("Here are the latest available options snapshots across all assets:")
            else:
                typer.echo("⚠️  No option data (options_chain channel) found in the database. Please collect options data first.")
        
        if snapshots:
            import datetime
            typer.echo("\n--- Available Options Snapshots (latest first) ---")
            for idx, ts in enumerate(snapshots, 1):
                dt_str = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                typer.echo(f"  [{idx}] {ts} ({dt_str})")
            choice = typer.prompt("Select snapshot by number or enter custom", default="1").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(snapshots):
                at = snapshots[int(choice) - 1]
            else:
                try:
                    at = int(choice)
                except ValueError:
                    at = None
        else:
            at_str = typer.prompt("Snapshot time (nanoseconds UTC, e.g. 1704067200000000000)").strip()
            try:
                at = int(at_str)
            except ValueError:
                at = None

    if not underlying or at is None:
        typer.echo("Error: Underlying and snapshot instant (at) are required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    df = client.iv_surface(underlying, at, rate=rate)
    if len(df) == 0:
        typer.echo("No options data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# term-structure  (Task 6.5)
# ---------------------------------------------------------------------------


@app.command(name="term-structure")
def term_structure_cmd(
    underlying: Annotated[
        str | None,
        typer.Option("--underlying", help="Underlying asset identifier, e.g. BTC."),
    ] = None,
    at: Annotated[
        int | None,
        typer.Option("--at", help="Snapshot instant (nanoseconds UTC)."),
    ] = None,
    rate: Annotated[
        float,
        typer.Option("--rate", help="Continuous risk-free rate (default 0.0)."),
    ] = 0.0,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print the ATM IV term structure at a given instant."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not underlying:
        underlyings = get_available_option_underlyings(data_dir)
        if underlyings:
            typer.echo(f"Available option underlyings in database: {', '.join(underlyings)}")
        underlying = typer.prompt("Underlying asset (e.g. BTC)").strip()

    if at is None:
        snapshots = get_available_option_snapshots(data_dir, underlying)
        if not snapshots:
            underlyings = get_available_option_underlyings(data_dir)
            if underlyings:
                typer.echo(f"⚠️  No options snapshots found for underlying '{underlying}'.")
                typer.echo(f"Available option underlyings in database: {', '.join(underlyings)}")
                snapshots = get_available_option_snapshots(data_dir, None)
                if snapshots:
                    typer.echo("Here are the latest available options snapshots across all assets:")
            else:
                typer.echo("⚠️  No option data (options_chain channel) found in the database. Please collect options data first.")
        
        if snapshots:
            import datetime
            typer.echo("\n--- Available Options Snapshots (latest first) ---")
            for idx, ts in enumerate(snapshots, 1):
                dt_str = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                typer.echo(f"  [{idx}] {ts} ({dt_str})")
            choice = typer.prompt("Select snapshot by number or enter custom", default="1").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(snapshots):
                at = snapshots[int(choice) - 1]
            else:
                try:
                    at = int(choice)
                except ValueError:
                    at = None
        else:
            at_str = typer.prompt("Snapshot time (nanoseconds UTC, e.g. 1704067200000000000)").strip()
            try:
                at = int(at_str)
            except ValueError:
                at = None

    if not underlying or at is None:
        typer.echo("Error: Underlying and snapshot instant (at) are required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    df = client.term_structure(underlying, at, rate=rate)
    if len(df) == 0:
        typer.echo("No options data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# mcp (Model Context Protocol Server)
# ---------------------------------------------------------------------------


@app.command()
def mcp(
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Start the Model Context Protocol (MCP) server over stdin/stdout."""
    import asyncio
    import sys

    from crypcodile.mcp_server import serve_stdio
    
    if sys.stdin.isatty():
        typer.echo("⚠️  Warning: MCP server is running on stdio and expects JSON-RPC input.", err=True)
        typer.echo("👉 It is meant to be run by an AI client (like Claude Desktop), not run interactively.", err=True)
        typer.echo("👉 Press Ctrl-C to exit.", err=True)

    typer.echo("Starting Crypcodile MCP Server on stdio...", err=True)
    try:
        asyncio.run(serve_stdio(data_dir=data_dir))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    typer.echo("Crypcodile MCP Server stopped.", err=True)


# ---------------------------------------------------------------------------
# api (FastAPI Web Server for x402 Micropayments Gated API)
# ---------------------------------------------------------------------------


@app.command()
def api(
    port: Annotated[int, typer.Option("--port", help="Port to bind the API server to.")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Host address to bind to.")] = "127.0.0.1",
) -> None:
    """Start the x402 Micropayment Gated API server."""
    import shutil
    import subprocess
    import os
    import sys
    
    node_path = shutil.which("node")
    portal_dir = Path(__file__).parent / "api_portal"
    server_js = portal_dir / "server.js"
    
    if node_path and server_js.exists():
        typer.echo(f"Starting Crypcodile Premium x402 API Web Portal (Node.js) on http://{host}:{port}...", err=True)
        typer.echo("Press CTRL+C to stop the server.", err=True)
        env = os.environ.copy()
        env["PORT"] = str(port)
        env["HOST"] = host
        
        proc = None
        try:
            # Spawn Node.js server with piped stdout and stderr redirected to stdout
            proc = subprocess.Popen(
                [node_path, str(server_js)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Read and print the Node.js output lines in real-time to stderr (unbuffered)
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    typer.echo(line.strip(), err=True)
                    
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, [node_path, str(server_js)])
                
        except KeyboardInterrupt:
            typer.echo("\nStopping Crypcodile Premium x402 API Web Portal...", err=True)
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            typer.echo("Crypcodile Premium x402 API Web Portal stopped.", err=True)
        except subprocess.CalledProcessError as e:
            typer.echo(f"Node.js server exited with error: {e}", err=True)
            sys.exit(e.returncode)
    else:
        typer.echo("Node.js runtime or server.js not found. Falling back to Python FastAPI server...", err=True)
        import uvicorn
        typer.echo(f"Starting Crypcodile x402 API server on http://{host}:{port}...", err=True)
        uvicorn.run("crypcodile.api_server:app", host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@app.command()
def update(
    force: Annotated[bool, typer.Option("--force", help="Force upgrade even if up-to-date.")] = False,
) -> None:
    """Check for updates and upgrade Crypcodile to the latest version from GitHub."""
    import sys
    import subprocess
    import re
    from crypcodile import __version__

    current_version = __version__
    typer.echo(f"⟳ Checking for updates... (current version {current_version})", err=True)

    # 1. Fetch latest version from remote tags
    latest_version = None
    try:
        git_cmd = ["git", "ls-remote", "--tags", "https://github.com/nazmiefearmutcu/Crypcodile.git"]
        output = subprocess.check_output(git_cmd, stderr=subprocess.DEVNULL).decode()
        tags = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            ref = parts[1]
            if ref.startswith("refs/tags/"):
                tag = ref.replace("refs/tags/", "")
                if tag.endswith("^{}"):
                    continue
                tags.append(tag)
        if tags:
            def version_key(v: str):
                clean_v = v.lstrip("v")
                parts = []
                for part in re.split(r"(\d+)", clean_v):
                    if part.isdigit():
                        parts.append(int(part))
                    else:
                        parts.append(part)
                return parts
            tags.sort(key=version_key)
            latest_version = tags[-1]
    except Exception:
        pass

    if latest_version:
        # Compare versions
        clean_current = current_version.lstrip("v")
        clean_latest = latest_version.lstrip("v")
        
        def parse_version(v: str) -> list[int]:
            return [int(x) for x in re.findall(r"\d+", v)]
            
        is_newer = False
        try:
            is_newer = parse_version(clean_latest) > parse_version(clean_current)
        except Exception:
            is_newer = clean_latest != clean_current

        if not is_newer and not force:
            typer.echo("✓ You are already on the latest version.", err=True)
            return
        
        if force:
            typer.echo(f"⟳ Force upgrading to {latest_version}...", err=True)
        else:
            typer.echo(f"⟳ Upgrading to {latest_version}...", err=True)
    else:
        typer.echo("⟳ Could not check version. Proceeding with upgrade...", err=True)

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "git+https://github.com/nazmiefearmutcu/Crypcodile.git",
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            target_v = latest_version if latest_version else "latest"
            typer.echo(f"✓ Successfully upgraded to {target_v}!", err=True)
        else:
            typer.echo("✗ Failed to upgrade Crypcodile.", err=True)
            raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"✗ Error upgrading Crypcodile: {e}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------


@app.command()
def shell() -> None:
    """Start an interactive Crypcodile shell."""
    import shlex
    import click
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    
    from crypcodile import __version__
    typer.echo(f"Welcome to Crypcodile Interactive Shell! (v{__version__})")
    typer.echo("Type 'help' to list commands. Type 'exit' or 'quit' to exit.")
    
    click_group = typer.main.get_group(app)
    
    commands = {}
    for name in click_group.list_commands(None):
        cmd = click_group.get_command(None, name)
        help_text = cmd.help or ""
        if help_text:
            help_text = help_text.split("\n")[0].strip()
        commands[name] = help_text
        
    import sys
    is_pytest = "pytest" in sys.modules
    session = None
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
    
    import signal
    original_handler = None
    if not is_pytest:
        try:
            original_handler = signal.getsignal(signal.SIGWINCH)
        except Exception:
            pass

        def sigwinch_handler(signum, frame):
            if original_handler and callable(original_handler):
                try:
                    original_handler(signum, frame)
                except Exception:
                    pass
            from prompt_toolkit.application import get_current_app
            app = get_current_app()
            if app and app.renderer:
                try:
                    app.renderer.reset(leave_alternate_screen=False)
                    app.invalidate()
                except Exception:
                    pass

        try:
            signal.signal(signal.SIGWINCH, sigwinch_handler)
        except Exception:
            pass

    try:
        while True:
            try:
                if is_pytest:
                    line = input("crypcodile> ").strip()
                else:
                    line = session.prompt("crypcodile> ").strip()
                if not line:
                    continue
                if line.lower() in ("exit", "quit"):
                    break
                if line.lower() == "shell":
                    typer.echo("You are already in the Crypcodile shell.")
                    continue
                
                if line.lower() in ("help", "?", "-h"):
                    args = ["--help"]
                else:
                    args = shlex.split(line)
                try:
                    click_group(args, standalone_mode=False)
                except click.exceptions.ClickException as e:
                    e.show()
                except SystemExit:
                    pass
                except Exception as e:
                    typer.echo(f"Error executing command: {e}", err=True)
            except (KeyboardInterrupt, EOFError):
                typer.echo("\nGoodbye!")
                break
    finally:
        if not is_pytest and original_handler:
            try:
                signal.signal(signal.SIGWINCH, original_handler)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

LOGO_ART = r"""                    .-._   _ _ _ _ _ _ _ _
         .-''-.__.-'00  '-' ' ' ' ' ' ' ' '-.
         '.___ '    .   .--_'-' '-' '-' _'-' '._
          V: V 'vv-'   '_   '.       .'  _..' '.'.
            '=.____.=_.--'   :_.__.__:_   '.   : :
                    (((____.-'        '-.  /   : :
                                      (((-'\ .' /
                                    _____..'  .'
                                   '-._____.-'   
   ____                               _ _ _ 
  / ___|_ __ _   _ _ __   ___ ___  __| (_) | ___ 
 | |   | '__| | | | '_ \ / __/ _ \/ _` | | |/ _ \
 | |___| |  | |_| | |_) | (_| (_) | (_| | | |  __/
  \____|_|   \__, | .__/ \___\___/\__,_|_|_|\___|
             |___/|_|                            """

LOGO = f"\033[32m{LOGO_ART}\033[0m"


def main() -> None:
    """Entry-point called by the ``crypcodile`` script."""
    import sys
    from crypcodile import __version__

    # Print the logo always to stderr, unless running the mcp/update command or tests
    if "mcp" not in sys.argv and "update" not in sys.argv and "pytest" not in sys.modules:
        if sys.stderr.isatty():
            sys.stderr.write(LOGO + f"\n             (v{__version__})\n\n")
            sys.stderr.flush()

    if len(sys.argv) == 1:
        sys.argv.append("shell")

    app()


if __name__ == "__main__":
    main()


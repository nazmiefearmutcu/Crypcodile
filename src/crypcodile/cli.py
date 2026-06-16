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

def is_interactive_stdin() -> bool:
    import sys
    return sys.stdin.isatty() or getattr(sys.stdin, "_mock_interactive", False)


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
                    break

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
        except KeyboardInterrupt:
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
    try:
        df = cat.query(f'SELECT DISTINCT symbol FROM "{channel}"')
        all_symbols = sorted([str(s) for s in df["symbol"].to_list() if s])
    except Exception as e:
        typer.echo(f"Error querying symbols from catalog: {e}", err=True)
        all_symbols = []

    if not all_symbols:
        typer.echo(f"No registered symbols found in channel '{channel}' on disk.", err=True)
        sym_input = typer.prompt("Symbol (e.g. BTC)")
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
        
        choice = typer.prompt("Search/Select", default="").strip()
        
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
        sym_input = typer.prompt("Symbol (e.g. BTC)")
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if symbols:
        symbols = resolve_input_symbols(data_dir, symbols)
    if frm is None:
        frm = typer.prompt("Start time", type=int, default=0)
    if to is None:
        to = typer.prompt("End time", type=int, default=9999999999999999999)

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
        sym_input = typer.prompt("Symbol (e.g. BTC)")
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if symbols:
        symbols = resolve_input_symbols(data_dir, symbols)
    if frm is None:
        frm = typer.prompt("Start time", type=int, default=0)
    if to is None:
        to = typer.prompt("End time", type=int, default=9999999999999999999)

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
                custom_input = typer.prompt("Enter symbol (e.g. BTC)")
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


def print_startup_banner(exchange: str, symbols: list[str], channels: list[str], data_dir: Path):
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    
    console = Console()
    
    title = Text("\n🐊 CRYPCODILE LIVE DATA STREAMER 🐊\n", style="bold green")
    
    info_table = Table.grid(padding=(0, 2))
    info_table.add_column("Key", style="bold cyan")
    info_table.add_column("Value", style="green")
    
    info_table.add_row("Exchange", f"⚡ {exchange.upper()}")
    info_table.add_row("Channels", f"📺 {', '.join(channels)}")
    info_table.add_row("Symbols", f"💱 {', '.join(symbols)}")
    info_table.add_row("Data Directory", f"📁 {data_dir.resolve()}")
    info_table.add_row("Status", "● Connecting to WebSocket...", style="bold yellow")
    
    cmds_table = Table.grid(padding=(0, 2))
    cmds_table.add_column("Command", style="bold magenta")
    cmds_table.add_column("Description", style="white")
    cmds_table.add_row("crypcodile query", "Run SQL queries against data lake")
    cmds_table.add_row("crypcodile catalog", "Check channel sizes and row counts")
    cmds_table.add_row("crypcodile basis", "Analyze spot-futures or perp basis")
    cmds_table.add_row("crypcodile funding-apr", "Calculate perpetual funding rates")
    
    panel_content = Group(
        title,
        Panel(info_table, title="[bold white]Connection details[/]", border_style="cyan"),
        Panel(cmds_table, title="[bold white]💡 How to query collected data (Historical/Charts)[/]", border_style="magenta"),
        Text("\nPress Ctrl-C to gracefully stop streaming and save buffered data.\n", style="bold red")
    )
    
    console.print(Panel(panel_content, border_style="green", expand=False))


async def run_dashboard(monitoring_sink: MonitoringSink, exchange: str, symbols: list[str], channels: list[str], data_dir: Path):
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    
    console = Console()
    
    def generate_layout() -> Panel:
        now = time.time()
        elapsed = int(now - monitoring_sink.start_time)
        
        title_text = Text()
        title_text.append("🐊 CRYPCODILE LIVE DATA STREAMER 🐊\n", style="bold green")
        
        conn_table = Table.grid(padding=(0, 2))
        conn_table.add_column("Key", style="bold cyan")
        conn_table.add_column("Val", style="white")
        conn_table.add_row("Exchange", f"[bold magenta]{exchange.upper()}[/]")
        conn_table.add_row("Channels", f"[bold yellow]{', '.join(channels)}[/]")
        conn_table.add_row("Data Dir", f"[bold blue]{data_dir}[/]")
        conn_table.add_row("Elapsed Time", f"{elapsed} seconds")
        
        rate = monitoring_sink.current_rate
        if not monitoring_sink.rates_deque and elapsed > 0:
            rate = monitoring_sink.total_records / elapsed
            
        time_since_last_msg = 999.0
        if monitoring_sink.last_ts_by_key:
            time_since_last_msg = now - max(monitoring_sink.last_ts_by_key.values())
            
        if time_since_last_msg > 10.0:
            status_text = "[bold yellow]● STALE (Waiting for data)[/]"
        else:
            status_text = "[bold green]● STREAMING[/]"
            
        rate_table = Table.grid(padding=(0, 2))
        rate_table.add_column("Key", style="bold cyan")
        rate_table.add_column("Val", style="white")
        rate_table.add_row("Status", status_text)
        rate_table.add_row("Total Records", f"[bold green]{monitoring_sink.total_records:,}[/]")
        rate_table.add_row("Message Rate", f"[bold green]{rate:.1f} rec/sec[/]")
        
        buffered_count = 0
        if hasattr(monitoring_sink.target, "_buffers"):
            buffered_count = sum(len(buf) for buf in monitoring_sink.target._buffers.values())
        rate_table.add_row("Buffered Rows", f"[bold red]{buffered_count:,}[/]")
        
        header_cols = Table.grid(padding=(0, 4))
        header_cols.add_column()
        header_cols.add_column()
        header_cols.add_row(conn_table, rate_table)
        
        stats_table = Table(title="[bold white]📊 Stream Statistics[/]", border_style="cyan")
        stats_table.add_column("Symbol", style="bold cyan")
        stats_table.add_column("Channel", style="bold yellow")
        stats_table.add_column("Count", justify="right", style="green")
        stats_table.add_column("Last Value", justify="right", style="magenta")
        stats_table.add_column("Trend", justify="center")
        stats_table.add_column("Value History (Sparkline)", width=32)
        
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
            
        footer_text = Text("\n💡 To view historical data and charts, run: ", style="dim")
        footer_text.append("crypcodile query \"SELECT * FROM trade\" --data-dir " + str(data_dir), style="bold yellow")
        footer_text.append("\nPress ", style="dim")
        footer_text.append("Ctrl-C", style="bold red")
        footer_text.append(" to stop streaming.", style="dim")
        
        group = Group(
            Align.center(title_text),
            Panel(header_cols, border_style="green"),
            stats_table,
            footer_text
        )
        return Panel(group, border_style="green", expand=False)

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
        sym_input = typer.prompt("Symbol (e.g. BTC)")
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
        print_startup_banner(exchange, symbols, channels, data_dir)

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
        _, selected_symbols = select_symbols_interactively(data_dir)
        if selected_symbols:
            symbol = selected_symbols[0]

    if not symbol:
        symbol = typer.prompt("Symbol (e.g. BTC)")
    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol])
        if resolved_syms:
            symbol = resolved_syms[0]
    if start is None:
        start = typer.prompt("Start time", type=int, default=0)
    if end is None:
        end = typer.prompt("End time", type=int, default=9999999999999999999)

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

    if start is None:
        start = typer.prompt("Start time", type=int, default=0)
    if end is None:
        end = typer.prompt("End time", type=int, default=9999999999999999999)

    is_interactive = is_interactive_stdin()

    # If neither perp nor future/spot is specified, ask user what mode they want
    if perp is None and (future is None or spot is None):
        mode = typer.prompt("Basis mode", default="perp")
        if mode == "perp":
            if is_interactive:
                _, selected_symbols = select_symbols_interactively(data_dir)
                if selected_symbols:
                    perp = selected_symbols[0]
            if not perp:
                perp = typer.prompt("Perpetual symbol (e.g. BTC)")
        else:
            if is_interactive:
                typer.echo("\nSelect futures symbol:")
                _, selected_futures = select_symbols_interactively(data_dir)
                if selected_futures:
                    future = selected_futures[0]
                typer.echo("\nSelect spot symbol:")
                _, selected_spots = select_symbols_interactively(data_dir)
                if selected_spots:
                    spot = selected_spots[0]
            
            if not future:
                future = typer.prompt("Futures symbol (e.g. BTC)")
            if not spot:
                spot = typer.prompt("Spot symbol (e.g. BTC)")

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
        underlying = typer.prompt("Underlying asset (e.g. BTC)")
    if at is None:
        at = typer.prompt("Snapshot time", type=int)

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
        underlying = typer.prompt("Underlying asset (e.g. BTC)")
    if at is None:
        at = typer.prompt("Snapshot time", type=int)

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

    from crypcodile.mcp_server import serve_stdio
    
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
    
    typer.echo("Welcome to Crypcodile Interactive Shell!")
    typer.echo("Type 'help' to list commands. Type 'exit' or 'quit' to exit.")
    
    click_group = typer.main.get_group(app)
    
    while True:
        try:
            line = input("crypcodile> ").strip()
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

    # Print the logo always to stderr, unless running the mcp/update command or tests
    if "mcp" not in sys.argv and "update" not in sys.argv and "pytest" not in sys.modules:
        if sys.stderr.isatty():
            sys.stderr.write(LOGO + "\n")
            sys.stderr.flush()

    if len(sys.argv) == 1:
        sys.argv.append("shell")

    app()


if __name__ == "__main__":
    main()


"""Typer CLI for Crypcodile (Task 3.5 / 6.5 / T7b).

Commands
--------
query          -- Execute DuckDB SQL against the data lake; print result table.
catalog        -- List all channels present in the data lake with row counts.
search         -- Ranked symbol search over the data lake inventory.
export         -- Export a channel x symbols x time range to a file.
replay         -- Stream canonical Records from the data lake, printed to stdout.
collect        -- Run live connectors and write data to the Parquet lake.
backfill       -- Fetch historical REST data into the Parquet lake.
funding-apr    -- Print per-event funding APR for a perpetual symbol.
basis          -- Print spot-future, spot-perp, or mark/index perpetual basis.
iv-surface     -- Print the implied-vol surface snapshot.
term-structure -- Print the ATM IV term structure.
vol-skew       -- Print per-strike IV and delta for a single expiry.
risk-reversal  -- Print risk-reversal and butterfly from vol skew.
open-interest  -- Aggregate open interest across exchanges from the lake.
liquidity-depth -- Per-block bid/ask depth at ±1/2/5% from mid (book snapshots).
sequencer-latency -- Sequencer production interval and ingestion delay (lake).
peg-deviation  -- Stablecoin peg deviation (lake or pure --price).
chaos-score    -- Normalized [0, 100] chaos score from pure risk metrics.
lending-stress -- LTV/health-factor stress under collateral haircut (pure nums).
gas-vol        -- Correlate gas costs vs volatility from CSV/JSON inputs.
smart-money    -- Summarize smart-money net flow from transfers CSV + watchlist.
label-transfers -- Label/filter transfer CSV rows via watchlist JSON (no RPC).
mev-sandwich   -- Flag sandwich patterns in trade sequences (CSV/JSON offline).
funding-predict -- Next-period funding rate from rates list or CSV/JSON (offline).

Usage examples::

    crypcodile query "SELECT count(*) FROM trade" --data-dir /data
    crypcodile catalog --data-dir /data
    crypcodile catalog --symbols --data-dir /data
    crypcodile search BTC --data-dir /data
    crypcodile export --channel trade --symbols BTC-PERPETUAL --from 0 --to 9e18 \\
                     --fmt csv --dest out/trades.csv --data-dir /data
    crypcodile replay --channels trade --symbols deribit:BTC-PERPETUAL \\
                     --from 0 --to 9e18 --data-dir /data
    crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \\
                      --channels trade --data-dir /data
    crypcodile collect --exchange binance --exchange deribit --symbols BTC \\
                      --channels trade --data-dir /data
    crypcodile collect --exchange binance,bybit --symbols BTCUSDT \\
                      --channels trade --data-dir /data
    crypcodile backfill --exchange binance --channel trade --symbols BTCUSDT \\
                       --from 1700000000000000000 --to 1700000100000000000 --data-dir /data
    crypcodile funding-apr --symbol deribit:BTC-PERPETUAL \\
                          --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile basis --future deribit:BTC-FUTURE --spot binance-spot:BTCUSDT \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile basis --perp deribit:BTC-PERPETUAL \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile basis --spot binance-spot:BTCUSDT --perp deribit:BTC-PERPETUAL \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crypcodile iv-surface --underlying BTC --at 1704067200000000000 --data-dir /data
    crypcodile term-structure --underlying BTC --at 1704067200000000000 --data-dir /data
    crypcodile vol-skew --underlying BTC --expiry-ns 1735689600000000000 \\
                       --at 1704067200000000000 --data-dir /data
    crypcodile risk-reversal --underlying BTC --expiry-ns 1735689600000000000 \\
                            --at 1704067200000000000 --target-delta 0.25 --data-dir /data
    crypcodile open-interest --symbol BTC --start 0 --end 9999999999999999999 \\
                            --data-dir /data
    crypcodile liquidity-depth --symbol base_onchain:DEGEN-WETH --data-dir /data
    crypcodile sequencer-latency --exchange base_onchain --data-dir /data
    crypcodile peg-deviation --price 0.98 --threshold 0.01
    crypcodile peg-deviation --symbol base_onchain:USDC-USDbC --data-dir /data
    crypcodile chaos-score --volatility 0.05 --stablecoin-deviation 0.002 \\
                          --orderbook-imbalance 0.1 --sequencer-delay 1.0
    crypcodile lending-stress --collateral-usd 10000 --debt-usd 5000 \\
                             --liquidation-threshold 0.8 --haircut-pct 0.20
    crypcodile gas-vol --gas-file gas.csv --vol-file vol.csv
    crypcodile smart-money --transfers transfers.csv --watchlist watchlist.json
    crypcodile label-transfers --transfers transfers.csv --watchlist watchlist.json \\
                              --min-usd 100000
    crypcodile mev-sandwich --trades swaps.csv
    crypcodile mev-sandwich --trades swaps.json --sandwiches-only
    crypcodile funding-predict --rates 0.0001,0.0002,0.00015
    crypcodile funding-predict --file funding.csv --window 5
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
        return typer.prompt(text, default=default)

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
        try:
            min_dt_str = datetime.datetime.fromtimestamp(min_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, OSError, OverflowError):
            min_dt_str = str(min_ts) if min_ts is not None else "unknown"

        try:
            max_dt_str = datetime.datetime.fromtimestamp(max_ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, OSError, OverflowError):
            max_dt_str = str(max_ts) if max_ts is not None else "unknown"

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
        if val.isdigit() and len(val) <= 19:
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
from crypcodile.client.backfill import run_historical_backfill
from crypcodile.client.collect import collect as collect_live
from crypcodile.exchanges.factory import list_exchanges, make_connector
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


def resolve_input_symbols(data_dir: Path, symbols_input: list[str], channels: list[str] | str | None = None) -> list[str]:
    """Resolve user entered symbols to matching catalog symbols if possible.

    Prefers :meth:`CrypcodileClient.resolve_symbols` with ``ambiguous="first"``
    when the lake has catalog data. Falls back to the legacy Catalog walk on
    empty lakes or any client failure (keeps original input on no match).
    """
    # Prefer client façade when catalog has data.
    try:
        from crypcodile.client.client import CrypcodileClient

        client = CrypcodileClient(data_dir=data_dir)
        if client.list_channels():
            channel: str | None = None
            if isinstance(channels, str):
                channel = channels
            elif isinstance(channels, (list, tuple)) and len(channels) == 1:
                channel = channels[0]
            # Multi-channel filters are not supported by resolve_symbols;
            # leave channel=None so inventory spans the lake.
            return client.resolve_symbols(
                list(symbols_input or []),
                channel=channel,
                ambiguous="first",
            )
    except Exception:
        pass

    # Legacy fallback: walk registered Catalog symbols directly.
    from crypcodile.store.catalog import Catalog

    all_registered = None

    def get_registered():
        nonlocal all_registered
        if all_registered is not None:
            return all_registered
        all_registered = set()
        try:
            cat = Catalog(data_dir)
            target_channels = cat._registered_channels
            if channels:
                if isinstance(channels, str):
                    ch_list = [channels]
                else:
                    ch_list = list(channels)
                target_channels = [c for c in ch_list if c in target_channels]

            for ch in target_channels:
                try:
                    df = cat.query(f'SELECT DISTINCT symbol FROM "{ch}"')
                    for s in df["symbol"].to_list():
                        if s:
                            all_registered.add(str(s))
                except Exception:
                    pass
        except Exception:
            pass
        return all_registered

    resolved = []
    for sym in symbols_input:
        sym_clean = sym.strip()
        if not sym_clean:
            continue

        # Fast path: if the input is already in canonical format (e.g. exchange:symbol)
        # we can bypass checking the DB entirely to avoid slow startup queries.
        if ":" in sym_clean:
            resolved.append(sym_clean)
            continue

        reg_symbols_set = get_registered()

        # 1. Exact match in registered symbols
        if sym_clean in reg_symbols_set:
            resolved.append(sym_clean)
            continue

        reg_symbols = sorted(list(reg_symbols_set))

        # 2. Case-insensitive exact match
        lower_sym = sym_clean.lower()
        matched = False
        for reg in reg_symbols:
            if reg.lower() == lower_sym:
                resolved.append(reg)
                matched = True
                break
        if matched:
            continue

        # 3. Prefix-less match (e.g., "BTC-PERPETUAL" matching "deribit:BTC-PERPETUAL")
        for reg in reg_symbols:
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
        for reg in reg_symbols:
            if lower_sym in reg.lower():
                matches.append(reg)
        if len(matches) >= 1:
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
        if is_interactive_stdin():
            sql = typer.prompt("SQL query")
        else:
            import sys
            sql = sys.stdin.read().strip()
            if not sql:
                typer.echo("Error: SQL query is required and stdin is empty.", err=True)
                raise typer.Exit(code=1)

    if not sql:
        typer.echo("Error: SQL query cannot be empty.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.query(sql)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(df)


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


@app.command()
def catalog(
    data_dir: _DataDirOpt = Path("data"),
    symbols: Annotated[
        bool,
        typer.Option(
            "--symbols",
            help="Print inventory summary (exchange, channel, symbol, coverage).",
        ),
    ] = False,
) -> None:
    """List channels present in the data lake with their row counts.

    Use ``--symbols`` to print a per-symbol inventory summary instead.
    """
    from crypcodile.client.client import CrypcodileClient
    from crypcodile.store.catalog import Catalog

    data_dir = resolve_data_dir(data_dir)

    client = CrypcodileClient(data_dir=data_dir)
    cat: Catalog = client._catalog

    if symbols:
        inv = client.inventory()
        if len(inv) == 0:
            typer.echo("No data found in: " + str(data_dir))
            raise typer.Exit(code=0)
        # Inventory summary table.
        cols = ["exchange", "channel", "symbol", "min_ts", "max_ts", "row_count"]
        typer.echo(
            f"{'exchange':<16}  {'channel':<16}  {'symbol':<32}  "
            f"{'min_ts':>20}  {'max_ts':>20}  {'row_count':>10}"
        )
        typer.echo("-" * 128)
        for row in inv.select(cols).iter_rows(named=True):
            typer.echo(
                f"{row['exchange']:<16}  {row['channel']:<16}  {row['symbol']:<32}  "
                f"{row['min_ts']:>20}  {row['max_ts']:>20}  {row['row_count']:>10,}"
            )
        raise typer.Exit(code=0)

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
# search
# ---------------------------------------------------------------------------


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Symbol search query.")] = "",
    channel: Annotated[
        str | None,
        typer.Option("--channel", help="Optional channel filter."),
    ] = None,
    exchange: Annotated[
        str | None,
        typer.Option("--exchange", help="Optional exchange filter."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum number of results."),
    ] = 20,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Ranked symbol search over the data lake inventory.

    Prints a table of matching symbols with score and coverage.  Empty
    results print ``No matches.`` and exit 0.
    """
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    query = query.strip()
    if not query:
        if is_interactive_stdin():
            query = typer.prompt("Search query").strip()
        if not query:
            typer.echo("Error: search query is required.", err=True)
            raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    df = client.search_symbols(
        query, channel=channel, exchange=exchange, limit=limit
    )

    if len(df) == 0:
        typer.echo("No matches.")
        raise typer.Exit(code=0)

    typer.echo(
        f"{'symbol':<32}  {'exchange':<12}  {'channels':<24}  "
        f"{'score':>5}  {'min_ts':>20}  {'max_ts':>20}  {'row_count':>10}"
    )
    typer.echo("-" * 140)
    for row in df.iter_rows(named=True):
        typer.echo(
            f"{row['symbol']:<32}  {row['exchange']:<12}  {row['channels']:<24}  "
            f"{row['score']:>5}  {row['min_ts']:>20}  {row['max_ts']:>20}  "
            f"{row['row_count']:>10,}"
        )


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
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of rows to export."),
    ] = None,
) -> None:
    """Export channel x symbols x time range to a file."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not channel or not symbols:
            typer.echo("Error: channel and symbols are required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if frm is None:
            frm = 0
        if to is None:
            to = 9999999999999999999
    else:
        # Interactive
        if not channel or not symbols:
            channel, selected_symbols = select_symbols_interactively(data_dir, channel)
            if selected_symbols:
                symbols = selected_symbols

        if not channel:
            channel = typer.prompt("Channel (e.g. trade)")
        if not symbols:
            sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel=channel)
            symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
        if frm is None or to is None:
            resolved_start, resolved_end = prompt_time_range_helper(data_dir, channel, symbols, default_start=0, default_end=9999999999999999999)
            if frm is None:
                frm = resolved_start
            if to is None:
                to = resolved_end

    if symbols:
        symbols = resolve_input_symbols(data_dir, symbols, channel)

    if not channel or not symbols:
        typer.echo("Error: Channel and symbols are required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        client.export(channel, symbols, frm, to, fmt=fmt, dest=dest, limit=limit)  # type: ignore[arg-type]
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
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

    if not is_interactive_stdin():
        if not channels or not symbols:
            typer.echo("Error: channels and symbols are required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if frm is None:
            frm = 0
        if to is None:
            to = 9999999999999999999
    else:
        # Interactive
        if not channels or not symbols:
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
        if frm is None or to is None:
            wiz_ch = channels[0] if channels else None
            resolved_start, resolved_end = prompt_time_range_helper(data_dir, wiz_ch, symbols, default_start=0, default_end=9999999999999999999)
            if frm is None:
                frm = resolved_start
            if to is None:
                to = resolved_end

    if symbols:
        symbols = resolve_input_symbols(data_dir, symbols, channels)

    if not channels or not symbols:
        typer.echo("Error: Channels and symbols are required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    count = 0
    try:
        for record in client.replay(channels, symbols, frm, to, limit=limit):
            typer.echo(repr(record))
            count += 1
            if limit is not None and count >= limit:
                break
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"-- {count} record(s) replayed.")


def select_collect_params_interactively(
    exchange: str | None,
    symbols: list[str] | None,
    channels: list[str] | None
) -> tuple[str, list[str], list[str]]:
    """Select exchange, channels, and symbols interactively for live data collection."""
    import sys

    valid_exchanges = list_exchanges()
    valid_channels = ["trade", "book_ticker", "book_snapshot", "book_delta"]
    
    suggested_symbols = {
        "binance": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "bybit": ["BTCUSDT", "ETHUSDT"],
        "coinbase": ["BTC-USD", "ETH-USD"],
        "deribit": ["BTC-PERPETUAL", "ETH-PERPETUAL", "SOL-PERPETUAL"],
        "okx": ["BTC-USDT", "ETH-USDT"],
        "base_onchain": ["cbBTC-USDC", "AERO-USDC", "WETH-USDC", "DEGEN-WETH", "WELL-WETH"],
        "gmx_synthetix": ["GMX:BTC-USD", "GMX:ETH-USD", "SYNTHETIX:ETH-USD"],
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
        typer.echo("  [C] Enter custom channel(s)")
        while True:
            choice = typer.prompt("Select channel(s)", default="1").strip()
            if not choice:
                typer.echo("Invalid selection. Try again.", err=True)
                continue
            if choice.lower() == "c":
                custom_input = typer.prompt("Enter channel(s), comma-separated").strip()
                custom_channels = [c.strip() for c in custom_input.split(",") if c.strip()]
                if custom_channels:
                    channels = custom_channels
                    break
            elif any(c.isdigit() for c in choice):
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
            else:
                input_channels = [c.strip() for c in choice.split(",") if c.strip()]
                if input_channels and all(ch in valid_channels for ch in input_channels):
                    channels = input_channels
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
            if not choice:
                typer.echo("Invalid selection. Try again.", err=True)
                continue
            if choice.lower() == "c":
                custom_input = prompt_with_autocomplete("Enter symbol (e.g. BTC)", suggestions)
                custom_symbols = [s.strip() for s in custom_input.split(",") if s.strip()]
                if custom_symbols:
                    symbols = [normalize_user_symbol(exchange, s) for s in custom_symbols]
                    break
            elif any(c.isdigit() for c in choice):
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
    import math
    prices = [p for p in prices if p is not None and math.isfinite(p)]
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
            
        if time_since_last_msg > 1.0:
            rate = 0.0
            
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


def expand_csv_options(values: list[str] | None) -> list[str]:
    """Expand repeated CLI options that may also contain commas.

    ``--exchange a --exchange b`` and ``--exchange a,b`` both yield
    ``["a", "b"]``.  Empty segments are dropped; order is preserved.
    """
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        for part in str(raw).split(","):
            item = part.strip()
            if item:
                out.append(item)
    return out


def unique_preserve(items: list[str]) -> list[str]:
    """Return *items* de-duplicated while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


@app.command()
def collect(
    exchange: Annotated[
        list[str] | None,
        typer.Option(
            "--exchange",
            help=(
                "Exchange name(s), e.g. deribit. Repeat the flag and/or use "
                "commas for multi-exchange collect (same symbols/channels for "
                "every exchange; symbols are normalized per exchange)."
            ),
        ),
    ] = None,
    symbols: Annotated[
        list[str] | None,
        typer.Option("--symbols", help="Symbol(s) to collect. Repeat for multiple."),
    ] = None,
    channels: Annotated[
        list[str] | None,
        typer.Option("--channels", help="Channel(s) to subscribe. Repeat for multiple."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
    dlq_report: Annotated[
        Path | None,
        typer.Option(
            "--dlq-report",
            help=(
                "Path for dead-letter report JSON written on stop when the DLQ "
                "is non-empty. Default: {data_dir}/dlq_report.json."
            ),
        ),
    ] = None,
    max_reconnects: Annotated[
        int | None,
        typer.Option(
            "--max-reconnects",
            help=(
                "Maximum connector reconnect attempts after a transport failure. "
                "Default: unlimited (-1). Use 0 to disable reconnects."
            ),
        ),
    ] = None,
    duration_seconds: Annotated[
        float | None,
        typer.Option(
            "--duration-seconds",
            help="Auto-stop collection after this many seconds.",
        ),
    ] = None,
) -> None:
    """Collect live market data from one or more exchanges into the Parquet lake.

    Press Ctrl-C (SIGINT) to stop gracefully — the sink is flushed before exit.
    Unparseable frames land in a dead-letter queue; on stop a JSON report is
    written when the queue is non-empty (see --dlq-report).

    Use --duration-seconds to auto-stop after a fixed wall-clock duration, and
    --max-reconnects to cap reconnect attempts (default unlimited).

    Multi-exchange: pass ``--exchange`` multiple times and/or comma-separate
    names (``--exchange binance,deribit``).  One connector is built per
    exchange.  **Limitation:** the same symbol and channel lists are applied
    to every exchange (each symbol string is normalized for that exchange,
    e.g. ``BTC`` → ``BTCUSDT`` on binance and ``BTC-PERPETUAL`` on deribit).
    Per-exchange symbol maps are not supported yet.

    Valid exchange names: binance, bybit, coinbase, deribit, okx,
    base_onchain, gmx_synthetix.

    Examples::

        crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \
                          --channels trade --channels book_delta --data-dir data

        crypcodile collect --exchange binance --exchange deribit \
                          --symbols BTC --channels trade --data-dir data

        crypcodile collect --exchange binance,bybit --symbols BTCUSDT \
                          --channels trade --data-dir data
    """
    exchanges = unique_preserve(
        [e.lower() for e in expand_csv_options(exchange)]
    )

    if not is_interactive_stdin():
        if not exchanges or not symbols or not channels:
            typer.echo(
                "Error: exchange, symbols, and channels are required in "
                "non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        # Interactive — wizard is single-exchange; multi-exchange only via flags.
        if not exchanges or not symbols or not channels:
            if len(exchanges) > 1:
                if not channels:
                    ch_input = typer.prompt("Channel (e.g. trade)")
                    channels = [c.strip() for c in ch_input.split(",") if c.strip()]
                if not symbols:
                    sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir)
                    symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
            else:
                ex_single = exchanges[0] if exchanges else None
                ex_single, symbols, channels = select_collect_params_interactively(
                    ex_single, symbols, channels
                )
                exchanges = [ex_single] if ex_single else []

        if not exchanges:
            exchange_prompt = typer.prompt("Exchange (e.g. deribit)")
            exchanges = unique_preserve(
                [e.lower() for e in expand_csv_options([exchange_prompt])]
            )
        if not symbols:
            sym_input = prompt_symbol("Symbol (e.g. BTC)", data_dir)
            symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
        if not channels:
            ch_input = typer.prompt("Channel (e.g. trade)")
            channels = [c.strip() for c in ch_input.split(",") if c.strip()]

    if not exchanges or not symbols or not channels:
        typer.echo("Error: Exchange, symbols, and channels are required.", err=True)
        raise typer.Exit(code=1)

    # Raw user symbols; normalized per exchange when building connectors.
    raw_symbols = [s for s in symbols if s and str(s).strip()]
    channel_list = list(channels)

    sink = ParquetSink(
        data_dir=data_dir,
        max_buffer_rows=10_000,
        flush_interval_seconds=5.0,
    )
    registry = InstrumentRegistry()
    monitoring_sink = MonitoringSink(sink)

    connectors: list = []
    per_exchange_symbols: dict[str, list[str]] = {}
    try:
        for ex in exchanges:
            ex_symbols = [
                normalize_user_symbol(ex, s) for s in raw_symbols if str(s).strip()
            ]
            ex_symbols = [s for s in ex_symbols if s]
            if not ex_symbols:
                typer.echo(
                    f"Error: no valid symbols after normalization for exchange {ex!r}.",
                    err=True,
                )
                raise typer.Exit(code=1)
            per_exchange_symbols[ex] = ex_symbols
            connector = make_connector(
                exchange=ex,
                symbols=ex_symbols,
                channels=channel_list,
                out=sink,
                registry=registry,
            )
            # Wire live WS transport (may already be set by tests/monkeypatch).
            if connector.transport is None:
                connector.transport = AiohttpWsTransport(connector.ws_url)
            connector.out = monitoring_sink
            connectors.append(connector)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Log line keeps single-exchange shape for backward-compatible tests.
    if len(exchanges) == 1:
        exchange_log = f"exchange={exchanges[0]!r}"
        symbols_log = per_exchange_symbols[exchanges[0]]
    else:
        exchange_log = f"exchanges={exchanges!r}"
        symbols_log = per_exchange_symbols

    typer.echo(
        f"Starting collection: {exchange_log} symbols={symbols_log} "
        f"channels={channel_list} data_dir={data_dir}"
        + (f" max_reconnects={max_reconnects}" if max_reconnects is not None else "")
        + (f" duration_seconds={duration_seconds}" if duration_seconds is not None else "")
    )

    collect_kwargs: dict = {
        "dlq_report_path": dlq_report,
        "data_dir": data_dir,
    }
    if max_reconnects is not None:
        collect_kwargs["max_reconnects"] = max_reconnects

    async def _run_collect_live() -> None:
        """Run collect_live, optionally auto-stopping after duration_seconds."""
        if duration_seconds is None:
            await collect_live(connectors, monitoring_sink, **collect_kwargs)
            return

        collect_task = asyncio.create_task(
            collect_live(connectors, monitoring_sink, **collect_kwargs)
        )

        async def _cancel_after() -> None:
            await asyncio.sleep(duration_seconds)
            collect_task.cancel()

        timer_task = asyncio.create_task(_cancel_after())
        try:
            await collect_task
        except asyncio.CancelledError:
            # Expected on duration expiry (and on outer cancellation).
            pass
        finally:
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass

    # Dashboard label: comma-joined exchange names.
    dashboard_exchange = ",".join(exchanges)
    # Prefer first exchange's normalized symbols for the status panel.
    dashboard_symbols = per_exchange_symbols[exchanges[0]]

    if is_interactive_stdin():

        async def collect_with_dashboard():
            dashboard_task = asyncio.create_task(
                run_dashboard(
                    monitoring_sink,
                    dashboard_exchange,
                    dashboard_symbols,
                    channel_list,
                    data_dir,
                )
            )
            try:
                await _run_collect_live()
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
            asyncio.run(_run_collect_live())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    typer.echo("Collection stopped. Data written to: " + str(data_dir))


# ---------------------------------------------------------------------------
# backfill  — historical REST data into the Parquet lake
# ---------------------------------------------------------------------------


@app.command()
def backfill(
    exchange: Annotated[
        str | None,
        typer.Option("--exchange", help="Exchange name: binance, bybit, okx, deribit."),
    ] = None,
    channel: Annotated[
        str | None,
        typer.Option(
            "--channel",
            help="Channel to backfill: trade, funding, ohlcv, open_interest.",
        ),
    ] = None,
    symbols: Annotated[
        list[str] | None,
        typer.Option("--symbols", help="Symbol(s) to backfill. Repeat for multiple."),
    ] = None,
    frm: Annotated[
        int | None,
        typer.Option("--from", "--start", help="Start of time range (nanoseconds UTC)."),
    ] = None,
    to: Annotated[
        int | None,
        typer.Option("--to", "--end", help="End of time range (nanoseconds UTC)."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
    market: Annotated[
        str,
        typer.Option("--market", help="Binance market: spot|usdm|coinm."),
    ] = "spot",
    category: Annotated[
        str,
        typer.Option("--category", help="Bybit category: spot|linear|inverse."),
    ] = "linear",
    inst_type: Annotated[
        str,
        typer.Option("--inst-type", help="OKX instType: SPOT|SWAP|FUTURES."),
    ] = "SWAP",
    interval: Annotated[
        str,
        typer.Option("--interval", help="OHLCV interval for Binance klines (e.g. 1m)."),
    ] = "1m",
    period: Annotated[
        str,
        typer.Option("--period", help="Open-interest hist period for Binance (e.g. 5m)."),
    ] = "5m",
) -> None:
    """Fetch historical market data via REST and write to the Parquet data lake.

    Supported exchanges: binance, bybit, okx, deribit.
    Channel availability depends on the exchange:

    - binance: trade, ohlcv, open_interest
    - bybit:   trade, funding, open_interest
    - okx:     trade, funding, open_interest
    - deribit: trade, funding

    Time bounds accept ``--from``/``--to`` or aliases ``--start``/``--end``
    (nanoseconds UTC).

    Example::

        crypcodile backfill --exchange binance --channel trade \\
                           --symbols BTCUSDT \\
                           --from 1700000000000000000 --to 1700000100000000000 \\
                           --data-dir data
    """
    from crypcodile.client.backfill import SUPPORTED_EXCHANGES

    if not is_interactive_stdin():
        if not exchange or not channel or not symbols:
            typer.echo(
                "Error: --exchange, --channel, and --symbols are required "
                "in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
        if frm is None or to is None:
            typer.echo(
                "Error: --from/--start and --to/--end are required "
                "in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if not exchange:
            exchange = typer.prompt(
                "Exchange (binance, bybit, okx, deribit)"
            )
        if not channel:
            channel = typer.prompt("Channel (e.g. trade)")
        if not symbols:
            sym_input = typer.prompt("Symbol(s), comma-separated (e.g. BTCUSDT)")
            symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
        if frm is None:
            frm = int(typer.prompt("Start ns (--from)", default="0"))
        if to is None:
            to = int(typer.prompt("End ns (--to)", default="9999999999999999999"))

    if not exchange or not channel or not symbols:
        typer.echo("Error: --exchange, --channel, and --symbols are required.", err=True)
        raise typer.Exit(code=1)
    if frm is None or to is None:
        typer.echo("Error: --from/--start and --to/--end are required.", err=True)
        raise typer.Exit(code=1)
    if frm > to:
        typer.echo("Error: --from must be <= --to.", err=True)
        raise typer.Exit(code=1)

    exchange = exchange.lower().strip()
    channel = channel.lower().strip()

    if exchange not in SUPPORTED_EXCHANGES:
        typer.echo(
            f"Error: Unsupported exchange {exchange!r} for backfill. "
            f"Supported: {sorted(SUPPORTED_EXCHANGES)}",
            err=True,
        )
        raise typer.Exit(code=1)

    symbols = [normalize_user_symbol(exchange, s) for s in symbols if s.strip()]
    if not symbols:
        typer.echo("Error: At least one symbol is required.", err=True)
        raise typer.Exit(code=1)

    sink = ParquetSink(
        data_dir=data_dir,
        max_buffer_rows=10_000,
        flush_interval_seconds=5.0,
    )

    typer.echo(
        f"Starting backfill: exchange={exchange!r} channel={channel!r} "
        f"symbols={symbols} from={frm} to={to} data_dir={data_dir}"
    )

    try:
        count = asyncio.run(
            run_historical_backfill(
                exchange=exchange,
                channel=channel,
                symbols=list(symbols),
                start_ns=int(frm),
                end_ns=int(to),
                sink=sink,
                market=market,
                category=category,
                inst_type=inst_type,
                interval=interval,
                period=period,
            )
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (KeyboardInterrupt, asyncio.CancelledError):
        typer.echo("Backfill interrupted.", err=True)
        raise typer.Exit(code=0) from None
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Backfill complete: {count} records written to {data_dir}")


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

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if start is None:
            start = 0
        if end is None:
            end = 9999999999999999999
    else:
        # Interactive
        if not symbol:
            _, selected_symbols = select_symbols_interactively(data_dir, channel="funding")
            if selected_symbols:
                symbol = selected_symbols[0]

        if not symbol:
            symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="funding")
        if start is None or end is None:
            resolved_start, resolved_end = prompt_time_range_helper(data_dir, "funding", [symbol] if symbol else None, default_start=0, default_end=9999999999999999999)
            if start is None:
                start = resolved_start
            if end is None:
                end = resolved_end

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "funding")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.funding_apr(symbol, start, end)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

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
        typer.Option(
            "--spot",
            help="Canonical spot symbol (spot-future or spot-perp mode).",
        ),
    ] = None,
    perp: Annotated[
        str | None,
        typer.Option(
            "--perp",
            help="Canonical perpetual symbol (mark/index mode alone, or spot-perp with --spot).",
        ),
    ] = None,
    expiry: Annotated[
        int | None,
        typer.Option("--expiry", help="Contract expiry (ns UTC; spot-future mode only)."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print spot-future, spot-perp, or mark/index perpetual basis.

    Modes:
      --perp alone              mark vs index (derivative_ticker)
      --future and --spot       spot-future basis (trade ASOF join)
      --spot and --perp         true spot-perp basis (spot vs perp mark)
    """
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if perp is not None:
        perp = perp.strip()
        if not perp:
            perp = None
    if future is not None:
        future = future.strip()
        if not future:
            future = None
    if spot is not None:
        spot = spot.strip()
        if not spot:
            spot = None

    # --perp and --future cannot be combined; --spot + --perp is spot-perp mode.
    if perp is not None and future is not None:
        typer.echo(
            "Error: --perp and --future are mutually exclusive "
            "(use --spot with --perp for spot-perp basis, or --future with --spot "
            "for spot-future basis).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Classify mode once symbols are known (None after empty-strip).
    def _basis_mode(
        p: str | None, f: str | None, s: str | None
    ) -> str | None:
        if p is not None and s is not None and f is None:
            return "spot_perp"
        if p is not None and s is None and f is None:
            return "perp"
        if f is not None and s is not None and p is None:
            return "spot_future"
        return None

    if not is_interactive_stdin():
        mode = _basis_mode(perp, future, spot)
        if mode is None:
            typer.echo(
                "Error: Specify one of: --perp; both --future and --spot; "
                "or both --spot and --perp in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
        if start is None:
            start = 0
        if end is None:
            end = 9999999999999999999
    else:
        # Interactive
        mode = _basis_mode(perp, future, spot)

        # Partial spot-future: only one of future/spot given (no perp)
        if mode is None and perp is None and (future is not None or spot is not None):
            if not future:
                _, selected_futures = select_symbols_interactively(data_dir, channel="trade")
                if selected_futures:
                    future = selected_futures[0]
                if not future:
                    future = prompt_symbol("Futures symbol (e.g. BTC)", data_dir, channel="trade")
            if not spot:
                _, selected_spots = select_symbols_interactively(data_dir, channel="trade")
                if selected_spots:
                    spot = selected_spots[0]
                if not spot:
                    spot = prompt_symbol("Spot symbol (e.g. BTC)", data_dir, channel="trade")
            mode = _basis_mode(perp, future, spot)

        # Partial spot-perp: only one of spot/perp given (no future)
        elif mode is None and future is None and (perp is not None or spot is not None):
            if not perp:
                _, selected_perps = select_symbols_interactively(
                    data_dir, channel="derivative_ticker"
                )
                if selected_perps:
                    perp = selected_perps[0]
                if not perp:
                    perp = prompt_symbol(
                        "Perpetual symbol (e.g. BTC)", data_dir, channel="derivative_ticker"
                    )
            if not spot:
                _, selected_spots = select_symbols_interactively(
                    data_dir, channel="trade"
                )
                if selected_spots:
                    spot = selected_spots[0]
                if not spot:
                    spot = prompt_symbol("Spot symbol (e.g. BTC)", data_dir, channel="trade")
            mode = _basis_mode(perp, future, spot)

        # Nothing specified: ask for mode
        elif mode is None and perp is None and future is None and spot is None:
            mode_choice = typer.prompt(
                "Basis mode (perp | spot-future | spot-perp)",
                default="perp",
            ).strip().lower()
            if mode_choice in ("spot-perp", "spot_perp", "spotperp"):
                typer.echo("\nSelect spot symbol:")
                _, selected_spots = select_symbols_interactively(data_dir, channel="trade")
                if selected_spots:
                    spot = selected_spots[0]
                typer.echo("\nSelect perpetual symbol:")
                _, selected_perps = select_symbols_interactively(
                    data_dir, channel="derivative_ticker"
                )
                if selected_perps:
                    perp = selected_perps[0]
                if not spot:
                    spot = prompt_symbol("Spot symbol (e.g. BTC)", data_dir, channel="trade")
                if not perp:
                    perp = prompt_symbol(
                        "Perpetual symbol (e.g. BTC)", data_dir, channel="derivative_ticker"
                    )
                mode = "spot_perp"
            elif mode_choice in ("spot-future", "spot_future", "future", "futures"):
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
                mode = "spot_future"
            else:
                # Default / "perp"
                _, selected_symbols = select_symbols_interactively(
                    data_dir, channel="derivative_ticker"
                )
                if selected_symbols:
                    perp = selected_symbols[0]
                if not perp:
                    perp = prompt_symbol(
                        "Perpetual symbol (e.g. BTC)", data_dir, channel="derivative_ticker"
                    )
                mode = "perp"

        if start is None or end is None:
            if mode == "perp":
                ch = "derivative_ticker"
                syms = [perp] if perp else None
            elif mode == "spot_perp":
                ch = "derivative_ticker"
                syms = [p for p in (perp, spot) if p]
            else:
                ch = "trade"
                syms = [s for s in (future, spot) if s] or None
            resolved_start, resolved_end = prompt_time_range_helper(
                data_dir, ch, syms, default_start=0, default_end=9999999999999999999
            )
            if start is None:
                start = resolved_start
            if end is None:
                end = resolved_end

        if mode is None:
            mode = _basis_mode(perp, future, spot)

    if perp:
        resolved = resolve_input_symbols(data_dir, [perp], ["derivative_ticker", "ticker"])
        if resolved:
            perp = resolved[0]
    if future:
        resolved = resolve_input_symbols(
            data_dir, [future], ["trade", "derivative_ticker", "ticker"]
        )
        if resolved:
            future = resolved[0]
    if spot:
        # Spot-perp may use trade or book_snapshot mid; include book_snapshot for resolve.
        spot_channels = ["trade", "ticker", "book_snapshot"]
        resolved = resolve_input_symbols(data_dir, [spot], spot_channels)
        if resolved:
            spot = resolved[0]

    # Re-derive mode after resolution (symbols may still be None if resolve failed).
    mode = _basis_mode(perp, future, spot)

    client = CrypcodileClient(data_dir=data_dir)

    try:
        if mode == "spot_perp":
            assert perp is not None and spot is not None
            df = client.spot_perp_basis(spot, perp, start, end)
        elif mode == "perp":
            assert perp is not None
            df = client.perp_basis(perp, start, end)
        elif mode == "spot_future":
            assert future is not None and spot is not None
            df = client.spot_future_basis(future, spot, start, end, expiry_ns=expiry)
        else:
            typer.echo(
                "Error: provide either --perp <symbol>; both --future <symbol> and "
                "--spot <symbol>; or both --spot <symbol> and --perp <symbol>.",
                err=True,
            )
            raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No basis data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
def get_latest_options_chain_date_glob(data_dir: Path) -> str | None:
    import glob
    pattern = str(Path(data_dir) / "exchange=*" / "channel=options_chain" / "date=*")
    date_paths = glob.glob(pattern)
    if not date_paths:
        return None
    def extract_date(path_str):
        parts = Path(path_str).name.split("=")
        if len(parts) == 2 and parts[0] == "date":
            return parts[1]
        return ""
    date_paths.sort(key=extract_date)
    latest_path = date_paths[-1]
    return str(Path(latest_path) / "bucket=*" / "part-*.parquet")


def get_available_option_underlyings(data_dir: Path) -> list[str]:
    """Get list of unique underlyings in options_chain."""
    from crypcodile.store.catalog import Catalog
    cat = Catalog(data_dir)
    if "options_chain" not in cat._registered_channels:
        return []
    
    # Try querying only the latest date partition first
    try:
        latest_glob = get_latest_options_chain_date_glob(data_dir)
        if latest_glob:
            escaped_glob = latest_glob.replace("'", "''")
            df = cat.query(f"SELECT DISTINCT underlying FROM read_parquet('{escaped_glob}', hive_partitioning=>true, union_by_name=>true) ORDER BY underlying")
            res = [str(x) for x in df["underlying"].to_list() if x]
            if res:
                return res
    except Exception:
        pass

    # Fallback to full database query
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

    # Try querying only the latest date partition first
    try:
        latest_glob = get_latest_options_chain_date_glob(data_dir)
        if latest_glob:
            escaped_glob = latest_glob.replace("'", "''")
            u_filter = ""
            if underlying:
                u_filter = f" WHERE UPPER(underlying) = '{underlying.upper()}'"
            sql = f"SELECT local_ts FROM read_parquet('{escaped_glob}', hive_partitioning=>true, union_by_name=>true){u_filter} ORDER BY local_ts DESC LIMIT 200"
            df = cat.query(sql)
            seen = set()
            res = []
            for x in df["local_ts"].to_list():
                if x and x not in seen:
                    seen.add(x)
                    res.append(int(x))
                    if len(res) >= 5:
                        break
            if res:
                return res
    except Exception:
        pass

    # Fallback to full database query
    try:
        u_filter = ""
        if underlying:
            u_filter = f" WHERE UPPER(underlying) = '{underlying.upper()}'"
        sql = f"SELECT local_ts FROM options_chain{u_filter} ORDER BY local_ts DESC LIMIT 1000"
        df = cat.query(sql)
        seen = set()
        res = []
        for x in df["local_ts"].to_list():
            if x and x not in seen:
                seen.add(x)
                res.append(int(x))
                if len(res) >= 5:
                    break
        return res
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
    from crypcodile.client.client import CrypcodileClient

    if not is_interactive_stdin():
        if not underlying or at is None:
            typer.echo("Error: underlying and at snapshot instant are required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
    else:
        # Interactive
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
                    try:
                        dt_str = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                    except Exception:
                        dt_str = "Invalid timestamp"
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
    try:
        df = client.iv_surface(underlying, at, rate=rate)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
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

    if not is_interactive_stdin():
        if not underlying or at is None:
            typer.echo("Error: underlying and at snapshot instant are required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
    else:
        # Interactive
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
                    try:
                        dt_str = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
                    except Exception:
                        dt_str = "Invalid timestamp"
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
    try:
        df = client.term_structure(underlying, at, rate=rate)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    if len(df) == 0:
        typer.echo("No options data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# vol-skew
# ---------------------------------------------------------------------------


def get_available_option_expiries(
    data_dir: Path,
    underlying: str | None = None,
    at_ns: int | None = None,
) -> list[int]:
    """Return distinct option expiries (ns UTC), optionally filtered by underlying / snapshot."""
    from crypcodile.store.catalog import Catalog

    cat = Catalog(data_dir)
    if "options_chain" not in cat._registered_channels:
        return []

    clauses: list[str] = []
    if underlying:
        clauses.append(f"UPPER(underlying) = '{underlying.upper()}'")
    if at_ns is not None:
        clauses.append(f"local_ts <= {int(at_ns)}")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    try:
        sql = (
            f"SELECT DISTINCT expiry FROM options_chain{where} "
            "ORDER BY expiry ASC LIMIT 50"
        )
        df = cat.query(sql)
        return [int(x) for x in df["expiry"].to_list() if x is not None]
    except Exception:
        return []


def _prompt_option_underlying_at_expiry(
    data_dir: Path,
    underlying: str | None,
    at: int | None,
    expiry_ns: int | None,
) -> tuple[str | None, int | None, int | None]:
    """Interactive prompts for underlying / snapshot / expiry used by vol-skew & risk-reversal."""
    import datetime

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
                typer.echo(
                    "⚠️  No option data (options_chain channel) found in the database. "
                    "Please collect options data first."
                )

        if snapshots:
            typer.echo("\n--- Available Options Snapshots (latest first) ---")
            for idx, ts in enumerate(snapshots, 1):
                try:
                    dt_str = datetime.datetime.fromtimestamp(
                        ts // 1_000_000_000, tz=datetime.UTC
                    ).strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    dt_str = "Invalid timestamp"
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
            at_str = typer.prompt(
                "Snapshot time (nanoseconds UTC, e.g. 1704067200000000000)"
            ).strip()
            try:
                at = int(at_str)
            except ValueError:
                at = None

    if expiry_ns is None:
        expiries = get_available_option_expiries(data_dir, underlying, at)
        if expiries:
            typer.echo("\n--- Available Expiries ---")
            for idx, exp in enumerate(expiries, 1):
                try:
                    dt_str = datetime.datetime.fromtimestamp(
                        exp // 1_000_000_000, tz=datetime.UTC
                    ).strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    dt_str = "Invalid timestamp"
                typer.echo(f"  [{idx}] {exp} ({dt_str})")
            choice = typer.prompt("Select expiry by number or enter custom", default="1").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(expiries):
                expiry_ns = expiries[int(choice) - 1]
            else:
                try:
                    expiry_ns = int(choice)
                except ValueError:
                    expiry_ns = None
        else:
            exp_str = typer.prompt(
                "Expiry (nanoseconds UTC, e.g. 1735689600000000000)"
            ).strip()
            try:
                expiry_ns = int(exp_str)
            except ValueError:
                expiry_ns = None

    return underlying, at, expiry_ns


@app.command(name="vol-skew")
def vol_skew_cmd(
    underlying: Annotated[
        str | None,
        typer.Option("--underlying", help="Underlying asset identifier, e.g. BTC."),
    ] = None,
    expiry_ns: Annotated[
        int | None,
        typer.Option(
            "--expiry-ns",
            "--expiry",
            help="Option expiry (nanoseconds UTC).",
        ),
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
    """Print per-strike IV and delta for a single expiry (vol skew)."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not underlying or at is None or expiry_ns is None:
            typer.echo(
                "Error: underlying, expiry-ns, and at snapshot instant are required "
                "in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        underlying, at, expiry_ns = _prompt_option_underlying_at_expiry(
            data_dir, underlying, at, expiry_ns
        )

    if not underlying or at is None or expiry_ns is None:
        typer.echo(
            "Error: Underlying, expiry (expiry-ns), and snapshot instant (at) are required.",
            err=True,
        )
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.vol_skew(underlying, expiry_ns, at, rate=rate)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    if len(df) == 0:
        typer.echo("No options data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# risk-reversal
# ---------------------------------------------------------------------------


@app.command(name="risk-reversal")
def risk_reversal_cmd(
    underlying: Annotated[
        str | None,
        typer.Option("--underlying", help="Underlying asset identifier, e.g. BTC."),
    ] = None,
    expiry_ns: Annotated[
        int | None,
        typer.Option(
            "--expiry-ns",
            "--expiry",
            help="Option expiry (nanoseconds UTC).",
        ),
    ] = None,
    at: Annotated[
        int | None,
        typer.Option("--at", help="Snapshot instant (nanoseconds UTC)."),
    ] = None,
    rate: Annotated[
        float,
        typer.Option("--rate", help="Continuous risk-free rate (default 0.0)."),
    ] = 0.0,
    target_delta: Annotated[
        float,
        typer.Option(
            "--target-delta",
            help="Target absolute delta for RR/BF (default 0.25).",
        ),
    ] = 0.25,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print risk-reversal and butterfly from the vol skew at a single expiry."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not underlying or at is None or expiry_ns is None:
            typer.echo(
                "Error: underlying, expiry-ns, and at snapshot instant are required "
                "in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        underlying, at, expiry_ns = _prompt_option_underlying_at_expiry(
            data_dir, underlying, at, expiry_ns
        )

    if not underlying or at is None or expiry_ns is None:
        typer.echo(
            "Error: Underlying, expiry (expiry-ns), and snapshot instant (at) are required.",
            err=True,
        )
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        skew_df = client.vol_skew(underlying, expiry_ns, at, rate=rate)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    if len(skew_df) == 0:
        typer.echo("No options data found.")
        raise typer.Exit(code=0)

    try:
        rr, bf = client.risk_reversal_butterfly(skew_df, target_delta=target_delta)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"risk_reversal: {rr}")
    typer.echo(f"butterfly: {bf}")


# ---------------------------------------------------------------------------
# slippage (Task R1)
# ---------------------------------------------------------------------------


@app.command(name="slippage")
def slippage_cmd(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="Canonical symbol, e.g. deribit:BTC-PERPETUAL."),
    ] = None,
    side: Annotated[
        str | None,
        typer.Option("--side", help="Execution side (buy or sell)."),
    ] = None,
    size: Annotated[
        float | None,
        typer.Option("--size", help="Base asset execution size."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Estimate execution slippage for a given symbol and size using the latest book snapshot."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if not side:
            typer.echo("Error: side is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if size is None:
            typer.echo("Error: size is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
    else:
        # Interactive
        if not symbol:
            _, selected_symbols = select_symbols_interactively(data_dir, channel="book_snapshot")
            if selected_symbols:
                symbol = selected_symbols[0]

        if not symbol:
            symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="book_snapshot")

        if not side:
            side = typer.prompt("Side (buy/sell)", default="buy")

        if size is None:
            size = typer.prompt("Size", type=float)

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "book_snapshot")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    if not side:
        typer.echo("Error: Side is required.", err=True)
        raise typer.Exit(code=1)

    if size is None:
        typer.echo("Error: Size is required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.estimate_slippage(symbol, side, size)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No slippage data calculated.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# ofi (Task R2)
# ---------------------------------------------------------------------------


@app.command(name="ofi")
def ofi_cmd(
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
    interval: Annotated[
        str | None,
        typer.Option("--interval", help="Time bin interval duration, e.g. 1s, 5m, 1h."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Calculate Order Flow Imbalance (OFI) index over time-binned intervals."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if start is None:
            start = 0
        if end is None:
            end = 9999999999999999999
        if not interval:
            interval = "1m"
    else:
        # Interactive
        if not symbol:
            _, selected_symbols = select_symbols_interactively(data_dir, channel="book_snapshot")
            if selected_symbols:
                symbol = selected_symbols[0]

        if not symbol:
            symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="book_snapshot")

        if start is None or end is None:
            resolved_start, resolved_end = prompt_time_range_helper(
                data_dir, "book_snapshot", [symbol] if symbol else None, default_start=0, default_end=9999999999999999999
            )
            if start is None:
                start = resolved_start
            if end is None:
                end = resolved_end

        if not interval:
            interval = typer.prompt("Interval (e.g. 1s, 1m, 5m)", default="1m")

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "book_snapshot")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    if not interval:
        typer.echo("Error: Interval is required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.calculate_ofi(symbol, start, end, interval)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No historical snapshots found for the given criteria.")
        raise typer.Exit(code=0)

    import datetime
    import polars as pl
    formatted_rows = []
    for r in df.to_dicts():
        ts = r["timestamp"]
        try:
            dt = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC)
            dt_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            dt_str = "Invalid timestamp"
        formatted_rows.append({
            "timestamp": dt_str,
            "best_bid": r["best_bid"],
            "best_ask": r["best_ask"],
            "ofi": r["ofi"]
        })
    df_formatted = pl.DataFrame(formatted_rows)
    typer.echo(df_formatted)


# ---------------------------------------------------------------------------
# liquidity-depth (block-level book depth from lake snapshots)
# ---------------------------------------------------------------------------


@app.command(name="liquidity-depth")
def liquidity_depth_cmd(
    symbol: Annotated[
        str | None,
        typer.Option(
            "--symbol",
            help="Canonical symbol, e.g. base_onchain:DEGEN-WETH or deribit:BTC-PERPETUAL.",
        ),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Calculate per-block bid/ask liquidity depth at ±1%, ±2%, ±5% from mid-price."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
    else:
        if not symbol:
            _, selected_symbols = select_symbols_interactively(
                data_dir, channel="book_snapshot"
            )
            if selected_symbols:
                symbol = selected_symbols[0]

        if not symbol:
            symbol = prompt_symbol(
                "Symbol (e.g. base_onchain:DEGEN-WETH)",
                data_dir,
                channel="book_snapshot",
            )

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "book_snapshot")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.calculate_block_liquidity_depth(symbol)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No book snapshots found for the given symbol.")
        raise typer.Exit(code=0)

    typer.echo(df)


# ---------------------------------------------------------------------------
# sequencer-latency (block production interval + ingestion delay from lake)
# ---------------------------------------------------------------------------


@app.command(name="sequencer-latency")
def sequencer_latency_cmd(
    exchange: Annotated[
        str,
        typer.Option(
            "--exchange",
            help="Exchange name to measure (e.g. base_onchain).",
        ),
    ] = "base_onchain",
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Measure sequencer production intervals and local ingestion delay from the lake."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)
    exchange = (exchange or "").strip() or "base_onchain"

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.calculate_sequencer_latency(exchange)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No sequencer latency data found.")
        raise typer.Exit(code=0)

    typer.echo(df)


# ---------------------------------------------------------------------------
# whale-alerts (Task R3)
# ---------------------------------------------------------------------------


@app.command(name="whale-alerts")
def whale_alerts_cmd(
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
    min_usd: Annotated[
        float | None,
        typer.Option("--min-usd", help="Minimum USD value threshold (price * amount)."),
    ] = None,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Track whale executions and liquidation alerts exceeding a USD value threshold."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if start is None:
            start = 0
        if end is None:
            end = 9999999999999999999
        if min_usd is None:
            min_usd = 100000.0
    else:
        # Interactive
        if not symbol:
            _, selected_symbols = select_symbols_interactively(data_dir, channel="trade")
            if selected_symbols:
                symbol = selected_symbols[0]

        if not symbol:
            symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="trade")

        if start is None or end is None:
            resolved_start, resolved_end = prompt_time_range_helper(
                data_dir, "trade", [symbol] if symbol else None, default_start=0, default_end=9999999999999999999
            )
            if start is None:
                start = resolved_start
            if end is None:
                end = resolved_end

        if min_usd is None:
            min_usd = typer.prompt("Min USD Value threshold", default=100000.0, type=float)

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "trade")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    if min_usd is None:
        typer.echo("Error: Min USD threshold is required.", err=True)
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.track_whale_alerts(symbol, start, end, min_usd)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No whale or liquidation alerts found.")
        raise typer.Exit(code=0)

    import datetime
    import polars as pl
    formatted_rows = []
    for r in df.to_dicts():
        ts = r["timestamp"]
        try:
            dt = datetime.datetime.fromtimestamp(ts // 1_000_000_000, tz=datetime.UTC)
            dt_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            dt_str = "Invalid timestamp"
        formatted_rows.append({
            "event_time": dt_str,
            "event_type": r["event_type"],
            "price": r["price"],
            "amount": r["amount"],
            "usd_value": r["usd_value"],
            "side": r["side"]
        })
    df_formatted = pl.DataFrame(formatted_rows)
    typer.echo(df_formatted)


# ---------------------------------------------------------------------------
# open-interest (Base risk analytics)
# ---------------------------------------------------------------------------


@app.command(name="open-interest")
def open_interest_cmd(
    symbol: Annotated[
        str | None,
        typer.Option(
            "--symbol",
            help="Substring filter for symbols (e.g. BTC). Omit to aggregate all.",
        ),
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
    """Aggregate open interest across exchanges with forward-fill alignment."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if start is None:
            start = 0
        if end is None:
            end = 9999999999999999999
    else:
        if symbol is None:
            symbol = typer.prompt(
                "Symbol filter (e.g. BTC, empty for all)",
                default="",
                show_default=False,
            )
            if not symbol:
                symbol = None
        if start is None or end is None:
            resolved_start, resolved_end = prompt_time_range_helper(
                data_dir,
                "open_interest",
                [symbol] if symbol else None,
                default_start=0,
                default_end=9999999999999999999,
            )
            if start is None:
                start = resolved_start
            if end is None:
                end = resolved_end

    if start is None:
        start = 0
    if end is None:
        end = 9999999999999999999

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.aggregate_open_interest(symbol, start, end)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No open interest data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# peg-deviation (Base risk analytics)
# ---------------------------------------------------------------------------


@app.command(name="peg-deviation")
def peg_deviation_cmd(
    symbol: Annotated[
        str | None,
        typer.Option(
            "--symbol",
            help="Canonical stablecoin symbol for lake mode, e.g. base_onchain:USDC-USDbC.",
        ),
    ] = None,
    price: Annotated[
        float | None,
        typer.Option(
            "--price",
            help="Mid price for pure mode (no lake). Absolute deviation from $1.00.",
        ),
    ] = None,
    bid: Annotated[
        float | None,
        typer.Option("--bid", help="Bid price for pure mode (paired with --ask)."),
    ] = None,
    ask: Annotated[
        float | None,
        typer.Option("--ask", help="Ask price for pure mode (paired with --bid)."),
    ] = None,
    threshold: Annotated[
        float,
        typer.Option("--threshold", help="Deviation alert threshold (e.g. 0.01 = 1%)."),
    ] = 0.01,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Detect stablecoin peg deviation from $1.00 (lake or pure --price/--bid/--ask)."""
    from crypcodile.analytics.peg_deviation import peg_deviation_from_price
    from crypcodile.client.client import CrypcodileClient

    # Pure path: --price or --bid/--ask
    mid: float | None = price
    if mid is None and bid is not None and ask is not None:
        mid = (float(bid) + float(ask)) / 2.0

    if mid is not None:
        result = peg_deviation_from_price(mid, threshold=threshold)
        typer.echo(
            f"price: {result['price']}\n"
            f"deviation_pct: {result['deviation_pct']}\n"
            f"threshold: {result['threshold']}\n"
            f"is_alert_triggered: {result['is_alert_triggered']}"
        )
        raise typer.Exit(code=0)

    # Lake path
    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo(
                "Error: provide --price/--bid+--ask (pure) or --symbol (lake) "
                "in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if not symbol:
            _, selected_symbols = select_symbols_interactively(
                data_dir, channel="book_ticker"
            )
            if selected_symbols:
                symbol = selected_symbols[0]
        if not symbol:
            symbol = prompt_symbol(
                "Symbol (e.g. base_onchain:USDC-USDbC)",
                data_dir,
                channel="book_ticker",
            )

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "book_ticker")
        if not resolved_syms:
            resolved_syms = resolve_input_symbols(data_dir, [symbol], "book_snapshot")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo(
            "Error: Symbol is required for lake mode (or pass --price for pure mode).",
            err=True,
        )
        raise typer.Exit(code=1)

    client = CrypcodileClient(data_dir=data_dir)
    try:
        df = client.calculate_peg_deviation(symbol, threshold)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if len(df) == 0:
        typer.echo("No peg deviation data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# chaos-score (Base risk analytics — pure numeric inputs)
# ---------------------------------------------------------------------------


@app.command(name="chaos-score")
def chaos_score_cmd(
    volatility: Annotated[
        float,
        typer.Option(
            "--volatility",
            help="Realized or implied volatility (soft-thresholded; higher -> more chaos).",
        ),
    ] = 0.0,
    stablecoin_deviation: Annotated[
        float,
        typer.Option(
            "--stablecoin-deviation",
            help="Absolute peg deviation from $1.00 (e.g. 0.02 = 2 cents).",
        ),
    ] = 0.0,
    orderbook_imbalance: Annotated[
        float,
        typer.Option(
            "--orderbook-imbalance",
            help="Order book imbalance in [-1, 1] (abs used; higher -> more chaos).",
        ),
    ] = 0.0,
    sequencer_delay: Annotated[
        float,
        typer.Option(
            "--sequencer-delay",
            help="Sequencer / exchange latency in seconds.",
        ),
    ] = 0.0,
) -> None:
    """Compute a normalized [0, 100] chaos score from pure risk metrics (no lake)."""
    from crypcodile.analytics.risk import calculate_chaos_score

    score = calculate_chaos_score(
        volatility=volatility,
        stablecoin_deviation=stablecoin_deviation,
        orderbook_imbalance=orderbook_imbalance,
        sequencer_delay=sequencer_delay,
    )
    typer.echo(
        f"volatility: {volatility}\n"
        f"stablecoin_deviation: {stablecoin_deviation}\n"
        f"orderbook_imbalance: {orderbook_imbalance}\n"
        f"sequencer_delay: {sequencer_delay}\n"
        f"chaos_score: {score}"
    )


# ---------------------------------------------------------------------------
# lending-stress (Base risk analytics — pure numeric LTV / health-factor)
# ---------------------------------------------------------------------------


@app.command(name="lending-stress")
def lending_stress_cmd(
    collateral_usd: Annotated[
        float,
        typer.Option(
            "--collateral-usd",
            help="Current collateral value in USD.",
        ),
    ],
    debt_usd: Annotated[
        float,
        typer.Option(
            "--debt-usd",
            help="Current debt value in USD.",
        ),
    ],
    liquidation_threshold: Annotated[
        float,
        typer.Option(
            "--liquidation-threshold",
            help="Liquidation threshold as a fraction (e.g. 0.8 for 80%).",
        ),
    ],
    haircut_pct: Annotated[
        float,
        typer.Option(
            "--haircut-pct",
            help="Collateral haircut as fraction or percent (0.20 or 20 for 20%).",
        ),
    ],
) -> None:
    """Stress-test a lending position's health factor under a collateral haircut (no lake/RPC)."""
    from crypcodile.analytics.lending_stress import lending_stress_test

    result = lending_stress_test(
        collateral_usd=collateral_usd,
        debt_usd=debt_usd,
        liquidation_threshold=liquidation_threshold,
        haircut_pct=haircut_pct,
    )

    def _fmt_hf(value: float) -> str:
        if value == float("inf"):
            return "inf"
        return str(value)

    typer.echo(
        f"collateral_usd: {collateral_usd}\n"
        f"debt_usd: {debt_usd}\n"
        f"liquidation_threshold: {liquidation_threshold}\n"
        f"haircut_pct: {haircut_pct}\n"
        f"current_health_factor: {_fmt_hf(float(result['current_health_factor']))}\n"
        f"simulated_health_factor: {_fmt_hf(float(result['simulated_health_factor']))}\n"
        f"is_liquidatable: {result['is_liquidatable']}\n"
        f"simulated_is_liquidatable: {result['simulated_is_liquidatable']}"
    )


# ---------------------------------------------------------------------------
# gas-vol (Base risk analytics — pure DF inputs)
# ---------------------------------------------------------------------------


def _load_series_dataframe(path: Path) -> "pl.DataFrame":
    """Load a CSV or JSON/JSONL series file into a Polars DataFrame."""
    import polars as pl

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pl.read_csv(path)
    if suffix == ".jsonl" or suffix == ".ndjson":
        return pl.read_ndjson(path)
    if suffix == ".json":
        return pl.read_json(path)
    # Fallback: try CSV then JSON
    try:
        return pl.read_csv(path)
    except Exception:
        return pl.read_json(path)


@app.command(name="gas-vol")
def gas_vol_cmd(
    gas_file: Annotated[
        Path | None,
        typer.Option(
            "--gas-file",
            help="CSV/JSON path with local_ts and a gas column (gas_price/gas_cost).",
        ),
    ] = None,
    vol_file: Annotated[
        Path | None,
        typer.Option(
            "--vol-file",
            help="CSV/JSON path with local_ts and a volatility column (volatility/vol).",
        ),
    ] = None,
) -> None:
    """Correlate gas costs with volatility (Pearson & Spearman) from CSV/JSON inputs."""
    import json

    from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation

    if not is_interactive_stdin():
        if gas_file is None or vol_file is None:
            typer.echo(
                "Error: --gas-file and --vol-file are required in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if gas_file is None:
            gas_file = Path(typer.prompt("Path to gas series file (CSV/JSON)"))
        if vol_file is None:
            vol_file = Path(typer.prompt("Path to volatility series file (CSV/JSON)"))

    if gas_file is None or vol_file is None:
        typer.echo("Error: --gas-file and --vol-file are required.", err=True)
        raise typer.Exit(code=1)

    try:
        gas_df = _load_series_dataframe(gas_file)
        vol_df = _load_series_dataframe(vol_file)
    except Exception as e:
        typer.echo(f"Error loading input files: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        result = gas_to_volatility_correlation(gas_df, vol_df)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(json.dumps(result, indent=2, allow_nan=True))


# ---------------------------------------------------------------------------
# smart-money / label-transfers (pure CSV + watchlist; no RPC)
# ---------------------------------------------------------------------------


@app.command(name="smart-money")
def smart_money_cmd(
    transfers: Annotated[
        Path | None,
        typer.Option(
            "--transfers",
            help="CSV/JSON/JSONL of transfers (from, to, usd_value[, timestamp]).",
        ),
    ] = None,
    watchlist: Annotated[
        Path | None,
        typer.Option(
            "--watchlist",
            help="JSON watchlist: addr->label map, list of addresses, or "
            '{"addresses": [...]} / {"watchlist": {...}}.',
        ),
    ] = None,
) -> None:
    """Summarize smart-money capital flows from a transfers file + address watchlist."""
    import polars as pl

    from crypcodile.analytics.smart_money import load_watchlist, summarize_smart_money

    if not is_interactive_stdin():
        if transfers is None or watchlist is None:
            typer.echo(
                "Error: --transfers and --watchlist are required in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if transfers is None:
            transfers = Path(typer.prompt("Path to transfers CSV/JSON"))
        if watchlist is None:
            watchlist = Path(typer.prompt("Path to watchlist JSON"))

    if transfers is None or watchlist is None:
        typer.echo("Error: --transfers and --watchlist are required.", err=True)
        raise typer.Exit(code=1)

    try:
        xfer_df = _load_series_dataframe(transfers)
        labels = load_watchlist(watchlist)
    except Exception as e:
        typer.echo(f"Error loading inputs: {e}", err=True)
        raise typer.Exit(code=1)

    if not labels:
        typer.echo("Error: watchlist is empty.", err=True)
        raise typer.Exit(code=1)

    try:
        rows = summarize_smart_money(xfer_df.to_dicts(), labels)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if not rows:
        typer.echo("No smart-money activity found for watchlist addresses.")
        raise typer.Exit(code=0)

    typer.echo(pl.DataFrame(rows))


@app.command(name="label-transfers")
def label_transfers_cmd(
    transfers: Annotated[
        Path | None,
        typer.Option(
            "--transfers",
            help="CSV/JSON/JSONL of transfers (from, to[, usd_value, ...]).",
        ),
    ] = None,
    watchlist: Annotated[
        Path | None,
        typer.Option(
            "--watchlist",
            help="JSON watchlist: addr->label map or list of addresses.",
        ),
    ] = None,
    min_usd: Annotated[
        float | None,
        typer.Option(
            "--min-usd",
            help="Optional USD threshold filter before labeling (whale filter).",
        ),
    ] = None,
    known_only: Annotated[
        bool,
        typer.Option(
            "--known-only",
            help="Only emit rows where from or to is on the watchlist.",
        ),
    ] = False,
) -> None:
    """Label transfer rows with watchlist names; optionally filter by USD (no RPC)."""
    import polars as pl

    from crypcodile.analytics.smart_money import load_watchlist
    from crypcodile.analytics.whale_transfers import (
        filter_transfers_by_usd,
        label_transfer_addresses,
    )

    if not is_interactive_stdin():
        if transfers is None or watchlist is None:
            typer.echo(
                "Error: --transfers and --watchlist are required in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if transfers is None:
            transfers = Path(typer.prompt("Path to transfers CSV/JSON"))
        if watchlist is None:
            watchlist = Path(typer.prompt("Path to watchlist JSON"))

    if transfers is None or watchlist is None:
        typer.echo("Error: --transfers and --watchlist are required.", err=True)
        raise typer.Exit(code=1)

    try:
        xfer_df = _load_series_dataframe(transfers)
        labels = load_watchlist(watchlist)
    except Exception as e:
        typer.echo(f"Error loading inputs: {e}", err=True)
        raise typer.Exit(code=1)

    rows: list[dict] = xfer_df.to_dicts()
    try:
        if min_usd is not None:
            rows = filter_transfers_by_usd(rows, min_usd)
        rows = label_transfer_addresses(rows, labels)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    if known_only:
        rows = [r for r in rows if r.get("is_known")]

    if not rows:
        typer.echo("No transfers matched filters.")
        raise typer.Exit(code=0)

    typer.echo(pl.DataFrame(rows))


# ---------------------------------------------------------------------------
# mev-sandwich (pure trade-sequence sandwich flags; CSV/JSON offline)
# ---------------------------------------------------------------------------


@app.command(name="mev-sandwich")
def mev_sandwich_cmd(
    trades: Annotated[
        Path | None,
        typer.Option(
            "--trades",
            help=(
                "CSV/JSON/JSONL trade sequence with columns "
                "block, pool, log_index, sender, is_buy."
            ),
        ),
    ] = None,
    sandwiches_only: Annotated[
        bool,
        typer.Option(
            "--sandwiches-only",
            help="Only emit rows flagged as sandwich legs (frontrun/victim/backrun).",
        ),
    ] = False,
) -> None:
    """Flag MEV sandwich patterns in an offline trade sequence (no RPC/lake)."""
    import polars as pl

    from crypcodile.analytics.mev_sandwich import detect_sandwiches

    if not is_interactive_stdin():
        if trades is None:
            typer.echo(
                "Error: --trades is required in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if trades is None:
            trades = Path(typer.prompt("Path to trades CSV/JSON"))

    if trades is None:
        typer.echo("Error: --trades is required.", err=True)
        raise typer.Exit(code=1)

    try:
        df = _load_series_dataframe(trades)
    except Exception as e:
        typer.echo(f"Error loading trades file: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        out = detect_sandwiches(df)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    n_flagged = int(out.filter(pl.col("is_sandwich")).height) if out.height else 0
    if sandwiches_only:
        out = out.filter(pl.col("is_sandwich"))

    if out.height == 0:
        typer.echo(
            "No sandwich legs found." if sandwiches_only else "No trades in input."
        )
        raise typer.Exit(code=0)

    typer.echo(out)
    typer.echo(
        f"sandwich_legs: {n_flagged} / {df.height}",
        err=True,
    )


# ---------------------------------------------------------------------------
# funding-predict (pure offline rates / CSV — rolling mean or XGBoost)
# ---------------------------------------------------------------------------


@app.command(name="funding-predict")
def funding_predict_cmd(
    rates: Annotated[
        str | None,
        typer.Option(
            "--rates",
            help="Comma-separated historical funding rates (e.g. 0.0001,0.0002,0.00015).",
        ),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="CSV/JSON/JSONL with a funding_rate column (optional feature columns).",
        ),
    ] = None,
    window: Annotated[
        int,
        typer.Option(
            "--window",
            help="Rolling window size for the heuristic fallback (default: 5).",
        ),
    ] = 5,
) -> None:
    """Predict next-period funding rate from pure offline history (no lake/RPC).

    Uses XGBoost when installed and trainable; otherwise a rolling-mean fallback.
    Provide either ``--rates`` or ``--file``.
    """
    import json

    from crypcodile.analytics.funding_prediction import predict_next_funding

    if not is_interactive_stdin():
        if rates is None and file is None:
            typer.echo(
                "Error: --rates or --file is required in non-interactive mode.",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        if rates is None and file is None:
            choice = typer.prompt("Input mode: rates or file", default="rates")
            if str(choice).strip().lower().startswith("f"):
                file = Path(typer.prompt("Path to funding history CSV/JSON"))
            else:
                rates = typer.prompt(
                    "Comma-separated funding rates (e.g. 0.0001,0.0002)"
                )

    if rates is None and file is None:
        typer.echo("Error: --rates or --file is required.", err=True)
        raise typer.Exit(code=1)

    if rates is not None and file is not None:
        typer.echo(
            "Error: provide only one of --rates or --file, not both.",
            err=True,
        )
        raise typer.Exit(code=1)

    if window < 1:
        typer.echo("Error: --window must be >= 1.", err=True)
        raise typer.Exit(code=1)

    try:
        if rates is not None:
            parts = [p.strip() for p in rates.split(",") if p.strip()]
            if not parts:
                typer.echo("Error: --rates is empty.", err=True)
                raise typer.Exit(code=1)
            try:
                rate_list = [float(p) for p in parts]
            except ValueError as e:
                typer.echo(f"Error: invalid --rates value: {e}", err=True)
                raise typer.Exit(code=1)
            result = predict_next_funding(rate_list, window_size=window)
        else:
            assert file is not None
            df = _load_series_dataframe(file)
            result = predict_next_funding(df, window_size=window)
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(json.dumps(result, indent=2))


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
        try:
            import uvicorn
        except ImportError:
            typer.echo("Error: uvicorn is required to run the Python FastAPI API server.", err=True)
            raise typer.Exit(code=1)
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
        
        is_newer = False
        try:
            from packaging.version import Version
            is_newer = Version(clean_latest) > Version(clean_current)
        except Exception:
            def parse_version(v: str) -> list[int]:
                return [int(x) for x in re.findall(r"\d+", v)]
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

    use_uv = False
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
        if os.getenv("VIRTUAL_ENV"):
            use_uv = True
    except Exception:
        pass

    if use_uv:
        cmd = [
            "uv",
            "pip",
            "install",
            "--upgrade",
            "git+https://github.com/nazmiefearmutcu/Crypcodile.git",
        ]
    else:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "git+https://github.com/nazmiefearmutcu/Crypcodile.git",
        ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            target_v = latest_version if latest_version else "latest"
            typer.echo(f"✓ Successfully upgraded to {target_v}!", err=True)
        else:
            typer.echo("✗ Failed to upgrade Crypcodile.", err=True)
            if result.stderr:
                typer.echo(f"Details:\n{result.stderr}", err=True)
            raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"✗ Error upgrading Crypcodile: {e}", err=True)
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# bookmap
# ---------------------------------------------------------------------------

from crypcodile.sink.base import Sink

class QueueSink(Sink):
    """A sink that routes normalized records into a multiprocessing/thread-safe queue."""
    def __init__(self, queue: Any) -> None:
        self.queue = queue

    async def put(self, record: Any) -> None:
        self.queue.put(record)

    async def flush(self) -> None:
        pass


class TaskDoneQueueWrapper:
    """Wraps a multiprocessing.Queue to provide a no-op task_done() method."""
    def __init__(self, q: Any) -> None:
        self.q = q

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        self.q.put(item, block, timeout)

    def put_nowait(self, item: Any) -> None:
        self.q.put_nowait(item)

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        return self.q.get(block, timeout)

    def get_nowait(self) -> Any:
        return self.q.get_nowait()

    def empty(self) -> bool:
        return self.q.empty()

    def task_done(self) -> None:
        # multiprocessing.Queue lacks task_done(); this wrapper prevents errors.
        pass


def run_bookmap_gui(queue: Any, historical_events: list[dict]) -> None:
    """Target function for the multiprocessing GUI process."""
    import sys
    try:
        from PyQt6.QtWidgets import QApplication
        from crypcodile.gui.bookmap_window import BookmapWindow
    except ImportError as e:
        sys.stderr.write(f"GUI dependencies not available: {e}\n")
        sys.stderr.flush()
        return

    app = QApplication(sys.argv)
    wrapped = TaskDoneQueueWrapper(queue)
    win = BookmapWindow(queue=wrapped)
    if historical_events:
        win.load_historical_data(historical_events)
    win.show()
    sys.exit(app.exec())


def run_live_feeder(exchange: str, symbol_raw: str, queue: Any) -> None:
    """Target function for the background live feed thread."""
    import asyncio
    from crypcodile.exchanges.factory import make_connector
    from crypcodile.instruments.registry import InstrumentRegistry
    from crypcodile.client.collect import collect as collect_live
    from crypcodile.ingest.transport import AiohttpWsTransport

    registry = InstrumentRegistry()
    sink = QueueSink(queue)
    try:
        connector = make_connector(
            exchange=exchange,
            symbols=[symbol_raw],
            channels=["book_delta", "trade"],
            out=sink,
            registry=registry,
        )
    except Exception as exc:
        import sys
        sys.stderr.write(f"Error creating connector: {exc}\n")
        sys.stderr.flush()
        return

    if connector.transport is None:
        connector.transport = AiohttpWsTransport(connector.ws_url)

    try:
        asyncio.run(collect_live([connector], sink))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        import sys
        sys.stderr.write(f"Feeder thread exception: {e}\n")
        sys.stderr.flush()


@app.command()
def bookmap(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="Canonical symbol, e.g. deribit:BTC-PERPETUAL."),
    ] = None,
    historical_hours: Annotated[
        float,
        typer.Option("--historical-hours", help="Number of historical hours to load."),
    ] = 2.0,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Launch the PyQt6 Bookmap visualizer with historical data and live updates."""
    import time
    import polars as pl
    import multiprocessing
    import threading
    from crypcodile.store.catalog import Catalog

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
    else:
        # Interactive mode
        if not symbol:
            _, selected_symbols = select_symbols_interactively(data_dir, channel="book_snapshot")
            if selected_symbols:
                symbol = selected_symbols[0]

        if not symbol:
            symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="book_snapshot")

    if symbol:
        resolved_syms = resolve_input_symbols(data_dir, [symbol], "book_snapshot")
        if resolved_syms:
            symbol = resolved_syms[0]

    if not symbol:
        typer.echo("Error: Symbol is required.", err=True)
        raise typer.Exit(code=1)

    if ":" not in symbol:
        typer.echo(f"Error: Symbol '{symbol}' is not in canonical format (exchange:symbol).", err=True)
        raise typer.Exit(code=1)

    parts = symbol.split(":", 1)
    exchange = parts[0]
    raw_symbol = parts[1]
    if exchange == "binance-spot":
        exchange = "binance"
    elif exchange == "bybit-spot":
        exchange = "bybit"

    catalog = Catalog(data_dir)

    # Determine end_ns based on max database timestamp or fallback to current time
    end_ns = time.time_ns()
    try:
        max_df = catalog.query(
            "SELECT max(local_ts) as max_t FROM book_snapshot WHERE symbol = ?",
            params=[symbol]
        )
        if len(max_df) > 0 and max_df["max_t"][0] is not None:
            end_ns = int(max_df["max_t"][0])
    except Exception:
        try:
            max_df = catalog.query(
                "SELECT max(local_ts) as max_t FROM trade WHERE symbol = ?",
                params=[symbol]
            )
            if len(max_df) > 0 and max_df["max_t"][0] is not None:
                end_ns = int(max_df["max_t"][0])
        except Exception:
            pass

    start_ns = end_ns - int(historical_hours * 3600 * 1_000_000_000)

    typer.echo(f"Querying historical data for {symbol}...")
    try:
        snap_df = catalog.scan("book_snapshot", symbol, start_ns, end_ns)
    except Exception:
        snap_df = pl.DataFrame()

    try:
        delta_df = catalog.scan("book_delta", symbol, start_ns, end_ns)
    except Exception:
        delta_df = pl.DataFrame()

    try:
        trade_df = catalog.scan("trade", symbol, start_ns, end_ns)
    except Exception:
        trade_df = pl.DataFrame()

    # Convert and normalize historical data
    events = []

    def df_to_list(df, channel_name):
        if df.is_empty():
            return []
        rows = df.to_dicts()
        for r in rows:
            r["channel"] = channel_name
        return rows

    events.extend(df_to_list(snap_df, "book_snapshot"))
    events.extend(df_to_list(delta_df, "book_delta"))
    events.extend(df_to_list(trade_df, "trade"))

    for r in events:
        if r.get("channel") in ("book_snapshot", "book_delta"):
            for side in ("bids", "asks"):
                original = r.get(side)
                normalized = []
                if original:
                    for item in original:
                        if isinstance(item, dict):
                            price = item.get("price")
                            amount = item.get("amount") if item.get("amount") is not None else item.get("size")
                            if price is not None and amount is not None:
                                normalized.append((float(price), float(amount)))
                        elif isinstance(item, (list, tuple)):
                            normalized.append((float(item[0]), float(item[1])))
                r[side] = normalized

    events.sort(key=lambda x: x.get("local_ts") or 0)

    # Initialize multiprocessing Queue and start GUI process
    queue = multiprocessing.Queue()
    gui_process = multiprocessing.Process(
        target=run_bookmap_gui,
        args=(queue, events),
        daemon=True
    )
    gui_process.start()

    # Start live feed connector thread
    feeder_thread = threading.Thread(
        target=run_live_feeder,
        args=(exchange, raw_symbol, queue),
        daemon=True
    )
    feeder_thread.start()

    typer.echo(f"Launched bookmap visualizer process and subscription thread for {symbol}.")


# ---------------------------------------------------------------------------
# gas-tracker
# ---------------------------------------------------------------------------

@app.command(name="gas-tracker")
def gas_tracker() -> None:
    """Launch the PyQt6 real-time Gas Tracker widget."""
    import sys
    try:
        from PyQt6.QtWidgets import QApplication, QMainWindow
        from crypcodile.gui.widgets.gas_tracker import GasTrackerWidget
    except ImportError as e:
        typer.echo(f"GUI dependencies not available: {e}", err=True)
        raise typer.Exit(code=1)

    app_qt = QApplication.instance()
    if app_qt is None:
        app_qt = QApplication(sys.argv)
    
    win = QMainWindow()
    win.setWindowTitle("Crypcodile Gas Tracker")
    widget = GasTrackerWidget()
    win.setCentralWidget(widget)
    win.resize(800, 400)
    win.show()
    
    if "pytest" not in sys.modules:
        sys.exit(app_qt.exec())


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
    is_interactive = is_interactive_stdin()
    session = None
    if is_interactive and not is_pytest:
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
    if is_interactive and not is_pytest:
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
                if not is_interactive or is_pytest:
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
                except click.exceptions.Exit:
                    pass
                except SystemExit:
                    pass
                except Exception as e:
                    typer.echo(f"Error executing command: {e}", err=True)
            except (KeyboardInterrupt, EOFError):
                typer.echo("\nGoodbye!")
                break
    finally:
        if is_interactive and not is_pytest and original_handler:
            try:
                signal.signal(signal.SIGWINCH, original_handler)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# indicators
# ---------------------------------------------------------------------------
@app.command()
def indicators(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", help="Canonical symbol, e.g. deribit:BTC-PERPETUAL."),
    ] = None,
    indicator: Annotated[
        str | None,
        typer.Option(
            "--indicator",
            help="Indicator to calculate (sma, ema, rsi, macd, bb, or all).",
        ),
    ] = None,
    period: Annotated[
        int,
        typer.Option(
            "--period",
            help="Smoothing/lookback window size (used for SMA, EMA, RSI, BB).",
        ),
    ] = 14,
    frm: Annotated[
        int | None,
        typer.Option("--from", help="Start of time range (nanoseconds UTC)."),
    ] = None,
    to: Annotated[
        int | None,
        typer.Option("--to", help="End of time range (nanoseconds UTC)."),
    ] = None,
    interval: Annotated[
        str,
        typer.Option("--interval", help="Resampling interval (e.g. 1m, 1h, 1d)."),
    ] = "1d",
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Calculate technical analysis indicators (SMA, EMA, RSI, MACD, BB) using Polars."""
    from crypcodile.client.client import CrypcodileClient

    data_dir = resolve_data_dir(data_dir)

    if not is_interactive_stdin():
        if not symbol:
            typer.echo("Error: symbol is required in non-interactive mode.", err=True)
            raise typer.Exit(code=1)
        if frm is None:
            frm = 0
        if to is None:
            to = 9999999999999999999
    else:
        # Interactive
        if not symbol:
            symbol = prompt_symbol("Symbol (e.g. BTC)", data_dir, channel="trade")
        if frm is None or to is None:
            resolved_start, resolved_end = prompt_time_range_helper(
                data_dir,
                "trade",
                [symbol],
                default_start=0,
                default_end=9999999999999999999,
            )
            if frm is None:
                frm = resolved_start
            if to is None:
                to = resolved_end

    client = CrypcodileClient(data_dir=data_dir)
    try:
        res = client.get_indicators(
            symbol,  # type: ignore[arg-type]
            frm,  # type: ignore[arg-type]
            to,  # type: ignore[arg-type]
            interval=interval,
            indicator=indicator,
            period=period,
        )
        if len(res) == 0:
            typer.echo("No data found for the given symbol and time range.")
            return
        typer.echo(res)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e


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


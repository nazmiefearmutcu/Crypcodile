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
from typing import Annotated

import typer

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

    if not sql:
        sql = typer.prompt("Enter DuckDB SQL query")
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

    if not channel:
        channel = typer.prompt("Enter channel name (e.g. trade)")
    if not symbols:
        sym_input = typer.prompt("Enter canonical symbol(s) (comma-separated, e.g. deribit:BTC-PERPETUAL)")
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if frm is None:
        frm = typer.prompt("Enter start of time range (nanoseconds UTC)", type=int, default=0)
    if to is None:
        to = typer.prompt("Enter end of time range (nanoseconds UTC)", type=int, default=9999999999999999999)

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

    if not channels:
        ch_input = typer.prompt("Enter channel name(s) (comma-separated, e.g. trade)")
        channels = [c.strip() for c in ch_input.split(",") if c.strip()]
    if not symbols:
        sym_input = typer.prompt("Enter canonical symbol(s) (comma-separated, e.g. deribit:BTC-PERPETUAL)")
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if frm is None:
        frm = typer.prompt("Enter start of time range (nanoseconds UTC)", type=int, default=0)
    if to is None:
        to = typer.prompt("Enter end of time range (nanoseconds UTC)", type=int, default=9999999999999999999)

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
    if not exchange:
        exchange = typer.prompt("Enter exchange name (e.g. deribit)")
    if not symbols:
        sym_input = typer.prompt("Enter symbol(s) to collect (comma-separated, e.g. BTC-PERPETUAL)")
        symbols = [s.strip() for s in sym_input.split(",") if s.strip()]
    if not channels:
        ch_input = typer.prompt("Enter channel(s) to subscribe (comma-separated, e.g. trade)")
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

    try:
        asyncio.run(collect_live([connector], sink))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass  # sink.close() is already called inside collect_live's finally block

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

    if not symbol:
        symbol = typer.prompt("Enter canonical symbol (e.g. deribit:BTC-PERPETUAL)")
    if start is None:
        start = typer.prompt("Enter start of time range (nanoseconds UTC)", type=int, default=0)
    if end is None:
        end = typer.prompt("Enter end of time range (nanoseconds UTC)", type=int, default=9999999999999999999)

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

    if start is None:
        start = typer.prompt("Enter start of time range (nanoseconds UTC)", type=int, default=0)
    if end is None:
        end = typer.prompt("Enter end of time range (nanoseconds UTC)", type=int, default=9999999999999999999)

    # If neither perp nor future/spot is specified, ask user what mode they want
    if perp is None and (future is None or spot is None):
        mode = typer.prompt("Select basis mode (perp or spot-future)", default="perp")
        if mode == "perp":
            perp = typer.prompt("Enter canonical perpetual symbol (e.g. deribit:BTC-PERPETUAL)")
        else:
            future = typer.prompt("Enter canonical futures symbol (e.g. deribit:BTC-FUTURE)")
            spot = typer.prompt("Enter canonical spot symbol (e.g. binance-spot:BTCUSDT)")

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

    if not underlying:
        underlying = typer.prompt("Enter underlying asset identifier (e.g. BTC)")
    if at is None:
        at = typer.prompt("Enter snapshot instant (nanoseconds UTC)", type=int)

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

    if not underlying:
        underlying = typer.prompt("Enter underlying asset identifier (e.g. BTC)")
    if at is None:
        at = typer.prompt("Enter snapshot instant (nanoseconds UTC)", type=int)

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

LOGO_ART = r"""                                                                                           ░  ▒▒░▒▓░ ▒    
                                                                                        ░▒▒▒▒▒▒░░░░░░▒▒   
                             ░░░░  ░▒▒▒▒▒▒                                            ░▒▒░░░░░░░░░░░░▒▒▒  
  ▒░▒░░░░▒                 ░▒▒░░░▒▒░░░▒▒░░▒░               ░▒    ░                  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒░░▓░▒ 
 ▒▒░░░▒▓░░▒░           ░░░▒▒▓▒░░░░░▒░▒░▓▓░░▓▒▒▒░     ░▓▒ ░▓▓▓▓░▒▓▓▓ ░▒▓░                         ▒▒▒▒░▓░▒░
▒░░░░░▒░░░░░░▒▒▒░▒▒▒▒░▒▒░░░░░░░░░░░▒░▓▓▓▓░░░░░░░▒▒░░▒▓▒▒▒░░░░░░░░░▒▒▒▒▒▒▒▓▓                      ▓▓▒▒▒░░▓
▒▒▒▒▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▒░▓▓▓▒░░░░░▒▒░░▒░░░░▒▓▓▒░▒▓▓▓░▒▓▓░░▒▓░░▒▒▒▒▓▓                ░░▓▒▒▒░░░▓
 ░░░▒▒▓░░▒▒░░░░░▒▒▒▒▒▒▒░░░░░░░░░░░░░▒░░░░░░░░░▒░░░░▒░░▒▒▒▒▒░░▒░░░▒▒░░░▒▒▒░▒▓▓▒▒▒▒▒▓▒        ░ ▒▓▓▒▒▒▒░░░░▓
  ░   ░ ░░▒▒▒▒▓▒▒▒░░▓▓▓▓▒▒▒░░░░░░░░░▒▓▒░░░░░░░░░▒░░░░░░▒▒▓▓▓░░▒▒▓▓▓░▒▒▓▓▒░░▒▒░░▒▓▓░▒▒▓▓▒▓▓▒▓▓▒▒▒▒░▒░░░░░░▓
       ░░   ░░       ░▓▓░▓▓▓▒▒▒▒▒▓▓▓▓▓░       ░░░░░░▒░░▒░░░▒░░░░░░░░▒░░░░░▒▒▓░░▒▓▒░▓▓▒░░░░▒░▒▓▒▒▒░░░░░▒░▒░
                      ▓▓▓▓▓▓▒▒▓▓▓▓▓▓▓▒        ░░░░░░░░░░▒░░▒░░░▒░░░░░▒░░░▒░░░░▒░░░░▒▒░▒▒░▒▒▒░░▒░░░░░░▒░░▓ 
                    ░▓▓▓▓▓▓▓▓▒▒▒▓▓▓▓░         ▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▒▒▒▒▒▒░░░░░░░░░░▒░ ░▓  
      ░░   ░░ ░░▒▓▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒░          ░▒░░░░░░░░░░░░░░░░▒░░░░░░░░░░░░░░░░░▒▒░░░░░░▒▒░░░░░▒░ ░▒▓   
      ▒░▒▒░░ ▒▓▓▒▒▒▒░▒▒▒▒░▒▒▒▒░░            ░▒░░░░░░░░░▒░░░░░░░░▒▒▒░░░░░░░░░░░░░▓░░░▒░░░░░░▓▒▒▒▒░ ░░▒▒    
     ▒░░░▒▒░░░░░▒░▒░░░                     ░▒▒▒▒▒░░░░░░▒░░░░░░░░░░░▒▒░░░░░░░░░░░▓░░░▒░░░░░▒▒▓░ ░ ░▒▒      
      ░▒▒░░░░                          ░░    ░   ░░░░▒▒▒▒░░░░░░▒░▒▒░░▒▒▒░░░▒░░▒▒▓▒░░░▒░░░▒▒▒▓░░▒▒▒        
           ░░░░░░░░▒▒▒▒▒▒▒▒░░░░░░░░░░░░░  ░░░   ░   ░  ▒▒▒░▒░░░░░░▒▒░░▓▒▒▒▒▒▒░▒░░▓░░░░░░░░▒▓▓▒▒           
                             ░░░▒▒▒▒▒▒░░░░░  ░░░░ ░░  ░░░▒▒▒░░░░░░░░░░░▓░ ░   ░  ▒▓▒░░░░░░░▒▒             
                                   ░▒▒▒▓▓▒▒▒▒░░ ░░░  ░░   ░▒▓▒▒░░░░░░░▒▒▒░░  ░░▒▒▒▒▓▒▒▒▒▒░░░▒▒            
                               ░▒▒▓▒░▒▒▒░░░▒▒▓▒▒▒▒▒▒▒▒▒░░▒▒▒░░░▒▒▒░░░░▒▓▓▒▒▒▒░░  ░▒▒░▒░░░░▒░░░▒           
                             ░▒▒▓▒▒▒▒▒▒▒▒▒▒░         ░▒▒▒▒▒▒░░░░░░░░░▒▒         ░▒░░▒▒▒░▒▒▒░░░▒▒          
                               ░░░ ▒▒░             ░▓▒▒▒▒░░░░░░░░▒▒▒▒░              ░░    ░               
                                                      ▒▒▒▒▒▒░▒▒░░░                                        
                                                      ░   ░▒░     
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

    # Print the logo always to stderr, unless running the mcp command or tests
    if "mcp" not in sys.argv and "pytest" not in sys.modules:
        if sys.stderr.isatty():
            sys.stderr.write(LOGO + "\n")
            sys.stderr.flush()

    if len(sys.argv) == 1:
        sys.argv.append("shell")

    app()


if __name__ == "__main__":
    main()


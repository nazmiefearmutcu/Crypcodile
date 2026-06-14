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
    sql: Annotated[str, typer.Argument(help="DuckDB SQL query to execute.")],
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Execute a DuckDB SQL query against the data lake and print the result."""
    from crypcodile.client.client import CrypcodileClient

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
    channel: Annotated[str, typer.Option("--channel", help="Channel name, e.g. trade.")],
    symbols: Annotated[
        list[str],
        typer.Option("--symbols", help="Canonical symbol(s). Repeat for multiple."),
    ],
    frm: Annotated[
        int,
        typer.Option("--from", help="Start of time range (nanoseconds UTC)."),
    ],
    to: Annotated[
        int,
        typer.Option("--to", help="End of time range (nanoseconds UTC)."),
    ],
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

    client = CrypcodileClient(data_dir=data_dir)
    client.export(channel, symbols, frm, to, fmt=fmt, dest=dest)  # type: ignore[arg-type]
    typer.echo(f"Exported to: {dest}")


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@app.command()
def replay(
    channels: Annotated[
        list[str],
        typer.Option("--channels", help="Channel name(s). Repeat for multiple."),
    ],
    symbols: Annotated[
        list[str],
        typer.Option("--symbols", help="Canonical symbol(s). Repeat for multiple."),
    ],
    frm: Annotated[
        int,
        typer.Option("--from", help="Start of time range (nanoseconds UTC)."),
    ],
    to: Annotated[
        int,
        typer.Option("--to", help="End of time range (nanoseconds UTC)."),
    ],
    data_dir: _DataDirOpt = Path("data"),
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Maximum number of records to print."),
    ] = None,
) -> None:
    """Stream canonical Records from the data lake, printed to stdout."""
    from crypcodile.client.client import CrypcodileClient

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
    exchange: Annotated[str, typer.Option("--exchange", help="Exchange name, e.g. deribit.")],
    symbols: Annotated[
        list[str],
        typer.Option("--symbols", help="Symbol(s) to collect. Repeat for multiple."),
    ],
    channels: Annotated[
        list[str],
        typer.Option("--channels", help="Channel(s) to subscribe. Repeat for multiple."),
    ],
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Collect live market data from an exchange and write to the Parquet data lake.

    Press Ctrl-C (SIGINT) to stop gracefully — the sink is flushed before exit.

    Valid exchange names: binance, bybit, coinbase, deribit, okx.

    Example::

        crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \\
                          --channels trade --channels book_delta --data-dir data
    """
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
        str,
        typer.Option("--symbol", help="Canonical symbol, e.g. deribit:BTC-PERPETUAL."),
    ],
    start: Annotated[
        int,
        typer.Option("--start", help="Start of time range (nanoseconds UTC)."),
    ],
    end: Annotated[
        int,
        typer.Option("--end", help="End of time range (nanoseconds UTC)."),
    ],
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print per-event funding APR and cumulative funding for a perpetual symbol."""
    from crypcodile.client.client import CrypcodileClient

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
        int,
        typer.Option("--start", help="Start of time range (nanoseconds UTC)."),
    ],
    end: Annotated[
        int,
        typer.Option("--end", help="End of time range (nanoseconds UTC)."),
    ],
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
        str,
        typer.Option("--underlying", help="Underlying asset identifier, e.g. BTC."),
    ],
    at: Annotated[
        int,
        typer.Option("--at", help="Snapshot instant (nanoseconds UTC)."),
    ],
    rate: Annotated[
        float,
        typer.Option("--rate", help="Continuous risk-free rate (default 0.0)."),
    ] = 0.0,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print the implied-vol surface snapshot at a given instant."""
    from crypcodile.client.client import CrypcodileClient

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
        str,
        typer.Option("--underlying", help="Underlying asset identifier, e.g. BTC."),
    ],
    at: Annotated[
        int,
        typer.Option("--at", help="Snapshot instant (nanoseconds UTC)."),
    ],
    rate: Annotated[
        float,
        typer.Option("--rate", help="Continuous risk-free rate (default 0.0)."),
    ] = 0.0,
    data_dir: _DataDirOpt = Path("data"),
) -> None:
    """Print the ATM IV term structure at a given instant."""
    from crypcodile.client.client import CrypcodileClient

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
# Entry-point
# ---------------------------------------------------------------------------



def main() -> None:
    """Entry-point called by the ``crypcodile`` script."""
    app()


if __name__ == "__main__":
    main()


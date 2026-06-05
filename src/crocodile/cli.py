"""Typer CLI for Crocodile (Task 3.5 / 6.5).

Commands
--------
query          -- Execute DuckDB SQL against the data lake; print result table.
catalog        -- List all channels present in the data lake with row counts.
export         -- Export a channel x symbols x time range to a file.
replay         -- Stream canonical Records from the data lake, printed to stdout.
collect        -- (stub) Run live connectors -- requires connector configuration.
funding-apr    -- Print per-event funding APR for a perpetual symbol.
basis          -- Print spot-future or perpetual basis.
iv-surface     -- Print the implied-vol surface snapshot.
term-structure -- Print the ATM IV term structure.

Usage examples::

    crocodile query "SELECT count(*) FROM trade" --data-dir /data
    crocodile catalog --data-dir /data
    crocodile export --channel trade --symbols BTC-PERPETUAL --from 0 --to 9e18 \\
                     --fmt csv --dest out/trades.csv --data-dir /data
    crocodile replay --channels trade --symbols deribit:BTC-PERPETUAL \\
                     --from 0 --to 9e18 --data-dir /data
    crocodile funding-apr --symbol deribit:BTC-PERPETUAL \\
                          --start 0 --end 9999999999999999999 --data-dir /data
    crocodile basis --future deribit:BTC-FUTURE --spot binance-spot:BTCUSDT \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crocodile basis --perp deribit:BTC-PERPETUAL \\
                    --start 0 --end 9999999999999999999 --data-dir /data
    crocodile iv-surface --underlying BTC --at 1704067200000000000 --data-dir /data
    crocodile term-structure --underlying BTC --at 1704067200000000000 --data-dir /data
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="crocodile",
    help="Crocodile -- open-source crypto market-data engine.",
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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)
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
    from crocodile.client.client import CrocodileClient
    from crocodile.store.catalog import Catalog

    cat: Catalog = CrocodileClient(data_dir=data_dir)._catalog

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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)
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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)
    count = 0
    for record in client.replay(channels, symbols, frm, to):
        typer.echo(repr(record))
        count += 1
        if limit is not None and count >= limit:
            break
    typer.echo(f"-- {count} record(s) replayed.")


# ---------------------------------------------------------------------------
# collect  (stub -- full wiring is M4)
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
    """Collect live market data from an exchange (requires connector configuration).

    This command is a stub in M3; full connector wiring lands in M4.
    """
    typer.echo(
        f"collect: exchange={exchange!r} symbols={symbols} channels={channels} "
        f"data_dir={data_dir}"
    )
    typer.echo("Live collection not yet implemented (M4). Exiting.")
    raise typer.Exit(code=0)


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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)
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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)

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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)
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
    from crocodile.client.client import CrocodileClient

    client = CrocodileClient(data_dir=data_dir)
    df = client.term_structure(underlying, at, rate=rate)
    if len(df) == 0:
        typer.echo("No options data found.")
        raise typer.Exit(code=0)
    typer.echo(df)


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry-point called by the ``crocodile`` script."""
    app()


if __name__ == "__main__":
    main()

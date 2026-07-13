from __future__ import annotations

import polars as pl
from crypcodile.store.catalog import Catalog


def aggregate_open_interest(
    catalog: Catalog,
    symbols: str | list[str] | None = None,
    start_ns: int = 0,
    end_ns: int = 0,
) -> pl.DataFrame:
    """Aggregate open interest across different exchanges and symbols.

    Queries the `open_interest` view, filters by symbols (substring match),
    and aligns the open interest data across exchanges with forward-filling.

    OI is tracked per (exchange, symbol) so multiple symbols on the same
    exchange at the same timestamp do not overwrite each other. Exchange
    columns are the sum of that exchange's symbols; ``total_oi`` is the
    sum across all (exchange, symbol) series.
    """
    try:
        catalog.refresh_views()
        raw_df = catalog.query(
            "SELECT * FROM \"open_interest\" "
            f"WHERE local_ts >= {start_ns} AND local_ts <= {end_ns}"
        )
    except Exception:
        return pl.DataFrame()

    if raw_df is None or len(raw_df) == 0:
        return pl.DataFrame()

    # Filter matching symbols
    if symbols is None:
        symbols_list = []
    elif isinstance(symbols, str):
        symbols_list = [symbols]
    else:
        try:
            symbols_list = list(symbols)
        except TypeError:
            symbols_list = []

    if symbols_list:
        filters = [
            pl.col("symbol").str.to_lowercase().str.contains(s.lower())
            for s in symbols_list
        ]
        filter_expr = filters[0]
        for f in filters[1:]:
            filter_expr = filter_expr | f
        raw_df = raw_df.filter(filter_expr)

    if len(raw_df) == 0:
        return pl.DataFrame()

    # Align across unique local_ts with forward-fill per (exchange, symbol)
    timestamps = sorted(raw_df["local_ts"].unique().to_list())
    exchanges = sorted(raw_df["exchange"].unique().to_list())
    series_keys = sorted(
        {
            (row["exchange"], row["symbol"])
            for row in raw_df.select(["exchange", "symbol"]).unique().iter_rows(named=True)
        }
    )

    # Map: ts -> (exchange, symbol) -> open_interest
    data_map: dict[int, dict[tuple[str, str], float]] = {}
    for row in raw_df.iter_rows(named=True):
        ts = row["local_ts"]
        key = (row["exchange"], row["symbol"])
        oi = float(row["open_interest"]) if row["open_interest"] is not None else 0.0
        if ts not in data_map:
            data_map[ts] = {}
        data_map[ts][key] = oi

    last_seen: dict[tuple[str, str], float] = {key: 0.0 for key in series_keys}
    records = []

    for ts in timestamps:
        current_data = data_map.get(ts, {})
        for key in series_keys:
            if key in current_data:
                last_seen[key] = current_data[key]

        # Sum symbols per exchange, then total across exchanges
        record: dict[str, float | int] = {"local_ts": ts}
        for exchange in exchanges:
            record[exchange] = sum(
                last_seen[key] for key in series_keys if key[0] == exchange
            )
        record["total_oi"] = sum(last_seen.values())
        records.append(record)

    return pl.DataFrame(records)

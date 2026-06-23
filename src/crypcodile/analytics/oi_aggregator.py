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

    # Align across unique local_ts with forward-fill for each exchange
    timestamps = sorted(raw_df["local_ts"].unique().to_list())
    exchanges = sorted(raw_df["exchange"].unique().to_list())

    # Map for quick lookup of raw values: ts -> exchange -> open_interest
    data_map: dict[int, dict[str, float]] = {}
    for row in raw_df.iter_rows(named=True):
        ts = row["local_ts"]
        exchange = row["exchange"]
        oi = float(row["open_interest"]) if row["open_interest"] is not None else 0.0
        if ts not in data_map:
            data_map[ts] = {}
        data_map[ts][exchange] = oi

    last_seen = {exchange: 0.0 for exchange in exchanges}
    records = []

    for ts in timestamps:
        current_data = data_map.get(ts, {})
        # Update last_seen with any new values
        for exchange in exchanges:
            if exchange in current_data:
                last_seen[exchange] = current_data[exchange]

        # Record state
        record = {"local_ts": ts}
        for exchange in exchanges:
            record[exchange] = last_seen[exchange]
        record["total_oi"] = sum(last_seen.values())
        records.append(record)

    return pl.DataFrame(records)

"""Basis analytics — spot-future basis and perpetual basis (Task 6.3).

Formulas
--------
``spot_future_basis``
    Uses an ASOF JOIN (DuckDB ``ASOF JOIN ... ON future.local_ts >=
    spot.local_ts``) to pair each future trade with the nearest **prior**
    spot trade.

    - ``basis     = future_price - spot_price``  (signed; positive means
      futures trade at a premium to spot, i.e. contango)
    - ``basis_pct = (future_price - spot_price) / spot_price``
    - ``annualized_pct = basis_pct * 365 / days_to_expiry``  where
      ``days_to_expiry = (expiry_ns - local_ts) / 86_400e9``
      (only present when ``expiry_ns`` is given).

``perp_basis``
    Reads the ``derivative_ticker`` channel for a perpetual contract.
    Rows where either ``mark_price`` or ``index_price`` is NULL are
    silently dropped.

    - ``basis     = mark_price - index_price``
    - ``basis_pct = (mark_price - index_price) / index_price``

Empty-result contract
---------------------
Both functions return ``pl.DataFrame()`` (zero rows, zero columns) when
the requested data does not exist — consistent with the ``resample_ohlcv``
and ``funding_apr`` contracts.
"""

from __future__ import annotations

import duckdb
import polars as pl

from crocodile.store.catalog import Catalog

__all__ = [
    "perp_basis",
    "spot_future_basis",
]


# ---------------------------------------------------------------------------
# spot_future_basis
# ---------------------------------------------------------------------------


def spot_future_basis(
    catalog: Catalog,
    future_symbol: str,
    spot_symbol: str,
    start_ns: int,
    end_ns: int,
    expiry_ns: int | None = None,
) -> pl.DataFrame:
    """Return basis rows for a spot-future pair using an ASOF JOIN.

    Each future trade is paired with the nearest prior spot trade (the most
    recent spot trade whose ``local_ts <= future.local_ts``).

    Args:
        catalog:        A :class:`~crocodile.store.catalog.Catalog` instance.
        future_symbol:  Canonical symbol for the futures leg (e.g.
                        ``"deribit:BTC-FUTURE"``).
        spot_symbol:    Canonical symbol for the spot leg (e.g.
                        ``"deribit:BTC-SPOT"``).
        start_ns:       Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:         Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        expiry_ns:      Optional nanosecond epoch of contract expiry.  When
                        provided, an ``annualized_pct`` column is added:
                        ``basis_pct * 365 / days_to_expiry``.

    Returns:
        A Polars DataFrame ordered by ``local_ts`` ascending with columns:

        ===============  =========  ==========================================
        local_ts         Int64      Future trade timestamp (nanoseconds UTC).
        future_price     Float64    Future trade price.
        spot_price       Float64    Nearest prior spot price (ASOF).
        basis            Float64    ``future_price - spot_price``.
        basis_pct        Float64    ``basis / spot_price``.
        annualized_pct   Float64    (only when ``expiry_ns`` given) Annualised
                                   basis = ``basis_pct * 365 / days_to_expiry``.
        ===============  =========  ==========================================

        Returns ``pl.DataFrame()`` when either leg has no data.
    """
    # Scan both legs — use the Catalog's partition-pruned scanner.
    future_df = catalog.scan("trade", future_symbol, start_ns, end_ns)
    spot_df = catalog.scan("trade", spot_symbol, start_ns, end_ns)

    if len(future_df) == 0 or len(spot_df) == 0:
        return pl.DataFrame()

    # Select only the columns we need to keep memory usage minimal.
    future_df = future_df.select(["local_ts", "price"]).rename({"price": "future_price"})
    spot_df = spot_df.select(["local_ts", "price"]).rename({"price": "spot_price"})

    # Run the ASOF JOIN in DuckDB.
    # We register the DataFrames as DuckDB relation objects so we don't need
    # to write them to disk or use the Catalog's global connection.
    conn = catalog._conn

    try:
        conn.register("_basis_future", future_df)
        conn.register("_basis_spot", spot_df)

        sql = """
            SELECT
                f.local_ts                          AS local_ts,
                f.future_price                      AS future_price,
                s.spot_price                        AS spot_price,
                f.future_price - s.spot_price       AS basis,
                (f.future_price - s.spot_price)
                    / s.spot_price                  AS basis_pct
            FROM _basis_future f
            ASOF JOIN _basis_spot s
                ON f.local_ts >= s.local_ts
            ORDER BY f.local_ts
        """
        result = conn.execute(sql)
        df: pl.DataFrame = result.pl()
    except (duckdb.CatalogException, duckdb.IOException, duckdb.BinderException):
        return pl.DataFrame()
    finally:
        try:
            conn.unregister("_basis_future")
        except Exception:
            pass
        try:
            conn.unregister("_basis_spot")
        except Exception:
            pass

    if len(df) == 0:
        return pl.DataFrame()

    # Optionally append annualized_pct.
    if expiry_ns is not None:
        ann_values: list[float] = []
        for row in df.iter_rows(named=True):
            days_to_expiry = (expiry_ns - row["local_ts"]) / 86_400e9
            if days_to_expiry > 0.0:
                ann_values.append(row["basis_pct"] * 365.0 / days_to_expiry)
            else:
                # Expired or same-timestamp: cannot annualise; use basis_pct as-is.
                ann_values.append(row["basis_pct"])
        df = df.with_columns(
            pl.Series("annualized_pct", ann_values, dtype=pl.Float64)
        )

    return df


# ---------------------------------------------------------------------------
# perp_basis
# ---------------------------------------------------------------------------


def perp_basis(
    catalog: Catalog,
    perp_symbol: str,
    start_ns: int,
    end_ns: int,
) -> pl.DataFrame:
    """Return basis rows for a perpetual contract (mark vs index).

    Reads the ``derivative_ticker`` channel and computes the funding basis
    between the mark price and the underlying index price.  Rows where
    either ``mark_price`` or ``index_price`` is NULL are silently dropped.

    Args:
        catalog:      A :class:`~crocodile.store.catalog.Catalog` instance.
        perp_symbol:  Canonical perpetual contract symbol (e.g.
                      ``"deribit:BTC-PERPETUAL"``).
        start_ns:     Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:       Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

    Returns:
        A Polars DataFrame ordered by ``local_ts`` ascending with columns:

        ===========  =========  ==========================================
        local_ts     Int64      Ticker timestamp (nanoseconds UTC).
        mark_price   Float64    Mark price.
        index_price  Float64    Index (spot reference) price.
        basis        Float64    ``mark_price - index_price``.
        basis_pct    Float64    ``basis / index_price``.
        ===========  =========  ==========================================

        Returns ``pl.DataFrame()`` when no data exists.
    """
    raw = catalog.scan("derivative_ticker", perp_symbol, start_ns, end_ns)
    if len(raw) == 0:
        return pl.DataFrame()

    # Select relevant columns; guard against optional-column absence.
    if "mark_price" not in raw.columns or "index_price" not in raw.columns:
        return pl.DataFrame()

    work = raw.select(["local_ts", "mark_price", "index_price"])

    # Drop rows where either price is null.
    work = work.filter(
        pl.col("mark_price").is_not_null() & pl.col("index_price").is_not_null()
    )

    if len(work) == 0:
        return pl.DataFrame()

    # Compute basis columns.
    work = work.with_columns(
        [
            (pl.col("mark_price") - pl.col("index_price")).alias("basis"),
            (
                (pl.col("mark_price") - pl.col("index_price")) / pl.col("index_price")
            ).alias("basis_pct"),
        ]
    ).sort("local_ts")

    # Ensure correct dtypes.
    work = work.with_columns(
        [
            pl.col("local_ts").cast(pl.Int64),
            pl.col("mark_price").cast(pl.Float64),
            pl.col("index_price").cast(pl.Float64),
            pl.col("basis").cast(pl.Float64),
            pl.col("basis_pct").cast(pl.Float64),
        ]
    )

    return work

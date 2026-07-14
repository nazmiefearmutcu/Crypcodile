"""CrypcodileClient - high-level API wrapping the DuckDB Catalog (Task 3.1-3.3).

``CrypcodileClient(data_dir)`` is the primary entry-point for users who want to
query and scan the Parquet data lake without interacting with the lower-level
``Catalog`` directly.

Methods
-------
query(sql)
    Execute arbitrary DuckDB SQL against the registered channel views.
    Returns a Polars DataFrame.

scan(channel, symbols, start_ns, end_ns)
    Return rows for one or more canonical symbols within a nanosecond time
    range, ordered by ``local_ts`` ascending.  When multiple symbols are
    provided the per-symbol DataFrames are concatenated (diagonal) and
    sorted globally.  Returns an empty DataFrame (0 rows, 0 columns) when no
    matching rows exist.

replay(channels, symbols, frm, to)
    Iterate over canonical Records across one or more channels and symbols
    within a nanosecond time range, globally sorted by ``local_ts``.  Uses
    the M2 k-way merge engine to combine per-(channel, symbol) streams.

export(channel, symbols, frm, to, fmt, dest)
    Write rows for the given channel x symbols x time range to a file in the
    specified format.  Supported formats: ``parquet``, ``csv``, ``arrow``,
    ``json``, ``jsonl``.  Parent directories are created automatically.

Analytics methods (Task 6.5)
----------------------------
funding_apr(symbol, start_ns, end_ns)
    Per-event annualised funding rate + cumulative funding.

spot_future_basis(future_symbol, spot_symbol, start_ns, end_ns, expiry_ns=None)
    Spot-future basis via ASOF JOIN on trade timestamps.

perp_basis(perp_symbol, start_ns, end_ns)
    Perpetual basis (mark price vs index price).

iv_surface(underlying, at_ns, rate=0.0)
    Implied-vol surface snapshot at ``at_ns``.

term_structure(underlying, at_ns, rate=0.0)
    ATM IV term structure at ``at_ns``.

vol_skew(underlying, expiry_ns, at_ns, rate=0.0)
    Per-strike IV and delta for a single expiry, ordered by strike.

risk_reversal_butterfly(skew_df, target_delta=0.25)
    25-delta risk reversal and butterfly from a vol_skew DataFrame.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import polars as pl

from crypcodile.client.export import ExportFmt
from crypcodile.client.export import export as _export
from crypcodile.replay.merge import replay as _kway_merge
from crypcodile.schema.records import Record
from crypcodile.store.catalog import Catalog
from crypcodile.store.rows import from_row


def _df_to_record_iter(df: pl.DataFrame) -> Iterator[Record]:
    """Yield Records from a Polars DataFrame, one row at a time.

    The DataFrame must contain a ``channel`` column (added by ``to_row()`` and
    preserved in the Parquet hive layout) so that ``from_row`` can reconstruct
    the correct Record type.

    The DataFrame is assumed to be pre-sorted by ``local_ts`` (the Catalog's
    ``scan`` method already returns rows ``ORDER BY local_ts``), so the
    resulting iterator is already sorted — a prerequisite for ``heapq.merge``.
    """
    for row_dict in df.to_dicts():
        yield from_row(row_dict)


class CrypcodileClient:
    """High-level data client wrapping the hive-partitioned Parquet catalog.

    Args:
        data_dir: Root directory of the data lake — the same path passed to
            ``ParquetSink``.

    Example::

        client = CrypcodileClient(data_dir="/data/crypcodile")
        df = client.query("SELECT count(*) FROM trade")
        df2 = client.scan("trade", ["deribit:BTC-PERPETUAL"], start_ns, end_ns)
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._catalog = Catalog(data_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, sql: str) -> pl.DataFrame:
        """Execute arbitrary DuckDB SQL against registered channel views.

        Channel views are named after the channel string (e.g. ``trade``,
        ``book_snapshot``).  Views are refreshed before each query so
        newly written Parquet files are visible without reconstructing the
        client.

        Args:
            sql: Any DuckDB-compatible SQL string.

        Returns:
            A Polars DataFrame containing the query result.
        """
        return self._catalog.query(sql)

    def scan(
        self,
        channel: str,
        symbols: list[str],
        start_ns: int,
        end_ns: int,
    ) -> pl.DataFrame:
        """Return rows for one or more symbols within a nanosecond time range.

        Partition pruning is applied per symbol by narrowing the glob path to
        relevant date partitions before executing the WHERE clause.  When
        multiple symbols are requested, each symbol is scanned independently
        and the results are concatenated then re-sorted by ``local_ts``
        (globally ordered).

        Args:
            channel:   Channel name, e.g. ``"trade"``, ``"book_snapshot"``.
            symbols:   List of canonical symbol strings, e.g.
                       ``["deribit:BTC-PERPETUAL", "binance-spot:BTC-USDT"]``.
                       An empty list returns an empty DataFrame immediately.
            start_ns:  Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns:    Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

        Returns:
            A Polars DataFrame ordered by ``local_ts`` ascending.  Returns an
            empty DataFrame (0 columns, 0 rows) when no rows match.
        """
        if not symbols:
            return pl.DataFrame()

        frames: list[pl.DataFrame] = []
        for symbol in symbols:
            df = self._catalog.scan(channel, symbol, start_ns, end_ns)
            if len(df) > 0:
                frames.append(df)

        if not frames:
            return pl.DataFrame()

        if len(frames) == 1:
            return frames[0]

        # Concatenate across symbols and re-sort globally by local_ts.
        combined = pl.concat(frames, how="diagonal")
        return combined.sort("local_ts")

    def replay(
        self,
        channels: list[str],
        symbols: list[str],
        frm: int,
        to: int,
        limit: int | None = None,
    ) -> Iterator[Record]:
        """Iterate over canonical Records sorted by ``local_ts`` (k-way merge).

        Reads matching Parquet partitions for each channel,
        reconstructs Record objects from the flat Parquet rows, and merges all
        per-channel streams using the M2 k-way merge engine.

        Args:
            channels: Channel names to include, e.g. ``["trade", "book_delta"]``.
            symbols:  Canonical symbols, e.g.
                      ``["deribit:BTC-PERPETUAL", "binance-spot:BTC-USDT"]``.
                      An empty list yields nothing immediately.
            frm:      Inclusive start of the time range (nanoseconds UTC).
            to:       Inclusive end of the time range (nanoseconds UTC).
            limit:    Optional maximum number of records to return.

        Yields:
            Record objects in non-decreasing ``local_ts`` order.
        """
        if not symbols or not channels:
            return iter([])

        # Build one sorted iterator per channel, querying all symbols in a single scan.
        streams: list[Iterator[Record]] = []
        for channel in channels:
            df = self._catalog.scan(channel, symbols, frm, to, limit=limit)
            if len(df) > 0:
                streams.append(_df_to_record_iter(df))

        if not streams:
            return iter([])

        return _kway_merge(streams)

    # ------------------------------------------------------------------
    # Analytics API (Task 6.5)
    # ------------------------------------------------------------------

    def funding_apr(
        self,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> pl.DataFrame:
        """Return per-event funding APR and cumulative funding.

        Thin wrapper over :func:`crypcodile.analytics.funding.funding_apr`.

        Args:
            symbol:   Canonical symbol string (e.g. ``"deribit:BTC-PERPETUAL"``).
            start_ns: Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns:   Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

        Returns:
            A Polars DataFrame with columns:
            ``funding_ts, funding_rate, interval_hours, apr, cumulative_funding``.
            Returns an empty DataFrame when no data exists.
        """
        from crypcodile.analytics.funding import funding_apr as _funding_apr

        return _funding_apr(self._catalog, symbol, start_ns, end_ns)

    def estimate_slippage(
        self,
        symbol: str,
        side: str,
        size: float | str,
        size_unit: str | None = None,
    ) -> pl.DataFrame:
        """Estimate execution slippage for a given symbol and size.

        Thin wrapper over :func:`crypcodile.analytics.slippage.estimate_slippage`.
        """
        from crypcodile.analytics.slippage import estimate_slippage as _estimate_slippage

        return _estimate_slippage(self._catalog, symbol, side, size, size_unit)

    def calculate_ofi(
        self,
        symbol: str,
        start_ns: int,
        end_ns: int,
        interval: str,
    ) -> pl.DataFrame:
        """Calculate Order Flow Imbalance (OFI) index over time-binned intervals.

        Thin wrapper over :func:`crypcodile.analytics.ofi.calculate_ofi`.
        """
        from crypcodile.analytics.ofi import calculate_ofi as _calculate_ofi

        return _calculate_ofi(self._catalog, symbol, start_ns, end_ns, interval)

    def track_whale_alerts(
        self,
        symbol: str,
        start_ns: int,
        end_ns: int,
        min_usd: float,
    ) -> pl.DataFrame:
        """Query and filter trades and liquidations exceeding a USD threshold.

        Thin wrapper over :func:`crypcodile.analytics.whale.track_whale_alerts`.
        """
        from crypcodile.analytics.whale import track_whale_alerts as _track_whale_alerts

        return _track_whale_alerts(self._catalog, symbol, start_ns, end_ns, min_usd)

    def spot_future_basis(
        self,
        future_symbol: str,
        spot_symbol: str,
        start_ns: int,
        end_ns: int,
        expiry_ns: int | None = None,
    ) -> pl.DataFrame:
        """Return spot-future basis via ASOF JOIN on trade timestamps.

        Thin wrapper over :func:`crypcodile.analytics.basis.spot_future_basis`.

        Args:
            future_symbol: Canonical symbol for the futures leg.
            spot_symbol:   Canonical symbol for the spot leg.
            start_ns:      Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns:        Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
            expiry_ns:     Optional expiry timestamp (nanoseconds UTC).  When
                           given, an ``annualized_pct`` column is appended.

        Returns:
            A Polars DataFrame with columns:
            ``local_ts, future_price, spot_price, basis, basis_pct``
            (and ``annualized_pct`` when ``expiry_ns`` is provided).
            Returns an empty DataFrame when either leg has no data.
        """
        from crypcodile.analytics.basis import spot_future_basis as _sfb

        return _sfb(self._catalog, future_symbol, spot_symbol, start_ns, end_ns, expiry_ns)

    def perp_basis(
        self,
        perp_symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> pl.DataFrame:
        """Return perpetual basis (mark price vs index price).

        Thin wrapper over :func:`crypcodile.analytics.basis.perp_basis`.

        Args:
            perp_symbol: Canonical perpetual contract symbol.
            start_ns:    Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns:      Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

        Returns:
            A Polars DataFrame with columns:
            ``local_ts, mark_price, index_price, basis, basis_pct``.
            Returns an empty DataFrame when no data exists.
        """
        from crypcodile.analytics.basis import perp_basis as _perp_basis

        return _perp_basis(self._catalog, perp_symbol, start_ns, end_ns)

    def iv_surface(
        self,
        underlying: str,
        at_ns: int,
        rate: float = 0.0,
    ) -> pl.DataFrame:
        """Return the implied-vol surface snapshot at ``at_ns``.

        Thin wrapper over :func:`crypcodile.analytics.volsurface.iv_surface`.

        Args:
            underlying: Underlying asset identifier (e.g. ``"BTC"``).
            at_ns:      Snapshot instant (nanoseconds UTC).
            rate:       Continuous risk-free rate (default 0.0).

        Returns:
            A Polars DataFrame with columns:
            ``expiry, strike, moneyness, opt_type, iv, source``.
            Returns an empty DataFrame when no data exists.
        """
        from crypcodile.analytics.volsurface import iv_surface as _iv_surface

        return _iv_surface(self._catalog, underlying, at_ns, rate)

    def term_structure(
        self,
        underlying: str,
        at_ns: int,
        rate: float = 0.0,
    ) -> pl.DataFrame:
        """Return the ATM IV term structure at ``at_ns``.

        Thin wrapper over :func:`crypcodile.analytics.volsurface.term_structure`.

        Args:
            underlying: Underlying asset identifier (e.g. ``"BTC"``).
            at_ns:      Snapshot instant (nanoseconds UTC).
            rate:       Continuous risk-free rate (default 0.0).

        Returns:
            A Polars DataFrame with columns:
            ``expiry, days_to_expiry, atm_strike, atm_iv``.
            Ordered by ``expiry`` ascending.
            Returns an empty DataFrame when no data exists.
        """
        from crypcodile.analytics.volsurface import term_structure as _term_structure

        return _term_structure(self._catalog, underlying, at_ns, rate)

    def vol_skew(
        self,
        underlying: str,
        expiry_ns: int,
        at_ns: int,
        rate: float = 0.0,
    ) -> pl.DataFrame:
        """Return per-strike IV and delta for a single expiry, ordered by strike.

        Thin wrapper over :func:`crypcodile.analytics.volsurface.vol_skew`.

        Args:
            underlying: Underlying asset identifier (e.g. ``"BTC"``).
            expiry_ns:  Expiry filter (nanoseconds UTC).
            at_ns:      Snapshot instant (nanoseconds UTC).
            rate:       Continuous risk-free rate (default 0.0).

        Returns:
            A Polars DataFrame with columns:
            ``strike, moneyness, opt_type, iv, delta``.
            Ordered by ``strike`` ascending.
            Returns an empty DataFrame when no data exists.
        """
        from crypcodile.analytics.volsurface import vol_skew as _vol_skew

        return _vol_skew(self._catalog, underlying, expiry_ns, at_ns, rate)

    def risk_reversal_butterfly(
        self,
        skew_df: pl.DataFrame,
        target_delta: float = 0.25,
    ) -> tuple[float | None, float | None]:
        """Compute the 25-delta risk reversal and butterfly from a skew DataFrame.

        Thin wrapper over
        :func:`crypcodile.analytics.volsurface.risk_reversal_butterfly`.

        Uses the call with delta nearest to ``+target_delta`` and the put with
        delta nearest to ``-target_delta``.

        Formulas:
        - ``rr = iv(call @ +target_delta) - iv(put @ -target_delta)``
        - ``bf = mean(iv_call_target, iv_put_target) - atm_iv``

        Args:
            skew_df:      Output of :meth:`vol_skew` for a single expiry.
            target_delta: Target absolute delta for RR/BF (default 0.25).

        Returns:
            ``(rr, bf)`` where each element is a float or ``None`` if the
            required options cannot be found.
        """
        from crypcodile.analytics.volsurface import (
            risk_reversal_butterfly as _risk_reversal_butterfly,
        )

        return _risk_reversal_butterfly(skew_df, target_delta)

    def export(
        self,
        channel: str,
        symbols: list[str],
        frm: int,
        to: int,
        fmt: ExportFmt,
        dest: Path | str,
        limit: int | None = None,
    ) -> None:
        """Write rows for a channel x symbols x time range to a file.

        Supported formats are ``parquet``, ``csv``, ``arrow``, ``json``,
        and ``jsonl``.  Parent directories of ``dest`` are created
        automatically.  An empty result (no matching rows) still creates
        the destination file.

        Args:
            channel: Channel name, e.g. ``"trade"``, ``"book_snapshot"``.
            symbols: Canonical symbol strings to include.  An empty list
                     writes an empty file.
            frm:     Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            to:      Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
            fmt:     Output format — one of ``parquet``, ``csv``, ``arrow``,
                     ``json``, ``jsonl``.
            dest:    Destination file path (string or :class:`~pathlib.Path`).
            limit:   Optional maximum number of rows to export.

        Raises:
            ValueError: If ``fmt`` is not a recognised format string.

        Example::

            client.export(
                "trade",
                ["deribit:BTC-PERPETUAL"],
                start_ns,
                end_ns,
                fmt="csv",
                dest="/tmp/btc_trades.csv",
            )
        """
        _export(self._catalog, channel, symbols, frm, to, fmt, Path(dest), limit=limit)

    def resample(
        self,
        symbol: str,
        start_ns: int,
        end_ns: int,
        interval: str,
        *,
        fill_empty: bool = False,
    ) -> pl.DataFrame:
        """Resample trade data in the DuckDB Catalog into OHLCV bars."""
        from crypcodile.resample.ohlcv import resample_ohlcv

        return resample_ohlcv(
            self._catalog,
            symbol,
            start_ns,
            end_ns,
            interval,
            fill_empty=fill_empty,
        )

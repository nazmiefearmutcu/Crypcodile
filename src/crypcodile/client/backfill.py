"""Historical REST backfill orchestrator (CLI / programmatic).

Dispatches to per-exchange ``*Backfill`` helpers under ``crypcodile.exchanges``.
Supported exchanges: ``binance``, ``bybit``, ``okx``, ``deribit``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink
from crypcodile.util.time import now_ns

log = logging.getLogger(__name__)

# exchange → set of channels that have a REST backfill path
SUPPORTED_CHANNELS: dict[str, frozenset[str]] = {
    "binance": frozenset({"trade", "ohlcv", "open_interest"}),
    "bybit": frozenset({"trade", "funding", "open_interest"}),
    "okx": frozenset({"trade", "funding", "open_interest"}),
    "deribit": frozenset({"trade", "funding"}),
}

SUPPORTED_EXCHANGES: frozenset[str] = frozenset(SUPPORTED_CHANNELS)


def _venue_for(exchange: str, market: str) -> str:
    """Canonical exchange label written into records."""
    if exchange == "binance":
        return f"binance-{market}"
    return exchange


async def iter_backfill(
    exchange: str,
    channel: str,
    symbol: str,
    start_ns: int,
    end_ns: int,
    *,
    market: str = "spot",
    category: str = "linear",
    inst_type: str = "SWAP",
    interval: str = "1m",
    period: str = "5m",
    local_ts: int | None = None,
    backfill_obj: Any | None = None,
) -> AsyncIterator[Record]:
    """Yield canonical Records for one symbol via the exchange REST backfill API.

    Parameters
    ----------
    exchange:
        One of :data:`SUPPORTED_EXCHANGES`.
    channel:
        Canonical channel name (``trade``, ``funding``, ``ohlcv``,
        ``open_interest``).
    symbol:
        Exchange-native symbol (e.g. ``BTCUSDT``, ``BTC-PERPETUAL``).
    start_ns / end_ns:
        Inclusive time bounds in nanoseconds UTC.
    market:
        Binance market segment (``spot`` / ``usdm`` / ``coinm``). Default spot.
    category:
        Bybit category (``spot`` / ``linear`` / ``inverse``). Default linear.
    inst_type:
        OKX instrument type (``SPOT`` / ``SWAP`` / ``FUTURES``). Default SWAP.
    interval:
        OHLCV bar interval for Binance klines (default ``1m``).
    period:
        Open-interest history period for Binance (default ``5m``).
    backfill_obj:
        Optional pre-built ``*Backfill`` instance (for tests / injection).
        When ``None``, a live REST-backed instance is created.
    """
    exchange = exchange.lower().strip()
    channel = channel.lower().strip()
    ts = local_ts if local_ts is not None else now_ns()
    venue = _venue_for(exchange, market)

    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"Unsupported exchange {exchange!r} for backfill. "
            f"Supported: {sorted(SUPPORTED_EXCHANGES)}"
        )

    allowed = SUPPORTED_CHANNELS[exchange]
    if channel not in allowed:
        raise ValueError(
            f"Channel {channel!r} is not supported for {exchange} backfill. "
            f"Supported channels: {sorted(allowed)}"
        )

    if exchange == "binance":
        async for rec in _binance_iter(
            channel, venue, symbol, start_ns, end_ns,
            interval=interval, period=period, local_ts=ts, bf=backfill_obj,
        ):
            yield rec
        return

    if exchange == "bybit":
        async for rec in _bybit_iter(
            channel, venue, symbol, category, start_ns, end_ns,
            local_ts=ts, bf=backfill_obj,
        ):
            yield rec
        return

    if exchange == "okx":
        async for rec in _okx_iter(
            channel, venue, symbol, inst_type, start_ns, end_ns,
            local_ts=ts, bf=backfill_obj,
        ):
            yield rec
        return

    if exchange == "deribit":
        async for rec in _deribit_iter(
            channel, symbol, start_ns, end_ns,
            local_ts=ts, bf=backfill_obj,
        ):
            yield rec
        return


async def _binance_iter(
    channel: str,
    venue: str,
    symbol: str,
    start_ns: int,
    end_ns: int,
    *,
    interval: str,
    period: str,
    local_ts: int,
    bf: Any | None,
) -> AsyncIterator[Record]:
    if bf is None:
        from crypcodile.exchanges.binance.backfill import make_live_backfill

        bf = make_live_backfill()

    if channel == "trade":
        async for rec in bf.backfill_aggtrades(
            venue=venue, symbol=symbol, start_ns=start_ns, end_ns=end_ns, local_ts=local_ts,
        ):
            yield rec
    elif channel == "ohlcv":
        async for rec in bf.backfill_klines(
            venue=venue,
            symbol=symbol,
            interval=interval,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec
    elif channel == "open_interest":
        async for rec in bf.backfill_open_interest_hist(
            venue=venue,
            symbol=symbol,
            period=period,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec


async def _bybit_iter(
    channel: str,
    venue: str,
    symbol: str,
    category: str,
    start_ns: int,
    end_ns: int,
    *,
    local_ts: int,
    bf: Any | None,
) -> AsyncIterator[Record]:
    if bf is None:
        from crypcodile.exchanges.bybit.backfill import make_live_backfill

        bf = make_live_backfill()

    if channel == "trade":
        async for rec in bf.backfill_trades(
            venue=venue,
            symbol=symbol,
            category=category,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec
    elif channel == "funding":
        async for rec in bf.backfill_funding(
            venue=venue,
            symbol=symbol,
            category=category,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec
    elif channel == "open_interest":
        async for rec in bf.backfill_open_interest(
            venue=venue,
            symbol=symbol,
            category=category,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec


async def _okx_iter(
    channel: str,
    venue: str,
    symbol: str,
    inst_type: str,
    start_ns: int,
    end_ns: int,
    *,
    local_ts: int,
    bf: Any | None,
) -> AsyncIterator[Record]:
    if bf is None:
        from crypcodile.exchanges.okx.backfill import make_live_backfill

        bf = make_live_backfill()

    if channel == "trade":
        async for rec in bf.backfill_trades(
            venue=venue,
            symbol=symbol,
            inst_type=inst_type,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec
    elif channel == "funding":
        async for rec in bf.backfill_funding(
            venue=venue,
            symbol=symbol,
            inst_type=inst_type,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec
    elif channel == "open_interest":
        async for rec in bf.backfill_open_interest(
            venue=venue,
            symbol=symbol,
            inst_type=inst_type,
            start_ns=start_ns,
            end_ns=end_ns,
            local_ts=local_ts,
        ):
            yield rec


async def _deribit_iter(
    channel: str,
    symbol: str,
    start_ns: int,
    end_ns: int,
    *,
    local_ts: int,
    bf: Any | None,
) -> AsyncIterator[Record]:
    if bf is None:
        from crypcodile.exchanges.deribit.backfill import make_live_backfill

        bf = make_live_backfill()

    if channel == "trade":
        async for rec in bf.backfill_trades(
            instrument=symbol, start_ns=start_ns, end_ns=end_ns, local_ts=local_ts,
        ):
            yield rec
    elif channel == "funding":
        async for rec in bf.backfill_funding(
            instrument=symbol, start_ns=start_ns, end_ns=end_ns, local_ts=local_ts,
        ):
            yield rec


async def run_historical_backfill(
    exchange: str,
    channel: str,
    symbols: list[str],
    start_ns: int,
    end_ns: int,
    sink: Sink,
    *,
    market: str = "spot",
    category: str = "linear",
    inst_type: str = "SWAP",
    interval: str = "1m",
    period: str = "5m",
    backfill_factory: Callable[[], Any] | None = None,
) -> int:
    """Run REST backfill for all *symbols* and write records into *sink*.

    Returns the number of records written.  Always closes the sink.
    """
    exchange = exchange.lower().strip()
    channel = channel.lower().strip()

    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"Unsupported exchange {exchange!r} for backfill. "
            f"Supported: {sorted(SUPPORTED_EXCHANGES)}"
        )

    allowed = SUPPORTED_CHANNELS[exchange]
    if channel not in allowed:
        raise ValueError(
            f"Channel {channel!r} is not supported for {exchange} backfill. "
            f"Supported channels: {sorted(allowed)}"
        )

    bf = backfill_factory() if backfill_factory is not None else None
    count = 0
    try:
        for symbol in symbols:
            async for rec in iter_backfill(
                exchange,
                channel,
                symbol,
                start_ns,
                end_ns,
                market=market,
                category=category,
                inst_type=inst_type,
                interval=interval,
                period=period,
                backfill_obj=bf,
            ):
                await sink.put(rec)
                count += 1
    finally:
        await sink.close()
    return count

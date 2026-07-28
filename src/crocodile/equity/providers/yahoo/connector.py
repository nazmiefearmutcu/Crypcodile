"""Yahoo as a registered provider: the option chain ``collect`` writes, and EOD history.

Six equity capability implementations read the ``options_chain`` channel — ``iv-surface``,
``term-structure``, ``vol-skew`` and ``risk-reversal`` through
:mod:`crocodile.core.analytics.volsurface`, ``open-interest`` through
:mod:`crocodile.equity.analytics.oi_aggregator`, and ``perp-basis``'s forward leg through
:mod:`crocodile.equity.analytics.carry` — and until this module existed, nothing in
``src/`` could put a row in it. :meth:`YahooClient.fetch_option_chain` had been able to
build the records the whole time; it was reachable from tests and from no shipped path.

**Why the option chain is a ``collect`` channel and not a ``backfill`` one.** Yahoo
publishes the chain *as it stands*: bids, asks, open interest and an implied volatility for
every live contract, with no history behind them. A ``backfill`` over a past range could
only answer with today's chain, which would put a snapshot taken now into a partition
labelled then — the exact confusion ``local_ts`` and ``source_ts`` exist to keep apart. So
this is the one pull source here that polls, and :meth:`backfill` refuses ``options_chain``
by name rather than quietly answering with the present.

**The module imports its own client lazily, and that is load-bearing.**
``equity.providers.factory`` imports every connector eagerly so that
:data:`~crocodile.equity.providers.factory.VALID_CHANNELS` can be derived from what they
declare rather than hand-written. :mod:`crocodile.equity.providers.yahoo.client` pulls in
``yfinance`` and ``pandas``, which cost about 1.2s — nearly quadrupling the CLI's import
time for every user who never asks for Yahoo. Deferring the import into the constructor
keeps the declaration cheap and the dependency where it is used, which is the same trade
``crypto.exchanges.factory`` makes by importing whole connectors lazily.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final

from crocodile.core.schema.enums import Channel
from crocodile.core.schema.records import Record
from crocodile.equity.providers.base import PullProvider

if TYPE_CHECKING:
    from crocodile.core.sink.base import Sink
    from crocodile.equity.providers.yahoo.client import YahooClient
    from crocodile.equity.reference.registry import InstrumentRegistry

log = logging.getLogger(__name__)

__all__ = ["POLLED_CHANNELS", "YahooProvider"]

_NS_PER_SECOND: Final = 1_000_000_000

POLLED_CHANNELS: Final[frozenset[str]] = frozenset({Channel.OPTIONS_CHAIN.value})
"""The channels :meth:`YahooProvider.run` re-reads. The rest are history, and history is
:meth:`YahooProvider.backfill`'s."""

_HISTORY_CHANNELS: Final[frozenset[str]] = frozenset(
    {Channel.OHLCV.value, Channel.CORP_ACTION.value, Channel.INSIDER.value}
)


def _iso_day(stamp_ns: int) -> str:
    """``YYYY-MM-DD`` for a nanosecond instant, in UTC.

    Yahoo's history endpoint takes calendar dates and the capability takes nanoseconds, so
    somebody has to round. UTC and not local time: every other date this codebase derives
    from an instant is UTC (``core.store.rows._date_from_ns`` partitions the lake that
    way), and a boundary that moved with the operator's timezone would put the same bar in
    two different partitions on two different machines.
    """
    return datetime.fromtimestamp(stamp_ns / _NS_PER_SECOND, tz=UTC).date().isoformat()


class YahooProvider(PullProvider):
    """Polls the live option chain, and pages EOD history and Form 4 scrapes on request.

    The client is injectable so both paths are testable against fixture payloads with no
    socket. Nothing here is keyed, so :attr:`wants_settings` stays false.
    """

    name = "yahoo"
    rest_url = "https://query2.finance.yahoo.com"

    supported_channels: ClassVar[frozenset[str]] = frozenset(
        POLLED_CHANNELS | _HISTORY_CHANNELS
    )

    unservable_channels: ClassVar[dict[str, str]] = {
        "quote": (
            "the endpoints this client reads publish a last price and a chain, not a "
            "two-sided top of book for the underlying. Building a Quote from them would "
            "repeat google_finance's retired defect — a quote of zero width at a price "
            "nobody quoted. Use alpaca or finnhub for equity quotes; the per-contract "
            "bid/ask that does exist is on the options_chain record, where it belongs."
        )
    }

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        client: YahooClient | None = None,
        poll_interval: float = 60.0,
        **_: Any,
    ) -> None:
        super().__init__(symbols, channels, out, registry)
        self.client = client if client is not None else self._default_client()
        self.poll_interval = poll_interval

    @staticmethod
    def _default_client() -> YahooClient:
        """Build the real client, importing ``yfinance`` only now. See the module docstring."""
        from crocodile.equity.providers.yahoo.client import YahooClient

        return YahooClient()

    async def close(self) -> None:
        """Release the client's session."""
        await self.client.close()

    async def run(self, max_reconnects: int = -1) -> None:
        """Re-read the option chain for every requested symbol until cancelled.

        ``max_reconnects`` is honoured as a budget of *consecutive* failed passes, which is
        what it means on the websocket connectors too: a run that has fetched successfully
        since the last error has recovered, and the budget resets. ``-1`` is unlimited and
        ``0`` means the first failure ends the run.

        A symbol whose fetch raises does not stop the pass — one delisted ticker in a
        watchlist of forty is a gap, and failing the run would turn it into the loss of the
        other thirty-nine, which is the same rule ``get_insider_transactions`` applies per
        filing.

        Raises:
            NotImplementedError: when none of the requested channels is one this source
                polls. Yahoo's history is a ``backfill``, and a poll loop that re-fetches
                the same finished day forever is a rate limit spent on nothing.
        """
        polled = [channel for channel in self.channels if channel in POLLED_CHANNELS]
        if not polled:
            raise NotImplementedError(
                f"yahoo polls {sorted(POLLED_CHANNELS)} and nothing else; "
                f"{sorted(self.channels)} is history, which `backfill` pages into the "
                f"same lake"
            )
        failures = 0
        while True:
            for symbol in self.symbols:
                try:
                    for record in await self.client.fetch_option_chain(symbol):
                        await self.out.put(record)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    log.error("yahoo: option chain fetch failed for %s: %s", symbol, exc)
                    if max_reconnects >= 0 and failures > max_reconnects:
                        raise
                    continue
                failures = 0
            await asyncio.sleep(self.poll_interval)

    async def backfill(
        self,
        channel: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> AsyncIterator[Record]:
        """Yield this symbol's daily bars, corporate actions or insider lines in range.

        ``ohlcv`` and ``corp_action`` come out of the same request — Yahoo returns splits
        and dividends inline with the history — so asking for either channel writes both,
        which is what ``msn_money`` and ``stooq`` already do and what the sink's per-record
        partitioning is for.

        ``insider`` here is the *scrape* of Yahoo's insider-transactions table, not a Form 4
        parse: it publishes a prose transaction label and no acquired/disposed column, so
        :attr:`~crocodile.core.schema.records.InsiderTransaction.acquired_disposed` is
        ``None`` on every row and ``whale-alerts`` falls back to its transaction-code map.
        ``sec_edgar`` reads the filing itself and is the better source; this one needs no
        User-Agent and no configuration, which is the trade.

        Raises:
            ValueError: for ``options_chain`` — see the module docstring — and for any
                channel this provider does not serve at all.
        """
        if channel == Channel.OPTIONS_CHAIN.value:
            raise ValueError(
                "yahoo publishes the option chain as it stands and keeps no history of it, "
                "so a backfill over a past range could only answer with the present. "
                "Collect it instead: `collect --sources yahoo --channels options_chain`."
            )
        if channel not in _HISTORY_CHANNELS:
            raise ValueError(
                f"yahoo serves {sorted(self.supported_channels or ())} and not {channel!r}"
            )
        if end_ns < start_ns:
            return

        if channel == Channel.INSIDER.value:
            for insider in await self.client.fetch_insider_transactions(symbol):
                stamped = _day_ns(insider.transaction_date)
                if stamped is None or start_ns <= stamped <= end_ns:
                    yield insider
            return

        for record in await self.client.fetch_eod_history(
            symbol, start=_iso_day(start_ns), end=_iso_day(end_ns)
        ):
            yield record


def _day_ns(raw: object) -> int | None:
    """UTC-midnight nanoseconds for a ``YYYY-MM-DD`` string, or ``None`` if unreadable.

    A row whose date cannot be read is kept by the caller rather than dropped: an
    unparseable date is not evidence that the transaction falls outside the window.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.strptime(raw.strip()[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int(parsed.timestamp()) * _NS_PER_SECOND

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Iterable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar

import aiohttp
from bs4 import BeautifulSoup, Tag

from crocodile.core.schema.enums import AssetClass, SecurityType, Side
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import Fundamental, IndexValue, Record, Trade
from crocodile.core.sink.base import Sink
from crocodile.equity.providers.base import Provider
from crocodile.equity.reference.identity import InstrumentIdentity
from crocodile.equity.reference.registry import InstrumentRegistry

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/125.0.0.0 Safari/537.36",  # noqa: E501
]


def get_spoofed_headers() -> dict[str, str]:
    muid = "".join(random.choices("0123456789ABCDEF", k=32))
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Cookie": f"MUID={muid};",
    }


def get_possible_google_symbols(symbol: str) -> list[str]:
    symbol_upper = symbol.upper()
    if ":" in symbol_upper:
        return [symbol_upper]
    if symbol_upper in ("^SPX", ".INX", "SPX"):
        return [".INX:INDEXSP"]
    if symbol_upper in ("^IXIC", "COMP"):
        return [".IXIC:INDEXNASDAQ"]
    if symbol_upper in ("^DJI", "DJI"):
        return [".DJI:INDEXDJX"]
    return [
        f"{symbol_upper}:NASDAQ",
        f"{symbol_upper}:NYSE",
        f"{symbol_upper}:INDEXSP",
        symbol_upper,
    ]


def parse_val_and_unit(val_str: str, key: str) -> tuple[float | None, str]:
    """Parse a Google Finance metric string.

    Returns ``(None, unit)`` when the value is missing/unparseable so callers
    can skip emission instead of writing fake zeros.
    """
    val_str = val_str.strip()
    # Missing placeholders (ASCII hyphen + common unicode dashes)
    if not val_str or val_str.upper() in ("N/A", "NA", "-", "--") or val_str in (
        "\u2014",  # em dash
        "\u2013",  # en dash
        "\u2212",  # minus sign
    ):
        return None, "unknown"

    currency = "USD"
    if val_str.startswith("$"):
        currency = "USD"
        val_str = val_str[1:]
    elif val_str.startswith("€"):
        currency = "EUR"
        val_str = val_str[1:]
    elif val_str.startswith("£"):
        currency = "GBP"
        val_str = val_str[1:]

    if val_str.endswith("%"):
        val_str = val_str[:-1]
        try:
            return float(val_str.replace(",", "")), "percent"
        except ValueError:
            return None, "percent"

    multiplier = 1.0
    if val_str.endswith("T"):
        multiplier = 1e12
        val_str = val_str[:-1]
    elif val_str.endswith("B"):
        multiplier = 1e9
        val_str = val_str[:-1]
    elif val_str.endswith("M"):
        multiplier = 1e6
        val_str = val_str[:-1]
    elif val_str.endswith("K"):
        multiplier = 1e3
        val_str = val_str[:-1]

    val_str = val_str.replace(",", "")
    try:
        val = float(val_str) * multiplier
    except ValueError:
        return None, "unknown"

    if key in (
        "Open",
        "High",
        "Low",
        "Mkt. cap",
        "Quarterly dividend",
        "52-wk high",
        "52-wk low",
        "EPS",
    ):
        unit = currency
    elif key in ("Volume", "Avg. vol.", "Shares outstanding"):
        unit = "shares"
    elif key in ("Dividend",):
        unit = "percent"
    elif key in ("No. of employees",):
        unit = "count"
    elif key in ("P/E ratio", "Beta"):
        unit = "ratio"
    else:
        unit = "unknown"

    return val, unit


def parse_date(date_str: str) -> str:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


TAG_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Mkt. cap": "market_cap",
    "Avg. vol.": "avg_volume",
    "Volume": "volume",
    "Dividend": "dividend_yield",
    "Quarterly dividend": "quarterly_dividend",
    "Ex-dividend date": "ex_dividend_date",
    "P/E ratio": "pe_ratio",
    "52-wk high": "52_week_high",
    "52-wk low": "52_week_low",
    "EPS": "eps",
    "Beta": "beta",
    "Shares outstanding": "shares_outstanding",
    "No. of employees": "employees",
}


SCRAPED_LAST_PRICE = "scraped_last_price"
"""The registered basis for a price lifted off a rendered quote page.

Before this, every scraped last price shipped the header default —
``prov=native, prov_basis='native', prov_confidence=1.0``, which reads as *the venue
reported this value directly* — beside ``conditions=["synthetic", "last_price"]``. The
condition string was the only dissent, and no consumer filters on it. A consumer built
on "warn whenever ``prov != NATIVE``" stayed silent on every one of these records.
"""

_UNPUBLISHED_SIZE = 0.0
"""The quantity a scraped last price carries, because the page publishes none.

``Trade.amount`` is required: a trade cannot exist without a quantity. The old value was
``1.0``, which is not a small error but a different kind of one — it made
``SELECT sum(amount) … WHERE source='google_finance'`` return the *poll count*, presented
as share volume, and the number grew with the scrape interval rather than with trading.

A structural zero is the encoding this codebase already uses for "this method carries no
size": ``ohlcv_from_quotes`` bars report ``volume = 0.0`` for the same reason and say so
in the registration. It sums to nothing, which is the truth about how much volume a
scrape contributes, and ``prov_basis`` names the method that produced it.
"""


class GoogleFinanceProvider(Provider):
    name = "google_finance"
    ws_url = ""
    rest_url = "https://www.google.com/finance"

    supported_channels: ClassVar[frozenset[str]] = frozenset(
        {"trade", "index_value", "fundamental"}
    )
    unservable_channels: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "quote": (
                "the quote page renders one number, the last price, and no bid/ask. The "
                "record built from it set bid_px = ask_px = price with bid_sz = ask_sz = "
                "1.0 and labelled it native — a two-sided quote of zero width, at a price "
                "nobody quoted, in sizes nobody posted. `Quote` requires four fields and "
                "the scrape observes none of them, so there is no honest record to emit "
                "and none is emitted. Use a provider with a real top of book (alpaca, "
                "finnhub)."
            )
        }
    )

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
    ) -> None:
        super().__init__(symbols, channels, out, registry)
        self.session: aiohttp.ClientSession | None = None
        self._running = False
        self._resolved_symbol_cache: dict[str, str] = {}

    async def list_instruments(self) -> list[InstrumentIdentity]:
        insts = []
        for sym in self.symbols:
            sec_type = (
                SecurityType.UNKNOWN
                if (sym.startswith("^") or sym.startswith("."))
                else SecurityType.CS
            )
            insts.append(
                InstrumentIdentity(
                    symbol=sym,
                    source=self.name,
                    symbol_raw=sym,
                    security_type=sec_type,
                )
            )
        return insts

    async def _subscribe(self, transport: Any) -> None:
        pass

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        return ()

    async def run(self, max_reconnects: int = -1) -> None:
        self._running = True
        try:
            async with aiohttp.ClientSession() as session:
                self.session = session
                while self._running:
                    for symbol in self.symbols:
                        try:
                            records = await self._scrape_symbol(symbol)
                            for rec in records:
                                await self.out.put(rec)
                        except Exception as e:
                            log.error("Google Finance scraper error for %s: %s", symbol, e)
                    # Poll interval
                    for _ in range(10):
                        if not self._running:
                            break
                        await asyncio.sleep(1.0)
        finally:
            self._running = False
            self.session = None

    async def _scrape_with_g_sym(
        self, symbol: str, g_sym: str, local_ts: int
    ) -> list[Record] | None:
        """Scrape one Google symbol, or return ``None`` if it does not name this security.

        ``None`` and ``[]`` are different answers and conflating them cost the caller
        its resolved-symbol cache. ``_scrape_symbol`` reads a falsy result as "this
        candidate is not the right Google symbol" and tries the next of four; once the
        `quote` channel stopped emitting a record, a correctly-resolved page returned
        `[]` for a `--channels quote` run, so the cache never populated, all four
        candidates were refetched every poll, and the loop ran forever at four times the
        rate with nothing to show. `None` now means "this page is not it"; an empty list
        means "this page is it and there was nothing on it to emit".
        """
        if not self.session:
            return None

        url = f"{self.rest_url}/quote/{g_sym}"
        headers = get_spoofed_headers()
        try:
            async with self.session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10.0),
            ) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

                soup = BeautifulSoup(html, "html.parser")

                # Parse price
                price_classes = ["N6SYTe", "YMlKec", "fxKbKc"]
                price_el = None
                for cls in price_classes:
                    price_el = soup.find(class_=cls)
                    if price_el:
                        break
                if not price_el:
                    price_el = soup.find(lambda tag: tag.has_attr("data-last-price"))
                if not price_el:
                    return None

                # Prefer data-last-price attribute (numeric), then visible text
                price: float | None = None
                attr_price = price_el.get("data-last-price") if hasattr(price_el, "get") else None
                if attr_price not in (None, ""):
                    try:
                        price = float(str(attr_price).replace(",", "").strip())
                    except ValueError:
                        price = None
                if price is None:
                    price_str = price_el.get_text(strip=True)
                    clean_price_str = price_str.strip()
                    for prefix in ["$", "€", "£", "¥", "₹", "A$", "C$", "HK$"]:
                        if clean_price_str.startswith(prefix):
                            clean_price_str = clean_price_str[len(prefix) :]
                            break
                    clean_price_str = clean_price_str.replace(",", "").strip()
                    try:
                        price = float(clean_price_str)
                    except ValueError:
                        return None

                # Parse source timestamp
                source_ts = None
                for attr in [
                    "data-last-normal-market-timestamp",
                    "data-last-market-timestamp",
                    "data-timestamp",
                ]:
                    time_el = soup.find(lambda tag, attr=attr: tag.has_attr(attr))
                    if isinstance(time_el, Tag):
                        try:
                            ts_str_val = time_el.get(attr)
                            if ts_str_val:
                                ts_val = int(ts_str_val)  # type: ignore[arg-type]
                                if ts_val < 1e11:
                                    source_ts = ts_val * 1_000_000_000
                                else:
                                    source_ts = ts_val * 1_000_000
                                break
                        except Exception:
                            pass

                if not source_ts:
                    try:
                        as_of_node = soup.find(string=lambda t: t and "As of " in t)
                        if as_of_node:
                            as_of_text = str(as_of_node).split("As of")[-1].strip()
                            from dateutil import parser as date_parser

                            dt = date_parser.parse(as_of_text, fuzzy=True)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
                            source_ts = int(dt.timestamp() * 1e9)
                    except Exception:
                        pass

                records: list[Record] = []

                # Real-time update - check InstrumentRegistry
                is_index = False
                inst = self.registry.get_raw(self.name, symbol)
                if inst:
                    is_index = inst.security_type == SecurityType.UNKNOWN
                else:
                    is_index = symbol.startswith("^") or symbol.startswith(".") or "INDEX" in g_sym

                if is_index:
                    if "index_value" in self.channels:
                        records.append(
                            IndexValue(
                                source=self.name,
                                symbol=symbol.upper(),
                                symbol_raw=symbol,
                                source_ts=source_ts,
                                local_ts=local_ts,
                                value=price,
                                asset_class=AssetClass.EQUITY,
                            )
                        )
                else:
                    if "trade" in self.channels:
                        tail = provenance_fields(SCRAPED_LAST_PRICE)
                        records.append(
                            Trade(
                                source=self.name,
                                symbol=symbol.upper(),
                                symbol_raw=symbol,
                                source_ts=source_ts,
                                local_ts=local_ts,
                                id="",
                                price=price,
                                amount=_UNPUBLISHED_SIZE,
                                conditions=["synthetic", "last_price"],
                                asset_class=AssetClass.EQUITY,
                                side=Side.UNKNOWN,
                                prov=tail.prov,
                                prov_basis=tail.prov_basis,
                                prov_confidence=tail.prov_confidence,
                                prov_inputs=tail.prov_inputs,
                            )
                        )

                # Parse fundamentals
                if "fundamental" in self.channels:
                    key_classes = ["SwQK7", "m61tGe", "gy1Zab"]
                    val_classes = ["dO6ijd", "P6K39c", "w26nd"]

                    key_els: list[Any] = []
                    for k_cls in key_classes:
                        els = soup.find_all(class_=k_cls)
                        if els:
                            key_els = els
                            break

                    for key_el in key_els:
                        val_el = None
                        # 1. Search in parent container first to prevent DOM-wide misalignment
                        parent = key_el.parent
                        if parent:
                            for v_cls in val_classes:
                                val_el = parent.find(class_=v_cls)
                                if val_el:
                                    break
                        # 2. Search sibling
                        if not val_el:
                            for v_cls in val_classes:
                                val_el = key_el.find_next_sibling(class_=v_cls)
                                if val_el:
                                    break

                        if val_el:
                            key_text = key_el.get_text(strip=True)
                            val_text = val_el.get_text(strip=True)
                            if key_text in TAG_MAP:
                                tag = TAG_MAP[key_text]
                                end_str = ""
                                if key_text == "Ex-dividend date":
                                    # The page publishes a date here and no number, and
                                    # Fundamental.val is required. It used to be filled with
                                    # 0.0 under unit="date" — the field admitting in one
                                    # column what prov=native denied in another — so
                                    # SELECT avg(val) ... WHERE source='google_finance'
                                    # averaged a sentinel in with real facts, and a consumer
                                    # filtering prov != 'native' to find the non-measurements
                                    # got nothing back. There is no record type here for a
                                    # date-valued fact, so the row is dropped rather than
                                    # given an invented quantity.
                                    continue
                                val, unit = parse_val_and_unit(val_text, key_text)
                                if val is None:
                                    continue
                                records.append(
                                    Fundamental(
                                        source=self.name,
                                        symbol=symbol.upper(),
                                        symbol_raw=symbol,
                                        source_ts=source_ts,
                                        local_ts=local_ts,
                                        taxonomy=self.name,
                                        tag=tag,
                                        unit=unit,
                                        val=val,
                                        end=end_str,
                                        asset_class=AssetClass.EQUITY,
                                    )
                                )
                return records
        except Exception as e:
            log.debug("Failed checking symbol possibility %s: %s", g_sym, e)
            return None

    async def _scrape_symbol(self, symbol: str) -> list[Record]:
        """Resolve ``symbol`` to a Google symbol, caching the one that worked.

        Resolution is decided by whether the page *loaded and parsed*, never by whether
        it produced a record: a correctly-resolved page with nothing to emit for the
        configured channels is still the right page. See :meth:`_scrape_with_g_sym`.
        """
        if not self.session:
            return []

        local_ts = time.time_ns()

        # Check cache first
        cached_g_sym = self._resolved_symbol_cache.get(symbol)
        if cached_g_sym:
            records = await self._scrape_with_g_sym(symbol, cached_g_sym, local_ts)
            if records is not None:
                return records
            # Invalidate cache if it fails
            self._resolved_symbol_cache.pop(symbol, None)

        possibilities = get_possible_google_symbols(symbol)
        for g_sym in possibilities:
            if g_sym == cached_g_sym:
                continue
            records = await self._scrape_with_g_sym(symbol, g_sym, local_ts)
            if records is not None:
                self._resolved_symbol_cache[symbol] = g_sym
                return records

        return []

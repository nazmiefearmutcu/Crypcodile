"""Every other test in this phase fills the lake by hand. These four ask the product to.

The four channels this file covers — ``options_chain``, ``macro_series``, ``insider`` and
``holding_13f`` — were read by eleven shipped equity capability implementations and written
by nothing, and the reason that survived roughly four hundred new tests is that all of them
build a lake with a ``ParquetSink`` in the test body. A test that writes ``options_chain``
itself proves that ``iv-surface`` can read one; it says nothing about whether the shipped
product can produce one, which is the question that was wrong.

So each test here starts at a *capability* — ``collect`` or ``backfill``, the only writers —
runs the real provider, the real sink and a real Parquet lake, and then reads the result back
through the capability registry. The only thing replaced is the network edge, and it is
replaced at the lowest boundary each source has: ``TreasuryYieldClient.fetch_par_yield_csv``
returns the checked-in CSV instead of fetching it, ``SecEdgarClient._request_json`` and
``._request_text`` answer from the checked-in filings, and the Yahoo client is swapped whole
because it wraps ``yfinance`` rather than speaking HTTP itself. Everything below those —
parsing, provenance, record construction, the sink's partitioning, the catalog's views, the
analytics' SQL — is the shipped code.

Nothing here touches the network. That is a property of the seams, not of a marker: no test
in this file constructs a client that could reach one.
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from typing import Any

import pytest

from crocodile.capabilities import load_all
from crocodile.capabilities.ops import BackfillParams, CollectParams
from crocodile.core.capability import REGISTRY, AssetClass, CapabilityContext
from crocodile.core.config import Settings
from crocodile.core.schema.enums import AssetClass as RecordAssetClass
from crocodile.core.schema.enums import OptType
from crocodile.core.schema.records import OHLCV, InsiderTransaction, OptionsChain
from crocodile.core.store.catalog import Catalog
from crocodile.equity.providers.sec_edgar.client import SecEdgarClient
from crocodile.equity.providers.treasury.client import TreasuryYieldClient
from crocodile.equity.providers.yahoo.connector import YahooProvider

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sec_edgar"
_TREASURY_CSV = (
    pathlib.Path(__file__).parent / "providers" / "fixtures" / "treasury_par_yield_2024.csv"
)

_DAY_NS = 86_400_000_000_000
_2024_START = 1_704_067_200_000_000_000  # 2024-01-01T00:00:00Z
_2024_END = 1_735_603_200_000_000_000  # 2024-12-31T00:00:00Z


def _observed_window() -> tuple[int, int]:
    """A range around *now*, for the reads that filter on observation time.

    Two clocks live on every record and they are not interchangeable. ``local_ts`` is when
    this process saw the row and is what the lake partitions on, so ``Catalog.scan`` — and
    therefore ``catalog-scan`` — bounds on it; ``transaction_date``, ``report_date`` and a
    curve point's ``source_ts`` are what the document *states*, and that is what a filing
    or a curve is asked about. A 2024 backfill run today writes 2024 business dates under
    today's observation instant, so the two windows below are different on purpose. Reading
    a 2024 filing back with a 2024 ``catalog-scan`` window returns nothing, correctly.
    """
    now = time.time_ns()
    return now - _DAY_NS, now + _DAY_NS


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _ctx(tmp_path: pathlib.Path) -> CapabilityContext:
    """A context over an empty lake, as a surface would build one.

    Empty on purpose: the only rows any assertion below sees are rows a capability put there
    during the test.
    """
    load_all()
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path, sec_user_agent="Crocodile Tests ops@example.com"),
        asset_class=AssetClass.EQUITY,
    )


def _run(name: str, ctx: CapabilityContext, params: Any) -> Any:
    """Invoke a capability the way a surface does: through the registry, by name."""
    return REGISTRY[name].impls[AssetClass.EQUITY].fn(ctx, params)


# ---------------------------------------------------------------------------
# Seams. One per source, each at that source's own network edge.
# ---------------------------------------------------------------------------


class _FakeYahoo:
    """Stands in for ``YahooClient``, which wraps ``yfinance`` rather than speaking HTTP.

    Swapped whole rather than patched at a request boundary because there is no request
    boundary to patch: the client hands a callable to ``yfinance`` and gets a DataFrame back.
    The seam is therefore the client's own published method signatures, which is the widest
    this one can be — and the provider, the sink, the lake and every capability below it are
    still the shipped objects.
    """

    def __init__(self, chain: list[OptionsChain], bars: list[OHLCV] | None = None) -> None:
        self.chain = chain
        self.bars = bars or []
        self.chain_calls = 0

    async def fetch_option_chain(
        self, symbol: str, expiry: str | None = None
    ) -> list[OptionsChain]:
        self.chain_calls += 1
        return [record for record in self.chain if record.underlying == symbol]

    async def fetch_eod_history(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> list[OHLCV]:
        return [record for record in self.bars if record.symbol == symbol]

    async def fetch_insider_transactions(self, symbol: str) -> list[InsiderTransaction]:
        return []

    async def close(self) -> None:
        return None


def _install_yahoo(monkeypatch: pytest.MonkeyPatch, client: _FakeYahoo) -> None:
    monkeypatch.setattr(YahooProvider, "_default_client", staticmethod(lambda: client))


def _install_treasury(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the one HTTP request the Treasury client makes, from the checked-in CSV.

    Below this line everything is real: :func:`parse_par_yield_csv` derives the tenor set from
    the header, mints the symbols, converts the percentages and builds the provenance tail.
    """

    async def _csv(self: TreasuryYieldClient, year: int) -> str:
        assert year == 2024, f"asked for {year}, and the fixture is 2024"
        return _TREASURY_CSV.read_text()

    monkeypatch.setattr(TreasuryYieldClient, "fetch_par_yield_csv", _csv)


_CIK = 320193
_ACCN_FORM4 = "0000320193-24-000001"
_ACCN_13F = "0000320193-24-000002"


def _install_sec_edgar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve EDGAR's registrant index, one issuer's submissions and two filings.

    The two request methods are the client's only network edge, so the ticker→CIK resolution,
    the form filter, the XSL-rendered-view strip, the ``index.json`` walk that finds an
    information table nobody can name in advance, and both XML parsers all run for real.
    """
    # `EXCO` because that is the issuer the checked-in Form 4 names, and a record's symbol
    # comes from the filing rather than from the ticker the caller looked it up by.
    tickers = {"0": {"cik_str": _CIK, "ticker": "EXCO", "title": "EXCO Resources Inc."}}
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": [_ACCN_FORM4, _ACCN_13F],
                "form": ["4", "13F-HR"],
                "filingDate": ["2024-02-05", "2024-05-15"],
                "reportDate": ["2024-02-01", "2024-03-31"],
                "primaryDocument": ["xslF345X03/wf-form4_170.xml", "primary_doc.xml"],
                "isXBRL": [0, 0],
            }
        }
    }
    index = {
        "directory": {
            "item": [
                {"name": "primary_doc.xml"},
                {"name": "form13fInfoTable.xml"},
                {"name": f"{_ACCN_13F}-index.xml"},
            ]
        }
    }

    async def _json(self: SecEdgarClient, url: str) -> Any:
        if "company_tickers" in url:
            return tickers
        if "/submissions/" in url:
            return submissions
        if url.endswith("index.json"):
            return index
        raise AssertionError(f"unexpected SEC JSON request: {url}")

    async def _text(self: SecEdgarClient, url: str) -> str:
        if url.endswith("wf-form4_170.xml"):
            return _fixture("form4_officer.xml")
        if url.endswith("primary_doc.xml"):
            return _fixture("form13f_primary_doc.xml")
        if url.endswith("form13fInfoTable.xml"):
            return _fixture("form13f_infotable_2024q1.xml")
        raise AssertionError(f"unexpected SEC text request: {url}")

    monkeypatch.setattr(SecEdgarClient, "_request_json", _json)
    monkeypatch.setattr(SecEdgarClient, "_request_text", _text)


def _option(strike: float, opt_type: OptType, iv: float, oi: float) -> OptionsChain:
    """One contract, in the shape :meth:`YahooClient.fetch_option_chain` returns."""
    return OptionsChain(
        source="yahoo",
        symbol=f"AAPL240621{'C' if opt_type is OptType.CALL else 'P'}{int(strike):08d}",
        symbol_raw="AAPL",
        source_ts=_2024_START + _DAY_NS,
        local_ts=_2024_START + _DAY_NS,
        asset_class=RecordAssetClass.EQUITY,
        underlying="AAPL",
        underlying_price=190.0,
        strike=strike,
        expiry=_2024_START + 180 * _DAY_NS,
        opt_type=opt_type,
        mark_iv=iv,
        bid_px=strike * 0.01,
        ask_px=strike * 0.011,
        last_price=strike * 0.0105,
        volume=100.0,
        open_interest=oi,
    )


def _bar(close: float, day: int) -> OHLCV:
    return OHLCV(
        source="yahoo",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=_2024_START + day * _DAY_NS,
        local_ts=_2024_START + day * _DAY_NS,
        asset_class=RecordAssetClass.EQUITY,
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=1_000_000.0,
        interval="1d",
    )


# ---------------------------------------------------------------------------
# options_chain — written by `collect`, read by the four vol capabilities.
# ---------------------------------------------------------------------------


def test_collect_writes_an_option_chain_that_iv_surface_reads_back(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``collect --sources yahoo --channels options_chain`` → lake → ``iv-surface``.

    The whole loop the defect broke. Before ``yahoo`` joined ``providers.factory._REGISTRY``,
    the first line of this test raised ``ValueError: Unknown provider 'yahoo'`` and the
    channel had no writer at all, so ``iv-surface`` for equities returned an empty frame on
    every lake the product could build.
    """
    chain = [
        _option(180.0, OptType.CALL, 0.30, 1_200.0),
        _option(190.0, OptType.CALL, 0.28, 3_400.0),
        _option(190.0, OptType.PUT, 0.31, 2_100.0),
    ]
    _install_yahoo(monkeypatch, _FakeYahoo(chain))
    ctx = _ctx(tmp_path)

    subscription = _run(
        "collect",
        ctx,
        CollectParams(
            sources=("yahoo",),
            symbols=("AAPL",),
            channels=("options_chain",),
            duration_seconds=0.25,
        ),
    )
    asyncio.run(subscription.run())

    assert (tmp_path / "source=yahoo").is_dir(), "the shipped writer wrote no partition"

    surface = _run(
        "iv-surface",
        ctx,
        REGISTRY["iv-surface"].params(underlying="AAPL", at_ns=_2024_START + 2 * _DAY_NS),
    )
    assert len(surface) == 3, f"iv-surface read {len(surface)} rows out of a three-contract chain"
    assert set(surface["strike"].to_list()) == {180.0, 190.0}
    assert all(value is not None for value in surface["iv"].to_list())


def test_the_same_lake_answers_open_interest_for_equities(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``open-interest``'s equity half sums the chain's own per-contract figure.

    The capability whose defect is recorded twice in this codebase: bound to
    ``fn=open_interest`` it read a channel no equity provider wrote, and the fix repointed it
    at ``options_chain``, which no equity provider wrote either. This is the first test that
    could tell the difference, because it is the first one that does not write the chain
    itself.
    """
    chain = [
        _option(180.0, OptType.CALL, 0.30, 1_200.0),
        _option(190.0, OptType.CALL, 0.28, 3_400.0),
        _option(190.0, OptType.PUT, 0.31, 2_100.0),
    ]
    _install_yahoo(monkeypatch, _FakeYahoo(chain))
    ctx = _ctx(tmp_path)

    asyncio.run(
        _run(
            "collect",
            ctx,
            CollectParams(
                sources=("yahoo",),
                symbols=("AAPL",),
                channels=("options_chain",),
                duration_seconds=0.25,
            ),
        ).run()
    )

    board = _run("open-interest", ctx, REGISTRY["open-interest"].params(symbols=("AAPL",)))
    assert len(board) > 0, "open-interest returned an empty board over a chain that is in the lake"
    total = float(board["yahoo"].sum())
    assert total == pytest.approx(1_200.0 + 3_400.0 + 2_100.0)


# ---------------------------------------------------------------------------
# macro_series — written by `backfill`, read by the three carry capabilities.
# ---------------------------------------------------------------------------


def test_backfill_writes_the_treasury_curve_and_the_carry_finds_its_risk_free_leg(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``backfill --source treasury`` → lake → ``spot-future-basis`` with a financing leg.

    The referee's exact reading was ``rows=1  risk_free_rate=None  carry_pct=None
    prov_confidence=0.667``: a row that reported a carry it could not compute, under a
    confidence that did not notice. Both columns are populated here, and neither was
    reachable by any command this product shipped.

    The price leg is written by a shipped writer too — ``backfill --source yahoo --channel
    ohlcv`` — so nothing in this lake was placed by hand.
    """
    _install_treasury(monkeypatch)
    _install_yahoo(monkeypatch, _FakeYahoo([], bars=[_bar(190.0 + day, day) for day in (5, 6, 7)]))
    ctx = _ctx(tmp_path)

    written_bars = asyncio.run(
        _run(
            "backfill",
            ctx,
            BackfillParams(
                source="yahoo",
                channel="ohlcv",
                symbols=("AAPL",),
                start_ns=_2024_START,
                end_ns=_2024_END,
            ),
        )
    )
    assert written_bars == 3

    written_curve = asyncio.run(
        _run(
            "backfill",
            ctx,
            BackfillParams(
                source="treasury",
                channel="macro_series",
                symbols=("*",),
                start_ns=_2024_START,
                end_ns=_2024_END,
            ),
        )
    )
    assert written_curve > 0, "the shipped writer put no curve point in the lake"

    observed_from, observed_to = _observed_window()
    curve = _run(
        "catalog-scan",
        ctx,
        REGISTRY["catalog-scan"].params(
            channel="macro_series",
            symbols=("treasury:UST10Y",),
            start_ns=observed_from,
            end_ns=observed_to,
        ),
    )
    assert len(curve) > 0
    # 4.05 % on 2024-01-05, stored as the decimal fraction every rate in this schema is.
    assert max(curve["value"].to_list()) == pytest.approx(0.0405)

    rows = _run(
        "spot-future-basis",
        ctx,
        REGISTRY["spot-future-basis"].params(
            future_symbol="AAPL",
            spot_symbol="AAPL",
            start_ns=_2024_START,
            end_ns=_2024_END,
            expiry_ns=_2024_START + 90 * _DAY_NS,
        ),
    )
    assert len(rows) > 0, "spot-future-basis returned nothing over a lake with both legs in it"
    assert rows["risk_free_rate"].null_count() == 0, (
        "the risk-free leg is still null, which is the defect: no shipped path wrote "
        "macro_series and the capability reported a carry it could not compute"
    )
    assert rows["carry_pct"].null_count() == 0


# ---------------------------------------------------------------------------
# insider and holding_13f — written by `backfill`, read by the filing capabilities.
# ---------------------------------------------------------------------------


def test_backfill_writes_form_4_lines_that_whale_alerts_reads_back(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``backfill --source sec_edgar --channel insider`` → lake → ``whale-alerts``.

    ``whale-alerts``' equity half reads the ``insider`` channel and had no writer, so its
    answer was an empty frame with the right six columns — which is what the shape assertions
    in ``tests/capabilities/test_analytics.py`` were checking.
    """
    _install_sec_edgar(monkeypatch)
    ctx = _ctx(tmp_path)

    written = asyncio.run(
        _run(
            "backfill",
            ctx,
            BackfillParams(
                source="sec_edgar",
                channel="insider",
                symbols=("EXCO",),
                start_ns=_2024_START,
                end_ns=_2024_END,
            ),
        )
    )
    assert written > 0, "the shipped writer wrote no Form 4 line"

    alerts = _run(
        "whale-alerts",
        ctx,
        REGISTRY["whale-alerts"].params(
            symbol="EXCO",
            start_ns=_2024_START,
            end_ns=_2024_END,
            min_usd=1_000.0,
        ),
    )
    assert len(alerts) > 0, "whale-alerts is empty over a lake whose insider channel is filled"
    assert set(alerts["side"].to_list()) <= {"buy", "sell"}
    assert all(value > 0 for value in alerts["usd_value"].to_list())


def test_backfill_writes_a_13f_information_table_the_registry_reads_back(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``backfill --source sec_edgar --channel holding_13f`` → lake → ``catalog-scan``.

    ``holding_13f`` is the one of the four with no capability that reads it *from the lake*:
    ``smart-money``'s equity half differences information tables handed to it in ``params``,
    which is why the channel could stay unwritten without any capability visibly failing. It
    is read back through ``catalog-scan`` — a capability, resolved through the registry the
    same way as the other three — and then put through ``smart-money``, so the round trip
    ends where the rows are actually consumed.
    """
    _install_sec_edgar(monkeypatch)
    ctx = _ctx(tmp_path)

    written = asyncio.run(
        _run(
            "backfill",
            ctx,
            BackfillParams(
                source="sec_edgar",
                channel="holding_13f",
                symbols=("CIK0000320193",),
                start_ns=_2024_START,
                end_ns=_2024_END,
            ),
        )
    )
    assert written > 0, "the shipped writer wrote no 13F position"

    observed_from, observed_to = _observed_window()
    positions = _run(
        "catalog-scan",
        ctx,
        REGISTRY["catalog-scan"].params(
            channel="holding_13f",
            symbols=tuple(sorted(_scan_symbols(ctx))),
            start_ns=observed_from,
            end_ns=observed_to,
        ),
    )
    assert len(positions) > 0
    assert set(positions["manager_name"].to_list()) != {None}

    flows = _run(
        "smart-money",
        ctx,
        REGISTRY["smart-money"].params(
            transfers=tuple(positions.to_dicts()),
            watchlist=tuple({str(row["manager_cik"]) for row in positions.to_dicts()}),
        ),
    )
    # One quarter is a baseline and not a flow — `filing_flows` says so, and this asserts the
    # round trip reached it rather than that a single table produced a difference.
    assert isinstance(flows, list)


def _scan_symbols(ctx: CapabilityContext) -> list[str]:
    """The symbols the 13F writer actually minted, asked of the lake rather than assumed.

    A ``Holding13F``'s symbol is the position's CUSIP, not the filer's ticker, so a test that
    named one would be asserting against the fixture's contents twice over.
    """
    frame = _run("catalog-symbols", ctx, REGISTRY["catalog-symbols"].params(channel="holding_13f"))
    if hasattr(frame, "to_dicts"):
        return [str(row["symbol"]) for row in frame.to_dicts()]
    return [str(value) for value in frame]

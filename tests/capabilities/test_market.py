"""The market-structure batch: its adapters, and the claims its declarations make.

Nothing here touches a venue. Four of the six capabilities reach the network in production,
and a test that reached Yahoo or a ccxt REST endpoint would fail on a plane, fail in CI
behind a proxy, and pass or fail depending on what BTC did this morning — so every network
edge is replaced at the module boundary and the adapter's own argument shuffling, filtering
and framing is what gets exercised. ``open-interest`` is the exception that needs no
mocking at all: its input is the lake, so it gets a real one, written into ``tmp_path``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import msgspec
import polars as pl
import pytest

from crocodile.capabilities import market
from crocodile.core.capability import (
    IRREDUCIBLE,
    PENDING_SYMMETRY,
    REGISTRY,
    SPEC_METHODS,
    AssetClass,
    CapabilityContext,
    ReturnKind,
    run_to_completion,
)
from crocodile.core.config import Settings
from crocodile.core.schema.provenance import Provenance, level_for, provenance_fields
from crocodile.core.schema.records import DepthProfile, OpenInterest
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.core.util.time import now_ns
from crocodile.crypto.instruments.registry import Instrument, Kind

_BASE_NS = 1_704_067_200_000_000_000

_MARKET_CAPABILITIES = (
    "list-exchanges",
    "markets",
    "universe",
    "census",
    "open-interest",
    "depth",
)


def _ctx(catalog: Any = None, asset_class: AssetClass = AssetClass.CRYPTO) -> CapabilityContext:
    """A context for the capabilities that do not read the lake.

    ``catalog=None`` is honest for those: they never dereference it, and handing them a real
    ``Catalog`` would hide an adapter that started to.
    """
    return CapabilityContext(catalog=catalog, settings=Settings(), asset_class=asset_class)


def _instrument(symbol: str, kind: Kind, base: str, quote: str) -> Instrument:
    return Instrument(
        canonical=f"binance:{symbol}",
        exchange="binance",
        symbol_raw=symbol,
        kind=kind,
        base=base,
        quote=quote,
    )


# ---------------------------------------------------------------------------
# The one answer to "what does an async implementation mean for three surfaces"
# ---------------------------------------------------------------------------


def test_a_coroutine_runs_to_a_value_when_the_caller_has_no_event_loop() -> None:
    """The CLI projection's branch: a synchronous caller gets a value, not a coroutine.

    Tested from here rather than from ``tests/conformance`` because this batch is where the
    helper was written and where its four call sites are. It lives in
    ``core/capability.py`` now, moved when a second batch wrote a bare ``asyncio.run``
    instead of finding it.
    """

    async def _answer() -> str:
        await asyncio.sleep(0)
        return "done"

    assert run_to_completion(_answer) == "done"


async def test_a_coroutine_still_runs_when_the_caller_is_already_on_an_event_loop() -> None:
    """The REST and MCP branch, which a bare ``asyncio.run`` would turn into a RuntimeError.

    This is the whole reason the helper exists: one declaration has to be callable from a
    synchronous CLI and from inside a running loop, and the failure it prevents is a
    capability that works on one surface and raises on another.
    """

    async def _answer() -> str:
        await asyncio.sleep(0)
        return "done"

    assert asyncio.get_running_loop() is not None
    assert run_to_completion(_answer) == "done"


async def test_a_failure_inside_the_coroutine_reaches_a_caller_that_is_on_a_loop() -> None:
    """A worker thread must not turn an error into a swallowed one."""

    async def _boom() -> None:
        raise ValueError("venue said no")

    with pytest.raises(ValueError, match="venue said no"):
        run_to_completion(_boom)


# ---------------------------------------------------------------------------
# list-exchanges
# ---------------------------------------------------------------------------


def test_list_exchanges_returns_the_crypto_connector_registry() -> None:
    from crocodile.crypto.exchanges.factory import list_exchanges as registry

    assert market.list_exchanges(_ctx(), market.ListExchangesParams()) == registry()


def test_list_providers_returns_the_equity_provider_registry() -> None:
    from crocodile.equity.providers.factory import list_providers as registry

    assert market.list_providers(_ctx(), market.ListExchangesParams()) == registry()


def test_list_exchanges_is_symmetric_today_rather_than_scheduled() -> None:
    """Both markets can already answer "which sources can this build pull from".

    The merge collapsed ``exchange=`` and ``provider=`` into one ``source=`` partition, so
    the two registries are two halves of one list rather than two different questions —
    which is why this capability needs no entry on either ledger.
    """
    cap = REGISTRY["list-exchanges"]
    assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    assert "list-exchanges" not in PENDING_SYMMETRY
    assert "list-exchanges" not in IRREDUCIBLE


def test_list_exchanges_takes_no_parameters_because_no_surface_offered_any() -> None:
    assert msgspec.structs.fields(market.ListExchangesParams) == ()


# ---------------------------------------------------------------------------
# markets
# ---------------------------------------------------------------------------


@pytest.fixture
def _two_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A venue universe with one native-only, one ccxt-only and one shadowed name."""
    monkeypatch.setattr(market.factory, "list_exchanges", lambda: ["binance", "base_onchain"])
    monkeypatch.setattr(market.factory, "list_ccxt_exchanges", lambda: ["binance", "kraken"])
    monkeypatch.setattr(
        market.factory, "list_all_exchanges", lambda: ["base_onchain", "binance", "kraken"]
    )


def test_markets_tags_a_venue_served_by_both_tiers_as_both(_two_tiers: None) -> None:
    """One row per venue, not two: a name in both routes to the native connector."""
    rows = market.markets(_ctx(), market.MarketsParams()).to_dicts()
    assert rows == [
        {"venue": "base_onchain", "native": True, "ccxt": False},
        {"venue": "binance", "native": True, "ccxt": True},
        {"venue": "kraken", "native": False, "ccxt": True},
    ]


def test_markets_filters_by_substring_case_insensitively(_two_tiers: None) -> None:
    frame = market.markets(_ctx(), market.MarketsParams(search="BIN"))
    assert frame["venue"].to_list() == ["binance"]


def test_markets_with_both_tier_flags_returns_the_overlap_rather_than_nothing(
    _two_tiers: None,
) -> None:
    """The CLI printed nothing for this combination, which was a rendering artefact.

    Two independent ``if`` blocks each skipped their own section, so asking for both tiers
    silently produced no output at all. Two filters over one row set compose, and the
    composition is the set of venues that really are both.
    """
    frame = market.markets(_ctx(), market.MarketsParams(native_only=True, ccxt_only=True))
    assert frame["venue"].to_list() == ["binance"]


def test_markets_reports_only_native_venues_when_the_ccxt_extra_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing optional extra is "no extra venues", never an error."""
    monkeypatch.setattr(market.factory, "list_ccxt_exchanges", lambda: [])
    monkeypatch.setattr(market.factory, "list_exchanges", lambda: ["binance"])
    monkeypatch.setattr(market.factory, "list_all_exchanges", lambda: ["binance"])
    assert market.markets(_ctx(), market.MarketsParams()).to_dicts() == [
        {"venue": "binance", "native": True, "ccxt": False}
    ]


def test_markets_returns_a_table_with_its_columns_when_nothing_matches(_two_tiers: None) -> None:
    """An empty result is still a table; a caller selecting a column must not get a KeyError."""
    frame = market.markets(_ctx(), market.MarketsParams(search="no-such-venue"))
    assert frame.height == 0
    assert frame.columns == ["venue", "native", "ccxt"]


# ---------------------------------------------------------------------------
# universe
# ---------------------------------------------------------------------------


@pytest.fixture
def _venue_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for ``load_markets`` — the one network call the enumerating branch makes."""
    instruments = [
        _instrument("BTCUSDT", Kind.SPOT, "BTC", "USDT"),
        _instrument("ETHUSDT", Kind.SPOT, "ETH", "USDT"),
        _instrument("BTCUSD_PERP", Kind.PERPETUAL, "BTC", "USD"),
    ]

    async def _fake(exchange: str, **_: Any) -> list[Instrument]:
        assert exchange == "binance"
        return instruments

    monkeypatch.setattr(market, "exchange_instruments", _fake)


def test_universe_enumerates_a_venue_into_one_row_per_instrument(_venue_instruments: None) -> None:
    rows = market.universe(_ctx(), market.UniverseParams(source="binance")).to_dicts()
    assert rows == [
        {"symbol": "BTCUSDT", "kind": "spot", "base": "BTC", "quote": "USDT", "rank": None},
        {"symbol": "ETHUSDT", "kind": "spot", "base": "ETH", "quote": "USDT", "rank": None},
        {"symbol": "BTCUSD_PERP", "kind": "perpetual", "base": "BTC", "quote": "USD", "rank": None},
    ]


def test_universe_applies_the_kind_and_quote_filters(_venue_instruments: None) -> None:
    frame = market.universe(
        _ctx(), market.UniverseParams(source="binance", kinds=("spot",), quote="usdt")
    )
    assert frame["symbol"].to_list() == ["BTCUSDT", "ETHUSDT"]


def test_universe_caps_the_enumerated_rows_at_the_limit(_venue_instruments: None) -> None:
    frame = market.universe(_ctx(), market.UniverseParams(source="binance", limit=1))
    assert frame["symbol"].to_list() == ["BTCUSDT"]


def test_universe_does_not_filter_by_quote_when_none_was_asked_for(
    _venue_instruments: None,
) -> None:
    """``top_symbols_by_volume`` defaults ``quote`` to USDT; the capability must not inherit it.

    An unrequested filter that silently empties a USD-quoted venue is worse than no filter,
    and the CLI passed its own ``None`` straight through for exactly that reason.
    """
    frame = market.universe(_ctx(), market.UniverseParams(source="binance"))
    assert "USD" in frame["quote"].to_list()


def test_universe_ranks_by_volume_when_top_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(exchange: str, n: int, **kwargs: Any) -> list[str]:
        assert (exchange, n) == ("binance", 2)
        assert kwargs == {"quote": "USDT", "kinds": {Kind.SPOT}}
        return ["BTC/USDT", "ETH/USDT"]

    monkeypatch.setattr(market, "top_symbols_by_volume", _fake)
    rows = market.universe(
        _ctx(),
        market.UniverseParams(source="binance", top=2, quote="USDT", kinds=("spot",)),
    ).to_dicts()
    assert [(row["symbol"], row["rank"]) for row in rows] == [("BTC/USDT", 1), ("ETH/USDT", 2)]


def test_universe_returns_the_same_columns_from_either_branch(
    monkeypatch: pytest.MonkeyPatch, _venue_instruments: None
) -> None:
    """Two column sets under one capability would be unrenderable by one projection.

    The ranked branch reads ``fetch_tickers`` and sees no instrument metadata; the
    enumerating branch reads ``load_markets`` and sees no ranking. A null says "this path
    does not observe it", and the schema stays one schema.
    """

    async def _fake(exchange: str, n: int, **_: Any) -> list[str]:
        return ["BTC/USDT"]

    monkeypatch.setattr(market, "top_symbols_by_volume", _fake)
    enumerated = market.universe(_ctx(), market.UniverseParams(source="binance"))
    ranked = market.universe(_ctx(), market.UniverseParams(source="binance", top=1))
    assert enumerated.columns == ranked.columns
    assert enumerated.schema == ranked.schema
    assert enumerated["rank"].null_count() == enumerated.height
    assert ranked["kind"].null_count() == ranked.height


def test_universe_rejects_an_unknown_kind_instead_of_matching_nothing() -> None:
    """A typo must not read as a venue with no perpetuals."""
    with pytest.raises(ValueError, match="unknown instrument kind"):
        market.universe(_ctx(), market.UniverseParams(source="binance", kinds=("perp",)))


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------


def test_census_passes_the_venue_list_and_page_count_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _fake(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"venues": {"enumerated": 2}}

    monkeypatch.setattr(market.census_mod, "market_census", _fake)
    snapshot = market.census(
        _ctx(), market.CensusParams(venues=("binance", "okx"), coin_pages=3)
    )
    assert snapshot == {"venues": {"enumerated": 2}}
    assert seen["venues"] == ["binance", "okx"]
    assert seen["coin_pages"] == 3


def test_census_asks_for_the_curated_majors_when_no_venue_was_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` and ``[]`` mean opposite things to ``market_census``: defaults, or no venues."""
    seen: dict[str, Any] = {}

    async def _fake(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(market.census_mod, "market_census", _fake)
    market.census(_ctx(), market.CensusParams())
    assert seen["venues"] is None


def test_census_stamps_the_snapshot_from_the_clock_rather_than_from_a_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generated_ns`` keeps ``market_census`` deterministic; it is not a user's choice."""
    seen: dict[str, Any] = {}

    async def _fake(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(market.census_mod, "market_census", _fake)
    monkeypatch.setattr(market, "now_ns", lambda: _BASE_NS)
    market.census(_ctx(), market.CensusParams())
    assert seen["generated_ns"] == _BASE_NS
    assert "generated_ns" not in {f.name for f in msgspec.structs.fields(market.CensusParams)}


# ---------------------------------------------------------------------------
# open-interest — the one capability whose input is the lake, so it gets a real one
# ---------------------------------------------------------------------------


def _oi(ts: int, source: str, symbol: str, value: float) -> OpenInterest:
    return OpenInterest(
        source=source,
        symbol=symbol,
        symbol_raw=symbol.split(":")[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        open_interest=value,
    )


@pytest.fixture
def _lake(tmp_path: Path) -> Catalog:
    """Two venues and two underlyings, so a substring filter has something to be wrong about."""

    async def _write() -> None:
        sink = ParquetSink(tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
        for record in (
            _oi(_BASE_NS + 1, "binance", "binance:BTCUSDT", 100.0),
            _oi(_BASE_NS + 1, "okx", "okx:BTC-USDT-SWAP", 50.0),
            _oi(_BASE_NS + 2, "binance", "binance:ETHUSDT", 7.0),
        ):
            await sink.put(record)
        await sink.flush()

    asyncio.run(_write())
    return Catalog(tmp_path)


def test_open_interest_aligns_the_venues_it_finds(_lake: Catalog) -> None:
    frame = market.open_interest(_ctx(_lake), market.OpenInterestParams())
    assert {"local_ts", "binance", "okx", "total_oi"} <= set(frame.columns)
    assert frame.height == 2


def test_open_interest_treats_each_entry_as_a_substring_pattern(_lake: Catalog) -> None:
    """The semantic the implementation has always had, asserted against a symbol it is not.

    ``BTC`` is not a symbol in this lake — ``binance:BTCUSDT`` and ``okx:BTC-USDT-SWAP``
    are — so a list-of-identities reading would return nothing here.
    """
    frame = market.open_interest(_ctx(_lake), market.OpenInterestParams(symbols=("BTC",)))
    assert frame["total_oi"].to_list() == [150.0]


def test_open_interest_ors_several_patterns_together(_lake: Catalog) -> None:
    """The reading REST could never deliver: its comma string went through as one pattern.

    ``?symbols=BTCUSDT,ETHUSDT`` matched no symbol at all and returned an empty board that
    reads exactly like an empty lake.
    """
    both = market.open_interest(
        _ctx(_lake), market.OpenInterestParams(symbols=("BTCUSDT", "ETHUSDT"))
    )
    assert both["total_oi"].to_list()[-1] == 107.0

    unsplit = market.open_interest(
        _ctx(_lake), market.OpenInterestParams(symbols=("BTCUSDT,ETHUSDT",))
    )
    assert unsplit.height == 0


def test_open_interest_with_no_pattern_covers_every_symbol(_lake: Catalog) -> None:
    assert market.open_interest(_ctx(_lake), market.OpenInterestParams())[
        "total_oi"
    ].to_list() == [150.0, 157.0]


def test_the_default_range_covers_the_whole_lake_rather_than_none_of_it(_lake: Catalog) -> None:
    """REST defaulted ``end`` to 0, which told every caller the market has no open interest."""
    defaulted = market.open_interest(_ctx(_lake), market.OpenInterestParams())
    explicit = market.open_interest(
        _ctx(_lake), market.OpenInterestParams(start_ns=0, end_ns=_BASE_NS + 10)
    )
    assert defaulted.height == explicit.height == 2


def test_the_end_of_time_default_is_representable_as_a_signed_64_bit_timestamp() -> None:
    """The CLI's 9999999999999999999 is not, and only worked because DuckDB widened it."""
    assert market._END_OF_TIME == 2**63 - 1
    assert market.OpenInterestParams().end_ns == market._END_OF_TIME


def test_open_interest_takes_one_plural_field_where_three_surfaces_disagreed() -> None:
    """CLI ``--symbol``, REST ``symbols``, MCP ``str | list[str]`` — one struct, one meaning."""
    fields = {f.name: f for f in msgspec.structs.fields(REGISTRY["open-interest"].params)}
    assert set(fields) == {"symbols", "start_ns", "end_ns"}
    assert fields["symbols"].default == ()


# ---------------------------------------------------------------------------
# depth
# ---------------------------------------------------------------------------


class _StubDepthSource:
    """A depth source that records how it was built and never leaves the process."""

    def __init__(self, profile: DepthProfile) -> None:
        self.profile = profile
        self.asked: list[str] = []

    async def snapshot(self, symbol: str) -> DepthProfile:
        self.asked.append(symbol)
        return self.profile


def _a_profile(basis: str, inputs: dict[str, Any]) -> DepthProfile:
    tail = provenance_fields(basis, inputs)
    return DepthProfile(
        source="synth",
        symbol="synth:AAPL",
        symbol_raw="AAPL",
        local_ts=_BASE_NS,
        asset_class=AssetClass.EQUITY,
        source_ts=None,
        bids=[(99.0, 10.0)],
        asks=[(101.0, 10.0)],
        reference_price=100.0,
        depth=2,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
    )


def test_depth_forwards_the_ladder_shape_to_the_source_and_the_symbol_to_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _StubDepthSource(_a_profile("yahoo_1m_vap", {"n_volume_bars": 390}))
    built: dict[str, Any] = {}

    def _select(**kwargs: Any) -> _StubDepthSource:
        built.update(kwargs)
        return source

    monkeypatch.setattr(market, "select_depth_source", _select)
    profile = market.depth(
        _ctx(asset_class=AssetClass.EQUITY),
        market.DepthParams(symbol="AAPL", method="typical", bins=12, top_n=3),
    )
    assert built == {"bins": 12, "top_n": 3, "method": "typical"}
    assert source.asked == ["AAPL"]
    assert profile is source.profile


def test_depth_declares_the_keyed_ceiling_rather_than_todays_environment() -> None:
    """``prov`` is a maximum, and which branch actually ran is on the record, not the ledger.

    ``select_depth_source`` returns Alpaca L1 when two keys are set and a modelled Yahoo VAP
    ladder otherwise, so a declaration that read the environment would say different things
    on two machines running the same build. The record each branch emits carries its own
    measured tail, which is where the difference belongs.
    """
    impl = REGISTRY["depth"].impls[AssetClass.EQUITY]
    assert impl.basis == "alpaca_l1"
    assert impl.prov is Provenance.DERIVED
    assert level_for("alpaca_l1") is Provenance.DERIVED

    keyless = _a_profile("yahoo_1m_vap", {"n_volume_bars": 390})
    assert keyless.prov is Provenance.SYNTHETIC
    assert keyless.is_synthetic

    keyed = _a_profile("alpaca_l1", {"n_quoted_sides": 2})
    assert keyed.prov is Provenance.DERIVED
    assert not keyed.is_synthetic


def test_depth_was_the_capability_whose_missing_half_was_the_crypto_one() -> None:
    """The batch's one backwards gap, closed — and asserted as closed rather than described.

    Every entry in ``SPEC_METHODS`` closes an *equity* gap, so while ``depth`` was
    asymmetric it was scheduled against M6, the only method that named depth at all, and M6
    describes the equity half that already shipped. That mismatch is what M8 was written
    for. The remaining four here still run the usual direction, which is what makes ``depth``
    worth a test of its own rather than a line in the parametrised sweep: it is the one whose
    two implementations are asymmetric in *kind*, one reaching a vendor and one reading the
    lake, and asserting they are both present is the cheapest way to notice if either leaves.
    """
    assert set(REGISTRY["depth"].impls) == {AssetClass.EQUITY, AssetClass.CRYPTO}
    assert "depth" not in PENDING_SYMMETRY
    for name in ("markets", "universe", "census", "open-interest"):
        assert set(REGISTRY[name].impls) == {AssetClass.CRYPTO}


def _book_frame(*, local_ts: int) -> pl.DataFrame:
    """One stored ``book_snapshot`` row, in the shape a lake read hands back."""
    return pl.DataFrame(
        {
            "source": ["deribit"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "symbol_raw": ["BTC-PERPETUAL"],
            "local_ts": [local_ts],
            "source_ts": [None],
            "bids": [[{"price": 99.0, "amount": 5.0}, {"price": 98.0, "amount": 4.0}]],
            "asks": [[{"price": 101.0, "amount": 5.0}, {"price": 102.0, "amount": 4.0}]],
        },
        schema_overrides={"source_ts": pl.Int64},
    )


class _RecordingCatalog:
    """A lake that answers one book row and remembers how it was asked.

    Enough of a ``Catalog`` for :meth:`CapabilityContext.query`, and nothing else — an
    adapter that reached for ``scan``, ``refresh_views`` or ``connection`` raises here rather
    than quietly working, which is the point.
    """

    def __init__(self, frame: pl.DataFrame) -> None:
        self.frame = frame
        self.seen: list[tuple[str, bool]] = []

    def query(self, sql: str, *, readonly: bool = False) -> pl.DataFrame:
        self.seen.append((sql, readonly))
        return self.frame


def test_the_crypto_half_reads_the_lake_under_the_surfaces_policy_not_around_it() -> None:
    """``ctx.query`` applies ``readonly`` and ``row_limit``; ``ctx.catalog.query`` does not.

    The assertion is the wrapper, not the call count: ``CapabilityContext.query`` is the only
    thing in the tree that wraps a statement in ``SELECT * FROM (…) LIMIT n`` and forwards
    ``readonly``, so seeing both at the catalog proves the adapter went through it. Reaching
    the catalog directly compiles and runs and ignores both fields, which is how the crypto
    CLI came to have no SQL guard while REST and MCP each grew their own.
    """
    catalog = _RecordingCatalog(_book_frame(local_ts=_BASE_NS))
    ctx = CapabilityContext(
        catalog=catalog,
        settings=Settings(),
        asset_class=AssetClass.CRYPTO,
        readonly=True,
        row_limit=5,
    )
    profile = market.depth_crypto(
        ctx, market.DepthParams(symbol="deribit:BTC-PERPETUAL", as_of_ns=_BASE_NS, top_n=2)
    )
    assert profile.reference_price == 100.0
    assert profile.asset_class is AssetClass.CRYPTO

    sql, readonly = catalog.seen[0]
    assert readonly is True
    assert sql.startswith("SELECT * FROM (") and sql.endswith("LIMIT 5")
    assert "book_snapshot" in sql


def test_the_crypto_half_defaults_its_instant_to_the_moment_it_is_called() -> None:
    """``None`` means "when you are asked", and a struct default cannot be a call.

    A default evaluated at import time would freeze the instant at process start, so a
    long-running REST process would answer every unqualified depth request against the book
    as it stood when the server booted.
    """
    catalog = _RecordingCatalog(_book_frame(local_ts=now_ns()))
    ctx = CapabilityContext(catalog=catalog, settings=Settings(), asset_class=AssetClass.CRYPTO)

    before = now_ns()
    market.depth_crypto(ctx, market.DepthParams(symbol="deribit:BTC-PERPETUAL"))
    after = now_ns()

    bound = int(catalog.seen[0][0].split("local_ts <= ")[1].split(" ")[0])
    assert before <= bound <= after


def test_the_two_halves_share_one_parameter_struct() -> None:
    """Symmetry is the same name *and* the same schema; two structs would drift."""
    cap = REGISTRY["depth"]
    assert cap.params is market.DepthParams
    fields = {f.name for f in msgspec.structs.fields(cap.params)}
    assert fields == {"symbol", "method", "bins", "top_n", "as_of_ns", "max_age_ns"}


# ---------------------------------------------------------------------------
# The declarations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _MARKET_CAPABILITIES)
def test_every_market_capability_is_declared_with_a_summary_and_a_params_struct(
    name: str,
) -> None:
    cap = REGISTRY[name]
    assert cap.summary.strip().endswith("."), f"{name} has no one-line summary"
    assert issubclass(cap.params, msgspec.Struct)
    assert cap.returns in (ReturnKind.TABLE, ReturnKind.SCALAR)


@pytest.mark.parametrize("name", _MARKET_CAPABILITIES)
def test_every_implementation_is_a_named_module_level_adapter(name: str) -> None:
    """A stack trace and the calling-convention gate both need a file and a line number."""
    for impl in REGISTRY[name].impls.values():
        assert impl.fn is getattr(market, impl.fn.__name__)
        assert impl.fn.__module__ == market.__name__


@pytest.mark.parametrize("name", _MARKET_CAPABILITIES)
def test_every_declared_basis_carries_a_registered_confidence_formula(name: str) -> None:
    """``level_for`` raises for an unregistered basis, which is the assertion."""
    for impl in REGISTRY[name].impls.values():
        assert isinstance(level_for(impl.basis), Provenance)


def test_the_ledger_schedules_every_asymmetric_capability_in_this_batch() -> None:
    scheduled = {
        name: PENDING_SYMMETRY[name] for name in _MARKET_CAPABILITIES if name in PENDING_SYMMETRY
    }
    assert scheduled == {
        "markets": "M3",
        "universe": "M3",
        "census": "M3",
        "open-interest": "M2",
    }
    for method in scheduled.values():
        assert method in SPEC_METHODS
    for name in _MARKET_CAPABILITIES:
        symmetric = set(REGISTRY[name].impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
        assert symmetric != (name in scheduled), f"{name} is scheduled and symmetric, or neither"


def test_no_capability_in_this_batch_claims_irreducibility() -> None:
    """None of these is a property of the market; every one of them is a schedule."""
    assert not set(_MARKET_CAPABILITIES) & set(IRREDUCIBLE)


def test_the_open_interest_frame_is_a_table_and_the_census_snapshot_is_not() -> None:
    """``ReturnKind`` is how a surface decides between rows and one object."""
    assert REGISTRY["open-interest"].returns is ReturnKind.TABLE
    assert REGISTRY["census"].returns is ReturnKind.SCALAR
    assert REGISTRY["depth"].returns is ReturnKind.SCALAR


def test_a_table_capability_returns_a_polars_frame_so_a_surface_can_page_it() -> None:
    """``ReturnKind.TABLE`` is a promise about the shape, and one frame type keeps it."""
    assert isinstance(
        market.markets(_ctx(), market.MarketsParams(search="no-such-venue")), pl.DataFrame
    )
    assert isinstance(market.list_exchanges(_ctx(), market.ListExchangesParams()), list)

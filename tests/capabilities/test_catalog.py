"""The lake-and-discovery batch, driven against a real lake on disk.

Every capability in ``crocodile.capabilities.catalog`` is exercised through its declared
adapter, against Parquet written by :class:`~crocodile.core.store.parquet_sink.ParquetSink`
into ``tmp_path`` — not against a stubbed catalog. A stub would agree with whatever the
adapter did, and half of what these capabilities answer is a property of the *filesystem*
(a partition directory with no parquet parts in it, two source prefixes in one root) that
no stub reproduces.

Three things beyond "does it return rows".

**Parity with the client.** Three adapters reproduce logic that lives in
``CrypcodileClient`` rather than delegating to it, because the context supplies a
``Catalog`` and the client insists on owning a ``data_dir``. Reproduced logic is how a fork
starts, so the reproduction is pinned: the tests below assert the adapter returns what the
client returns, on the same lake, including for the ambiguous and unmatched inputs where an
"equivalent" rewrite is most likely to differ.

**The SQL policy.** ``query`` shipped three of them. The tests here drive the same
capability through three contexts and assert the policy tracks the *surface*, since that
is the whole of what moving it into :class:`~crocodile.core.capability.CapabilityContext`
bought.

**Symmetry, executed rather than declared.** Gate 2 checks that an equity implementation
exists. These call the equity one against equity rows in the same lake, which is the part a
registry cannot check.
"""

from __future__ import annotations

import pathlib

import polars as pl
import pytest

from crocodile.capabilities import catalog as batch
from crocodile.core.capability import REGISTRY, AssetClass, CapabilityContext
from crocodile.core.config import Settings
from crocodile.core.schema.enums import Side
from crocodile.core.schema.records import OHLCV, BookSnapshot, Trade
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.client.client import CrypcodileClient

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14T22:13:20Z
_SECOND = 1_000_000_000

_BTC = "deribit:BTC-PERPETUAL"
_ETH = "deribit:ETH-PERPETUAL"
_BINANCE_BTC = "binance-spot:BTC-USDT"
_AAPL = "AAPL"


def _trade(symbol: str, source: str, price: float, local_ts: int) -> Trade:
    return Trade(
        source=source,
        symbol=symbol,
        symbol_raw=symbol.rsplit(":", 1)[-1],
        source_ts=local_ts,
        local_ts=local_ts,
        asset_class=AssetClass.CRYPTO,
        id=f"{symbol}-{price}",
        price=price,
        amount=1.5,
        side=Side.BUY,
    )


def _snapshot(local_ts: int) -> BookSnapshot:
    return BookSnapshot(
        source="deribit",
        symbol=_BTC,
        symbol_raw="BTC-PERPETUAL",
        source_ts=local_ts,
        local_ts=local_ts,
        asset_class=AssetClass.CRYPTO,
        bids=[(100.0, 5.0)],
        asks=[(101.0, 4.0)],
        depth=1,
        sequence_id=1,
        is_snapshot=True,
    )


def _bar(local_ts: int, close: float) -> OHLCV:
    """An equity bar, so the same lake answers for both asset classes."""
    return OHLCV(
        source="yahoo",
        symbol=_AAPL,
        symbol_raw=_AAPL,
        source_ts=local_ts,
        local_ts=local_ts,
        asset_class=AssetClass.EQUITY,
        interval="1m",
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=1_000.0,
    )


@pytest.fixture
async def lake(tmp_path: pathlib.Path) -> pathlib.Path:
    """A lake holding both asset classes, two exchanges, and one empty partition.

    The empty ``channel=funding`` directory is not decoration: it is what a collector that
    started and wrote nothing looks like, and it is the case ``catalog`` and
    ``catalog-stats`` report as ``0`` while a view-backed listing cannot report at all.
    """
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1000, flush_interval_seconds=9999)
    await sink.put(_trade(_BTC, "deribit", 100.0, _BASE_TS))
    await sink.put(_trade(_BTC, "deribit", 101.0, _BASE_TS + _SECOND))
    await sink.put(_trade(_BTC, "deribit", 102.0, _BASE_TS + 2 * _SECOND))
    await sink.put(_trade(_ETH, "deribit", 20.0, _BASE_TS + 3 * _SECOND))
    await sink.put(_trade(_BINANCE_BTC, "binance-spot", 103.0, _BASE_TS + 4 * _SECOND))
    await sink.put(_snapshot(_BASE_TS))
    await sink.put(_bar(_BASE_TS, 190.0))
    await sink.put(_bar(_BASE_TS + 60 * _SECOND, 191.0))
    await sink.flush()
    (tmp_path / "source=deribit" / "channel=funding").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _ctx(
    lake_dir: pathlib.Path,
    *,
    asset_class: AssetClass = AssetClass.CRYPTO,
    readonly: bool = False,
    row_limit: int | None = None,
) -> CapabilityContext:
    return CapabilityContext(
        catalog=Catalog(lake_dir),
        settings=Settings(data_dir=lake_dir),
        asset_class=asset_class,
        readonly=readonly,
        row_limit=row_limit,
    )


# ---------------------------------------------------------------------------
# query — and the policy that used to live in three places
# ---------------------------------------------------------------------------


async def test_query_runs_sql_against_the_lake(lake: pathlib.Path) -> None:
    df = batch.query(_ctx(lake), batch.QueryParams(sql="SELECT count(*) AS n FROM trade"))
    assert df["n"][0] == 5


async def test_query_on_a_trusting_surface_still_allows_what_the_cli_allowed(
    lake: pathlib.Path,
) -> None:
    """The local CLI reached ``Catalog.query`` with no guard, and keeps that.

    ``assert_readonly_sql`` is keyword-level and over-rejects — ``SELECT 'delete me'`` trips
    it — so making the guard unconditional would have broken working local queries. The
    context defaults to trusting, which is why the guard has to be something a surface opts
    into rather than something a parameter carries.
    """
    df = batch.query(_ctx(lake), batch.QueryParams(sql="SELECT 'delete me' AS phrase"))
    assert df["phrase"][0] == "delete me"


async def test_query_on_a_readonly_surface_rejects_mutating_sql(lake: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="Only SELECT"):
        batch.query(
            _ctx(lake, readonly=True),
            batch.QueryParams(sql="DROP TABLE trade"),
        )


async def test_query_on_a_readonly_surface_rejects_reading_a_file_the_caller_named(
    lake: pathlib.Path,
) -> None:
    """The half a mutating-keyword scan misses: a ``SELECT`` that exfiltrates.

    The crypto REST route rejected 19 mutating keywords and nothing else, so a read of an
    absolute path — which is a ``SELECT``, and mutates nothing — went straight through.
    """
    with pytest.raises(ValueError, match="external readers"):
        batch.query(
            _ctx(lake, readonly=True),
            batch.QueryParams(sql="SELECT * FROM read_parquet('/etc/hosts')"),
        )


async def test_query_on_a_readonly_surface_rejects_a_second_statement(
    lake: pathlib.Path,
) -> None:
    with pytest.raises(ValueError, match="Multi-statement"):
        batch.query(
            _ctx(lake, readonly=True),
            batch.QueryParams(sql="SELECT 1; SELECT 2"),
        )


async def test_query_honours_the_surfaces_row_limit_without_a_parameter(
    lake: pathlib.Path,
) -> None:
    """The REST ``limit`` became ``row_limit`` on the context, and this is the proof.

    No field on ``QueryParams`` can raise this ceiling, which is the difference between a
    cap the surface imposes and one the caller requests.
    """
    df = batch.query(_ctx(lake, row_limit=2), batch.QueryParams(sql="SELECT * FROM trade"))
    assert len(df) == 2
    assert batch.QueryParams.__struct_fields__ == ("sql",)


async def test_query_reaches_the_lake_through_the_context_not_the_catalog(
    lake: pathlib.Path,
) -> None:
    """``ctx.catalog.query`` would compile and run while ignoring both policy fields.

    Asserted by behaviour rather than by reading the source: a readonly context must refuse
    what the catalog would happily execute, and both halves are driven here so the test
    fails if the adapter is switched to the direct call.
    """
    ctx = _ctx(lake, readonly=True)
    assert len(ctx.catalog.query("SELECT * FROM trade")) == 5
    with pytest.raises(ValueError):
        batch.query(ctx, batch.QueryParams(sql="PRAGMA database_list"))


# ---------------------------------------------------------------------------
# catalog / catalog-stats / catalog-summary
# ---------------------------------------------------------------------------


async def test_catalog_counts_every_channel_including_the_empty_one(
    lake: pathlib.Path,
) -> None:
    df = batch.catalog(_ctx(lake), batch.NoParams())
    counts = dict(zip(df["channel"].to_list(), df["row_count"].to_list(), strict=True))
    assert counts["trade"] == 5
    assert counts["book_snapshot"] == 1
    assert counts["ohlcv"] == 2
    assert counts["funding"] == 0, "a partition directory with no parts has zero rows, not none"


async def test_catalog_on_an_empty_lake_is_an_empty_table_with_the_columns(
    tmp_path: pathlib.Path,
) -> None:
    """An empty lake must still answer with a shaped table, or a surface cannot render it."""
    df = batch.catalog(_ctx(tmp_path), batch.NoParams())
    assert len(df) == 0
    assert df.columns == ["channel", "row_count"]
    assert df.schema["row_count"] == pl.Int64


async def test_catalog_stats_reports_the_same_numbers_as_catalog(lake: pathlib.Path) -> None:
    """The two disagreed about the same lake until both projected ``channel_row_counts``."""
    table = batch.catalog(_ctx(lake), batch.NoParams())
    stats = batch.catalog_stats(_ctx(lake), batch.NoParams())
    assert stats["row_counts"] == dict(
        zip(table["channel"].to_list(), table["row_count"].to_list(), strict=True)
    )
    assert stats["channel_count"] == len(table)


async def test_catalog_stats_matches_the_client(lake: pathlib.Path) -> None:
    assert batch.catalog_stats(_ctx(lake), batch.NoParams()) == CrypcodileClient(
        data_dir=lake
    ).catalog_stats()


async def test_catalog_summary_matches_the_client(lake: pathlib.Path) -> None:
    """The adapter reproduces ``CrypcodileClient.catalog_summary``; this is the diff."""
    assert batch.catalog_summary(_ctx(lake), batch.NoParams()) == CrypcodileClient(
        data_dir=lake
    ).catalog_summary()


async def test_catalog_summary_separates_on_disk_exchanges_from_channels(
    lake: pathlib.Path,
) -> None:
    summary = batch.catalog_summary(_ctx(lake), batch.NoParams())
    assert summary["exchanges_on_disk"] == ["binance-spot", "deribit", "yahoo"]
    assert summary["exchange_count"] == 3
    assert summary["channel_count"] == len(summary["channels"])


# ---------------------------------------------------------------------------
# catalog-channels / catalog-dates / catalog-exchanges
# ---------------------------------------------------------------------------


async def test_catalog_channels_lists_a_directory_with_no_parquet_parts(
    lake: pathlib.Path,
) -> None:
    channels = batch.catalog_channels(_ctx(lake), batch.NoParams())
    assert channels == sorted(channels)
    assert {"trade", "book_snapshot", "ohlcv", "funding"} <= set(channels)


async def test_catalog_channels_matches_the_client(lake: pathlib.Path) -> None:
    assert batch.catalog_channels(_ctx(lake), batch.NoParams()) == CrypcodileClient(
        data_dir=lake
    ).list_channels()


async def test_catalog_dates_lists_the_partitions_a_scan_would_read(
    lake: pathlib.Path,
) -> None:
    dates = batch.catalog_dates(_ctx(lake), batch.ChannelParams(channel="trade"))
    assert dates == ["2023-11-14"]


async def test_catalog_dates_for_an_unknown_channel_is_empty(lake: pathlib.Path) -> None:
    assert batch.catalog_dates(_ctx(lake), batch.ChannelParams(channel="no-such-channel")) == []


async def test_catalog_dates_requires_a_channel_rather_than_defaulting_to_blank() -> None:
    """A blank default answers a caller who forgot the argument with an empty list.

    That is indistinguishable from a channel with no data, which is why the CLI made the
    option required and the MCP tool listed it in ``required``; only REST defaulted it.
    """
    with pytest.raises(TypeError):
        batch.ChannelParams()  # type: ignore[call-arg]


async def test_catalog_exchanges_lists_hive_partitions_not_connectors(
    lake: pathlib.Path,
) -> None:
    assert batch.catalog_exchanges(_ctx(lake), batch.NoParams()) == [
        "binance-spot",
        "deribit",
        "yahoo",
    ]


# ---------------------------------------------------------------------------
# catalog-symbols / catalog-inventory / data-coverage
# ---------------------------------------------------------------------------


async def test_catalog_symbols_lists_every_symbol_sorted(lake: pathlib.Path) -> None:
    assert batch.catalog_symbols(_ctx(lake), batch.CatalogFilterParams()) == sorted(
        [_AAPL, _BINANCE_BTC, _BTC, _ETH]
    )


async def test_catalog_symbols_filters_by_channel_and_exchange(lake: pathlib.Path) -> None:
    ctx = _ctx(lake)
    assert batch.catalog_symbols(ctx, batch.CatalogFilterParams(channel="book_snapshot")) == [_BTC]
    assert batch.catalog_symbols(ctx, batch.CatalogFilterParams(source="binance-spot")) == [
        _BINANCE_BTC
    ]


async def test_a_blank_filter_is_no_filter_rather_than_no_results(lake: pathlib.Path) -> None:
    """REST spelled an absent filter ``""``; passing it through would empty the answer.

    ``Catalog.inventory`` treats a non-``None`` channel that is not registered as an empty
    result, so the normalisation is what stops a surface's own default from looking like an
    empty lake.
    """
    ctx = _ctx(lake)
    everything = batch.catalog_symbols(ctx, batch.CatalogFilterParams())
    assert batch.catalog_symbols(ctx, batch.CatalogFilterParams(channel="", source="  ")) == (
        everything
    )
    assert len(batch.catalog_inventory(ctx, batch.CatalogFilterParams(channel=" "))) == len(
        batch.catalog_inventory(ctx, batch.CatalogFilterParams())
    )


async def test_catalog_symbols_matches_the_client(lake: pathlib.Path) -> None:
    client = CrypcodileClient(data_dir=lake)
    ctx = _ctx(lake)
    assert batch.catalog_symbols(ctx, batch.CatalogFilterParams()) == client.list_symbols()
    assert (
        batch.catalog_symbols(ctx, batch.CatalogFilterParams(channel="trade"))
        == client.list_symbols(channel="trade")
    )


async def test_catalog_inventory_carries_coverage_for_every_symbol(lake: pathlib.Path) -> None:
    inv = batch.catalog_inventory(_ctx(lake), batch.CatalogFilterParams())
    assert inv.columns == ["exchange", "channel", "symbol", "min_ts", "max_ts", "row_count"]
    btc = inv.filter((pl.col("symbol") == _BTC) & (pl.col("channel") == "trade"))
    assert btc["row_count"][0] == 3
    assert btc["min_ts"][0] == _BASE_TS
    assert btc["max_ts"][0] == _BASE_TS + 2 * _SECOND


async def test_catalog_inventory_matches_the_client(lake: pathlib.Path) -> None:
    assert batch.catalog_inventory(_ctx(lake), batch.CatalogFilterParams()).equals(
        CrypcodileClient(data_dir=lake).inventory()
    )


async def test_data_coverage_returns_rows_for_one_exact_symbol(lake: pathlib.Path) -> None:
    df = batch.data_coverage(_ctx(lake), batch.DataCoverageParams(symbol=_BTC))
    assert set(df["symbol"].to_list()) == {_BTC}
    assert set(df["channel"].to_list()) == {"trade", "book_snapshot"}


async def test_data_coverage_does_not_match_on_a_prefix(lake: pathlib.Path) -> None:
    """Exact match, not search: ``deribit:BTC`` is a search query, not a coverage subject."""
    assert len(batch.data_coverage(_ctx(lake), batch.DataCoverageParams(symbol="deribit:BTC"))) == 0


async def test_data_coverage_of_a_blank_symbol_keeps_the_inventory_schema(
    lake: pathlib.Path,
) -> None:
    """The contract all three surfaces documented, without a second copy of the columns."""
    df = batch.data_coverage(_ctx(lake), batch.DataCoverageParams(symbol="   "))
    assert len(df) == 0
    assert df.columns == ["exchange", "channel", "symbol", "min_ts", "max_ts", "row_count"]


async def test_data_coverage_matches_the_client(lake: pathlib.Path) -> None:
    assert batch.data_coverage(_ctx(lake), batch.DataCoverageParams(symbol=_BTC)).equals(
        CrypcodileClient(data_dir=lake).data_coverage(_BTC)
    )


# ---------------------------------------------------------------------------
# catalog-scan
# ---------------------------------------------------------------------------


async def test_catalog_scan_returns_rows_in_the_range_sorted_by_timestamp(
    lake: pathlib.Path,
) -> None:
    df = batch.catalog_scan(
        _ctx(lake),
        batch.ScanParams(
            channel="trade",
            symbols=(_BTC,),
            start_ns=_BASE_TS,
            end_ns=_BASE_TS + 2 * _SECOND,
        ),
    )
    assert df["local_ts"].to_list() == sorted(df["local_ts"].to_list())
    assert df["price"].to_list() == [100.0, 101.0, 102.0]


async def test_catalog_scan_merges_several_symbols_the_rest_route_could_not_ask_for(
    lake: pathlib.Path,
) -> None:
    """The route narrowed a multi-symbol read to ``[symbol]``; the capability keeps it."""
    df = batch.catalog_scan(
        _ctx(lake),
        batch.ScanParams(
            channel="trade",
            symbols=(_BTC, _ETH, _BINANCE_BTC),
            start_ns=_BASE_TS,
            end_ns=_BASE_TS + 10 * _SECOND,
        ),
    )
    assert len(df) == 5
    assert df["local_ts"].to_list() == sorted(df["local_ts"].to_list())


async def test_catalog_scan_matches_the_client_for_the_same_range(lake: pathlib.Path) -> None:
    params = batch.ScanParams(
        channel="trade",
        symbols=(_BTC, _BINANCE_BTC),
        start_ns=_BASE_TS,
        end_ns=_BASE_TS + 10 * _SECOND,
    )
    client = CrypcodileClient(data_dir=lake)
    assert batch.catalog_scan(_ctx(lake), params).equals(
        client.scan("trade", [_BTC, _BINANCE_BTC], params.start_ns, params.end_ns)
    )


async def test_catalog_scan_limit_is_the_callers_and_bounds_the_merge_not_each_symbol(
    lake: pathlib.Path,
) -> None:
    """Four matching rows, a limit of two, and two returned — not two per symbol."""
    df = batch.catalog_scan(
        _ctx(lake),
        batch.ScanParams(
            channel="trade",
            symbols=(_BTC, _ETH),
            start_ns=_BASE_TS,
            end_ns=_BASE_TS + 10 * _SECOND,
            limit=2,
        ),
    )
    assert len(df) == 2
    assert df["local_ts"].to_list() == [_BASE_TS, _BASE_TS + _SECOND]


async def test_catalog_scan_pushdown_keeps_the_rows_the_merge_would_have_kept(
    lake: pathlib.Path,
) -> None:
    """The globally first *n* rows hold at most *n* from any one symbol, so both bounds agree.

    Driven rather than argued: the limited scan must return the head of the unlimited one,
    or the push-down is dropping a row the caller asked for.
    """
    unlimited = batch.catalog_scan(
        _ctx(lake),
        batch.ScanParams(
            channel="trade",
            symbols=(_BTC, _ETH, _BINANCE_BTC),
            start_ns=_BASE_TS,
            end_ns=_BASE_TS + 10 * _SECOND,
        ),
    )
    limited = batch.catalog_scan(
        _ctx(lake),
        batch.ScanParams(
            channel="trade",
            symbols=(_BTC, _ETH, _BINANCE_BTC),
            start_ns=_BASE_TS,
            end_ns=_BASE_TS + 10 * _SECOND,
            limit=4,
        ),
    )
    assert limited.equals(unlimited.head(4))


async def test_catalog_scan_cannot_be_used_to_raise_the_surfaces_ceiling(
    lake: pathlib.Path,
) -> None:
    """A caller's ``limit`` narrows; it never widens. The smaller of the two wins."""

    def _scan(limit: int) -> batch.ScanParams:
        return batch.ScanParams(
            channel="trade",
            symbols=(_BTC, _ETH, _BINANCE_BTC),
            start_ns=_BASE_TS,
            end_ns=_BASE_TS + 10 * _SECOND,
            limit=limit,
        )

    assert len(batch.catalog_scan(_ctx(lake, row_limit=2), _scan(100))) == 2
    assert len(batch.catalog_scan(_ctx(lake, row_limit=2), _scan(1))) == 1


async def test_catalog_scan_of_no_symbols_is_empty(lake: pathlib.Path) -> None:
    df = batch.catalog_scan(
        _ctx(lake),
        batch.ScanParams(channel="trade", symbols=(), start_ns=_BASE_TS, end_ns=_BASE_TS + _SECOND),
    )
    assert len(df) == 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_ranks_an_exact_symbol_above_a_substring(lake: pathlib.Path) -> None:
    df = batch.search(_ctx(lake), batch.SearchParams(q="BTC"))
    assert set(df["symbol"].to_list()) == {_BTC, _BINANCE_BTC}
    assert df["score"].to_list() == sorted(df["score"].to_list(), reverse=True)


async def test_search_limit_is_the_callers_ranking_cutoff(lake: pathlib.Path) -> None:
    """All three surfaces agreed on 20 and on it being a user parameter, so it stays one."""
    assert batch.SearchParams(q="BTC").limit == 20
    assert len(batch.search(_ctx(lake), batch.SearchParams(q="BTC", limit=1))) == 1


async def test_search_matches_the_client(lake: pathlib.Path) -> None:
    assert batch.search(_ctx(lake), batch.SearchParams(q="BTC")).equals(
        CrypcodileClient(data_dir=lake).search_symbols("BTC")
    )


async def test_search_for_nothing_present_is_an_empty_table(lake: pathlib.Path) -> None:
    assert len(batch.search(_ctx(lake), batch.SearchParams(q="XRP"))) == 0


# ---------------------------------------------------------------------------
# resolve-symbols — the adapter that reproduces client logic, diffed against it
# ---------------------------------------------------------------------------


async def test_resolve_symbols_passes_through_a_canonical_symbol(lake: pathlib.Path) -> None:
    resolved = batch.resolve_symbols(
        _ctx(lake), batch.ResolveSymbolsParams(symbols=(_BTC, _ETH))
    )
    assert resolved == [_BTC, _ETH]


async def test_resolve_symbols_ranks_a_free_form_input(lake: pathlib.Path) -> None:
    assert batch.resolve_symbols(
        _ctx(lake), batch.ResolveSymbolsParams(symbols=("ETH-PERPETUAL",))
    ) == [_ETH]


async def test_resolve_symbols_refuses_an_ambiguous_input_by_default(
    lake: pathlib.Path,
) -> None:
    """Default ``error`` on all three surfaces: four matches is a question, not an answer."""
    with pytest.raises(ValueError, match="Ambiguous symbol"):
        batch.resolve_symbols(_ctx(lake), batch.ResolveSymbolsParams(symbols=("BTC",)))


async def test_resolve_symbols_first_and_all_take_the_other_two_branches(
    lake: pathlib.Path,
) -> None:
    ctx = _ctx(lake)
    first = batch.resolve_symbols(
        ctx, batch.ResolveSymbolsParams(symbols=("BTC",), ambiguous="first")
    )
    every = batch.resolve_symbols(
        ctx, batch.ResolveSymbolsParams(symbols=("BTC",), ambiguous="all")
    )
    assert len(first) == 1
    assert set(every) == {_BTC, _BINANCE_BTC}
    assert first[0] == every[0], "'first' must take the highest-ranked match"


async def test_resolve_symbols_raises_on_an_input_nothing_matched(lake: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="No symbols matched"):
        batch.resolve_symbols(_ctx(lake), batch.ResolveSymbolsParams(symbols=("XRP",)))


async def test_resolve_symbols_rejects_an_unknown_ambiguous_mode(lake: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="ambiguous must be"):
        batch.resolve_symbols(
            _ctx(lake),
            batch.ResolveSymbolsParams(symbols=(_BTC,), ambiguous="whatever"),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("symbols", "ambiguous"),
    [
        ((_BTC, _ETH), "error"),
        (("ETH-PERPETUAL",), "error"),
        (("BTC",), "first"),
        (("BTC",), "all"),
        ((" ", _BTC), "error"),
    ],
    ids=["passthrough", "ranked", "ambiguous_first", "ambiguous_all", "blank_input_skipped"],
)
async def test_resolve_symbols_agrees_with_the_client_it_reproduces(
    lake: pathlib.Path, symbols: tuple[str, ...], ambiguous: str
) -> None:
    """The reproduction diffed against the original, on the same lake.

    Two implementations of one algorithm is how this codebase's worst bugs started: same
    name, same signature, different arithmetic. Asserting the answers rather than the shapes
    is the only check that would have caught them.
    """
    mine = batch.resolve_symbols(
        _ctx(lake),
        batch.ResolveSymbolsParams(symbols=symbols, ambiguous=ambiguous),  # type: ignore[arg-type]
    )
    theirs = CrypcodileClient(data_dir=lake).resolve_symbols(
        list(symbols),
        ambiguous=ambiguous,  # type: ignore[arg-type]
    )
    assert mine == theirs


async def test_resolve_symbols_reports_the_same_failures_as_the_client(
    lake: pathlib.Path,
) -> None:
    """A rewrite that raises a different sentence is a rewrite REST turns into a worse 400."""
    client = CrypcodileClient(data_dir=lake)
    for bad in ("XRP", "BTC"):
        with pytest.raises(ValueError) as mine:
            batch.resolve_symbols(_ctx(lake), batch.ResolveSymbolsParams(symbols=(bad,)))
        with pytest.raises(ValueError) as theirs:
            client.resolve_symbols([bad])
        assert str(mine.value) == str(theirs.value)


# ---------------------------------------------------------------------------
# Symmetry, executed
# ---------------------------------------------------------------------------

_THIRTEEN = (
    "query",
    "catalog",
    "catalog-summary",
    "catalog-stats",
    "catalog-channels",
    "catalog-dates",
    "catalog-symbols",
    "catalog-inventory",
    "catalog-exchanges",
    "catalog-scan",
    "search",
    "resolve-symbols",
    "data-coverage",
)


@pytest.mark.parametrize("name", _THIRTEEN)
def test_every_capability_in_this_batch_is_declared_for_both_asset_classes(name: str) -> None:
    from crocodile import capabilities

    capabilities.load_all()
    cap = REGISTRY[name]
    assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    crypto, equity = cap.impls[AssetClass.CRYPTO], cap.impls[AssetClass.EQUITY]
    assert crypto.fn is equity.fn, "one lake, one implementation; two would be free to drift"
    assert (crypto.prov, crypto.basis) == (equity.prov, equity.basis)


@pytest.mark.parametrize("name", _THIRTEEN)
def test_no_capability_in_this_batch_is_excused_from_symmetry(name: str) -> None:
    """Nothing here is scheduled or irreducible: the same ``Catalog`` serves both markets."""
    from crocodile import capabilities
    from crocodile.core.capability import IRREDUCIBLE, PENDING_SYMMETRY

    capabilities.load_all()
    assert name not in PENDING_SYMMETRY
    assert name not in IRREDUCIBLE


async def test_the_equity_implementation_answers_against_equity_rows(lake: pathlib.Path) -> None:
    """Gate 2 checks an equity impl exists; this calls it, on equity rows, in one lake.

    The bars written by the fixture are ``asset_class=EQUITY`` under ``source=yahoo``, and
    the crypto trades are in the same root — which is also the case that proves the answer
    is not accidentally scoped to whichever market wrote first.
    """
    ctx = _ctx(lake, asset_class=AssetClass.EQUITY)
    for name, params in (
        ("catalog-symbols", batch.CatalogFilterParams(channel="ohlcv")),
        ("catalog-inventory", batch.CatalogFilterParams(source="yahoo")),
    ):
        result = REGISTRY[name].impls[AssetClass.EQUITY].fn(ctx, params)
        assert len(result) == 1

    coverage = REGISTRY["data-coverage"].impls[AssetClass.EQUITY].fn(
        ctx, batch.DataCoverageParams(symbol=_AAPL)
    )
    assert coverage["row_count"][0] == 2
    assert batch.catalog_dates(ctx, batch.ChannelParams(channel="ohlcv")) == ["2023-11-14"]
    assert batch.query(ctx, batch.QueryParams(sql="SELECT count(*) AS n FROM ohlcv"))["n"][0] == 2


def test_every_adapter_is_reachable_through_the_registry_it_declared_into() -> None:
    """A declaration nobody can call through is a declaration that is not wired up."""
    from crocodile import capabilities

    capabilities.load_all()
    declared = {
        name: REGISTRY[name].impls[AssetClass.CRYPTO].fn for name in _THIRTEEN
    }
    assert declared == {
        "query": batch.query,
        "catalog": batch.catalog,
        "catalog-summary": batch.catalog_summary,
        "catalog-stats": batch.catalog_stats,
        "catalog-channels": batch.catalog_channels,
        "catalog-dates": batch.catalog_dates,
        "catalog-symbols": batch.catalog_symbols,
        "catalog-inventory": batch.catalog_inventory,
        "catalog-exchanges": batch.catalog_exchanges,
        "catalog-scan": batch.catalog_scan,
        "search": batch.search,
        "resolve-symbols": batch.resolve_symbols,
        "data-coverage": batch.data_coverage,
    }


def test_every_alias_is_a_name_that_was_on_the_wire() -> None:
    """The MCP tool names that do not fall out of the capability name under any transform.

    ``catalog_summary`` and ``data_coverage`` need no alias — replacing the hyphen gives
    them back. These seven do not, so a caller wired to one of them would simply stop
    finding the tool, which is the silent loss this merge exists to stop.
    """
    from crocodile import capabilities

    capabilities.load_all()
    assert {name: REGISTRY[name].aliases for name in _THIRTEEN if REGISTRY[name].aliases} == {
        "query": ("query_market_data",),
        "catalog-channels": ("list_data_channels",),
        "catalog-dates": ("list_dates",),
        "catalog-symbols": ("list_symbols",),
        "catalog-inventory": ("inventory_snapshot",),
        "catalog-exchanges": ("list_exchanges_on_disk",),
        "search": ("search_symbols",),
    }

"""Tests for Catalog inventory + ranked symbol search (Wave 2 Tasks 1–2)."""

from __future__ import annotations

import pathlib

import polars as pl

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookSnapshot, Trade
from crypcodile.store.catalog import Catalog
from crypcodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14 UTC

_INVENTORY_COLS = ["exchange", "channel", "symbol", "min_ts", "max_ts", "row_count"]
_SEARCH_COLS = ["symbol", "exchange", "channels", "score", "min_ts", "max_ts", "row_count"]


def _trade(
    price: float = 1.0,
    local_ts: int = _BASE_TS,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
    symbol_raw: str | None = None,
) -> Trade:
    if symbol_raw is None:
        symbol_raw = symbol.rsplit(":", 1)[-1]
    return Trade(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol_raw,
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(price),
        price=price,
        amount=2.0,
        side=Side.BUY,
    )


def _snap(
    local_ts: int = _BASE_TS,
    exchange: str = "deribit",
    symbol: str = "deribit:BTC-PERPETUAL",
) -> BookSnapshot:
    return BookSnapshot(
        exchange=exchange,
        symbol=symbol,
        symbol_raw=symbol.rsplit(":", 1)[-1],
        exchange_ts=local_ts,
        local_ts=local_ts,
        bids=[(100.0, 5.0), (99.0, 0.0)],
        asks=[(101.0, 4.0)],
        depth=2,
        sequence_id=42,
        is_snapshot=True,
    )


async def _write_fixtures(data_dir: pathlib.Path) -> None:
    """Write 3 trades + 1 book_snapshot using ParquetSink."""
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade(100.0, local_ts=_BASE_TS))
    await sink.put(_trade(200.0, local_ts=_BASE_TS + 1_000_000_000))
    await sink.put(_trade(300.0, local_ts=_BASE_TS + 2_000_000_000))
    await sink.put(_snap(local_ts=_BASE_TS))
    await sink.flush()


# ---------------------------------------------------------------------------
# list_channels
# ---------------------------------------------------------------------------


def test_list_channels_empty_lake(tmp_path: pathlib.Path) -> None:
    """Empty lake → []."""
    cat = Catalog(tmp_path)
    assert cat.list_channels() == []


def test_list_channels_missing_data_dir(tmp_path: pathlib.Path) -> None:
    """Missing data directory → []."""
    cat = Catalog(tmp_path / "no_such_dir")
    assert cat.list_channels() == []


def test_list_channels_empty_partition_dirs_without_parquet(
    tmp_path: pathlib.Path,
) -> None:
    """Channel dirs without parquet still appear (filesystem, no DuckDB view).

    Construct Catalog on an empty lake first: ``_refresh_views`` / DuckDB
    cannot register views for globs with no parquet parts. Filesystem
    ``list_channels`` still discovers later-created empty partitions.
    """
    cat = Catalog(tmp_path)
    assert cat.list_channels() == []

    (tmp_path / "exchange=deribit" / "channel=trade").mkdir(parents=True)
    (tmp_path / "exchange=deribit" / "channel=funding").mkdir(parents=True)
    (tmp_path / "exchange=binance" / "channel=trade").mkdir(parents=True)
    (tmp_path / "not_an_exchange" / "channel=trade").mkdir(parents=True)
    (tmp_path / "exchange=deribit" / "channel=").mkdir(parents=True)  # empty suffix
    (tmp_path / "exchange=deribit" / "readme.txt").write_text("ignore")

    assert cat.list_channels() == ["funding", "trade"]


def test_catalog_constructs_with_preexisting_empty_partitions(
    tmp_path: pathlib.Path,
) -> None:
    """Catalog() must not raise when empty channel dirs exist before init.

    DuckDB ``read_parquet`` fails when the hive glob matches no parts;
    ``_create_view`` must skip those channels so client/CLI construction
    still succeeds. ``list_channels`` still discovers them; they are not
    registered as views.
    """
    (tmp_path / "exchange=deribit" / "channel=trade").mkdir(parents=True)
    (tmp_path / "exchange=deribit" / "channel=funding").mkdir(parents=True)

    cat = Catalog(tmp_path)  # must not raise
    assert cat.list_channels() == ["funding", "trade"]
    assert cat._registered_channels == set()
    # inventory over empty partitions is empty (stable schema).
    inv = cat.inventory()
    assert len(inv) == 0
    assert list(inv.columns) == [
        "exchange",
        "channel",
        "symbol",
        "min_ts",
        "max_ts",
        "row_count",
    ]


async def test_list_channels_with_fixtures(tmp_path: pathlib.Path) -> None:
    """After writing trades + snapshots, both channels are listed sorted."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    assert cat.list_channels() == ["book_snapshot", "trade"]


def test_list_channels_skips_special_char_suffixes(tmp_path: pathlib.Path) -> None:
    """Glob / control / padded channel= suffixes must not be advertised.

    Construct Catalog on empty lake first so ``_refresh_views`` is not asked
    to register views over empty partitions, then plant adversarial dirs.
    """
    cat = Catalog(tmp_path)
    base = tmp_path / "exchange=deribit"
    (base / "channel=trade").mkdir(parents=True)
    (base / "channel=funding").mkdir(parents=True)
    # Glob metacharacters — unsafe for Path.glob / DuckDB patterns.
    (base / "channel=trade*glob").mkdir(parents=True)
    (base / "channel=trade?").mkdir(parents=True)
    (base / "channel=trade[0]").mkdir(parents=True)
    # Whitespace padding (list_dates strips user input → orphaned).
    (base / "channel=  padded  ").mkdir(parents=True)
    # Relative / empty suffixes.
    (base / "channel=.").mkdir(parents=True)
    (base / "channel=..").mkdir(parents=True)
    (base / "channel=").mkdir(parents=True)
    # Control character in name.
    (base / "channel=trade\nline").mkdir(parents=True)

    assert cat.list_channels() == ["funding", "trade"]


def test_list_channels_resolve_checks_channel_symlink(
    tmp_path: pathlib.Path,
) -> None:
    """Channel dirs whose resolve() escapes data_dir are ignored."""
    cat = Catalog(tmp_path)
    outside = tmp_path.parent / f"outside_chan_{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        ex = tmp_path / "exchange=deribit"
        ex.mkdir()
        (ex / "channel=trade").mkdir()
        (ex / "channel=escape").symlink_to(outside)
        assert cat.list_channels() == ["trade"]
    finally:
        try:
            outside.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# list_dates
# ---------------------------------------------------------------------------


def test_list_dates_empty_lake(tmp_path: pathlib.Path) -> None:
    """Empty lake → []."""
    cat = Catalog(tmp_path)
    assert cat.list_dates("trade") == []


def test_list_dates_empty_or_whitespace_channel(tmp_path: pathlib.Path) -> None:
    cat = Catalog(tmp_path)
    assert cat.list_dates("") == []
    assert cat.list_dates("   ") == []


def test_list_dates_rejects_unsafe_channel(tmp_path: pathlib.Path) -> None:
    """Path / glob metacharacters must not be used as path segments."""
    cat = Catalog(tmp_path)
    assert cat.list_dates("../etc") == []
    assert cat.list_dates("trade/../trade") == []
    assert cat.list_dates("trade*") == []
    assert cat.list_dates(".") == []
    assert cat.list_dates("..") == []


async def test_list_dates_with_fixtures(tmp_path: pathlib.Path) -> None:
    """Single-day fixtures → one date partition for trade and book_snapshot."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    assert cat.list_dates("trade") == ["2023-11-14"]
    assert cat.list_dates("book_snapshot") == ["2023-11-14"]
    assert cat.list_dates("no_such_channel") == []


async def test_list_dates_multiday_and_multi_exchange(tmp_path: pathlib.Path) -> None:
    """Distinct dates across days; same date on two exchanges is deduped."""
    # Exact midnight 2023-11-15 UTC (matches catalog multiday tests).
    day2_ts = 1_700_006_400_000_000_000
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade(1.0, local_ts=_BASE_TS, exchange="deribit"))
    await sink.put(
        _trade(
            2.0,
            local_ts=day2_ts,
            exchange="deribit",
            symbol="deribit:BTC-PERPETUAL",
        )
    )
    await sink.put(
        _trade(
            3.0,
            local_ts=_BASE_TS,
            exchange="binance",
            symbol="binance-spot:BTC-USDT",
            symbol_raw="BTCUSDT",
        )
    )
    await sink.flush()

    cat = Catalog(tmp_path)
    assert cat.list_dates("trade") == ["2023-11-14", "2023-11-15"]


# ---------------------------------------------------------------------------
# list_exchanges_on_disk
# ---------------------------------------------------------------------------


def test_list_exchanges_on_disk_empty_lake(tmp_path: pathlib.Path) -> None:
    """Empty lake → []."""
    cat = Catalog(tmp_path)
    assert cat.list_exchanges_on_disk() == []


def test_list_exchanges_on_disk_missing_data_dir(tmp_path: pathlib.Path) -> None:
    """Non-existent data_dir → []."""
    cat = Catalog(tmp_path / "does_not_exist")
    assert cat.list_exchanges_on_disk() == []


def test_list_exchanges_on_disk_ignores_non_exchange_entries(
    tmp_path: pathlib.Path,
) -> None:
    """Only exchange=* directories count; files and other dirs are ignored."""
    (tmp_path / "not_a_partition").mkdir()
    (tmp_path / "exchange=").mkdir()  # empty suffix → ignored
    (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "exchange=deribit").mkdir()
    cat = Catalog(tmp_path)
    assert cat.list_exchanges_on_disk() == ["deribit"]


def test_list_exchanges_on_disk_skips_special_char_suffixes(
    tmp_path: pathlib.Path,
) -> None:
    """Glob / control / padded exchange= suffixes must not be advertised."""
    (tmp_path / "exchange=deribit").mkdir()
    (tmp_path / "exchange=binance").mkdir()
    (tmp_path / "exchange=weird*ex").mkdir()
    (tmp_path / "exchange=weird?").mkdir()
    (tmp_path / "exchange=  pad  ").mkdir()
    (tmp_path / "exchange=.").mkdir()
    (tmp_path / "exchange=..").mkdir()
    cat = Catalog(tmp_path)
    assert cat.list_exchanges_on_disk() == ["binance", "deribit"]


async def test_list_exchanges_on_disk_with_fixtures(tmp_path: pathlib.Path) -> None:
    """Single-exchange fixtures → one partition name."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    assert cat.list_exchanges_on_disk() == ["deribit"]


async def test_list_exchanges_on_disk_multi_exchange(tmp_path: pathlib.Path) -> None:
    """Distinct exchanges are sorted and deduped."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade(1.0, exchange="deribit"))
    await sink.put(
        _trade(
            2.0,
            exchange="binance",
            symbol="binance-spot:BTC-USDT",
            symbol_raw="BTCUSDT",
        )
    )
    await sink.put(
        _trade(
            3.0,
            exchange="binance",
            symbol="binance-spot:ETH-USDT",
            symbol_raw="ETHUSDT",
        )
    )
    await sink.flush()

    cat = Catalog(tmp_path)
    assert cat.list_exchanges_on_disk() == ["binance", "deribit"]


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------


def test_inventory_empty_lake_has_stable_schema(tmp_path: pathlib.Path) -> None:
    """Empty lake → empty DF with stable inventory schema."""
    cat = Catalog(tmp_path)
    df = cat.inventory()
    assert isinstance(df, pl.DataFrame)
    assert len(df) == 0
    assert df.columns == _INVENTORY_COLS
    assert df.schema["min_ts"] == pl.Int64
    assert df.schema["row_count"] == pl.Int64


async def test_inventory_shows_symbols_and_row_counts(tmp_path: pathlib.Path) -> None:
    """inventory() reports symbols and row counts after writing via ParquetSink."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.inventory()

    assert set(df.columns) == set(_INVENTORY_COLS)
    assert len(df) >= 1

    trade_rows = df.filter(pl.col("channel") == "trade")
    assert len(trade_rows) == 1
    row = trade_rows.row(0, named=True)
    assert row["exchange"] == "deribit"
    assert row["symbol"] == "deribit:BTC-PERPETUAL"
    assert row["row_count"] == 3
    assert row["min_ts"] == _BASE_TS
    assert row["max_ts"] == _BASE_TS + 2_000_000_000

    snap_rows = df.filter(pl.col("channel") == "book_snapshot")
    assert len(snap_rows) == 1
    assert snap_rows["row_count"][0] == 1


async def test_inventory_filter_channel(tmp_path: pathlib.Path) -> None:
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.inventory(channel="trade")
    assert len(df) == 1
    assert df["channel"][0] == "trade"
    assert df["row_count"][0] == 3


async def test_inventory_filter_exchange(tmp_path: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
    await sink.put(_trade(1.0, exchange="deribit", symbol="deribit:BTC-PERPETUAL"))
    await sink.put(
        _trade(2.0, exchange="binance", symbol="binance:BTCUSDT", symbol_raw="BTCUSDT")
    )
    await sink.flush()

    cat = Catalog(tmp_path)
    df = cat.inventory(exchange="binance")
    assert len(df) == 1
    assert df["exchange"][0] == "binance"
    assert df["symbol"][0] == "binance:BTCUSDT"


async def test_inventory_unknown_channel_empty(tmp_path: pathlib.Path) -> None:
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.inventory(channel="no_such_channel")
    assert len(df) == 0
    assert df.columns == _INVENTORY_COLS


async def test_inventory_empty_channel_treated_as_none(tmp_path: pathlib.Path) -> None:
    """Empty / whitespace channel must not filter inventory to nothing."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    full = cat.inventory()
    assert len(full) >= 1
    assert len(cat.inventory(channel="")) == len(full)
    assert len(cat.inventory(channel="   ")) == len(full)
    assert len(cat.inventory(exchange="")) == len(full)
    assert len(cat.inventory(exchange="  ")) == len(full)


# ---------------------------------------------------------------------------
# search_symbols
# ---------------------------------------------------------------------------


async def test_search_ranks_exact_above_substring(tmp_path: pathlib.Path) -> None:
    """Exact full-symbol match scores higher than substring match."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=20, flush_interval_seconds=9999)
    await sink.put(_trade(1.0, symbol="deribit:BTC-PERPETUAL", symbol_raw="BTC-PERPETUAL"))
    await sink.put(
        _trade(
            2.0,
            exchange="binance",
            symbol="binance:ETHBTC",
            symbol_raw="ETHBTC",
        )
    )
    await sink.put(
        _trade(
            3.0,
            exchange="okx",
            symbol="okx:BTC-USDT-SWAP",
            symbol_raw="BTC-USDT-SWAP",
        )
    )
    await sink.flush()

    cat = Catalog(tmp_path)
    # Exact full match for deribit:BTC-PERPETUAL; others may substring-match "BTC"
    df = cat.search_symbols("deribit:BTC-PERPETUAL")
    assert len(df) >= 1
    assert df["symbol"][0] == "deribit:BTC-PERPETUAL"
    assert df["score"][0] == 100

    # Substring / prefix search on BTC — exact raw / prefix should beat pure substring.
    df_btc = cat.search_symbols("BTC")
    assert len(df_btc) >= 2
    scores = df_btc["score"].to_list()
    assert scores == sorted(scores, reverse=True)
    # No exact full match for "BTC"; best is prefix/substring on raw (60 or 40).
    assert df_btc["score"][0] >= df_btc["score"][-1]
    # ETHBTC contains BTC as substring (40); BTC-PERPETUAL / BTC-USDT-SWAP prefix (60).
    top_symbols = df_btc.filter(pl.col("score") == df_btc["score"].max())["symbol"].to_list()
    assert any("BTC-PERPETUAL" in s or "BTC-USDT" in s for s in top_symbols)
    eth_rows = df_btc.filter(pl.col("symbol") == "binance:ETHBTC")
    if len(eth_rows):
        assert eth_rows["score"][0] == 40
        assert eth_rows["score"][0] < df_btc["score"][0]


def test_search_empty_q_returns_empty_schema(tmp_path: pathlib.Path) -> None:
    """Empty q → empty DF with search schema."""
    cat = Catalog(tmp_path)
    df = cat.search_symbols("")
    assert len(df) == 0
    assert df.columns == _SEARCH_COLS


def test_search_whitespace_only_q_returns_empty(tmp_path: pathlib.Path) -> None:
    """Whitespace-only q is treated as empty."""
    cat = Catalog(tmp_path)
    df = cat.search_symbols("   ")
    assert len(df) == 0
    assert df.columns == _SEARCH_COLS


async def test_search_empty_q_with_data_still_empty(tmp_path: pathlib.Path) -> None:
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.search_symbols("")
    assert len(df) == 0
    assert df.columns == _SEARCH_COLS


async def test_search_filter_channel_and_exchange(tmp_path: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=20, flush_interval_seconds=9999)
    await sink.put(_trade(1.0, exchange="deribit", symbol="deribit:BTC-PERPETUAL"))
    await sink.put(
        _trade(2.0, exchange="binance", symbol="binance:BTCUSDT", symbol_raw="BTCUSDT")
    )
    await sink.put(_snap(exchange="deribit", symbol="deribit:BTC-PERPETUAL"))
    await sink.flush()

    cat = Catalog(tmp_path)

    by_ex = cat.search_symbols("BTC", exchange="binance")
    assert len(by_ex) == 1
    assert by_ex["exchange"][0] == "binance"
    assert by_ex["symbol"][0] == "binance:BTCUSDT"

    by_ch = cat.search_symbols("BTC", channel="book_snapshot")
    assert len(by_ch) == 1
    assert by_ch["channels"][0] == "book_snapshot"
    assert by_ch["symbol"][0] == "deribit:BTC-PERPETUAL"


async def test_search_limit_enforced(tmp_path: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=50, flush_interval_seconds=9999)
    for i, sym in enumerate(
        [
            "deribit:BTC-PERPETUAL",
            "deribit:ETH-PERPETUAL",
            "deribit:SOL-PERPETUAL",
            "binance:BTCUSDT",
            "binance:ETHUSDT",
        ]
    ):
        exchange = sym.split(":")[0]
        await sink.put(
            _trade(
                float(i + 1),
                exchange=exchange,
                symbol=sym,
                symbol_raw=sym.split(":", 1)[1],
            )
        )
    await sink.flush()

    cat = Catalog(tmp_path)
    # Broad substring that hits several symbols.
    df = cat.search_symbols("PERPETUAL", limit=2)
    assert len(df) == 2

    df_all = cat.search_symbols("PERPETUAL", limit=20)
    assert len(df_all) == 3


async def test_search_limit_non_positive_returns_empty(tmp_path: pathlib.Path) -> None:
    """limit < 1 must not use Polars head(-n) (which drops last n rows)."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)

    for bad_limit in (0, -1, -5):
        df = cat.search_symbols("BTC", limit=bad_limit)
        assert len(df) == 0
        assert df.schema == cat.search_symbols("").schema


async def test_search_aggregates_multi_channel(tmp_path: pathlib.Path) -> None:
    """Same symbol on trade + book_snapshot → one row, channels joined, counts summed."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.search_symbols("deribit:BTC-PERPETUAL")
    assert len(df) == 1
    row = df.row(0, named=True)
    assert row["score"] == 100
    assert row["exchange"] == "deribit"
    assert set(row["channels"].split(",")) == {"book_snapshot", "trade"}
    assert row["row_count"] == 4  # 3 trades + 1 snapshot
    assert row["min_ts"] == _BASE_TS
    assert row["max_ts"] == _BASE_TS + 2_000_000_000


async def test_search_exact_raw_score(tmp_path: pathlib.Path) -> None:
    """Exact match on the raw portion (after last ':') scores 90."""
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.search_symbols("BTC-PERPETUAL")
    assert len(df) >= 1
    assert df["symbol"][0] == "deribit:BTC-PERPETUAL"
    assert df["score"][0] == 90


async def test_search_no_match_empty(tmp_path: pathlib.Path) -> None:
    await _write_fixtures(tmp_path)
    cat = Catalog(tmp_path)
    df = cat.search_symbols("ZZZZ-NO-MATCH")
    assert len(df) == 0
    assert df.columns == _SEARCH_COLS

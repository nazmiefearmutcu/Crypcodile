"""A Parquet file with the pre-merge crypto column set, read through the public path.

``_header``'s legacy branch decides the asset class and the origin of every row
in every crypto lake written before the union merge, and until now it was
exercised only by hand-built dicts carrying the eight fields the test author
remembered. A real file carries the whole ``_CRYPTO_COMMON_FIELDS`` plus
``_CRYPTO_CHANNEL_EXTRA["trade"]`` column set — ``liquidation``, ``l1_gas_fee``,
``sender`` and the rest, all null — because ``_write_parquet_sync`` materialises
every schema key as a column. That difference is not cosmetic: it is precisely
what made the equity ``exchange`` column present-and-null, and a hand-built dict
that simply omits a column cannot reproduce it.

``tests/store/test_source_partition.py`` renames a canonical lake's directories,
which is the right fixture for the migration tests and is not this one: those
files still carry ``asset_class`` and ``source_ts``. The schema here comes from
``parquet_sink``'s own crypto table, which is still declared because those files
exist and cannot be rewritten.
"""

from __future__ import annotations

import pathlib

import polars as pl

from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import Trade
from crocodile.core.store.parquet_sink import FAMILY_CRYPTO, _channel_schema
from crocodile.core.store.rows import _symbol_bucket

_TS = 1_700_000_000_000_000_000  # 2023-11-14
_DATE = "2023-11-14"
_SYMBOL = "deribit:BTC-PERPETUAL"


def _write_premerge_trade(data_dir: pathlib.Path, **values: object) -> None:
    """Write one ``exchange=`` partition holding a file with the retired column set."""
    schema = _channel_schema("trade", FAMILY_CRYPTO)
    bucket = _symbol_bucket(_SYMBOL)
    # Start from every declared column as a null, exactly as the sink does, so
    # the file has the columns the fork wrote rather than the ones this test
    # happens to care about.
    row: dict[str, object] = dict.fromkeys(schema)
    row |= {
        "exchange": "deribit",
        "symbol": _SYMBOL,
        "symbol_raw": "BTC-PERPETUAL",
        "exchange_ts": _TS,
        "local_ts": _TS,
        "channel": "trade",
        "date": _DATE,
        "bucket": bucket,
    }
    row |= values
    part_dir = (
        data_dir / "exchange=deribit" / "channel=trade" / f"date={_DATE}" / f"bucket={bucket}"
    )
    part_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([row], schema=schema).write_parquet(part_dir / "part-0.parquet")


def test_a_premerge_crypto_file_replays_as_a_canonical_record(tmp_path: pathlib.Path) -> None:
    """The whole path: real columns, real Parquet, real client.

    ``migrate_lake`` renames directories and never rewrites a byte, so every
    crypto file already on disk keeps this shape forever. Reading one as
    anything other than the record it describes would empty a query rather than
    fail it.
    """
    from crocodile.crypto.client.client import CrypcodileClient

    _write_premerge_trade(tmp_path, id="7", price=42.0, amount=1.5, side="buy")

    (record,) = CrypcodileClient(data_dir=tmp_path).replay(
        ["trade"], [_SYMBOL], _TS - 1, _TS + 1
    )

    assert isinstance(record, Trade)
    assert record.source == "deribit"
    assert record.source_ts == _TS
    assert record.asset_class is AssetClass.CRYPTO
    assert record.price == 42.0
    assert record.amount == 1.5
    assert record.side is Side.BUY


def test_the_null_columns_of_a_premerge_file_read_back_as_absent(
    tmp_path: pathlib.Path,
) -> None:
    """Every optional column the fork declared is in the file, holding a null.

    This is the shape that broke the asset-class branch: a column that exists
    and says nothing. The reader must treat those as unset rather than as
    values, and the header must still be established from the columns that do
    hold something.
    """
    from crocodile.crypto.client.client import CrypcodileClient

    _write_premerge_trade(tmp_path, id="7", price=42.0, amount=1.5, side="buy")

    df = pl.read_parquet(sorted(tmp_path.rglob("part-*.parquet")))
    assert {"liquidation", "l1_gas_fee", "sender", "is_smart_wallet"} <= set(df.columns)

    (record,) = CrypcodileClient(data_dir=tmp_path).replay(
        ["trade"], [_SYMBOL], _TS - 1, _TS + 1
    )

    assert isinstance(record, Trade)
    assert record.liquidation is None
    assert record.l1_gas_fee is None
    assert record.sender is None
    assert record.is_smart_wallet is None
    # No provenance tail was ever written to these files, and the fork only ever
    # wrote venue-reported records, so the struct defaults are the truth here.
    assert record.prov is Provenance.NATIVE
    assert record.prov_basis == "native"
    assert record.prov_inputs == []

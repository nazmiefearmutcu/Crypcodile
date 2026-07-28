"""Two bug-fix regressions whose subject is the lake, not the command that reached it.

The rest of this file was regression cover for the crypto Typer app and went with it. Three
groups, three reasons:

* ``query`` from a pipe, the ``export``/``replay``/``collect``/``funding-apr``
  non-interactive guards and the ``basis`` mode combinations were behaviour of hand-written
  commands. ``query``, ``export``, ``replay``, ``collect``, ``funding-apr`` and the three
  basis capabilities are projected now, and required parameters replace the guards —
  ``tests/conformance/test_surfaces.py`` and ``tests/surfaces/test_end_to_end.py`` own what
  a surface does with a missing or bad one.
* ``make_sparkline``, ``select_collect_params_interactively`` and
  ``prompt_time_range_helper`` were that module's own helpers and have no successor at all.
* What survives is below: neither test invokes a CLI. One pins the export schema of an empty
  frame, the other pins that a 21-digit timestamp bound reaches a real error rather than a
  silently wrong scan.
"""

import pathlib

import polars as pl
import pytest

from crocodile.core.store.catalog import Catalog
from crocodile.crypto.client.export import export as client_export


def test_empty_dataframe_export_schema(tmp_path: pathlib.Path) -> None:
    catalog = Catalog(tmp_path)
    dest = tmp_path / "empty_export.parquet"
    client_export(catalog, "trade", [], 0, 9999999999999999999, "parquet", dest)
    assert dest.exists()
    df = pl.read_parquet(dest)
    assert len(df) == 0
    assert "price" in df.columns
    assert "amount" in df.columns
    assert "channel" in df.columns
    assert "date" in df.columns


def test_adversarial_timestamp_overflow(tmp_path: pathlib.Path) -> None:
    # Testing extremely large timestamp overflow, against a file shaped the way
    # the product writes one today. This built a crypto-family row under an
    # `exchange=` partition — the retired union's file schema, which no live
    # writer produces — by taking `_channel_schema`'s default family, so the
    # bound it guards was being checked on a shape the CLI will not meet.
    from crocodile.core.store.parquet_sink import FAMILY_CANONICAL, _channel_schema

    schema = _channel_schema("trade", FAMILY_CANONICAL)
    df = pl.DataFrame([{
        "symbol": "deribit:BTC-PERPETUAL",
        "symbol_raw": "BTC-PERPETUAL",
        "asset_class": "crypto",
        "source_ts": 1700000000000,
        "local_ts": 1700000000000,
        "prov": "native",
        "prov_basis": "native",
        "prov_confidence": 1.0,
        "prov_inputs": [],
        "channel": "trade",
        "date": "2023-11-14",
        "bucket": 0,
        "id": "t1",
        "price": 30000.0,
        "amount": 1.0,
        "side": "buy",
        "liquidation": "false",
    }], schema=schema)
    part_dir = tmp_path / "source=deribit" / "channel=trade" / "date=2023-11-14" / "bucket=0"
    part_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(part_dir / "part-0.parquet")

    from crocodile.crypto.client.client import CrypcodileClient
    client = CrypcodileClient(data_dir=tmp_path)

    # 21-digit timestamp or larger should cause datetime/OverflowError when scanned
    with pytest.raises((OverflowError, OSError, ValueError)):
        client.scan("trade", ["deribit:BTC-PERPETUAL"], 0, 999999999999999999999)

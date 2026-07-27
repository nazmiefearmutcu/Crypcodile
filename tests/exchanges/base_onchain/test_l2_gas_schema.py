import polars as pl

from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import Trade
from crocodile.core.store.parquet_sink import FAMILY_CANONICAL, _channel_schema
from crocodile.core.store.rows import from_row, to_row


def test_l2_gas_schema_fields():
    # Instantiate Trade with gas and smart wallet fields
    trade = Trade(
        source="base_onchain",
        symbol="base_onchain:AERO-USDC",
        symbol_raw="AERO-USDC",
        source_ts=1700000000000000000,
        local_ts=1700000000500000000,
        asset_class=AssetClass.CRYPTO,
        id="0xabc-0",
        price=1.25,
        amount=100.0,
        side=Side.BUY,
        l1_gas_fee=0.0001,
        l2_gas_fee=0.00005,
        gas_price=50000000.0,
        sender="0x5030000000000000000000000000000000000000",
        is_smart_wallet=True
    )
    
    # Assert fields are accessible
    assert trade.l1_gas_fee == 0.0001
    assert trade.l2_gas_fee == 0.00005
    assert trade.gas_price == 50000000.0
    assert trade.sender == "0x5030000000000000000000000000000000000000"
    assert trade.is_smart_wallet is True

    # Test serialization to flat dict row
    row = to_row(trade)
    assert row["l1_gas_fee"] == 0.0001
    assert row["l2_gas_fee"] == 0.00005
    assert row["gas_price"] == 50000000.0
    assert row["sender"] == "0x5030000000000000000000000000000000000000"
    assert row["is_smart_wallet"] is True

    # Test deserialization back to Trade struct
    reconstructed = from_row(row)
    assert isinstance(reconstructed, Trade)
    assert reconstructed.l1_gas_fee == 0.0001
    assert reconstructed.l2_gas_fee == 0.00005
    assert reconstructed.gas_price == 50000000.0
    assert reconstructed.sender == "0x5030000000000000000000000000000000000000"
    assert reconstructed.is_smart_wallet is True

def test_parquet_schema_inclusion():
    # The canonical family, named rather than defaulted. `_channel_schema`
    # defaults to the crypto family for callers that predate the merge, and this
    # test took that default while claiming to guard the file `base_onchain`
    # writes — a connector that emits canonical records, into a canonical file.
    # It was asserting against a schema table no live writer uses.
    schema = _channel_schema("trade", FAMILY_CANONICAL)
    assert "l1_gas_fee" in schema
    assert "l2_gas_fee" in schema
    assert "gas_price" in schema
    assert "sender" in schema
    assert "is_smart_wallet" in schema
    assert schema["l1_gas_fee"] == pl.Float64
    assert schema["sender"] == pl.Utf8
    assert schema["is_smart_wallet"] == pl.Boolean

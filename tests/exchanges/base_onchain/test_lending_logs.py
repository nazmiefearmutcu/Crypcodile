import polars as pl
from crypcodile.schema.records import ReserveDataUpdated, LiquidationCall
from crypcodile.store.rows import to_row, from_row
from crypcodile.store.parquet_sink import _channel_schema
from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector

def test_lending_records_row_conversions():
    # 1. Test ReserveDataUpdated
    reserve_update = ReserveDataUpdated(
        exchange="base_onchain",
        symbol="lending:AAVE_V3",
        symbol_raw="AAVE_V3",
        exchange_ts=1700000000000000000,
        local_ts=1700000000500000000,
        reserve="0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
        liquidity_rate=0.045,
        stable_borrow_rate=0.065,
        variable_borrow_rate=0.055,
        liquidity_index=10000023,
        variable_borrow_index=10000045
    )

    row = to_row(reserve_update)
    assert row["channel"] == "reserve_data_updated"
    assert row["reserve"] == "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"
    assert row["liquidity_rate"] == 0.045
    assert row["liquidity_index"] == 10000023

    reconstructed = from_row(row)
    assert isinstance(reconstructed, ReserveDataUpdated)
    assert reconstructed.liquidity_rate == 0.045
    assert reconstructed.liquidity_index == 10000023

    # 2. Test LiquidationCall
    liq_call = LiquidationCall(
        exchange="base_onchain",
        symbol="lending:SEAMLESS",
        symbol_raw="SEAMLESS",
        exchange_ts=1700000000000000000,
        local_ts=1700000000500000000,
        collateral_asset="0x4200000000000000000000000000000000000006",
        debt_asset="0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
        user="0xUserAddressHere",
        debt_to_cover=120.5,
        liquidated_collateral_amount=0.08,
        liquidator="0xLiquidatorAddressHere",
        receive_a_token=True
    )

    row_liq = to_row(liq_call)
    assert row_liq["channel"] == "liquidation_call"
    assert row_liq["collateral_asset"] == "0x4200000000000000000000000000000000000006"
    assert row_liq["debt_to_cover"] == 120.5
    assert row_liq["receive_a_token"] is True

    reconstructed_liq = from_row(row_liq)
    assert isinstance(reconstructed_liq, LiquidationCall)
    assert reconstructed_liq.collateral_asset == "0x4200000000000000000000000000000000000006"
    assert reconstructed_liq.debt_to_cover == 120.5
    assert reconstructed_liq.receive_a_token is True

def test_lending_logs_parquet_schema():
    rdu_schema = _channel_schema("reserve_data_updated")
    assert rdu_schema["reserve"] == pl.Utf8
    assert rdu_schema["liquidity_rate"] == pl.Float64
    assert rdu_schema["liquidity_index"] == pl.Int64

    lc_schema = _channel_schema("liquidation_call")
    assert lc_schema["collateral_asset"] == pl.Utf8
    assert lc_schema["debt_to_cover"] == pl.Float64
    assert lc_schema["receive_a_token"] == pl.Boolean

def test_lending_logs_connector_normalization():
    # Test that connector.normalize yields correct records for lending updates
    # Create mock connector
    from unittest.mock import MagicMock
    connector = BaseOnchainConnector(symbols=["AERO-USDC"], channels=["trade"], out=MagicMock(), registry=MagicMock())
    
    # 1. Test ReserveDataUpdated msg
    msg_rdu = {
        "type": "lending_update",
        "event": "ReserveDataUpdated",
        "exchange": "base_onchain",
        "pool": "AAVE_V3",
        "timestamp": 1700000000,
        "reserve": "0xReserve",
        "liquidity_rate": 0.05,
        "stable_borrow_rate": 0.07,
        "variable_borrow_rate": 0.06,
        "liquidity_index": 1000,
        "variable_borrow_index": 2000
    }
    records = list(connector.normalize(msg_rdu, local_ts=1700000000500000000))
    assert len(records) == 1
    assert isinstance(records[0], ReserveDataUpdated)
    assert records[0].reserve == "0xReserve"
    assert records[0].liquidity_rate == 0.05

    # 2. Test LiquidationCall msg
    msg_lc = {
        "type": "lending_update",
        "event": "LiquidationCall",
        "exchange": "base_onchain",
        "pool": "SEAMLESS",
        "timestamp": 1700000000,
        "collateral_asset": "0xCollateral",
        "debt_asset": "0xDebt",
        "user": "0xUser",
        "debt_to_cover": 500.0,
        "liquidated_collateral_amount": 2.5,
        "liquidator": "0xLiquidator",
        "receive_a_token": False
    }
    records_lc = list(connector.normalize(msg_lc, local_ts=1700000000500000000))
    assert len(records_lc) == 1
    assert isinstance(records_lc[0], LiquidationCall)
    assert records_lc[0].collateral_asset == "0xCollateral"
    assert records_lc[0].debt_to_cover == 500.0
    assert records_lc[0].receive_a_token is False

import os

import pytest
from unittest.mock import AsyncMock, MagicMock
from crypcodile.exchanges.superchain.gas_oracle import SuperchainGasOracle
from crypcodile.exchanges.superchain.connector import SuperchainConnector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.sink.base import Sink

@pytest.mark.asyncio
async def test_superchain_gas_oracle_queries():
    oracle = SuperchainGasOracle()
    
    # Mock Web3
    w3 = AsyncMock()
    mock_contract = MagicMock()
    # Setup call chain: contract.functions.l1BaseFee().call()
    mock_contract.functions.l1BaseFee.return_value.call = AsyncMock(return_value=123456789)
    mock_contract.functions.overhead.return_value.call = AsyncMock(return_value=2100)
    mock_contract.functions.scalar.return_value.call = AsyncMock(return_value=684000)
    mock_contract.functions.getL1Fee.return_value.call = AsyncMock(return_value=456)
    
    w3.eth.contract = MagicMock(return_value=mock_contract)
    
    # Query gas metrics
    base_fee = await oracle.get_l1_base_fee(w3)
    assert base_fee == 123456789
    
    overhead = await oracle.get_l1_overhead(w3)
    assert overhead == 2100
    
    scalar = await oracle.get_l1_scalar(w3)
    assert scalar == 684000
    
    l1_fee = await oracle.get_l1_fee_for_calldata(w3, b"\x00\x01\x02")
    assert l1_fee == 456

def test_superchain_connector_initialization(monkeypatch):
    registry = InstrumentRegistry()
    sink = MagicMock(spec=Sink)

    # Ensure parent would not pick up RPC from a polluted environment.
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    before_env = dict(os.environ)

    # Test initialization with standard settings
    connector = SuperchainConnector(
        symbols=["AERO-USDC"],
        channels=["trade"],
        out=sink,
        registry=registry,
        rpc_url="http://localhost:8545",
        chain_id=10,  # Optimism
    )

    assert connector.chain_id == 10
    assert connector.name == "superchain"
    assert connector.transport.exchange == "superchain"
    assert connector.transport.rpc_urls == ["http://localhost:8545"]
    # Per-exchange SyncRecovery state path (not hardcoded base_onchain.json).
    assert connector.transport.sync_recovery.state_path.endswith("superchain.json")
    # Must not mutate process environment for RPC configuration.
    assert os.environ.get("BASE_RPC_URL") is None
    assert os.environ == before_env


def test_superchain_connector_custom_exchange_name(monkeypatch):
    """Custom exchange identity propagates to transport recovery state file."""
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    connector = SuperchainConnector(
        symbols=["OP-USDC"],
        channels=["trade"],
        out=MagicMock(spec=Sink),
        registry=InstrumentRegistry(),
        rpc_url="http://localhost:8545",
        chain_id=10,
        exchange="optimism",
    )
    assert connector.name == "optimism"
    assert connector.transport.exchange == "optimism"
    assert connector.transport.sync_recovery.state_path.endswith("optimism.json")

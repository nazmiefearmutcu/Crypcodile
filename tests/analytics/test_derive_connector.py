from __future__ import annotations

import time
from unittest.mock import MagicMock, patch
import pytest

from crypcodile.analytics.blackscholes import PureMathGreeksSolverAdapter
from crypcodile.exchanges.derive.connector import DeriveConnector
from crypcodile.schema.enums import OptType
from crypcodile.schema.records import OptionsChain


def test_derive_connector_connection() -> None:
    """Test that connect initializes the Web3 and viewer contract objects."""
    connector = DeriveConnector(rpc_url="http://dummy-rpc.com")
    assert connector.w3 is None
    assert connector.viewer_contract is None

    with patch("crypcodile.exchanges.derive.connector.Web3") as mock_web3:
        mock_w3_instance = MagicMock()
        mock_web3.return_value = mock_w3_instance
        connector.connect()

        assert connector.w3 is not None
        assert connector.viewer_contract is not None
        mock_web3.assert_called_once_with(mock_web3.HTTPProvider.return_value)
        mock_web3.HTTPProvider.assert_called_once_with("http://dummy-rpc.com")


def test_derive_connector_fetch_and_normalize() -> None:
    """Test fetching options chain, underlying filtering, decimal scaling, and field mapping."""
    connector = DeriveConnector(rpc_url="http://dummy-rpc.com")

    # Connect mock setup
    connector.w3 = MagicMock()
    mock_contract = MagicMock()
    connector.viewer_contract = mock_contract

    # Create dummy markets data scaled by 1e18
    now = int(time.time())
    mock_expiry = now + 86400 * 7  # 7 days expiry

    mock_markets_data = [
        # BTC Call option
        (
            "0x1111111111111111111111111111111111111111",
            "BTC",
            int(60000 * 1e18),  # strike
            mock_expiry,        # expiry
            True,               # isCall
            int(1500 * 1e18),   # price
            int(0.50 * 1e18),   # mark_iv
            int(1480 * 1e18),   # bidPrice
            int(2 * 1e18),      # bidSize
            int(1520 * 1e18),   # askPrice
            int(3 * 1e18),      # askSize
            int(50 * 1e18),     # openInterest
        ),
        # BTC Put option
        (
            "0x2222222222222222222222222222222222222222",
            "BTC",
            int(58000 * 1e18),
            mock_expiry,
            False,              # isCall (PUT)
            int(1200 * 1e18),
            int(0.55 * 1e18),
            int(1180 * 1e18),
            int(4 * 1e18),
            int(1220 * 1e18),
            int(5 * 1e18),
            int(40 * 1e18),
        ),
        # ETH option (should be filtered out when querying BTC)
        (
            "0x3333333333333333333333333333333333333333",
            "ETH",
            int(3000 * 1e18),
            mock_expiry,
            True,
            int(100 * 1e18),
            int(0.60 * 1e18),
            int(95 * 1e18),
            int(10 * 1e18),
            int(105 * 1e18),
            int(12 * 1e18),
            int(200 * 1e18),
        ),
    ]

    mock_contract.functions.getMarkets.return_value.call.return_value = mock_markets_data

    # Query for BTC
    chains = connector.fetch_options_chain(underlying_symbol="BTC", underlying_price=60000.0)

    # Check results
    assert len(chains) == 2
    for chain in chains:
        assert isinstance(chain, OptionsChain)
        assert chain.exchange == "derive"
        assert chain.underlying == "BTC"
        assert chain.underlying_price == 60000.0
        assert chain.expiry == mock_expiry

    # Verify first (Call) option values
    c_chain = [c for c in chains if c.opt_type == OptType.CALL][0]
    assert c_chain.strike == pytest.approx(60000.0)
    assert c_chain.mark_price == pytest.approx(1500.0)
    assert c_chain.mark_iv == pytest.approx(0.50)
    assert c_chain.bid_px == pytest.approx(1480.0)
    assert c_chain.bid_sz == pytest.approx(2.0)
    assert c_chain.ask_px == pytest.approx(1520.0)
    assert c_chain.ask_sz == pytest.approx(3.0)
    assert c_chain.open_interest == pytest.approx(50.0)
    assert c_chain.delta is None  # no solver passed

    # Verify second (Put) option values
    p_chain = [p for p in chains if p.opt_type == OptType.PUT][0]
    assert p_chain.strike == pytest.approx(58000.0)
    assert p_chain.mark_price == pytest.approx(1200.0)
    assert p_chain.mark_iv == pytest.approx(0.55)
    assert p_chain.bid_px == pytest.approx(1180.0)
    assert p_chain.bid_sz == pytest.approx(4.0)
    assert p_chain.ask_px == pytest.approx(1220.0)
    assert p_chain.ask_sz == pytest.approx(5.0)
    assert p_chain.open_interest == pytest.approx(40.0)


def test_derive_connector_greeks_calculation() -> None:
    """Test that Greeks are calculated and populated correctly when a solver is passed."""
    connector = DeriveConnector(rpc_url="http://dummy-rpc.com")
    connector.w3 = MagicMock()
    mock_contract = MagicMock()
    connector.viewer_contract = mock_contract

    now = int(time.time())
    mock_expiry = now + 86400 * 30  # 30 days expiry

    mock_markets_data = [
        (
            "0x1111111111111111111111111111111111111111",
            "BTC",
            int(60000 * 1e18),
            mock_expiry,
            True,
            int(2500 * 1e18),
            int(0.40 * 1e18),  # 40% IV
            int(2400 * 1e18),
            int(1 * 1e18),
            int(2600 * 1e18),
            int(1 * 1e18),
            int(10 * 1e18),
        )
    ]
    mock_contract.functions.getMarkets.return_value.call.return_value = mock_markets_data

    # Use pure math solver
    solver = PureMathGreeksSolverAdapter()
    chains = connector.fetch_options_chain(
        underlying_symbol="BTC",
        underlying_price=60000.0,
        greeks_solver=solver,
        rate=0.02,
    )

    assert len(chains) == 1
    opt = chains[0]
    assert opt.delta is not None
    assert opt.gamma is not None
    assert opt.vega is not None
    assert opt.theta is not None
    assert opt.rho is not None

    # Delta should be around 0.5 for ATM Call
    assert 0.4 < opt.delta < 0.6
    assert opt.gamma > 0.0
    assert opt.vega > 0.0
    assert opt.theta < 0.0

from __future__ import annotations

import logging
import time
from typing import Any

from web3 import Web3

from crypcodile.analytics.blackscholes import GreeksSolverAdapter
from crypcodile.schema.enums import OptType
from crypcodile.schema.records import OptionsChain

log = logging.getLogger(__name__)

# ABI for querying the option markets.
# Matches common Lyra V2/Derive viewer contract design for fetching option markets data.
MARKET_VIEWER_ABI = [
    {
        "inputs": [],
        "name": "getMarkets",
        "outputs": [
            {
                "components": [
                    {"name": "marketAddress", "type": "address"},
                    {"name": "underlying", "type": "string"},
                    {"name": "strike", "type": "uint256"},
                    {"name": "expiry", "type": "uint256"},
                    {"name": "isCall", "type": "bool"},
                    {"name": "price", "type": "uint256"},
                    {"name": "iv", "type": "uint256"},
                    {"name": "bidPrice", "type": "uint256"},
                    {"name": "bidSize", "type": "uint256"},
                    {"name": "askPrice", "type": "uint256"},
                    {"name": "askSize", "type": "uint256"},
                    {"name": "openInterest", "type": "uint256"},
                ],
                "name": "",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


class DeriveConnector:
    """Web3-based connector for Derive/Lyra options chain."""

    def __init__(self, rpc_url: str, viewer_address: str | None = None) -> None:
        self.rpc_url = rpc_url
        self.viewer_address = viewer_address or "0xDe711De711De711De711De711De711De711De711"
        self.w3: Web3 | None = None
        self.viewer_contract: Any = None

    def connect(self) -> None:
        """Connect to the Web3 provider and initialize contracts."""
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.viewer_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.viewer_address),
            abi=MARKET_VIEWER_ABI
        )

    def fetch_options_chain(
        self,
        underlying_symbol: str = "BTC",
        underlying_price: float = 60000.0,
        greeks_solver: GreeksSolverAdapter | None = None,
        rate: float = 0.0,
    ) -> list[OptionsChain]:
        """Query the option markets and normalize into OptionsChain records.

        If a greeks_solver is provided, the option Greeks (delta, gamma, vega, theta, rho)
        will be calculated dynamically.
        """
        if not self.w3 or not self.viewer_contract:
            raise RuntimeError("Connector is not connected. Call connect() first.")

        try:
            markets_data = self.viewer_contract.functions.getMarkets().call()
        except Exception as e:
            log.error(f"Failed to query Derive markets: {e}")
            return []

        local_ts = int(time.time() * 1000)
        chains = []

        for market in markets_data:
            # market elements:
            # 0: marketAddress (address)
            # 1: underlying (string)
            # 2: strike (uint256)
            # 3: expiry (uint256)
            # 4: isCall (bool)
            # 5: price (uint256)
            # 6: iv (uint256)
            # 7: bidPrice (uint256)
            # 8: bidSize (uint256)
            # 9: askPrice (uint256)
            # 10: askSize (uint256)
            # 11: openInterest (uint256)
            m_underlying = market[1]
            if m_underlying.upper() != underlying_symbol.upper():
                continue

            strike = round(float(market[2]) / 1e18, 8)
            expiry = int(market[3])
            is_call = market[4]
            opt_type = OptType.CALL if is_call else OptType.PUT

            mark_price = round(float(market[5]) / 1e18, 8)
            mark_iv = round(float(market[6]) / 1e18, 8)
            bid_px = round(float(market[7]) / 1e18, 8)
            bid_sz = round(float(market[8]) / 1e18, 8)
            ask_px = round(float(market[9]) / 1e18, 8)
            ask_sz = round(float(market[10]) / 1e18, 8)
            open_interest = round(float(market[11]) / 1e18, 8)

            # Calculate expiries in years for Greeks calculation
            t_years = max(0.0, (expiry - (local_ts / 1000.0)) / (365.25 * 86400.0))

            delta = None
            gamma = None
            vega = None
            theta = None
            rho = None

            if greeks_solver is not None and t_years > 0.0 and mark_iv > 0.0:
                try:
                    g = greeks_solver.greeks(
                        forward=underlying_price,
                        strike=strike,
                        t_years=t_years,
                        vol=mark_iv,
                        opt_type=opt_type,
                        rate=rate,
                    )
                    delta = g.delta
                    gamma = g.gamma
                    vega = g.vega
                    theta = g.theta
                    rho = g.rho
                except Exception as ex:
                    log.warning(f"Error computing Greeks for {underlying_symbol} {strike} {opt_type}: {ex}")

            # naming conventions
            expiry_date_str = time.strftime("%y%m%d", time.gmtime(expiry))
            strike_str = str(int(strike))
            type_char = "C" if is_call else "P"
            symbol_raw = f"{underlying_symbol}-{expiry_date_str}-{strike_str}-{type_char}"
            symbol = f"{underlying_symbol}_{symbol_raw}"

            record = OptionsChain(
                exchange="derive",
                symbol=symbol,
                symbol_raw=symbol_raw,
                exchange_ts=local_ts,
                local_ts=local_ts,
                underlying=underlying_symbol,
                underlying_price=underlying_price,
                strike=strike,
                expiry=expiry,
                opt_type=opt_type,
                mark_price=mark_price,
                mark_iv=mark_iv,
                bid_px=bid_px,
                bid_sz=bid_sz,
                bid_iv=mark_iv,
                ask_px=ask_px,
                ask_sz=ask_sz,
                ask_iv=mark_iv,
                last_price=mark_price,
                open_interest=open_interest,
                delta=delta,
                gamma=gamma,
                vega=vega,
                theta=theta,
                rho=rho,
            )
            chains.append(record)

        return chains

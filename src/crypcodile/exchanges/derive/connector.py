from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Iterable
from typing import Any

from web3 import Web3

from crypcodile.analytics.blackscholes import GreeksSolverAdapter
from crypcodile.exchanges.base import Connector, backoff_delays
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import OptType
from crypcodile.schema.records import OptionsChain, Record
from crypcodile.sink.base import Sink

log = logging.getLogger(__name__)

# Default public Base RPC — Derive/Lyra V2 markets live on Base L2.
_DEFAULT_RPC_URL = "https://base-rpc.publicnode.com"
_DEFAULT_VIEWER = "0xDe711De711De711De711De711De711De711De711"
_DEFAULT_POLL_INTERVAL = 30.0

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
    """Web3-based client for Derive/Lyra options chain (pull / RPC).

    Not a :class:`~crypcodile.exchanges.base.Connector` by itself — use
    :class:`DerivePollConnector` for collect/factory integration.
    """

    def __init__(self, rpc_url: str, viewer_address: str | None = None) -> None:
        self.rpc_url = rpc_url
        self.viewer_address = viewer_address or _DEFAULT_VIEWER
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


def _underlying_from_symbol(symbol: str) -> str:
    """Map a collect symbol to a Derive underlying ticker.

    Accepts plain underlyings (``BTC``, ``ETH``) and common prefixed forms
    (``BTC-USD``, ``DERIVE:BTC``).
    """
    core = symbol.split(":")[-1]
    return core.split("-")[0].upper()


class DerivePollConnector(Connector):
    """Poll-style :class:`~crypcodile.exchanges.base.Connector` for Derive options.

    Wraps :class:`DeriveConnector`: each poll cycle calls
    :meth:`DeriveConnector.fetch_options_chain` for every configured underlying
    and writes :class:`~crypcodile.schema.records.OptionsChain` records into
    the sink.  No websocket transport is used; :meth:`run` owns the loop.

    Factory / constructor kwargs
    ----------------------------
    rpc_url:
        Web3 HTTP RPC endpoint (default: Base public RPC).
    viewer_address:
        Market viewer contract address (optional; placeholder default).
    poll_interval:
        Seconds between successful poll cycles (default ``30.0``).
    underlying_price:
        Spot/forward used for record fields and optional greeks (default
        ``60000.0``).  Prefer per-underlying maps in a future revision.
    greeks_solver:
        Optional :class:`~crypcodile.analytics.blackscholes.GreeksSolverAdapter`.
    rate:
        Risk-free rate for greeks (default ``0.0``).
    """

    name = "derive"
    ws_url = ""
    rest_url = _DEFAULT_RPC_URL

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        *,
        rpc_url: str = _DEFAULT_RPC_URL,
        viewer_address: str | None = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        underlying_price: float = 60000.0,
        greeks_solver: GreeksSolverAdapter | None = None,
        rate: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            symbols=symbols,
            channels=channels,
            out=out,
            registry=registry,
        )
        self.rpc_url = rpc_url
        self.viewer_address = viewer_address or _DEFAULT_VIEWER
        self.poll_interval = poll_interval
        self.underlying_price = underlying_price
        self.greeks_solver = greeks_solver
        self.rate = rate
        # Low-level Web3 pull client; connect() is deferred to run().
        self.client = DeriveConnector(
            rpc_url=self.rpc_url,
            viewer_address=self.viewer_address,
        )
        # No WS transport — run() polls directly.
        self.transport = None

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        """No-op: options records are produced by :meth:`fetch_options_chain`."""
        return ()

    async def list_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        for symbol in self.symbols:
            underlying = _underlying_from_symbol(symbol)
            instruments.append(
                Instrument(
                    canonical=symbol,
                    exchange=self.name,
                    symbol_raw=symbol,
                    kind=Kind.OPTION,
                    base=underlying,
                    quote="USD",
                )
            )
        return instruments

    def subscribe_channels(self) -> list[str]:
        return list(self.channels) if self.channels else ["options_chain"]

    async def _subscribe(self, transport: Transport) -> None:
        """No-op — Derive is pull-only (no websocket subscribe)."""

    async def _poll_once(self) -> int:
        """Fetch options for all symbols; put each chain into the sink.

        Returns the number of records written.
        """
        if self.client.w3 is None or self.client.viewer_contract is None:
            await asyncio.to_thread(self.client.connect)

        written = 0
        for symbol in self.symbols:
            underlying = _underlying_from_symbol(symbol)
            chains = await asyncio.to_thread(
                self.client.fetch_options_chain,
                underlying,
                self.underlying_price,
                self.greeks_solver,
                self.rate,
            )
            for rec in chains:
                await self.out.put(rec)
                written += 1
        return written

    async def run(self, max_reconnects: int = -1) -> None:
        """Supervised poll loop: fetch options chain → sink → sleep.

        Honours *max_reconnects* for consecutive poll failures (``-1`` =
        unlimited, ``0`` = fail after first error without retry).  Successful
        cycles reset the failure counter.  Cancel with task cancellation
        (SIGINT / collect shutdown).
        """
        attempt = 0
        try:
            while True:
                try:
                    n = await self._poll_once()
                    log.debug(
                        "derive: polled %d OptionsChain record(s) for %s",
                        n,
                        self.symbols,
                    )
                    attempt = 0
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "Connector %s poll error (attempt %d): %s",
                        self.name,
                        attempt,
                        exc,
                    )
                    if max_reconnects == 0 or (
                        max_reconnects > 0 and attempt >= max_reconnects
                    ):
                        raise
                    delay = backoff_delays(
                        attempt, jitter=0.25, rand=random.random()
                    )
                    log.info("derive: retrying poll in %.2fs...", delay)
                    await asyncio.sleep(delay)
                    attempt += 1
                    # Force reconnect on next poll.
                    self.client.w3 = None
                    self.client.viewer_contract = None
        finally:
            if self._session is not None and not self._session.closed:
                await self._session.close()

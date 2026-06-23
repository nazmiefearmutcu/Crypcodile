import os
import logging
from typing import Any, Iterable
from crypcodile.exchanges.base import Connector
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink
from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector

log = logging.getLogger(__name__)

class SuperchainConnector(BaseOnchainConnector):
    """Generalized Superchain (OP Stack) on-chain connector.
    
    Supports Optimism, Base, Mode, Zora, and other Superchain rollups by 
    configuring RPC endpoints, target chain IDs, and precompile parameters.
    """

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        rpc_url: str | None = None,
        chain_id: int = 10, # default to Optimism Mainnet
        custom_pools: dict[str, dict[str, Any]] | None = None,
        exchange: str = "optimism",
        **kwargs: Any
    ) -> None:
        # Override default Base RPC with custom chain RPC if specified
        if rpc_url:
            os.environ["BASE_RPC_URL"] = rpc_url
        super().__init__(
            symbols=symbols,
            channels=channels,
            out=out,
            registry=registry,
            custom_pools=custom_pools,
            **kwargs
        )
        self.chain_id = chain_id
        self.name = exchange
        log.info(f"Initialized SuperchainConnector for chain ID {self.chain_id} using RPC: {rpc_url or 'default'}")

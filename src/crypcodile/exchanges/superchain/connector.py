import logging
from typing import Any

from crypcodile.exchanges.base_onchain.connector import BaseOnchainConnector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.sink.base import Sink

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
        chain_id: int = 10,  # default to Optimism Mainnet
        custom_pools: dict[str, dict[str, Any]] | None = None,
        exchange: str = "superchain",
        **kwargs: Any,
    ) -> None:
        # Set identity before parent init so transport uses the correct exchange name.
        self.name = exchange
        self.chain_id = chain_id
        # Pass rpc_url to parent; do not mutate os.environ.
        super().__init__(
            symbols=symbols,
            channels=channels,
            out=out,
            registry=registry,
            custom_pools=custom_pools,
            rpc_url=rpc_url,
            **kwargs,
        )
        log.info(
            "Initialized SuperchainConnector for chain ID %s using RPC: %s",
            self.chain_id,
            rpc_url or "default",
        )

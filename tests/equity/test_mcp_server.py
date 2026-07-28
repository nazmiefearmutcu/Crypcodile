"""What is left of the equity MCP server: an async Web3 context manager, rehomed.

The file this used to import — ``crocodile.equity.legacy.mcp_server`` — was a copy of the
crypto one inside an equities library. Everything in it that answered a question is a
capability now, projected by :mod:`crocodile.surfaces.mcp`, and Gate 4
(``tests/conformance/test_surfaces.py``) owns the tool list. The only part that was not a
hand-copy of something the registry declares is the pair of Base-mainnet pool readers,
which moved to :mod:`crocodile.crypto.exchanges.base_onchain.price` — hence a test in the
equity tree still naming a crypto module. It stays here rather than moving because this is
the file that recorded the property, and ``tests/exchanges/base_onchain/test_servers.py``
covers the two readers themselves but never constructs the real :class:`AsyncWeb3`.

Two tests left with their subject. ``test_get_onchain_price_stock_fallback`` and
``test_get_base_market_data_stock_fallback`` pinned the fork's yfinance branch: eight
hardcoded tickers answered with ``pool_address="equity_feed"`` and reserves of ``0.0``, and
a ninth fell through to a list of Base DEX pools. That branch was deliberately not carried
across — ``search`` and ``indicators`` answer an equity question out of the lake, for every
ticker — so there is nothing left for those two to assert against.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("web3")

from crocodile.crypto.exchanges.base_onchain.price import AsyncWeb3


async def test_async_web3_export() -> None:
    """``AsyncWeb3`` is importable, and leaving its ``async with`` disconnects the provider.

    The disconnect is the whole reason for the subclass: every RPC call in ``price.py`` goes
    through ``execute_with_retry_and_failover``, which opens one of these per attempt and
    can try six times across two URLs. A context manager that did not close its provider
    would leak a session per attempt.
    """
    from web3.providers.async_base import AsyncBaseProvider

    mock_provider = MagicMock(spec=AsyncBaseProvider)
    mock_provider.disconnect = AsyncMock()

    async with AsyncWeb3(mock_provider) as w3:
        assert w3 is not None

    mock_provider.disconnect.assert_called_once()

"""Connector hardening: deterministic errors are not retried, and IPC never blocks the loop.

The four payment tests that shared this file — block-timestamp freshness, the on-disk
``PAYMENTS_DB`` file, ``_save_db_file``'s atomic write, and ``PersistentDict._sync``'s error
logging — went with the x402 gate; the ledger that survives is
:mod:`crocodile.surfaces.payments`, which is not this package's subject.
"""

import pytest
from unittest.mock import MagicMock, patch

from crocodile.crypto.exchanges.base_onchain.connector import BaseOnchainTransport

# Mock exceptions
from web3.exceptions import ContractLogicError


@pytest.mark.asyncio
async def test_deterministic_exceptions_not_retried() -> None:
    """Verify that deterministic exceptions like ContractLogicError are not retried in _call_with_retry."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=1.0)

    call_count = 0
    async def mock_deterministic_fail():
        nonlocal call_count
        call_count += 1
        raise ContractLogicError("Execution reverted")

    with pytest.raises(ContractLogicError, match="Execution reverted"):
        await transport._call_with_retry(mock_deterministic_fail)

    # Should only attempt once
    assert call_count == 1


@pytest.mark.asyncio
async def test_non_blocking_ipc() -> None:
    """Verify that _load_ipc is a coroutine function (async/non-blocking) and uses to_thread."""
    import inspect
    from crocodile.crypto.exchanges.base_onchain.connector import _load_ipc

    assert inspect.iscoroutinefunction(_load_ipc)

@pytest.mark.asyncio
async def test_write_ipc_non_blocking() -> None:
    """Verify that _write_ipc creates an asyncio task with asyncio.to_thread."""
    from crocodile.crypto.exchanges.base_onchain.connector import IPCDict

    ipc_dict = IPCDict("TEST_WRITE_IPC")

    with patch("asyncio.to_thread") as mock_to_thread, \
         patch("asyncio.get_running_loop") as mock_get_loop:

        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop

        ipc_dict._write_ipc()

        # Check that asyncio.to_thread was called
        mock_to_thread.assert_called_once()
        # Check that loop.create_task was called with the returned coroutine
        mock_loop.create_task.assert_called_once()

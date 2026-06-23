import asyncio
import json
import pytest
from crypcodile.exchanges.base_onchain.faucet_mock import BaseSepoliaFaucetMockStream

@pytest.mark.asyncio
async def test_faucet_mock_stream_generation():
    queue = asyncio.Queue()
    # Use small intervals to speed up tests
    stream = BaseSepoliaFaucetMockStream(queue, interval_min=0.01, interval_max=0.05)
    
    stream.start()
    assert stream._running is True
    
    # Wait for at least one message to arrive in the queue
    try:
        msg_bytes = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg_bytes is not None
        
        # Verify content
        msg = json.loads(msg_bytes.decode())
        assert msg["type"] == "onchain_update"
        assert msg["pool"] == "ETH-FAUCET"
        assert msg["pool_type"] == "faucet"
        
        swaps = msg["swaps"]
        assert len(swaps) == 1
        assert swaps[0]["price"] == 0.0
        assert swaps[0]["amount"] > 0
        assert swaps[0]["sender"].startswith("0x")
    finally:
        await stream.stop()
        assert stream._running is False

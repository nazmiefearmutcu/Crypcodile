"""Unit tests for AiohttpWsTransport.send() — binary vs text frame regression.

Root-cause bug: send() used send_bytes() which Deribit silently rejects with
bad_request (code 11050).  Fix: decode to str and use send_str() instead.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from crypcodile.ingest.transport import AiohttpWsTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_transport_with_fake_ws() -> tuple[AiohttpWsTransport, MagicMock]:
    """Return an AiohttpWsTransport whose _ws is pre-set to a MagicMock."""
    transport = AiohttpWsTransport("wss://fake.example.com/ws")
    fake_ws = MagicMock()
    fake_ws.send_str = AsyncMock()
    fake_ws.send_bytes = AsyncMock()
    transport._ws = fake_ws  # inject without calling connect()
    return transport, fake_ws


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAiohttpWsTransportSend:
    def test_send_uses_text_frame_not_binary(self) -> None:
        """send() must call send_str with the decoded string, not send_bytes."""
        transport, fake_ws = _make_transport_with_fake_ws()
        payload = b'{"hello":"world"}'

        asyncio.run(transport.send(payload))

        fake_ws.send_str.assert_awaited_once_with('{"hello":"world"}')
        fake_ws.send_bytes.assert_not_called()

    def test_send_noop_when_ws_is_none(self) -> None:
        """send() must be a no-op (no exception) when _ws is None."""
        transport = AiohttpWsTransport("wss://fake.example.com/ws")
        assert transport._ws is None
        # Should not raise
        asyncio.run(transport.send(b'{"op":"ping"}'))

    def test_send_str_receives_decoded_unicode(self) -> None:
        """Payload with unicode round-trips correctly through decode."""
        transport, fake_ws = _make_transport_with_fake_ws()
        # A realistic Deribit subscribe message (kept as a bytes literal)
        channel = "book.BTC-PERPETUAL.100ms"
        payload = (
            b'{"jsonrpc":"2.0","method":"public/subscribe",'
            b'"params":{"channels":["book.BTC-PERPETUAL.100ms"]},"id":1}'
        )

        asyncio.run(transport.send(payload))

        expected = (
            '{"jsonrpc":"2.0","method":"public/subscribe",'
            f'"params":{{"channels":["{channel}"]}},"id":1}}'
        )
        fake_ws.send_str.assert_awaited_once_with(expected)
        fake_ws.send_bytes.assert_not_called()

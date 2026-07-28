"""Rate limiting on the one payment route the merge keeps.

Two tests left, and both are deliberate behaviour changes rather than gaps.

``test_cors_headers`` asserted ``access-control-allow-origin: *`` on the paid data route.
The merged server adds no CORS middleware at all unless ``CROCODILE_CORS_ORIGINS`` names
the origins, so there is no wildcard left to assert — it was there for a demo dashboard
served from this same origin, which did not need it, and it made a payment-gated route
spendable from any page in a visitor's browser.

``test_rate_limiting_market_data`` asserted 100 requests then 429 on
``GET /api/v1/market-data``. That route is gone with the x402 verifier that gated it, and
the limiter is now applied to ``POST /api/v1/simulate-payment`` and nothing else: **no
capability GET route is rate limited**. That is a real reduction in coverage of the
deployed server and is recorded here rather than left to be discovered.
"""

import asyncio

import aiohttp
import pytest


@pytest.mark.asyncio
async def test_rate_limiting_simulate_payment(api_server: str) -> None:
    """A hundred concurrent claims are served, the hundred-and-first is refused.

    Concurrent rather than sequential on purpose: the window is guarded by a
    ``threading.Lock`` and a limiter that counted correctly only when asked one request at a
    time would not be one.
    """
    async with aiohttp.ClientSession() as session:
        payload = {"payment_id": "dummy", "tx_hash": "0xhash", "signature": "0x" + "0" * 130}

        async def make_req() -> int:
            async with session.post(f"{api_server}/api/v1/simulate-payment", json=payload) as resp:
                return resp.status

        statuses = await asyncio.gather(*(make_req() for _ in range(100)))
        for status in statuses:
            # A malformed signature or an unknown payment id — but never 429.
            assert status in (400, 404)

        async with session.post(f"{api_server}/api/v1/simulate-payment", json=payload) as resp:
            assert resp.status == 429
            data = await resp.json()
            assert data["detail"] == "Too Many Requests"

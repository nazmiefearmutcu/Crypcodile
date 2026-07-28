"""Rate limiting on the one payment route the merge keeps.

Both CORS tests and the `market-data` rate-limit test left: `GET /api/v1/market-data` no
longer exists, and CORS is no longer wildcard-by-default — the server adds no CORS
middleware at all unless `CROCODILE_CORS_ORIGINS` names the origins, so there is no
`access-control-allow-origin: *` left to assert.
"""

import aiohttp
import pytest


@pytest.mark.asyncio
async def test_rate_limiting_simulate_payment(api_server) -> None:
    async with aiohttp.ClientSession() as session:
        # Generate a dummy payload
        payload = {
            "payment_id": "dummy",
            "tx_hash": "0xhash",
            "signature": "0x" + "0" * 130
        }
        # 100 requests to /api/v1/simulate-payment
        # Since it is a dummy payment, it will fail validation or return 404, 400, etc., but not 429.
        for i in range(100):
            async with session.post(f"{api_server}/api/v1/simulate-payment", json=payload) as resp:
                # The response can be 400 or 404, but definitely not 429
                assert resp.status in (400, 404)

        # 101st request should be 429
        async with session.post(f"{api_server}/api/v1/simulate-payment", json=payload) as resp:
            assert resp.status == 429
            data = await resp.json()
            assert data["detail"] == "Too Many Requests"

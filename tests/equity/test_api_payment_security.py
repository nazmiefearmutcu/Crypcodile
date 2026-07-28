"""The x402 ledger's two surviving routes: simulate a payment, and dump the ledger.

These used to drive ``crocodile.equity.legacy.api_server``. The subject is now
:func:`crocodile.surfaces.server.build_server` plus :mod:`crocodile.surfaces.payments`, and
this file is the only coverage either has, so the properties that survived the merge are
pinned here rather than assumed.

Six tests left with the half of x402 that did not come across — the on-chain verification
path. ``get_market_data`` fetched a USDC transfer receipt over RPC, matched the Transfer
topic against a hardcoded contract, compared the amount to ``PRICE_USDC`` and only then
served a Base DEX price. That verifier gated exactly one route, the route is not in the
capability registry, and neither is carried across, so:

* ``test_symbol_binding_on_redeem`` (the paid answer must be for the symbol that was paid
  for), ``test_two_phase_refund_on_data_failure`` (a failed fetch refunds ``spent`` back to
  ``paid``) and ``test_concurrent_double_redeem_one_wins`` all pinned redeeming a payment
  for data. Nothing redeems anything now. The last one's *real* subject — read, decide and
  write the ledger under one lock — moved to ``simulate-payment``, which is the remaining
  route that does exactly that, and is kept below under that name.
* ``test_metrics_token_enforced`` pinned ``METRICS_TOKEN`` / ``X-Metrics-Token`` on
  ``/metrics``. The merged server serves ``/metrics`` unauthenticated; the token is gone.
* ``test_allow_simulation_false_without_pytest`` pinned a branch that read ``sys.modules``
  and enabled simulation whenever pytest was imported. The new helper reads one environment
  variable and nothing else, so there is no pytest branch left to defeat.
* ``test_default_payments_file_path`` pinned ``STOCKODILE_HOME`` / ``~/.stockodile``; see
  ``tests/equity/test_path_defaults.py`` for why that root moved.

``test_simulate_payment_rejects_a_reused_tx_hash`` is the one arrival: the replay check it
asserts was pinned only by ``tests/equity/e2e/test_tier4_real_world.py``, through the
deleted redeem path, and the check itself is still in ``simulate-payment``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("eth_account")

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

# Building the app imports FastAPI and eth_account; allow headroom under pytest-timeout's
# thread method.
pytestmark = pytest.mark.timeout(120)


@pytest.fixture()
def payments_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the ledger at a file of this test's own and turn simulation on."""
    path = tmp_path / "payments_db.json"
    monkeypatch.setenv("PAYMENTS_FILE", str(path))
    monkeypatch.setenv("ALLOW_SIMULATION", "true")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    return path


@pytest.fixture()
def client(payments_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The deployable app, with a limiter of its own.

    ``server._RATE_LIMITER`` is module state shared by every app this process builds, so a
    test that spends requests from it would bleed into the next one. The legacy fixture
    cleared the global for the same reason; replacing it is the same fix without reaching
    into the limiter's internals.
    """
    from crocodile.core.config import Settings
    from crocodile.surfaces import payments, server

    monkeypatch.setattr(
        server,
        "_RATE_LIMITER",
        payments.SlidingWindowRateLimiter(window_size=60.0, max_requests=100),
    )
    lake = tmp_path / "lake"
    lake.mkdir(parents=True, exist_ok=True)
    return TestClient(server.build_server(settings=Settings(data_dir=lake)))


def _sign(payment_id: str, key: str = "0x" + "1" * 64) -> tuple[str, str]:
    account = Account.from_key(key)
    signature = account.sign_message(encode_defunct(text=payment_id)).signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    return signature, account.address


def _seed(payments_file: Path, payment_id: str, record: dict[str, Any]) -> None:
    """Put one row in the ledger.

    The legacy tests minted a pending row by asking for the paid route and reading the 402
    challenge back. There is no paid route to challenge anyone now, and the ledger is a file
    with a documented shape, so the row is written directly.
    """
    ledger: dict[str, Any] = (
        json.loads(payments_file.read_text()) if payments_file.exists() else {}
    )
    ledger[payment_id] = record
    payments_file.parent.mkdir(parents=True, exist_ok=True)
    payments_file.write_text(json.dumps(ledger))


def _ledger(payments_file: Path) -> dict[str, Any]:
    return dict(json.loads(payments_file.read_text()))


# ---------------------------------------------------------------------------
# Simulation is opt-in
# ---------------------------------------------------------------------------


def test_allow_simulation_default_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset means off. Simulation marks a payment paid without one having been made.

    Asserted by calling the helper rather than by reading its source, which is what the
    legacy version did — a source scan passes just as happily on a default nobody reaches.
    """
    from crocodile.surfaces import server

    monkeypatch.delenv("ALLOW_SIMULATION", raising=False)
    assert server._allow_simulation() is False

    monkeypatch.setenv("ALLOW_SIMULATION", "true")
    assert server._allow_simulation() is True


def test_simulate_payment_disabled_when_allow_simulation_false(
    client: TestClient, payments_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With simulation off the route refuses before it touches the ledger."""
    monkeypatch.setenv("ALLOW_SIMULATION", "false")
    _seed(payments_file, "pid-nosim", {"status": "pending"})
    signature, _ = _sign("pid-nosim")

    response = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "pid-nosim", "tx_hash": "0xnosim", "signature": signature},
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "simulat" in detail or "disabled" in detail
    assert _ledger(payments_file)["pid-nosim"]["status"] == "pending"


# ---------------------------------------------------------------------------
# The ledger refuses to be spent twice
# ---------------------------------------------------------------------------


def test_simulate_payment_rejects_spent(client: TestClient, payments_file: Path) -> None:
    """A row that has already been consumed is not pending, and is refused."""
    _seed(payments_file, "pid-spent", {"status": "pending"})
    signature, signer = _sign("pid-spent")
    payload = {"payment_id": "pid-spent", "tx_hash": "0xsim1", "signature": signature}

    accepted = client.post("/api/v1/simulate-payment", json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["payment_record"]["sender"] == signer

    record = _ledger(payments_file)["pid-spent"] | {"status": "spent"}
    _seed(payments_file, "pid-spent", record)

    refused = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "pid-spent", "tx_hash": "0xsim2", "signature": signature},
    )
    assert refused.status_code == 400
    detail = refused.json()["detail"].lower()
    assert "spent" in detail or "already" in detail


def test_simulate_payment_rejects_paid(client: TestClient, payments_file: Path) -> None:
    """The same row cannot be marked paid twice, which is the double-credit case."""
    _seed(payments_file, "pid-paid", {"status": "pending"})
    signature, _ = _sign("pid-paid")

    first = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "pid-paid", "tx_hash": "0xpaid1", "signature": signature},
    )
    assert first.status_code == 200, first.text
    assert _ledger(payments_file)["pid-paid"]["status"] == "paid"

    second = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "pid-paid", "tx_hash": "0xpaid2", "signature": signature},
    )
    assert second.status_code == 400
    detail = second.json()["detail"].lower()
    assert "paid" in detail or "already" in detail or "processed" in detail
    # The refused call must not have overwritten the hash the accepted one recorded.
    assert _ledger(payments_file)["pid-paid"]["tx_hash"] == "0xpaid1"


def test_simulate_payment_rejects_a_reused_tx_hash(
    client: TestClient, payments_file: Path
) -> None:
    """One transaction settles one payment id.

    Without this a caller mints a second challenge and presents the same settled
    transaction against it, paying once and being credited twice. The equity fork pinned it
    only through the on-chain redeem path, which is gone; the check itself is still on the
    route that writes the ledger.
    """
    _seed(payments_file, "pid-first", {"status": "pending"})
    _seed(payments_file, "pid-second", {"status": "pending"})
    tx_hash = "0x" + "e" * 64

    first_sig, _ = _sign("pid-first")
    accepted = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "pid-first", "tx_hash": tx_hash, "signature": first_sig},
    )
    assert accepted.status_code == 200, accepted.text

    second_sig, _ = _sign("pid-second")
    replayed = client.post(
        "/api/v1/simulate-payment",
        json={"payment_id": "pid-second", "tx_hash": tx_hash, "signature": second_sig},
    )
    assert replayed.status_code == 400
    assert "already processed" in replayed.json()["detail"].lower()
    assert _ledger(payments_file)["pid-second"]["status"] == "pending"


async def test_concurrent_simulate_one_wins(
    payments_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent claims on one pending id: exactly one 200, one 400, final ``paid``.

    Read-decide-write is one step or it is not safe, and this is the property the deleted
    ``test_concurrent_double_redeem_one_wins`` was really about — the redeem route is gone,
    the lock and the race are not. Uses ``httpx.AsyncClient`` because ``TestClient``
    serialises requests across threads and so cannot exercise an :class:`asyncio.Lock` at
    all.
    """
    from httpx import ASGITransport, AsyncClient

    from crocodile.core.config import Settings
    from crocodile.surfaces import payments, server

    monkeypatch.setattr(
        server,
        "_RATE_LIMITER",
        payments.SlidingWindowRateLimiter(window_size=60.0, max_requests=100),
    )
    lake = tmp_path / "lake"
    lake.mkdir(parents=True, exist_ok=True)
    app = server.build_server(settings=Settings(data_dir=lake))

    _seed(payments_file, "pid-race", {"status": "pending"})
    signature, _ = _sign("pid-race")
    payload = {"payment_id": "pid-race", "tx_hash": "0xrace", "signature": signature}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first, second = await asyncio.gather(
            ac.post("/api/v1/simulate-payment", json=payload),
            ac.post("/api/v1/simulate-payment", json=payload),
        )

    assert sorted([first.status_code, second.status_code]) == [200, 400]
    assert _ledger(payments_file)["pid-race"]["status"] == "paid"


# ---------------------------------------------------------------------------
# The ledger dump is behind a key, and says nothing when there is none
# ---------------------------------------------------------------------------


def test_admin_payments_hidden_without_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404, not 401: a 401 tells an unauthenticated caller the route is worth guessing at."""
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert client.get("/api/v1/admin/payments").status_code == 404


def test_admin_payments_requires_correct_token(
    client: TestClient, payments_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ADMIN_API_KEY`` / ``X-Admin-Key``, which is what the crypto fork used.

    The equity fork spelled these ``ADMIN_TOKEN`` / ``X-Admin-Token``; one route survived
    the merge and it is the crypto spelling, so an equity deployment renames its variable.
    ``Authorization: Bearer`` is accepted too, which neither fork's *equity* route was.
    """
    monkeypatch.setenv("ADMIN_API_KEY", "secret-admin")
    _seed(payments_file, "pid-listed", {"status": "pending"})

    assert client.get("/api/v1/admin/payments").status_code == 401
    assert client.get(
        "/api/v1/admin/payments", headers={"X-Admin-Key": "wrong"}
    ).status_code == 401

    allowed = client.get("/api/v1/admin/payments", headers={"X-Admin-Key": "secret-admin"})
    assert allowed.status_code == 200
    assert allowed.json()["pid-listed"]["status"] == "pending"

    bearer = client.get(
        "/api/v1/admin/payments", headers={"Authorization": "Bearer secret-admin"}
    )
    assert bearer.status_code == 200


# ---------------------------------------------------------------------------
# Telemetry, and the ledger's own durability
# ---------------------------------------------------------------------------


def test_metrics_open_without_token(client: TestClient) -> None:
    """``/metrics`` answers unauthenticated, and publishes the uptime gauge.

    The gauge assertion is the surviving half of ``test_metrics_token_enforced``: the token
    is gone, the exposition it was guarding is not, and it is spelled ``crocodile_`` now
    rather than ``stockodile_``.
    """
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "crocodile_uptime_seconds" in response.text
    assert "stockodile_uptime_seconds" not in response.text


def test_atomic_save_uses_replace(payments_file: Path) -> None:
    """A reader never observes a half-written ledger, and no ``.tmp`` is left behind."""
    from crocodile.surfaces.payments import PaymentsStore

    record = {"status": "pending", "symbol": "X"}
    asyncio.run(PaymentsStore().set("abc", record))

    assert payments_file.exists()
    assert not Path(str(payments_file) + ".tmp").exists()
    assert json.loads(payments_file.read_text()) == {"abc": record}

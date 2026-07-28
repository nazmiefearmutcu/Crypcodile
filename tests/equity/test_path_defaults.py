"""Safe defaults for machine-local paths and secrets (no hardcoded home dirs).

One test left, and its departure is a deliberate behaviour change rather than a gap.
``test_default_payments_file_uses_stockodile_home`` pinned
``equity/legacy/api_server._default_payments_file`` answering ``$STOCKODILE_HOME`` and then
``~/.stockodile/payments_db.json``. There is one ledger now, resolved by
:func:`crocodile.surfaces.payments.payments_path`, and it answers ``PAYMENTS_FILE`` first,
then a per-run temporary file under pytest, then ``~/.crypcodile/payments_db.json`` — the
crypto fork's root, kept because renaming it would orphan a live deployment's ledger. There
is no ``STOCKODILE_HOME`` branch to pin, so the assertion has no successor; what replaces it
is ``tests/equity/test_api_payment_security.py``, which drives the real ledger through the
route that administers it.

What survives here is the narrower claim the file was really for: no production module
resolves a path out of somebody's home checkout, and the one env override that *is* still
read by an equity-inherited module keeps working.
"""

from __future__ import annotations

import os

import pytest


def test_get_ipc_file_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``CUSTOM_POOLS_IPC_FILE`` overrides the Base on-chain IPC path.

    ``exchanges/base_onchain`` is a crypto module that the equity fork inherited
    and then edited. The merge kept the crypto original, so its default is the
    checkout-relative one and is *not* derived from ``STOCKODILE_HOME`` — the
    fork's home-derived variant went with its duplicate. Pinning that here keeps
    a later edit from quietly relocating an existing deployment's IPC file.
    """
    from crocodile.crypto.exchanges.base_onchain import connector as base_onchain

    monkeypatch.setenv("CUSTOM_POOLS_IPC_FILE", "/tmp/custom_ipc.json")
    assert base_onchain._get_ipc_file() == "/tmp/custom_ipc.json"

    monkeypatch.delenv("CUSTOM_POOLS_IPC_FILE", raising=False)
    monkeypatch.setenv("STOCKODILE_HOME", "/tmp/stockodile-home")
    assert base_onchain._get_ipc_file() == os.path.abspath(".custom_pools_ipc.json")


def test_no_machine_home_hardcodes_in_src() -> None:
    """Regression: production source under src/crocodile must not embed /Users/nazmi paths."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "src", "crocodile")
    root = os.path.abspath(root)
    assert os.path.isdir(root), f"package source not found at {root}"
    offenders: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if "/Users/nazmi/" in text:
                offenders.append(path)
    assert offenders == []

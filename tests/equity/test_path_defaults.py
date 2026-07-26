"""Safe defaults for machine-local paths and secrets (no hardcoded home dirs).

The package is now ``crocodile``, but the on-disk state roots are deliberately
not renamed with it: ``STOCKODILE_HOME`` and ``~/.stockodile`` still name where
an existing equity deployment keeps its state, and renaming them would orphan
those files silently. These tests pin the names so the rename cannot leak in.
"""

from __future__ import annotations

import os

import pytest


def test_default_payments_file_uses_stockodile_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """The equity state root is still ``STOCKODILE_HOME`` / ``~/.stockodile``.

    This assertion used to live on the equity fork's own copy of the Base
    on-chain connector, which the merge deduplicated away (see
    ``test_get_ipc_file_prefers_env``). The home-derived default it was really
    guarding survives on the equity module that owns it.
    """
    from crocodile.equity.legacy import api_server

    monkeypatch.delenv("STOCKODILE_HOME", raising=False)

    path = api_server._default_payments_file()
    assert path.endswith(os.path.join(".stockodile", "payments_db.json"))
    # Must not embed a machine-specific project checkout path (home expand is fine).
    assert not path.startswith("/Users/nazmi/Stockodile/")
    assert not path.startswith("/Users/nazmi/Desktop/Stockodile/")

    monkeypatch.setenv("STOCKODILE_HOME", "/tmp/stockodile-home")
    path = api_server._default_payments_file()
    assert path == os.path.join("/tmp/stockodile-home", "payments_db.json")


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

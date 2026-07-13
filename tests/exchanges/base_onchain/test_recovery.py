import os

import pytest
from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport
from crypcodile.ingest.sync_recovery import SyncRecovery
from crypcodile.ingest.rollback_manager import RollbackManager

def test_sync_recovery(tmp_path):
    state_path = str(tmp_path / "sync.json")
    recovery = SyncRecovery(state_path)
    
    assert recovery.get_last_block("AERO-USDC") is None
    
    recovery.save_last_block("AERO-USDC", 12345)
    
    recovery2 = SyncRecovery(state_path)
    assert recovery2.get_last_block("AERO-USDC") == 12345


def test_transport_sync_recovery_state_uses_exchange_name(tmp_path, monkeypatch):
    """SyncRecovery state file is per-exchange, not hardcoded base_onchain.json."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # expanduser("~") uses HOME on Unix; also cover cases that use Path.home().
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / p[2:]) if p.startswith("~/") else p)

    base = BaseOnchainTransport(
        "http://localhost:8545",
        symbols=["AERO-USDC"],
        exchange="base_onchain",
    )
    assert base.sync_recovery.state_path.endswith("base_onchain.json")
    assert "base_onchain.json" in base.sync_recovery.state_path
    assert not base.sync_recovery.state_path.endswith("superchain.json")

    sc = BaseOnchainTransport(
        "http://localhost:8545",
        symbols=["WETH-USDC"],
        exchange="superchain",
    )
    assert sc.sync_recovery.state_path.endswith("superchain.json")
    assert sc.exchange == "superchain"
    # Distinct state files so concurrent connectors do not clobber each other.
    assert base.sync_recovery.state_path != sc.sync_recovery.state_path

def test_rollback_manager():
    manager = RollbackManager(max_depth=10)
    
    fork = manager.process_block(100, "hash_A", "hash_parent_X", [])
    assert fork is None
    
    fork = manager.process_block(101, "hash_B", "hash_A", [])
    assert fork is None
    
    fork = manager.process_block(102, "hash_C", "hash_wrong", [])
    assert fork == 101
    
    assert 101 not in manager.buffer
    assert 102 not in manager.buffer
    assert 100 in manager.buffer

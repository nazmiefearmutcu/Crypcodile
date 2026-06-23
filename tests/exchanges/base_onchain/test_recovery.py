import pytest
from crypcodile.ingest.sync_recovery import SyncRecovery
from crypcodile.ingest.rollback_manager import RollbackManager

def test_sync_recovery(tmp_path):
    state_path = str(tmp_path / "sync.json")
    recovery = SyncRecovery(state_path)
    
    assert recovery.get_last_block("AERO-USDC") is None
    
    recovery.save_last_block("AERO-USDC", 12345)
    
    recovery2 = SyncRecovery(state_path)
    assert recovery2.get_last_block("AERO-USDC") == 12345

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

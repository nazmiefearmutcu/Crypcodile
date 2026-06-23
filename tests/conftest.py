import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Safeguard against xgboost C-library loading failures on macOS
try:
    import xgboost
except Exception:
    sys.modules["xgboost"] = MagicMock()

@pytest.fixture(autouse=True, scope="function")
def configure_payments_env(tmp_path):
    temp_db = tmp_path / "payments_db.json"
    os.environ["PAYMENTS_FILE"] = str(temp_db)
    temp_ipc = tmp_path / "custom_pools_ipc.json"
    os.environ["CUSTOM_POOLS_IPC_FILE"] = str(temp_ipc)
    yield
    if "PAYMENTS_FILE" in os.environ:
        del os.environ["PAYMENTS_FILE"]
    if "CUSTOM_POOLS_IPC_FILE" in os.environ:
        del os.environ["CUSTOM_POOLS_IPC_FILE"]


@pytest.fixture(autouse=True, scope="function")
def mock_sync_state_path(tmp_path):
    temp_dir = str(tmp_path / "sync_state")
    original_expanduser = os.path.expanduser
    
    def mock_expanduser(path):
        if path == "~/.crypcodile/sync_state":
            return temp_dir
        return original_expanduser(path)
        
    with patch("os.path.expanduser", mock_expanduser):
        yield

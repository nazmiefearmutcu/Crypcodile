import os
import sys

# Prevent OpenMP and OpenBLAS multithreading deadlocks/slowness on macOS Apple Silicon
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import pytest
from unittest.mock import patch, MagicMock

# Force Qt offscreen platform for headless tests run in CI/CLI environments
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Safeguard against xgboost C-library loading failures on macOS
sys.modules["xgboost"] = MagicMock()

# NOTE: PyQt6 / pyqtgraph are intentionally NOT mocked here. The FlowMap GUI
# tests exercise real (offscreen) Qt widgets — QT_QPA_PLATFORM=offscreen above
# keeps that headless and hang-free, which is why a blanket sys.modules stub of
# PyQt6 would break them (e.g. QMainWindow.move missing on a dummy widget).

# Safeguard against matplotlib interactive GUI backend loading delays/hangs on macOS
try:
    import matplotlib
    matplotlib.use('Agg')
except ImportError:
    pass




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

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


# Safeguard against xgboost C-library loading failures on macOS
sys.modules["xgboost"] = MagicMock()

# Safeguard against PyQt6 and pyqtgraph loading/GUI event hangs on macOS
class DummyQWidget:
    def __init__(self, *args, **kwargs):
        pass
        
class DummyQMainWindow(DummyQWidget):
    def setCentralWidget(self, *args, **kwargs):
        pass
    def setStyleSheet(self, *args, **kwargs):
        pass
    def resize(self, *args, **kwargs):
        pass
    def setWindowTitle(self, *args, **kwargs):
        pass

mock_qt = MagicMock()
mock_qt.QtWidgets.QMainWindow = DummyQMainWindow
mock_qt.QtWidgets.QWidget = DummyQWidget

sys.modules['PyQt6'] = mock_qt
sys.modules['PyQt6.QtCore'] = mock_qt.QtCore
sys.modules['PyQt6.QtGui'] = mock_qt.QtGui
sys.modules['PyQt6.QtWidgets'] = mock_qt.QtWidgets
sys.modules['pyqtgraph'] = MagicMock()

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

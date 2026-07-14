import sys
import os
import pytest
from unittest.mock import MagicMock, patch

try:
    import PyQt6
    HAS_GUI_LIBS = True
except ImportError:
    HAS_GUI_LIBS = False
    
    # Minimal stubbing to allow module imports in headless test environments
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
            
    sys.modules['PyQt6'] = MagicMock()
    sys.modules['PyQt6.QtCore'] = MagicMock()
    sys.modules['PyQt6.QtGui'] = MagicMock()
    sys.modules['PyQt6.QtWidgets'] = MagicMock()

from crypcodile.gui.flowmap_window import (
    FlowmapWindow,
    compute_hist_target_bw,
    compute_hist_vis_rows,
    _MIN_HIST_BW,
    _MIN_HIST_VIS_ROWS,
    _DEFAULT_WINDOW_W,
    _DEFAULT_WINDOW_H,
)
from flowmap.core import Side


# --- FIND-P236-01: pure hist bw helpers (no Qt required) ---

def test_compute_hist_target_bw_default_never_below_min():
    bw = compute_hist_target_bw()
    assert bw >= _MIN_HIST_BW
    # Default 1500 window minus sidebar/margins yields hundreds of columns.
    assert bw >= 800


def test_compute_hist_target_bw_tiny_window_floors_at_min():
    assert compute_hist_target_bw(1) == _MIN_HIST_BW
    assert compute_hist_target_bw(50, sidebar_w=0) == _MIN_HIST_BW
    assert compute_hist_target_bw(100, min_bw=128) == 128


def test_compute_hist_target_bw_scales_with_width():
    narrow = compute_hist_target_bw(1000, sidebar_w=0, min_bw=1)
    wide = compute_hist_target_bw(2000, sidebar_w=0, min_bw=1)
    assert wide > narrow


def test_compute_hist_target_bw_column_width():
    # Wider columns → fewer bins.
    fine = compute_hist_target_bw(_DEFAULT_WINDOW_W, column_width=1.0, min_bw=1)
    coarse = compute_hist_target_bw(_DEFAULT_WINDOW_W, column_width=2.0, min_bw=1)
    assert fine > coarse
    assert fine // 2 == coarse


def test_compute_hist_vis_rows_default_and_floor():
    vr = compute_hist_vis_rows()
    assert vr >= _MIN_HIST_VIS_ROWS
    assert compute_hist_vis_rows(10) == _MIN_HIST_VIS_ROWS
    assert compute_hist_vis_rows(_DEFAULT_WINDOW_H, min_vis_rows=1) >= 100


@pytest.mark.skipif(not HAS_GUI_LIBS, reason="PyQt6 not installed")
def test_ensure_hist_engine_geometry_not_1x1():
    """FIND-P236-01: pre-show DensityEngine must not stay 1-wide before hist binning."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # historical_hours=0 skips load_historical_data in __init__
    window = FlowmapWindow(
        initial_symbol="binance-spot:BTCUSDT",
        data_dir="data",
        historical_hours=0,
    )
    # DensityEngine __init__ uses 1×1 until resizeEvent / our ensure path.
    # (Some platforms may have already resized the window; still assert post-ensure.)
    before_w = int(window.heatmap._engine.get_buffer().shape[1])

    vis_rows, target_bw = window._ensure_hist_engine_geometry()
    buf = window.heatmap._engine.get_buffer()
    assert target_bw >= _MIN_HIST_BW
    assert vis_rows >= _MIN_HIST_VIS_ROWS
    assert buf.shape[1] >= _MIN_HIST_BW
    assert buf.shape[1] == target_bw
    assert window.heatmap._last_hm_w == target_bw
    # If we started undersized (the P0 repro), ensure must grow the buffer.
    if before_w < _MIN_HIST_BW:
        assert buf.shape[1] > before_w

@pytest.mark.skipif(not HAS_GUI_LIBS, reason="PyQt6 not installed")
def test_flowmap_window_initialization():
    """Test that FlowmapWindow correctly instantiates and configures its components."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    window = FlowmapWindow(initial_symbol="binance-spot:BTCUSDT", data_dir="data", historical_hours=1.5)
    
    # Assert window setup and symbol mapping
    assert "Crypcodile Flowmap Visualizer" in window.windowTitle()
    assert window._order_book.symbol == "binance-spot:BTCUSDT"
    assert window._source.symbol == "binance-spot:BTCUSDT"
    
    # Assert Significant Iceberg dock components exist
    assert hasattr(window, "_iceberg_dock")
    assert hasattr(window, "_iceberg_table")
    assert hasattr(window, "_min_iceberg_size_spin")
    
    assert window._min_iceberg_size_spin.value() == 1.0
    assert window._iceberg_table.columnCount() == 5
    assert window._iceberg_table.rowCount() == 0

@pytest.mark.skipif(not HAS_GUI_LIBS, reason="PyQt6 not installed")
def test_flowmap_window_iceberg_tracking():
    """Test that the Significant Iceberg table correctly processes and filters signals."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    window = FlowmapWindow(initial_symbol="binance-spot:BTCUSDT", data_dir="data")
    
    # 1. Test iceberg detection under min size filter (size 0.5 < min 1.0)
    window._min_iceberg_size_spin.setValue(1.0)
    event_small = {
        'price': 60000.0,
        'size': 0.5,
        'side': Side.BUY,
        'timestamp': 1700000000.0,
        'hidden_vol': 0.3
    }
    window._on_iceberg_detected(event_small)
    assert window._iceberg_table.rowCount() == 0
    
    # 2. Test iceberg detection meeting size threshold (size 2.5 >= min 1.0)
    event_large = {
        'price': 60100.0,
        'size': 2.5,
        'side': Side.SELL,
        'timestamp': 1700000005.0,
        'hidden_vol': 1.8
    }
    window._on_iceberg_detected(event_large)
    assert window._iceberg_table.rowCount() == 1
    
    # Assert values in row
    assert window._iceberg_table.item(0, 1).text() == "SELL"
    assert window._iceberg_table.item(0, 2).text() == "60100.00"
    assert window._iceberg_table.item(0, 3).text() == "2.50"
    assert window._iceberg_table.item(0, 4).text() == "1.80"
    
    # 3. Test clear table
    window._clear_iceberg_table()
    assert window._iceberg_table.rowCount() == 0

@pytest.mark.skipif(not HAS_GUI_LIBS, reason="PyQt6 not installed")
def test_flowmap_window_llt_tracking():
    """Test that the Large Lot Tracker table correctly filters and populates resting orders."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    window = FlowmapWindow(initial_symbol="binance-spot:BTCUSDT", data_dir="data")
    
    assert hasattr(window, "_llt_dock")
    assert hasattr(window, "_llt_table")
    assert hasattr(window, "_min_llt_size_spin")
    
    # 1. Mock levels
    from types import SimpleNamespace
    levels = [
        SimpleNamespace(price=60000.0, bid_size=50.0, ask_size=0.0),
        SimpleNamespace(price=60100.0, bid_size=0.0, ask_size=5.0),
        SimpleNamespace(price=60200.0, bid_size=0.0, ask_size=100.0),
    ]
    
    # Set threshold to 15.0 (60000.0 and 60200.0 should be included, 60100.0 excluded)
    window._min_llt_size_spin.setValue(15.0)
    window._update_llt_table(levels)
    
    # Row count should be 2 (60200.0 and 60000.0) sorted by price descending
    assert window._llt_table.rowCount() == 2
    
    assert window._llt_table.item(0, 0).text() == "ASK"
    assert window._llt_table.item(0, 1).text() == "60200.00"
    assert window._llt_table.item(0, 2).text() == "100.00"
    
    assert window._llt_table.item(1, 0).text() == "BID"
    assert window._llt_table.item(1, 1).text() == "60000.00"
    assert window._llt_table.item(1, 2).text() == "50.00"

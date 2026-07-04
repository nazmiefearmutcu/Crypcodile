import sys
import queue
import time
from unittest.mock import MagicMock, patch

import pytest

try:
    import PyQt6
    import pyqtgraph
    HAS_GUI_LIBS = True
except ImportError:
    HAS_GUI_LIBS = False
    
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
            
    from unittest.mock import MagicMock
    import sys
    
    mock_qt = MagicMock()
    mock_qt.QtWidgets.QMainWindow = DummyQMainWindow
    mock_qt.QtWidgets.QWidget = DummyQWidget
    
    sys.modules['PyQt6'] = mock_qt
    sys.modules['PyQt6.QtCore'] = mock_qt.QtCore
    sys.modules['PyQt6.QtGui'] = mock_qt.QtGui
    sys.modules['PyQt6.QtWidgets'] = mock_qt.QtWidgets
    sys.modules['pyqtgraph'] = MagicMock()

from crypcodile.gui.flowmap_window import FlowmapWindow
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Trade, BookSnapshot, BookDelta


def test_flowmap_window_logical_handling():
    """Test event handling and internal states without requiring a display."""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    
    with patch.object(FlowmapWindow, 'update_plots') as mock_update, \
         patch('crypcodile.gui.flowmap_window.HistoricalLoaderThread.start') as mock_loader_start:
        window = FlowmapWindow(initial_symbol="binance-spot:BTCUSDT", data_dir="data")
        
        # Verify initial states
        assert window.cum_delta == 0.0
        assert len(window.book_history) == 0
        assert len(window.trade_history) == 0
        
        # 1. Process Snapshot
        snap = BookSnapshot(
            exchange="binance",
            symbol="BTC/USDT",
            symbol_raw="BTCUSDT",
            exchange_ts=1700000000000000000,
            local_ts=1700000000000000000,
            bids=[(99.0, 1.5), (98.0, 2.0)],
            asks=[(101.0, 1.0), (102.0, 3.0)],
            depth=2
        )
        window.handle_event(snap)
        
        assert window.current_bids == {99.0: 1.5, 98.0: 2.0}
        assert window.current_asks == {101.0: 1.0, 102.0: 3.0}
        assert len(window.book_history) == 1
        assert window.book_history[-1]['mid'] == 100.0  # (99.0 + 101.0) / 2
        
        # 2. Process Delta
        delta = BookDelta(
            exchange="binance",
            symbol="BTC/USDT",
            symbol_raw="BTCUSDT",
            exchange_ts=1700000000200000000,
            local_ts=1700000000200000000,
            bids=[(99.0, 0.0), (98.5, 4.0)],
            asks=[(101.0, 2.5)]
        )
        window.handle_event(delta)
        
        assert window.current_bids == {98.0: 2.0, 98.5: 4.0}
        assert window.current_asks == {101.0: 2.5, 102.0: 3.0}
        assert len(window.book_history) == 2
        assert window.book_history[-1]['mid'] == 99.75  # (98.5 + 101.0) / 2
        
        # 3. Process Trade
        tr = Trade(
            exchange="binance",
            symbol="BTC/USDT",
            symbol_raw="BTCUSDT",
            exchange_ts=1700000000300000000,
            local_ts=1700000000300000000,
            id="t1",
            price=100.0,
            amount=0.5,
            side=Side.BUY
        )
        window.handle_event(tr)
        
        assert window.cum_delta == 0.5
        assert len(window.trade_history) == 1
        assert window.trade_history[0]['price'] == 100.0
        assert window.trade_history[0]['amount'] == 0.5
        assert window.trade_history[0]['side'] == 'buy'


@pytest.mark.skipif(not HAS_GUI_LIBS, reason="PyQt6/pyqtgraph not installed")
def test_flowmap_window_qt_render():
    """Test rendering of plots when PyQt6 and pyqtgraph are installed."""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    
    with patch('crypcodile.gui.flowmap_window.HistoricalLoaderThread.start') as mock_loader_start:
        window = FlowmapWindow(initial_symbol="binance-spot:BTCUSDT", data_dir="data")
    
    # Send events
    snap = BookSnapshot(
        exchange="binance",
        symbol="BTC/USDT",
        symbol_raw="BTCUSDT",
        exchange_ts=1700000000000000000,
        local_ts=1700000000000000000,
        bids=[(100.0, 1.0)],
        asks=[(101.0, 2.0)],
        depth=1
    )
    window.handle_event(snap)
    window.update_plots()
    
    assert window.price_plot is not None
    assert window.image_item is not None
    assert window.trade_scatter is not None
    assert window.depth_plot is not None
    assert window.delta_plot is not None

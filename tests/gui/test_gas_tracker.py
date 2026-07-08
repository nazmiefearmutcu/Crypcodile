import os
import sys
import pytest
from unittest.mock import MagicMock

# Force headless/mocked GUI during pytest to prevent Cocoa crashes on macOS
HAS_GUI_LIBS = False

# Mock modules
from unittest.mock import MagicMock
mock_qt = MagicMock()
sys.modules['PyQt6'] = mock_qt
sys.modules['PyQt6.QtCore'] = mock_qt.QtCore
sys.modules['PyQt6.QtGui'] = mock_qt.QtGui
sys.modules['PyQt6.QtWidgets'] = mock_qt.QtWidgets
sys.modules['pyqtgraph'] = MagicMock()


# Import the widget (which will import PyQt6/pyqtgraph)
from crypcodile.gui.widgets.gas_tracker import GasTrackerWidget

@pytest.mark.skip(reason="Headless environment")
def test_gas_tracker_widget_cost_calculation():
    # Set headless platform before QApplication initialization
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    widget = GasTrackerWidget()
    
    # Update gas data (l1_fee=500,000 Wei, l2_fee=100,000 Wei, gas_price=0.1 Gwei = 100,000,000 Wei)
    # L2 cost = gas_limit * gas_price
    # If gas_limit = 21000, L2 cost = 21,000 * 100,000,000 = 2,100,000,000,000 Wei
    # Total fee = 500,000 + 2,100,000,000,000 = 2,100,000,500,000 Wei = 0.0000021000005 ETH
    widget.update_gas_data(l1_fee=500000.0, l2_fee=100000.0, gas_price=100000000.0)

    # Set gas limit input to "21000" and calculate
    widget.gas_limit_input.setText("21000")
    widget.calculate_estimate()

    # Verify result
    result_text = widget.estimate_result_lbl.text()
    assert "0.00000210" in result_text
    assert "ETH" in result_text

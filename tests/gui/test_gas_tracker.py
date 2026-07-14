import os
import pytest


# NOTE: do NOT stub PyQt6/pyqtgraph in sys.modules at import time. This module
# used to do that globally, which corrupted Qt state for the real (offscreen)
# FlowMap GUI tests collected in the same session and aborted the process with a
# Cocoa SIGABRT. The single test here is skipped anyway; keep the Qt imports
# local to it.
@pytest.mark.skip(reason="Headless environment")
def test_gas_tracker_widget_cost_calculation():
    # Set headless platform before QApplication initialization
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PyQt6.QtWidgets import QApplication
    from crypcodile.gui.widgets.gas_tracker import GasTrackerWidget
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

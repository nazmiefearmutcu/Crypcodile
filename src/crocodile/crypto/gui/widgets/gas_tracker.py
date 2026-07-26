import sys
import time
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg

# Configure pyqtgraph for dark mode
pg.setConfigOption('background', '#121212')
pg.setConfigOption('foreground', '#E0E0E0')
pg.setConfigOption('antialias', True)

class GasTrackerWidget(QtWidgets.QWidget):
    """PyQt6 Widget showing L2 gas metrics, plots, and a transaction cost estimator."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.gas_price_history = []
        self.timestamps = []
        self.start_time = time.time()
        self.init_ui()

        # Simple timer to update mock data if not updated externally
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._generate_mock_update)
        self.timer.start(1000) # update every second

    def init_ui(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background-color: #121212;
                color: #E0E0E0;
                font-family: Arial, sans-serif;
                font-size: 13px;
            }
            QLabel {
                font-weight: bold;
            }
            QLineEdit {
                background-color: #1E1E1E;
                border: 1px solid #333333;
                color: #FFFFFF;
                padding: 4px;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #1E4620;
                color: #FFFFFF;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2E6630;
            }
        """)

        # Main Layout
        main_layout = QtWidgets.QHBoxLayout(self)

        # Left Panel (Metrics & Estimator)
        left_panel = QtWidgets.QVBoxLayout()
        main_layout.addLayout(left_panel, stretch=1)

        # Title
        title_label = QtWidgets.QLabel("Base L2 Gas Tracker")
        title_label.setStyleSheet("font-size: 16px; color: #4CAF50; margin-bottom: 10px;")
        left_panel.addWidget(title_label)

        # Metric labels
        self.l2_gas_price_lbl = QtWidgets.QLabel("L2 Gas Price: 0.00 Gwei")
        left_panel.addWidget(self.l2_gas_price_lbl)

        self.l1_fee_lbl = QtWidgets.QLabel("L1 Gas Fee: 0.00 Wei")
        left_panel.addWidget(self.l1_fee_lbl)

        self.l2_fee_lbl = QtWidgets.QLabel("L2 Gas Fee: 0.00 Wei")
        left_panel.addWidget(self.l2_fee_lbl)

        self.split_lbl = QtWidgets.QLabel("Split (L1 / L2): 0.0% / 0.0%")
        left_panel.addWidget(self.split_lbl)

        left_panel.addSpacing(20)

        # Estimator Group
        est_title = QtWidgets.QLabel("Transaction Cost Estimator")
        est_title.setStyleSheet("font-size: 14px; color: #81C784;")
        left_panel.addWidget(est_title)

        left_panel.addWidget(QtWidgets.QLabel("Gas Limit (units):"))
        self.gas_limit_input = QtWidgets.QLineEdit()
        self.gas_limit_input.setText("21000") # default transfer gas limit
        left_panel.addWidget(self.gas_limit_input)

        self.estimate_btn = QtWidgets.QPushButton("Calculate Cost")
        self.estimate_btn.clicked.connect(self.calculate_estimate)
        left_panel.addWidget(self.estimate_btn)

        self.estimate_result_lbl = QtWidgets.QLabel("Est. Total Fee: -- ETH")
        self.estimate_result_lbl.setStyleSheet("font-size: 14px; color: #FFF; margin-top: 10px;")
        left_panel.addWidget(self.estimate_result_lbl)

        left_panel.addStretch()

        # Right Panel (Plot)
        right_panel = QtWidgets.QVBoxLayout()
        main_layout.addLayout(right_panel, stretch=2)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setTitle("L2 Effective Gas Price (Gwei)", color='#E0E0E0')
        self.plot_widget.setLabel('left', 'Gwei', color='#E0E0E0')
        self.plot_widget.setLabel('bottom', 'Time (s)', color='#E0E0E0')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color='#4CAF50', width=2))
        right_panel.addWidget(self.plot_widget)

        # Cache variables
        self.current_gas_price_wei = 0.0
        self.current_l1_fee_wei = 0.0
        self.current_l2_fee_wei = 0.0

    def update_gas_data(self, l1_fee: float, l2_fee: float, gas_price: float) -> None:
        """Call this to update the UI with new gas data (in Wei)."""
        self.current_l1_fee_wei = l1_fee
        self.current_l2_fee_wei = l2_fee
        self.current_gas_price_wei = gas_price

        # Update metrics (Gwei conversion for display)
        gwei_price = gas_price / 1e9
        self.l2_gas_price_lbl.setText(f"L2 Gas Price: {gwei_price:.4f} Gwei")
        self.l1_fee_lbl.setText(f"L1 Gas Fee: {l1_fee:,.0f} Wei")
        self.l2_fee_lbl.setText(f"L2 Gas Fee: {l2_fee:,.0f} Wei")

        total_fee = l1_fee + l2_fee
        if total_fee > 0:
            l1_pct = (l1_fee / total_fee) * 100
            l2_pct = (l2_fee / total_fee) * 100
            self.split_lbl.setText(f"Split (L1 / L2): {l1_pct:.1f}% / {l2_pct:.1f}%")
        else:
            self.split_lbl.setText("Split (L1 / L2): 0.0% / 0.0%")

        # Update plot
        self.gas_price_history.append(gwei_price)
        self.timestamps.append(time.time() - self.start_time)

        # Limit history length
        if len(self.gas_price_history) > 60:
            self.gas_price_history.pop(0)
            self.timestamps.pop(0)

        self.curve.setData(self.timestamps, self.gas_price_history)
        self.calculate_estimate()

    def calculate_estimate(self) -> None:
        try:
            limit = float(self.gas_limit_input.text())
        except ValueError:
            self.estimate_result_lbl.setText("Invalid Gas Limit")
            return

        # L2 fee component = limit * gas_price
        # Total cost estimate (ETH) = (L1 fee + L2 fee) / 1e18
        l2_cost = limit * self.current_gas_price_wei
        total_wei = self.current_l1_fee_wei + l2_cost
        total_eth = total_wei / 1e18
        
        self.estimate_result_lbl.setText(f"Est. Total Fee: {total_eth:.8f} ETH")

    def _generate_mock_update(self) -> None:
        """Fallback mock updater to show live animations when running standalone."""
        import random
        # Base L2 gas is usually extremely low (e.g. 0.05 to 0.2 Gwei)
        gas_price = random.uniform(0.05, 0.15) * 1e9 # in wei
        l1_fee = random.uniform(200000, 500000) # L1 fee in wei
        l2_fee = 21000 * gas_price
        self.update_gas_data(l1_fee, l2_fee, gas_price)

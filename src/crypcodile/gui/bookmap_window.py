import math
import queue
import time
from collections import deque

import numpy as np
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookDelta, BookSnapshot, Trade


class BookmapWindow(QtWidgets.QMainWindow):
    """
    A responsive, dark-themed PyQt6 window for order book depth and trade visualization.
    Displays:
    - Order Book Depth Heatmap (rolling price-vs-time grid using pg.ImageItem)
    - Cumulative Delta Line Chart (running volume delta)
    - L2 Depth Profile (vertical sidebar showing horizontal bids & asks bars)
    - Trade Bubbles (overlay scatter plot scaled by volume, colored by side)
    
    Supports initial historical loading and live queue-based streaming updates.
    """
    
    def __init__(self, queue: queue.Queue | None = None, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Crypcodile Bookmap Visualizer")
        self.resize(1200, 800)
        
        # Queue streaming
        self.queue = queue
        
        # State data structures
        self.current_bids = {}  # price -> size
        self.current_asks = {}  # price -> size
        self._updating_plots = False
        self.y_range_initialized = False
        self.x_range_initialized = False
        self.auto_scroll = True
        
        # Time-binned order book history for linear X-axis heatmap
        self.time_resolution_s = 0.2  # 200ms per heatmap column
        self.max_history_len = 1000  # maximum column bins
        self.book_history = deque(maxlen=self.max_history_len)
        
        # Trade and cumulative delta history
        self.trade_history = []
        self.delta_history = []
        self.cum_delta = 0.0
        
        # Heatmap resolution configuration
        self.num_price_bins = 200
        self.default_price_range = 50.0  # default vertical window size if not set
        
        self.init_ui()
        
        # If queue is provided, start polling immediately
        if self.queue is not None:
            self.start_streaming(self.queue)

    def init_ui(self):
        # Set dark-themed application stylesheet
        self.setStyleSheet("""
            QMainWindow {
                background-color: #121212;
            }
            QWidget {
                background-color: #121212;
                color: #E0E0E0;
                font-family: Arial, sans-serif;
            }
        """)
        
        # Set pyqtgraph global config for dark mode
        pg.setConfigOption('background', '#121212')
        pg.setConfigOption('foreground', '#E0E0E0')
        pg.setConfigOption('antialias', True)
        
        # Central layout widget
        self.central_widget = pg.GraphicsLayoutWidget(parent=self)
        self.setCentralWidget(self.central_widget)
        
        # 1. Main Price Plot (Heatmap & Trade Bubbles)
        self.price_plot = self.central_widget.addPlot(row=0, col=0)
        self.price_plot.setTitle("Order Book Heatmap & Trades", color='#E0E0E0', size='12pt')
        self.price_plot.showGrid(x=True, y=True, alpha=0.15)
        # Hide X-axis numbers on top plot (delta plot directly beneath shares X axis)
        self.price_plot.getAxis('bottom').setStyle(showValues=False)
        self.price_plot.getAxis('left').setLabel("Price", color='#E0E0E0')
        
        # Heatmap Image Item
        self.image_item = pg.ImageItem()
        self.price_plot.addItem(self.image_item)
        
        # Heatmap Lookup Table (Colormap)
        # Gradient: Dark Grey -> Blue -> Magenta -> Orange -> Yellow/White
        pos = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        color = np.array([
            [18, 18, 18, 255],     # Dark grey background
            [0, 100, 255, 255],    # Cool blue
            [255, 0, 128, 255],    # Purple/magenta
            [255, 150, 0, 255],    # Warm orange
            [255, 255, 200, 255]   # Soft yellow/white for highest liquidity
        ], dtype=np.ubyte)
        cmap = pg.ColorMap(pos, color)
        lut = cmap.getLookupTable(start=0.0, stop=1.0, nPts=256)
        self.image_item.setLookupTable(lut)
        
        # Trade Bubbles Scatter Plot
        self.trade_scatter = pg.ScatterPlotItem(pxMode=True)
        self.price_plot.addItem(self.trade_scatter)
        
        # 2. Vertical L2 Depth Profile Sidebar (linked to price plot Y-axis)
        self.depth_plot = self.central_widget.addPlot(row=0, col=1)
        self.depth_plot.setTitle("L2 Depth Profile", color='#E0E0E0', size='12pt')
        self.depth_plot.showGrid(x=True, y=True, alpha=0.15)
        # Hide left axis numbers since it is Y-linked to the price plot
        self.depth_plot.getAxis('left').setStyle(showValues=False)
        self.depth_plot.getAxis('bottom').setLabel("Size", color='#E0E0E0')
        
        # Horizontal bars for Bids (Green) and Asks (Red)
        self.bids_bar = pg.BarGraphItem(
            x0=[], y=[], height=[], width=[],
            brush=pg.mkBrush(0, 200, 100, 160), pen=None
        )
        self.asks_bar = pg.BarGraphItem(
            x0=[], y=[], height=[], width=[],
            brush=pg.mkBrush(230, 50, 50, 160), pen=None
        )
        self.depth_plot.addItem(self.bids_bar)
        self.depth_plot.addItem(self.asks_bar)
        
        # 3. Cumulative Delta Chart (linked to price plot X-axis)
        # Use DateAxisItem for nice human-readable timestamps on the X-axis
        self.delta_plot = self.central_widget.addPlot(
            row=1, col=0,
            axisItems={'bottom': pg.DateAxisItem(orientation='bottom')}
        )
        self.delta_plot.setTitle("Cumulative Delta", color='#E0E0E0', size='10pt')
        self.delta_plot.showGrid(x=True, y=True, alpha=0.15)
        self.delta_plot.getAxis('bottom').setLabel("Time", color='#E0E0E0')
        self.delta_plot.getAxis('left').setLabel("Cum Delta", color='#E0E0E0')
        
        # Cumulative Delta Line Curve
        self.delta_curve = pg.PlotDataItem(pen=pg.mkPen('#00BFFF', width=2))
        self.delta_plot.addItem(self.delta_curve)
        
        # Layout styling & stretching
        self.central_widget.ci.layout.setColumnStretchFactor(0, 5)
        self.central_widget.ci.layout.setColumnStretchFactor(1, 1)
        self.central_widget.ci.layout.setRowStretchFactor(0, 4)
        self.central_widget.ci.layout.setRowStretchFactor(1, 1)
        
        # Synchronize viewport scaling/panning
        self.delta_plot.setXLink(self.price_plot)
        self.depth_plot.setYLink(self.price_plot)
        
        # Disable auto-range to prevent layout calculation loops
        self.price_plot.enableAutoRange(enable=False)
        self.delta_plot.enableAutoRange(enable=False)
        self.depth_plot.enableAutoRange(enable=False)
        
        # Connect view range changes to update plots dynamically
        # (e.g. recalculate heatmap price bins when zooming in/out)
        self.price_plot.sigYRangeChanged.connect(self.on_view_changed)
        self.price_plot.sigXRangeChanged.connect(self.on_view_changed)

    def on_view_changed(self):
        if getattr(self, "_updating_plots", False):
            return
            
        # Detect if user panned away from the live edge
        if self.book_history:
            latest_ts = self.book_history[-1]['timestamp']
            x_range = self.price_plot.viewRange()[0]
            # If the right edge of the viewport is more than 3 seconds behind the latest timestamp,
            # assume the user panned away and disable auto-scroll
            if latest_ts - x_range[1] > 3.0:
                self.auto_scroll = False
            else:
                self.auto_scroll = True
                
        self.update_plots()

    def handle_event(self, event):
        """Processes a single normalized event or dict representation."""
        # Detect event type and extract data
        is_dict = isinstance(event, dict)
        channel = event.get('channel') if is_dict else getattr(event, 'channel', None)
        if channel is None:
            if isinstance(event, BookSnapshot) or (is_dict and event.get('is_snapshot') is True):
                channel = 'book_snapshot'
            elif isinstance(event, BookDelta) or (is_dict and event.get('is_snapshot') is False):
                channel = 'book_delta'
            elif isinstance(event, Trade):
                channel = 'trade'
            
        if channel == 'book_snapshot' or isinstance(event, BookSnapshot):
            if is_dict:
                bids_list = event.get('bids') or []
                asks_list = event.get('asks') or []
            else:
                bids_list = getattr(event, 'bids', None) or []
                asks_list = getattr(event, 'asks', None) or []
            
            self.current_bids.clear()
            self.current_asks.clear()
            for px, sz in bids_list:
                if sz > 0:
                    self.current_bids[px] = sz
            for px, sz in asks_list:
                if sz > 0:
                    self.current_asks[px] = sz
            
            # Trim to prevent memory/CPU growth
            if len(self.current_bids) > 200:
                sorted_bid_prices = sorted(self.current_bids.keys(), reverse=True)[:200]
                self.current_bids = {px: self.current_bids[px] for px in sorted_bid_prices}
            if len(self.current_asks) > 200:
                sorted_ask_prices = sorted(self.current_asks.keys())[:200]
                self.current_asks = {px: self.current_asks[px] for px in sorted_ask_prices}
                    
            ts = self._extract_timestamp(event)
            mid = self._calculate_mid()
            self._add_to_book_history(ts, self.current_bids, self.current_asks, mid)
            
        elif channel == 'book_delta' or isinstance(event, BookDelta):
            if is_dict:
                bids_list = event.get('bids') or []
                asks_list = event.get('asks') or []
            else:
                bids_list = getattr(event, 'bids', None) or []
                asks_list = getattr(event, 'asks', None) or []
            
            for px, sz in bids_list:
                if sz == 0.0:
                    self.current_bids.pop(px, None)
                else:
                    self.current_bids[px] = sz
            for px, sz in asks_list:
                if sz == 0.0:
                    self.current_asks.pop(px, None)
                else:
                    self.current_asks[px] = sz
            
            # Trim to prevent memory/CPU growth
            if len(self.current_bids) > 200:
                sorted_bid_prices = sorted(self.current_bids.keys(), reverse=True)[:200]
                self.current_bids = {px: self.current_bids[px] for px in sorted_bid_prices}
            if len(self.current_asks) > 200:
                sorted_ask_prices = sorted(self.current_asks.keys())[:200]
                self.current_asks = {px: self.current_asks[px] for px in sorted_ask_prices}
                    
            ts = self._extract_timestamp(event)
            mid = self._calculate_mid()
            self._add_to_book_history(ts, self.current_bids, self.current_asks, mid)
            
        elif channel == 'trade' or isinstance(event, Trade):
            if is_dict:
                px = event.get('price', 0.0)
                sz = event.get('amount', 0.0)
                side = event.get('side', 'buy')
            else:
                px = getattr(event, 'price', 0.0)
                sz = getattr(event, 'amount', 0.0)
                side = getattr(event, 'side', 'buy')
            
            if hasattr(side, 'value'):
                side_str = side.value
            else:
                side_str = str(side).lower()
                
            if side_str == 'buy':
                self.cum_delta += sz
            elif side_str == 'sell':
                self.cum_delta -= sz
                
            ts = self._extract_timestamp(event)
            self.trade_history.append({
                'timestamp': ts,
                'price': px,
                'amount': sz,
                'side': side_str
            })
            if len(self.trade_history) > 1000:
                self.trade_history.pop(0)
                
            self.delta_history.append((ts, self.cum_delta))
            if len(self.delta_history) > 1000:
                self.delta_history.pop(0)

    def _extract_timestamp(self, event) -> float:
        if isinstance(event, dict):
            local_ts = event.get('local_ts')
        else:
            local_ts = getattr(event, 'local_ts', None)
        if local_ts is not None:
            return float(local_ts) / 1e9
        return time.time()

    def _calculate_mid(self) -> float:
        best_bid = max(self.current_bids.keys()) if self.current_bids else None
        best_ask = min(self.current_asks.keys()) if self.current_asks else None
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            return best_bid
        elif best_ask is not None:
            return best_ask
        return 100.0

    def _add_to_book_history(self, ts: float, bids: dict, asks: dict, mid: float):
        bin_ts = math.floor(ts / self.time_resolution_s) * self.time_resolution_s
        if not self.book_history:
            self.book_history.append({
                'timestamp': bin_ts,
                'bids': dict(bids),
                'asks': dict(asks),
                'mid': mid
            })
        elif bin_ts > self.book_history[-1]['timestamp']:
            # Fill time gaps linearly to keep the horizontal space uniform
            last_ts = self.book_history[-1]['timestamp']
            gap_bins = round((bin_ts - last_ts) / self.time_resolution_s) - 1
            for i in range(gap_bins):
                gap_ts = last_ts + (i + 1) * self.time_resolution_s
                self.book_history.append({
                    'timestamp': gap_ts,
                    'bids': dict(self.book_history[-1]['bids']),
                    'asks': dict(self.book_history[-1]['asks']),
                    'mid': self.book_history[-1]['mid']
                })
            self.book_history.append({
                'timestamp': bin_ts,
                'bids': dict(bids),
                'asks': dict(asks),
                'mid': mid
            })
        else:
            # Accumulate/overwrite changes in current active time bin
            self.book_history[-1]['bids'] = dict(bids)
            self.book_history[-1]['asks'] = dict(asks)
            self.book_history[-1]['mid'] = mid

    def update_plots(self):
        if not self.book_history:
            return
            
        self._updating_plots = True
        try:
            # Query view Range from plots
            x_range = self.price_plot.viewRange()[0]
            y_range = self.price_plot.viewRange()[1]
            
            latest_state = self.book_history[-1]
            mid = latest_state['mid']
            
            p_min, p_max = y_range[0], y_range[1]
            t_min, t_max = x_range[0], x_range[1]
            
            # Initial setup of coordinate scale if not yet initialized
            if not self.y_range_initialized:
                p_min = mid - self.default_price_range / 2
                p_max = mid + self.default_price_range / 2
                self.price_plot.setYRange(p_min, p_max, padding=0)
                self.y_range_initialized = True
            elif getattr(self, "auto_scroll", True):
                # Auto-center Y range if the mid price moves close to the edges of the viewport
                margin = (p_max - p_min) * 0.15
                if mid < p_min + margin or mid > p_max - margin:
                    center_y = mid
                    half_height = (p_max - p_min) / 2.0
                    p_min = center_y - half_height
                    p_max = center_y + half_height
                    self.price_plot.setYRange(p_min, p_max, padding=0)
                
            if getattr(self, "auto_scroll", True):
                # Auto-scroll X range to show the latest data
                view_width = t_max - t_min if (self.x_range_initialized and t_max > t_min) else 60.0
                t_max = latest_state['timestamp']
                t_min = t_max - view_width
                self.price_plot.setXRange(t_min, t_max, padding=0)
                self.x_range_initialized = True
                
            bin_width = (p_max - p_min) / self.num_price_bins
            if bin_width <= 0:
                bin_width = 1.0
                
            # 1. Update Heatmap
            # Filter history to states within or adjacent to visible viewport
            visible_states = [
                s for s in self.book_history
                if t_min - 5 <= s['timestamp'] <= t_max + 5
            ]
            if not visible_states:
                visible_states = list(self.book_history)
                
            num_time_steps = len(visible_states)
            grid = np.zeros((num_time_steps, self.num_price_bins))
            
            for i, state in enumerate(visible_states):
                for price, size in state['bids'].items():
                    if p_min <= price < p_max:
                        bin_idx = int((price - p_min) / bin_width)
                        if 0 <= bin_idx < self.num_price_bins:
                            grid[i, bin_idx] += size
                for price, size in state['asks'].items():
                    if p_min <= price < p_max:
                        bin_idx = int((price - p_min) / bin_width)
                        if 0 <= bin_idx < self.num_price_bins:
                            grid[i, bin_idx] += size
                            
            grid = np.log1p(grid)  # Compresses depth variations log-scale
            
            self.image_item.setImage(grid, autoLevels=True)
            t_start = visible_states[0]['timestamp']
            t_end = visible_states[-1]['timestamp']
            rect_w = max(t_end - t_start, self.time_resolution_s)
            self.image_item.setRect(pg.QtCore.QRectF(t_start, p_min, rect_w, p_max - p_min))
            
            # 2. Update Trade Bubbles
            self.update_trade_bubbles(t_min, t_max)
            
            # 3. Update L2 Depth Profile
            self.update_depth_profile(p_min, p_max, bin_width)
            
            # 4. Update Cumulative Delta
            self.update_cumulative_delta(t_min, t_max)
            
        finally:
            self._updating_plots = False

    def update_trade_bubbles(self, t_min: float, t_max: float):
        visible_trades = [t for t in self.trade_history if t_min - 5 <= t['timestamp'] <= t_max + 5]
        if not visible_trades:
            self.trade_scatter.setData(x=[], y=[], size=[], brush=[], pen=[])
            return
            
        x = [t['timestamp'] for t in visible_trades]
        y = [t['price'] for t in visible_trades]
        
        volumes = np.array([t['amount'] for t in visible_trades])
        if len(volumes) > 0:
            max_vol = volumes.max()
            if max_vol == 0:
                max_vol = 1.0
            # Scale bubble size range [6px, 30px]
            sizes = 6 + 24 * (np.sqrt(volumes) / np.sqrt(max_vol))
        else:
            sizes = []
            
        brushes = []
        for t in visible_trades:
            if t['side'] == 'buy' or t['side'] == Side.BUY:
                brushes.append(pg.mkBrush(0, 255, 100, 160))   # semi-transparent neon green
            else:
                brushes.append(pg.mkBrush(255, 50, 50, 160))    # semi-transparent bright red
                
        self.trade_scatter.setData(x=x, y=y, size=sizes, brush=brushes, pen=pg.mkPen(None))

    def update_depth_profile(self, p_min: float, p_max: float, bin_width: float):
        if not self.book_history:
            return
            
        latest_state = self.book_history[-1]
        
        bids_bins = np.zeros(self.num_price_bins)
        asks_bins = np.zeros(self.num_price_bins)
        
        for price, size in latest_state['bids'].items():
            if p_min <= price < p_max:
                bin_idx = int((price - p_min) / bin_width)
                if 0 <= bin_idx < self.num_price_bins:
                    bids_bins[bin_idx] += size
                    
        for price, size in latest_state['asks'].items():
            if p_min <= price < p_max:
                bin_idx = int((price - p_min) / bin_width)
                if 0 <= bin_idx < self.num_price_bins:
                    asks_bins[bin_idx] += size
                    
        bin_centers = p_min + (np.arange(self.num_price_bins) + 0.5) * bin_width
        bar_heights = np.ones(self.num_price_bins) * bin_width
        
        # Update horizontal bars using x0 start boundaries
        self.bids_bar.setOpts(
            x0=np.zeros(self.num_price_bins),
            y=bin_centers,
            width=bids_bins,
            height=bar_heights,
            brush=pg.mkBrush(0, 200, 100, 160),
            pen=pg.mkPen(None)
        )
        self.asks_bar.setOpts(
            x0=np.zeros(self.num_price_bins),
            y=bin_centers,
            width=asks_bins,
            height=bar_heights,
            brush=pg.mkBrush(230, 50, 50, 160),
            pen=pg.mkPen(None)
        )
        
        # Re-scale L2 depth plot's X axis (Volume size) to fit the book profile
        max_depth = max(bids_bins.max(), asks_bins.max())
        if max_depth > 0:
            self.depth_plot.setXRange(0, max_depth * 1.1, padding=0)

    def update_cumulative_delta(self, t_min: float, t_max: float):
        visible_points = [p for p in self.delta_history if t_min - 5 <= p[0] <= t_max + 5]
        if not visible_points:
            self.delta_curve.setData(x=[], y=[])
            return
            
        x = [p[0] for p in visible_points]
        y = [p[1] for p in visible_points]
        self.delta_curve.setData(x=x, y=y)

    def load_historical_data(self, events: list):
        """Populates charts with initial database/file snapshots and trades."""
        for event in events:
            self.handle_event(event)
        self.update_plots()

    def start_streaming(self, event_queue: queue.Queue):
        """Subscribes and polls live websocket events from the queue thread-safely."""
        self.queue = event_queue
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.process_queue)
        self.timer.start(100)  # poll every 100ms

    def process_queue(self):
        """Reads all items in the queue and updates charts in batch."""
        if self.queue is None:
            return
            
        import sys
        import traceback
        has_updates = False
        try:
            while not self.queue.empty():
                try:
                    event = self.queue.get_nowait()
                    self.handle_event(event)
                    has_updates = True
                    self.queue.task_done()
                except queue.Empty:
                    break
        except Exception as e:
            sys.stderr.write(f"Error in process_queue loop: {e}\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
                
        if has_updates:
            try:
                self.update_plots()
            except Exception as e:
                sys.stderr.write(f"Error in update_plots: {e}\n")
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()


if __name__ == "__main__":
    # Launch demonstration window with live simulated market depth and trades
    import random
    import sys
    import threading
    
    app = QtWidgets.QApplication(sys.argv)
    q = queue.Queue()
    
    win = BookmapWindow(queue=q)
    win.show()
    
    def feeder():
        mid = 50000.0
        ts_ns = int(time.time() * 1e9)
        
        # Initial depth snapshots
        bids = [(mid - i * 5.0, random.uniform(1.0, 15.0)) for i in range(50)]
        asks = [(mid + i * 5.0, random.uniform(1.0, 15.0)) for i in range(50)]
        
        q.put({
            'channel': 'book_snapshot',
            'local_ts': ts_ns,
            'bids': bids,
            'asks': asks
        })
        time.sleep(1.0)
        
        # Streaming live updates
        while True:
            time.sleep(random.uniform(0.02, 0.1))
            ts_ns = int(time.time() * 1e9)
            
            # Event 1: trade bubble
            if random.random() < 0.25:
                trade_p = mid + random.uniform(-15.0, 15.0)
                trade_sz = random.uniform(0.1, 5.0)
                side = 'buy' if random.random() < 0.52 else 'sell'
                q.put({
                    'channel': 'trade',
                    'local_ts': ts_ns,
                    'price': trade_p,
                    'amount': trade_sz,
                    'side': side
                })
                
            # Event 2: delta updates
            delta_bids = [
                (mid - random.randint(0, 49) * 5.0, random.uniform(0.0, 20.0))
                for _ in range(5)
            ]
            delta_asks = [
                (mid + random.randint(0, 49) * 5.0, random.uniform(0.0, 20.0))
                for _ in range(5)
            ]
            # randomly delete a level
            if random.random() < 0.2:
                delta_bids.append((mid - 10.0, 0.0))
                
            q.put({
                'channel': 'book_delta',
                'local_ts': ts_ns,
                'bids': delta_bids,
                'asks': delta_asks
            })
            
            # slowly shift mid price
            mid += random.uniform(-2.0, 2.0)

    t = threading.Thread(target=feeder, daemon=True)
    t.start()
    
    sys.exit(app.exec())

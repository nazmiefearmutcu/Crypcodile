import math
import queue
import time
import asyncio
from collections import deque
import numpy as np
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookDelta, BookSnapshot, Trade
from crypcodile.store.catalog import Catalog
from crypcodile.exchanges.factory import make_connector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.client.collect import collect as collect_live
from crypcodile.ingest.transport import AiohttpWsTransport
from crypcodile.sink.base import Sink

class IngestQueueSink(Sink):
    """A sink that routes normalized records into a thread-safe queue."""
    def __init__(self, queue_obj) -> None:
        self.queue_obj = queue_obj

    async def put(self, record) -> None:
        self.queue_obj.put(record)

    async def flush(self) -> None:
        pass


class LiveFeederThread(QtCore.QThread):
    """Background thread running asyncio event loop to stream live events via connector."""
    def __init__(self, exchange: str, symbol_raw: str, queue_obj):
        super().__init__()
        # Map canonical/catalog exchange names to factory connector names
        ex = exchange.lower()
        if ex.startswith("binance"):
            self.exchange = "binance"
        elif ex.startswith("bybit"):
            self.exchange = "bybit"
        elif ex.startswith("okx"):
            self.exchange = "okx"
        else:
            self.exchange = ex
            
        self.symbol_raw = symbol_raw
        self.queue_obj = queue_obj
        self.loop = None
        self.running = True

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        registry = InstrumentRegistry()
        sink = IngestQueueSink(self.queue_obj)
        
        try:
            connector = make_connector(
                exchange=self.exchange,
                symbols=[self.symbol_raw],
                channels=["book_delta", "trade"],
                out=sink,
                registry=registry,
            )
            if connector.transport is None:
                connector.transport = AiohttpWsTransport(connector.ws_url)
                
            async def run_collect():
                await collect_live([connector], sink)
                
            self.loop.run_until_complete(run_collect())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            import sys
            sys.stderr.write(f"Feeder thread exception: {e}\n")
            sys.stderr.flush()

    def stop(self):
        self.running = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.cancel_all_tasks)

    def cancel_all_tasks(self):
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.loop.stop()


class HistoricalLoaderThread(QtCore.QThread):
    """Background thread to query DuckDB historical data without freezing the GUI."""
    loaded = QtCore.pyqtSignal(list)

    def __init__(self, data_dir: str, symbol: str, historical_hours: float):
        super().__init__()
        self.data_dir = data_dir
        self.symbol = symbol
        self.historical_hours = historical_hours

    def run(self):
        try:
            import polars as pl
            catalog = Catalog(self.data_dir)
            end_ns = int(time.time_ns())
            
            try:
                max_df = catalog.query(
                    f"SELECT max(local_ts) as max_t FROM trade WHERE symbol = '{self.symbol}'"
                )
                if len(max_df) > 0 and max_df["max_t"][0] is not None:
                    end_ns = int(max_df["max_t"][0])
            except Exception:
                pass

            start_ns = end_ns - int(self.historical_hours * 3600 * 1_000_000_000)

            try:
                snap_df = catalog.scan("book_snapshot", self.symbol, start_ns, end_ns)
            except Exception:
                snap_df = pl.DataFrame()

            try:
                delta_df = catalog.scan("book_delta", self.symbol, start_ns, end_ns)
            except Exception:
                delta_df = pl.DataFrame()

            try:
                trade_df = catalog.scan("trade", self.symbol, start_ns, end_ns)
            except Exception:
                trade_df = pl.DataFrame()

            events = []

            def df_to_list(df, channel_name):
                if df.is_empty():
                    return []
                rows = df.to_dicts()
                for r in rows:
                    r["channel"] = channel_name
                return rows

            events.extend(df_to_list(snap_df, "book_snapshot"))
            events.extend(df_to_list(delta_df, "book_delta"))
            events.extend(df_to_list(trade_df, "trade"))

            for r in events:
                if r.get("channel") in ("book_snapshot", "book_delta"):
                    for side in ("bids", "asks"):
                        original = r.get(side)
                        normalized = []
                        if original:
                            for item in original:
                                if isinstance(item, dict):
                                    price = item.get("price")
                                    amount = item.get("amount") if item.get("amount") is not None else item.get("size")
                                    if price is not None and amount is not None:
                                        normalized.append((float(price), float(amount)))
                                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                                    normalized.append((float(item[0]), float(item[1])))
                        r[side] = normalized

            events.sort(key=lambda x: x.get("local_ts") or 0)
            self.loaded.emit(events)
        except Exception as e:
            import sys
            sys.stderr.write(f"Error in HistoricalLoaderThread: {e}\n")
            sys.stderr.flush()
            self.loaded.emit([])


class SuggestionPopup(QtWidgets.QFrame):
    """Binance-like custom autocomplete popup supporting category tabs and instant search filtering."""
    selected = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Shadow.Raised)
        self.setStyleSheet("""
            SuggestionPopup {
                background-color: #1A1A1A;
                border: 1px solid #333333;
                border-radius: 6px;
            }
            QTabBar::tab {
                background-color: #222222;
                color: #888888;
                padding: 6px 12px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 9pt;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #1A1A1A;
                color: #00BFFF;
                border-bottom: 2px solid #00BFFF;
            }
            QListWidget {
                background-color: #1A1A1A;
                border: none;
                color: #E0E0E0;
            }
            QListWidget::item {
                padding: 8px 12px;
                border-bottom: 1px solid #252525;
            }
            QListWidget::item:hover {
                background-color: #2D2D2D;
            }
            QListWidget::item:selected {
                background-color: #00BFFF;
                color: #121212;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tabs
        self.tab_bar = QtWidgets.QTabBar(self)
        self.tab_bar.addTab("All")
        self.tab_bar.addTab("Spot")
        self.tab_bar.addTab("Perp/Futures")
        self.tab_bar.currentChanged.connect(self.filter_items)
        layout.addWidget(self.tab_bar)

        # List
        self.list_widget = QtWidgets.QListWidget(self)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        layout.addWidget(self.list_widget)

        self.all_symbols = []
        self.current_filter_text = ""

    def set_symbols(self, symbols):
        self.all_symbols = symbols
        self.filter_items()

    def filter_text(self, text):
        self.current_filter_text = text.lower()
        self.filter_items()

    def filter_items(self):
        self.list_widget.clear()
        tab_idx = self.tab_bar.currentIndex()
        
        category_filter = None
        if tab_idx == 1:
            category_filter = "spot"
        elif tab_idx == 2:
            category_filter = "perp"

        for item in self.all_symbols:
            symbol = item["symbol"]
            display = item["display"]
            category = item["category"]

            if category_filter and category != category_filter:
                continue

            if self.current_filter_text and self.current_filter_text not in display.lower() and self.current_filter_text not in symbol.lower():
                continue

            list_item = QtWidgets.QListWidgetItem(display)
            list_item.setData(QtCore.Qt.ItemDataRole.UserRole, symbol)
            self.list_widget.addItem(list_item)

    def on_item_clicked(self, item):
        symbol = item.data(QtCore.Qt.ItemDataRole.UserRole)
        self.selected.emit(symbol)
        self.hide()


class FlowmapWindow(QtWidgets.QMainWindow):
    """
    A responsive, dark-themed PyQt6 window for order book depth and trade flow visualization.
    Integrates Binance-like search suggestions, dynamic symbol switching, background historical data
    queries, and self-contained WebSocket streaming.
    """
    
    def __init__(self, initial_symbol: str, data_dir: str, historical_hours: float = 2.0, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Crypcodile Flowmap Visualizer")
        self.resize(1200, 800)
        
        self.symbol = initial_symbol
        self.data_dir = data_dir
        self.historical_hours = historical_hours
        
        # State data structures
        self.current_bids = {}
        self.current_asks = {}
        self._updating_plots = False
        self.y_range_initialized = False
        self.x_range_initialized = False
        self.auto_scroll = True
        self.sensitivity = 50
        
        self.time_resolution_s = 0.2
        self.max_history_len = 1000
        self.book_history = deque(maxlen=self.max_history_len)
        self.trade_history = []
        self.delta_history = []
        self.cum_delta = 0.0
        
        self.num_price_bins = 200
        self.default_price_range = 50.0
        
        # Background loading and streaming threads
        self.historical_loader = None
        self.feeder_thread = None
        
        # Event queue
        self.event_queue = queue.Queue()
        
        self.init_ui()
        
        # Load suggestions and symbols list
        suggestions = self.get_all_suggestions()
        self.suggestion_popup.set_symbols(suggestions)
        
        # Load initial symbol data
        self.set_symbol(self.symbol)
        
        # Setup batch processing timer
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.process_queue)
        self.timer.start(100)

    def init_ui(self):
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
        
        pg.setConfigOption('background', '#121212')
        pg.setConfigOption('foreground', '#E0E0E0')
        pg.setConfigOption('antialias', True)
        
        main_container = QtWidgets.QWidget(self)
        self.setCentralWidget(main_container)
        main_layout = QtWidgets.QVBoxLayout(main_container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)
        
        # 0. Control Panel layout
        control_panel = QtWidgets.QHBoxLayout()
        
        # Binance-like search bar
        self.search_input = QtWidgets.QLineEdit(self)
        self.search_input.setPlaceholderText("Search Symbol (e.g. BTCUSDT)...")
        self.search_input.setFixedWidth(250)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #1E1E1E;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 6px 10px;
                color: #E0E0E0;
                font-size: 10pt;
            }
            QLineEdit:focus {
                border-color: #00BFFF;
            }
        """)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        self.search_input.selectionChanged.connect(self.show_suggestion_popup)
        
        # Sensitivity Slider
        self.sens_label = QtWidgets.QLabel("Heatmap Sensitivity: 50%", self)
        self.sens_label.setStyleSheet("font-weight: bold; color: #E0E0E0;")
        
        self.sens_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, self)
        self.sens_slider.setMinimum(1)
        self.sens_slider.setMaximum(100)
        self.sens_slider.setValue(50)
        self.sens_slider.setFixedWidth(150)
        self.sens_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #333333;
                height: 8px;
                background: #222222;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #00BFFF;
                border: 1px solid #00BFFF;
                width: 14px;
                height: 14px;
                margin: -3px 0;
                border-radius: 7px;
            }
        """)
        self.sens_slider.valueChanged.connect(self.on_sensitivity_changed)
        
        # Auto-Scroll Checkbox
        self.scroll_checkbox = QtWidgets.QCheckBox("Auto-Scroll", self)
        self.scroll_checkbox.setChecked(True)
        self.scroll_checkbox.setStyleSheet("font-weight: bold; color: #E0E0E0;")
        self.scroll_checkbox.stateChanged.connect(self.on_scroll_checkbox_changed)
        
        control_panel.addWidget(QtWidgets.QLabel("Symbol:", self))
        control_panel.addWidget(self.search_input)
        control_panel.addSpacing(20)
        control_panel.addWidget(self.sens_label)
        control_panel.addWidget(self.sens_slider)
        control_panel.addSpacing(20)
        control_panel.addWidget(self.scroll_checkbox)
        control_panel.addStretch(1)
        
        main_layout.addLayout(control_panel)
        
        # Central layout widget
        self.central_widget = pg.GraphicsLayoutWidget(parent=self)
        main_layout.addWidget(self.central_widget)
        
        # 1. Main Price Plot (Heatmap & Trade Bubbles)
        self.price_plot = self.central_widget.addPlot(row=0, col=0)
        self.price_plot.setTitle("Order Book Heatmap & Trades", color='#E0E0E0', size='12pt')
        self.price_plot.showGrid(x=True, y=True, alpha=0.15)
        self.price_plot.getAxis('bottom').setStyle(showValues=False)
        self.price_plot.getAxis('left').setLabel("Price", color='#E0E0E0')
        
        # Heatmap Image Item
        self.image_item = pg.ImageItem()
        self.price_plot.addItem(self.image_item)
        
        # Heatmap Lookup Table (Colormap)
        pos = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
        color = np.array([
            [18, 18, 18, 255],     # Dark grey background
            [0, 100, 255, 255],    # Cool blue
            [255, 0, 128, 255],    # Purple/magenta
            [255, 150, 0, 255],    # Warm orange
            [255, 255, 200, 255]   # Soft yellow/white
        ], dtype=np.ubyte)
        cmap = pg.ColorMap(pos, color)
        lut = cmap.getLookupTable(start=0.0, stop=1.0, nPts=256)
        self.image_item.setLookupTable(lut)
        
        # Trade Bubbles Scatter Plot
        self.trade_scatter = pg.ScatterPlotItem(pxMode=True)
        self.price_plot.addItem(self.trade_scatter)
        
        # 2. Vertical L2 Depth Profile Sidebar
        self.depth_plot = self.central_widget.addPlot(row=0, col=1)
        self.depth_plot.setTitle("L2 Depth Profile", color='#E0E0E0', size='12pt')
        self.depth_plot.showGrid(x=True, y=True, alpha=0.15)
        self.depth_plot.getAxis('left').setStyle(showValues=False)
        self.depth_plot.getAxis('bottom').setLabel("Size", color='#E0E0E0')
        
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
        
        # 3. Cumulative Delta Chart
        self.delta_plot = self.central_widget.addPlot(
            row=1, col=0,
            axisItems={'bottom': pg.DateAxisItem(orientation='bottom')}
        )
        self.delta_plot.setTitle("Cumulative Delta", color='#E0E0E0', size='10pt')
        self.delta_plot.showGrid(x=True, y=True, alpha=0.15)
        self.delta_plot.getAxis('left').setLabel("Cum Delta", color='#E0E0E0')
        
        self.delta_curve = pg.PlotDataItem(pen=pg.mkPen('#00BFFF', width=2))
        self.delta_plot.addItem(self.delta_curve)
        
        self.central_widget.ci.layout.setColumnStretchFactor(0, 5)
        self.central_widget.ci.layout.setColumnStretchFactor(1, 1)
        self.central_widget.ci.layout.setRowStretchFactor(0, 4)
        self.central_widget.ci.layout.setRowStretchFactor(1, 1)
        
        self.delta_plot.setXLink(self.price_plot)
        self.depth_plot.setYLink(self.price_plot)
        
        self.price_plot.enableAutoRange(enable=False)
        self.delta_plot.enableAutoRange(enable=False)
        self.depth_plot.enableAutoRange(enable=False)
        
        self.price_plot.sigYRangeChanged.connect(self.on_view_changed)
        self.price_plot.sigXRangeChanged.connect(self.on_view_changed)

        # Suggestion Popup
        self.suggestion_popup = SuggestionPopup(self)
        self.suggestion_popup.selected.connect(self.set_symbol)
        self.suggestion_popup.hide()

    def show_suggestion_popup(self):
        pos = self.search_input.mapTo(self, QtCore.QPoint(0, self.search_input.height()))
        self.suggestion_popup.setGeometry(pos.x(), pos.y(), self.search_input.width() + 100, 250)
        self.suggestion_popup.show()
        self.suggestion_popup.raise_()

    def on_search_text_changed(self, text):
        if self.suggestion_popup.isHidden():
            self.show_suggestion_popup()
        self.suggestion_popup.filter_text(text)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if hasattr(self, "suggestion_popup") and self.suggestion_popup.isVisible():
            pos = event.position().toPoint()
            if not self.suggestion_popup.geometry().contains(pos) and not self.search_input.geometry().contains(pos):
                self.suggestion_popup.hide()

    def on_view_changed(self):
        if getattr(self, "_updating_plots", False):
            return
            
        if self.book_history:
            latest_ts = self.book_history[-1]['timestamp']
            x_range = self.price_plot.viewRange()[0]
            if latest_ts - x_range[1] > 3.0:
                if self.auto_scroll:
                    self.auto_scroll = False
                    self.scroll_checkbox.blockSignals(True)
                    self.scroll_checkbox.setChecked(False)
                    self.scroll_checkbox.blockSignals(False)
            else:
                if not self.auto_scroll:
                    self.auto_scroll = True
                    self.scroll_checkbox.blockSignals(True)
                    self.scroll_checkbox.setChecked(True)
                    self.scroll_checkbox.blockSignals(False)
                
        self.update_plots()

    def on_sensitivity_changed(self, value):
        self.sensitivity = value
        self.sens_label.setText(f"Heatmap Sensitivity: {value}%")
        self.update_plots()

    def on_scroll_checkbox_changed(self, state):
        self.auto_scroll = (state == 2 or state == QtCore.Qt.CheckState.Checked.value or bool(state))
        self.update_plots()

    def set_symbol(self, new_symbol: str):
        """Dynamically switches the visualizer to inspect a new symbol."""
        if not new_symbol or ":" not in new_symbol:
            return
            
        self.symbol = new_symbol
        self.setWindowTitle(f"Crypcodile Flowmap Visualizer - [{self.symbol}]")
        self.search_input.setText(self.symbol)
        
        # Stop existing feeder thread
        if self.feeder_thread:
            self.feeder_thread.stop()
            self.feeder_thread.wait()
            self.feeder_thread = None
            
        # Cancel any active historical loader thread to prevent race conditions
        if self.historical_loader:
            try:
                self.historical_loader.loaded.disconnect(self.on_historical_data_loaded)
            except Exception:
                pass
            if self.historical_loader.isRunning():
                self.historical_loader.terminate()
                self.historical_loader.wait()
            self.historical_loader = None
            
        # Clear queue and data
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
                
        self.current_bids.clear()
        self.current_asks.clear()
        self.book_history.clear()
        self.trade_history.clear()
        self.delta_history.clear()
        self.cum_delta = 0.0
        
        self.x_range_initialized = False
        self.y_range_initialized = False
        
        self.price_plot.setTitle(f"Loading {self.symbol}...", color='#FFA500')
        
        # Query new historical data asynchronously
        self.historical_loader = HistoricalLoaderThread(self.data_dir, self.symbol, self.historical_hours)
        self.historical_loader.loaded.connect(self.on_historical_data_loaded)
        self.historical_loader.start()

    def on_historical_data_loaded(self, events):
        self.price_plot.setTitle(f"Order Book Heatmap & Trades [{self.symbol}]", color='#E0E0E0', size='12pt')
        
        # Load historical data
        for event in events:
            self.handle_event(event)
            
        self.update_plots()
        
        # Split symbol for raw live ingestion
        parts = self.symbol.split(":", 1)
        exchange = parts[0]
        symbol_raw = parts[1]
        
        # Start new live feeder thread
        self.feeder_thread = LiveFeederThread(exchange, symbol_raw, self.event_queue)
        self.feeder_thread.start()

    def handle_event(self, event):
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
            
            # Prevent excessive level accumulation (AI-slop sorting optimization)
            if len(self.current_bids) > 400:
                mid = self._calculate_mid()
                if mid is not None:
                    self.current_bids = {px: sz for px, sz in self.current_bids.items() if px >= mid * 0.8}
            if len(self.current_asks) > 400:
                mid = self._calculate_mid()
                if mid is not None:
                    self.current_asks = {px: sz for px, sz in self.current_asks.items() if px <= mid * 1.2}
                    
            ts = self._extract_timestamp(event)
            mid = self._calculate_mid()
            if mid is not None:
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
            
            # Prevent excessive level accumulation (AI-slop sorting optimization)
            if len(self.current_bids) > 400:
                mid = self._calculate_mid()
                if mid is not None:
                    self.current_bids = {px: sz for px, sz in self.current_bids.items() if px >= mid * 0.8}
            if len(self.current_asks) > 400:
                mid = self._calculate_mid()
                if mid is not None:
                    self.current_asks = {px: sz for px, sz in self.current_asks.items() if px <= mid * 1.2}
                    
            ts = self._extract_timestamp(event)
            mid = self._calculate_mid()
            if mid is not None:
                self._add_to_book_history(ts, self.current_bids, self.current_asks, mid)
            
        elif channel == 'trade' or isinstance(event, Trade):
            ts = self._extract_timestamp(event)
            if is_dict:
                price = float(event.get('price', 0.0))
                amount = float(event.get('amount', 0.0))
                side = event.get('side')
            else:
                price = float(getattr(event, 'price', 0.0))
                amount = float(getattr(event, 'amount', 0.0))
                side = getattr(event, 'side', None)
                
            self.trade_history.append({
                'timestamp': ts,
                'price': price,
                'amount': amount,
                'side': side
            })
            if len(self.trade_history) > 1000:
                self.trade_history.pop(0)
                
            val = Side.BUY if isinstance(side, Side) else side
            sign = 1.0 if (val == Side.BUY or str(val).lower() == "buy") else -1.0
            self.cum_delta += amount * sign
            
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

    def _calculate_mid(self) -> float | None:
        best_bid = max(self.current_bids.keys()) if self.current_bids else None
        best_ask = min(self.current_asks.keys()) if self.current_asks else None
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        elif best_bid is not None:
            return best_bid
        elif best_ask is not None:
            return best_ask
        return None

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
            self.book_history[-1]['bids'] = dict(bids)
            self.book_history[-1]['asks'] = dict(asks)
            self.book_history[-1]['mid'] = mid

    def update_plots(self):
        if not self.book_history:
            return
            
        self._updating_plots = True
        try:
            x_range = self.price_plot.viewRange()[0]
            y_range = self.price_plot.viewRange()[1]
            
            latest_state = self.book_history[-1]
            mid = latest_state['mid']
            
            p_min, p_max = y_range[0], y_range[1]
            t_min, t_max = x_range[0], x_range[1]
            
            if not self.y_range_initialized:
                p_min = mid - self.default_price_range / 2
                p_max = mid + self.default_price_range / 2
                self.price_plot.setYRange(p_min, p_max, padding=0)
                self.y_range_initialized = True
            elif getattr(self, "auto_scroll", True):
                margin = (p_max - p_min) * 0.15
                if mid < p_min + margin or mid > p_max - margin:
                    center_y = mid
                    half_height = (p_max - p_min) / 2.0
                    p_min = center_y - half_height
                    p_max = center_y + half_height
                    self.price_plot.setYRange(p_min, p_max, padding=0)
                
            if getattr(self, "auto_scroll", True):
                view_width = t_max - t_min if (self.x_range_initialized and t_max > t_min) else 60.0
                t_max = latest_state['timestamp']
                t_min = t_max - view_width
                self.price_plot.setXRange(t_min, t_max, padding=0)
                self.x_range_initialized = True
                
            bin_width = (p_max - p_min) / self.num_price_bins
            if bin_width <= 0:
                bin_width = 1.0
                
            # 1. Update Heatmap
            if math.isnan(t_min) or math.isnan(t_max):
                visible_states = list(self.book_history)
            else:
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
                            
            grid = np.log1p(grid)
            
            max_val = grid.max() if grid.size > 0 else 1.0
            if max_val <= 0:
                max_val = 1.0
                
            sensitivity = getattr(self, "sensitivity", 50)
            threshold = max_val * (50.0 / float(sensitivity))
            if threshold <= 0:
                threshold = 1.0
                
            self.image_item.setImage(grid, autoLevels=False)
            self.image_item.setLevels((0.0, threshold))
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
            sizes = 5 + (volumes / max_vol) * 20
        else:
            sizes = [10] * len(visible_trades)
            
        brushes = []
        for t in visible_trades:
            val = Side.BUY if isinstance(t['side'], Side) else t['side']
            if val == Side.BUY or str(val).lower() == "buy":
                brushes.append(pg.mkBrush(0, 220, 120, 180))
            else:
                brushes.append(pg.mkBrush(240, 60, 60, 180))
                
        self.trade_scatter.setData(x=x, y=y, size=sizes, brush=brushes, pen=None)

    def update_depth_profile(self, p_min: float, p_max: float, bin_width: float):
        if not self.book_history:
            return
            
        latest_state = self.book_history[-1]
        
        bin_centers = np.linspace(p_min + bin_width/2, p_max - bin_width/2, self.num_price_bins)
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
                    
        bar_heights = bin_width * 0.8
        
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

    def load_catalog_symbols(self):
        try:
            catalog = Catalog(self.data_dir)
            tables = ["trade", "book_snapshot", "book_delta"]
            db_symbols = set()
            for t in tables:
                try:
                    df = catalog.query(f"SELECT DISTINCT symbol FROM {t}")
                    for sym in df["symbol"].to_list():
                        if sym:
                            db_symbols.add(sym)
                except Exception:
                    pass
            return list(db_symbols)
        except Exception:
            return []

    def get_all_suggestions(self):
        popular_bases = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT"]
        exchanges = [
            ("binance-spot", "spot"),
            ("binance-usdm", "perp"),
            ("bybit-perp", "perp"),
            ("okx-perp", "perp"),
            ("hyperliquid-perp", "perp")
        ]
        
        suggestions = []
        seen = set()
        
        for base in popular_bases:
            usdt_pair = f"{base}USDT"
            for exch, cat in exchanges:
                canonical = f"{exch}:{usdt_pair}"
                if canonical not in seen:
                    suggestions.append({
                        "symbol": canonical,
                        "display": f"{usdt_pair} ({exch.upper()})",
                        "category": cat
                    })
                    seen.add(canonical)

        db_symbols = self.load_catalog_symbols()
        for sym in db_symbols:
            if sym not in seen:
                parts = sym.split(":", 1)
                exch = parts[0] if len(parts) > 1 else ""
                cat = "spot" if "spot" in exch else "perp"
                suggestions.append({
                    "symbol": sym,
                    "display": f"{parts[1] if len(parts) > 1 else sym} ({exch.upper()})",
                    "category": cat
                })
                seen.add(sym)
                
        return suggestions

    def process_queue(self):
        has_updates = False
        while not self.event_queue.empty():
            try:
                event = self.event_queue.get_nowait()
                self.handle_event(event)
                has_updates = True
            except queue.Empty:
                break
        if has_updates:
            self.update_plots()

    def closeEvent(self, event):
        if self.historical_loader:
            try:
                self.historical_loader.loaded.disconnect(self.on_historical_data_loaded)
            except Exception:
                pass
            if self.historical_loader.isRunning():
                self.historical_loader.terminate()
                self.historical_loader.wait()
        if self.feeder_thread:
            self.feeder_thread.stop()
            if not self.feeder_thread.wait(1000):
                self.feeder_thread.terminate()
                self.feeder_thread.wait()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        if event.key() == QtCore.Qt.Key.Key_Escape:
            if hasattr(self, "suggestion_popup") and self.suggestion_popup.isVisible():
                self.suggestion_popup.hide()

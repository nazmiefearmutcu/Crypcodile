import sys
import os
import math
import time

# Default MainWindow geometry (flowmap/ui/main_window.py: resize(1500, 950)).
_DEFAULT_WINDOW_W = 1500
_DEFAULT_WINDOW_H = 950
# HeatmapWidget margins / defaults.
_DEFAULT_PRICE_AXIS_W = 62
_DEFAULT_RIGHT_MARGIN_W = 60
_DEFAULT_SIDEBAR_W = 320
_DEFAULT_COLUMN_WIDTH = 1.0
_DEFAULT_ROW_HEIGHT = 4
# FIND-P236-01: never equal-time-bin history into a 1-column buffer.
_MIN_HIST_BW = 64
_MIN_HIST_VIS_ROWS = 100


def compute_hist_target_bw(
    window_width: int = _DEFAULT_WINDOW_W,
    *,
    price_axis_w: int = _DEFAULT_PRICE_AXIS_W,
    right_margin_w: int = _DEFAULT_RIGHT_MARGIN_W,
    column_width: float = _DEFAULT_COLUMN_WIDTH,
    sidebar_w: int = _DEFAULT_SIDEBAR_W,
    heatmap_stretch: float = 0.8,
    min_bw: int = _MIN_HIST_BW,
) -> int:
    """Compute heatmap history column count for equal-time historical binning.

    Mirrors HeatmapWidget geometry::

        hm_w = heatmap_w - price_axis_w
        timeline_w = hm_w - right_margin_w
        target_bw = int(timeline_w / column_width)

    ``heatmap_w`` is estimated from the default MainWindow layout
    (window minus sidebar, then ~80% of the left pane for the heatmap /
    volume-profile 8:2 stretch). Floor at ``min_bw`` (default 64) so a
    pre-show 1×1 DensityEngine buffer never collapses multi-hour history
    into a single bin (FIND-P236-01).
    """
    cw = float(column_width) if column_width and column_width > 0 else 1.0
    left_w = max(1, int(window_width) - int(sidebar_w))
    stretch = heatmap_stretch if 0.0 < heatmap_stretch <= 1.0 else 0.8
    heatmap_w = max(1, int(left_w * stretch))
    hm_w = max(1, heatmap_w - int(price_axis_w))
    timeline_w = max(1, hm_w - int(right_margin_w))
    bw = max(1, int(timeline_w / cw))
    return max(int(min_bw), bw)


def compute_hist_vis_rows(
    window_height: int = _DEFAULT_WINDOW_H,
    *,
    row_height: int = _DEFAULT_ROW_HEIGHT,
    heatmap_row_stretch: float = 5.0 / 6.0,
    min_vis_rows: int = _MIN_HIST_VIS_ROWS,
) -> int:
    """Compute visible price rows for pre-show DensityEngine.resize (FIND-P236-01)."""
    rh = int(row_height) if row_height and row_height > 0 else _DEFAULT_ROW_HEIGHT
    stretch = heatmap_row_stretch if 0.0 < heatmap_row_stretch <= 1.0 else (5.0 / 6.0)
    heatmap_h = max(1, int(int(window_height) * stretch))
    vr = max(1, heatmap_h // rh)
    return max(int(min_vis_rows), vr)


# Append the independent flowmap directory to python's import path (portable).
# Order: FLOWMAP_HOME env → sibling of Crypcodile repo → common local checkouts.
def _resolve_flowmap_path() -> str | None:
    candidates = []
    env = os.environ.get("FLOWMAP_HOME")
    if env:
        candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    # .../Crypcodile/src/crypcodile/gui → up to home/workspace siblings
    for rel in (
        os.path.join(here, "..", "..", "..", "..", "flowmap"),  # ~/Crypcodile → ~/flowmap
        os.path.join(here, "..", "..", "..", "..", "..", "flowmap"),
        os.path.expanduser("~/flowmap"),
    ):
        candidates.append(os.path.abspath(rel))
    for c in candidates:
        if c and os.path.isdir(c) and os.path.isdir(os.path.join(c, "flowmap")):
            return c
    return None

flowmap_path = _resolve_flowmap_path()
if flowmap_path and flowmap_path not in sys.path:
    sys.path.insert(0, flowmap_path)

from flowmap.ui.main_window import MainWindow as StandaloneMainWindow
from flowmap.core import Level2Snapshot, Level2Update, Trade, BBO, Side as FlowmapSide, is_buy_side

def dict_to_flowmap_objects(event: dict) -> list:
    channel = event.get("channel")
    local_ts_ns = event.get("local_ts", 0)
    ts_sec = local_ts_ns / 1_000_000_000.0
    symbol = event.get("symbol", "")
    
    if channel == "book_snapshot":
        bids = tuple((float(p), float(s)) for (p, s) in event.get("bids", []) if s > 0)
        asks = tuple((float(p), float(s)) for (p, s) in event.get("asks", []) if s > 0)
        return [Level2Snapshot(
            timestamp=ts_sec,
            symbol=symbol,
            bids=bids,
            asks=asks,
            bid_depth=len(bids),
            ask_depth=len(asks)
        )]
        
    elif channel == "book_delta":
        if event.get("is_snapshot") is True:
            bids = tuple((float(p), float(s)) for (p, s) in event.get("bids", []) if s > 0)
            asks = tuple((float(p), float(s)) for (p, s) in event.get("asks", []) if s > 0)
            return [Level2Snapshot(
                timestamp=ts_sec,
                symbol=symbol,
                bids=bids,
                asks=asks,
                bid_depth=len(bids),
                ask_depth=len(asks)
            )]
            
        updates = []
        for p, s in event.get("bids", []):
            updates.append(Level2Update(
                timestamp=ts_sec,
                symbol=symbol,
                side=FlowmapSide.BID,
                price=float(p),
                size=float(s)
            ))
        for p, s in event.get("asks", []):
            updates.append(Level2Update(
                timestamp=ts_sec,
                symbol=symbol,
                side=FlowmapSide.ASK,
                price=float(p),
                size=float(s)
            ))
        return updates
        
    elif channel == "trade":
        raw_side = event.get("side", "")
        mapped_side = FlowmapSide.BUY if is_buy_side(raw_side) else FlowmapSide.SELL
            
        return [Trade(
            timestamp=ts_sec,
            symbol=symbol,
            price=float(event.get("price", 0.0)),
            size=float(event.get("amount", 0.0) or event.get("size", 0.0)),
            side=mapped_side,
            trade_id=event.get("id"),
            is_liquidation=bool(event.get("liquidation"))
        )]
        
    return []


class FlowmapWindow(StandaloneMainWindow):
    """
    FlowmapWindow wraps the standalone MainWindow to integrate it into Crypcodile.
    By inheriting directly from the standalone MainWindow, any updates to the
    independent flowmap code are immediately reflected here.
    """
    def __init__(self, initial_symbol: str = "binance-spot:SOLUSDT", data_dir: str | None = None, historical_hours: float = 2.0):
        if not data_dir:
            data_dir = os.environ.get("FLOWMAP_DATA_DIR") or (
                os.path.expanduser("~/data")
                if os.path.isdir(os.path.expanduser("~/data"))
                else "."
            )
        super().__init__(symbol=initial_symbol, data_dir=data_dir, historical_hours=historical_hours)
        self.setWindowTitle(f"Crypcodile Flowmap Visualizer - [{initial_symbol}]")
        
        # Load historical data and populate visualizer state before live feed starts
        if historical_hours > 0:
            try:
                self.load_historical_data(data_dir, initial_symbol, historical_hours)
            except Exception as e:
                import sys
                sys.stderr.write(f"Error loading historical data: {e}\n")
                sys.stderr.flush()
                try:
                    self._status.showMessage(
                        f"Historical preload failed: {e}  |  live feed still available"
                    )
                except Exception:
                    pass

    def _ensure_hist_engine_geometry(self) -> tuple[int, int]:
        """Resize DensityEngine (and heatmap widget if pre-show) before hist binning.

        FIND-P236-01: ``DensityEngine`` starts as a 1×1 buffer; hist preload runs
        in ``__init__`` before ``show()``, so reading buffer width collapses the
        entire window into one equal-time bin. Return ``(vis_rows, target_bw)``
        with ``target_bw >= 64``.
        """
        hm = self.heatmap
        engine = hm._engine
        col_w = float(getattr(hm, "column_width", _DEFAULT_COLUMN_WIDTH) or _DEFAULT_COLUMN_WIDTH)
        row_h = int(getattr(hm, "row_height", _DEFAULT_ROW_HEIGHT) or _DEFAULT_ROW_HEIGHT)
        price_axis_w = int(getattr(hm, "price_axis_w", _DEFAULT_PRICE_AXIS_W))
        right_margin_w = int(getattr(hm, "right_margin_w", _DEFAULT_RIGHT_MARGIN_W))

        # Prefer live widget metrics once layout has run; otherwise default window.
        widget_w = int(hm.width()) if hasattr(hm, "width") else 0
        widget_h = int(hm.height()) if hasattr(hm, "height") else 0
        laid_out = widget_w > 100 and widget_h > 100

        if laid_out:
            vis_rows = max(1, widget_h // max(1, row_h))
            hm_w = max(1, widget_w - price_axis_w)
            timeline_w = max(1, hm_w - right_margin_w)
            target_bw = max(1, int(timeline_w / col_w))
        else:
            # Match MainWindow default 1500×950 + layout estimate.
            win_w = int(self.width()) if hasattr(self, "width") and int(self.width()) >= 900 else _DEFAULT_WINDOW_W
            win_h = int(self.height()) if hasattr(self, "height") and int(self.height()) >= 600 else _DEFAULT_WINDOW_H
            target_bw = compute_hist_target_bw(
                win_w,
                price_axis_w=price_axis_w,
                right_margin_w=right_margin_w,
                column_width=col_w,
            )
            vis_rows = compute_hist_vis_rows(win_h, row_height=row_h)
            # Force widget size so later push_snapshot / rebuild_heatmap use the
            # same geometry (they read height()/width(), not engine buffer).
            intended_w = int(target_bw * col_w) + price_axis_w + right_margin_w
            intended_h = int(vis_rows * row_h)
            if hasattr(hm, "resize"):
                hm.resize(max(intended_w, 200), max(intended_h, 200))

        # Hard floors — never bin history into width 1 / tiny strip.
        target_bw = max(_MIN_HIST_BW, int(target_bw))
        vis_rows = max(_MIN_HIST_VIS_ROWS, int(vis_rows))

        engine.resize(vis_rows, target_bw)
        # Keep HeatmapWidget size tracking in sync so push_snapshot does not
        # immediately rebuild down to an unshown 1-wide geometry.
        if hasattr(hm, "_last_vis_rows"):
            hm._last_vis_rows = vis_rows
        if hasattr(hm, "_last_hm_w"):
            hm._last_hm_w = target_bw

        buf_w = int(engine.get_buffer().shape[1])
        if buf_w < _MIN_HIST_BW:
            raise RuntimeError(
                f"FIND-P236-01: hist engine buffer width is {buf_w} after resize "
                f"(need >= {_MIN_HIST_BW}); refusing to bin history into width 1"
            )
        return vis_rows, max(target_bw, buf_w)

    def load_historical_data(self, data_dir: str, symbol: str, historical_hours: float) -> None:
        """Pre-load historical database records and populate visualizer history state in equal time bins."""
        import polars as pl
        from crypcodile.store.catalog import Catalog
        
        catalog = Catalog(data_dir)
        end_ns = int(time.time_ns())
        
        try:
            max_df = catalog.query(
                ("SELECT max(local_ts) as max_t FROM trade WHERE symbol = '" + str(symbol).replace("'","''") + "'")
            )
            if len(max_df) > 0 and max_df["max_t"][0] is not None:
                end_ns = int(max_df["max_t"][0])
        except Exception:
            pass

        start_ns = end_ns - int(historical_hours * 3600 * 1_000_000_000)
        
        try:
            snap_df = catalog.scan("book_snapshot", symbol, start_ns, end_ns)
        except Exception:
            snap_df = pl.DataFrame()

        try:
            delta_df = catalog.scan("book_delta", symbol, start_ns, end_ns)
        except Exception:
            delta_df = pl.DataFrame()

        try:
            trade_df = catalog.scan("trade", symbol, start_ns, end_ns)
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
        
        if not events:
            return
            
        events.sort(key=lambda x: x.get("local_ts") or 0)
        
        # Normalize bids/asks
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

        # FIND-P236-01: size DensityEngine before reading bin count (not 1×1 pre-show).
        _vis_rows, bw = self._ensure_hist_engine_geometry()
        assert bw >= _MIN_HIST_BW, f"hist bin width must be >= {_MIN_HIST_BW}, got {bw}"

        # Divide the events into bw (width of the heatmap) bins
        first_ts = start_ns
        last_ts = end_ns
        total_span = last_ts - first_ts
        if total_span <= 0:
            total_span = 1

        bin_duration = total_span / bw
        
        # Bin boundaries
        bin_edges = [first_ts + i * bin_duration for i in range(bw + 1)]
        
        # Group events by bins
        bin_events = [[] for _ in range(bw)]
        for ev in events:
            ts = ev.get("local_ts", first_ts)
            bin_idx = min(bw - 1, max(0, int((ts - first_ts) / bin_duration)))
            bin_events[bin_idx].append(ev)
            
        # Reset current state
        self._order_book.reset()
        self._order_book.symbol = symbol
        self._pulse.reset()
        if hasattr(self, 'volume_profile') and self.volume_profile is not None:
            self.volume_profile.reset()
        if hasattr(self, 'heatmap') and self.heatmap is not None:
            self.heatmap.reset()
            # reset() → rebuild_heatmap may re-read unshown widget size; re-apply
            # target geometry so equal-time pushes keep the intended bw (FIND-P236-01).
            self._ensure_hist_engine_geometry()
            
        # Process bin-by-bin and push exactly 1 snapshot to heatmap per bin
        for i, ev_list in enumerate(bin_events):
            bin_trades = []
            
            for ev in ev_list:
                objs = dict_to_flowmap_objects(ev)
                for obj in objs:
                    if isinstance(obj, Level2Snapshot):
                        self._order_book.apply_snapshot(obj)
                    elif isinstance(obj, Level2Update):
                        self._order_book.apply_update(obj)
                    elif isinstance(obj, Trade):
                        self._order_book.record_trade(obj)
                        bin_trades.append(obj)
                    elif isinstance(obj, BBO):
                        self._order_book.apply_bbo(obj)
                        
            # Push trade updates to pulse & volume profile
            if bin_trades:
                self.heatmap.add_trades(bin_trades)
                self._pulse.add_trades(bin_trades)
                if hasattr(self, 'volume_profile') and self.volume_profile is not None:
                    self.volume_profile.add_trades(bin_trades)
                    
            # Snapshot state at the end of the bin
            levels = self._order_book.get_levels()
            bbo = self._order_book.bbo
            cvd = self._order_book.get_volume_delta()
            
            bin_ts_sec = bin_edges[i + 1] / 1_000_000_000.0
            self.heatmap.push_snapshot(levels, bbo, bin_ts_sec, cvd=cvd)
            
        # 3. Optional small gap-fill between last DB timestamp and "now".
        # NEVER wipe preloaded history when the gap is large (e.g. DB last
        # update days ago). That used to call heatmap.reset() and throw away
        # the entire equal-time hist pass — user saw empty chart until live
        # WS caught up (endless-loop root cause for "hist loads then vanishes").
        now_ns = int(time.time_ns())
        gap_ns = now_ns - last_ts
        if gap_ns > 0 and bin_duration > 0:
            gap_bins = int(gap_ns / bin_duration)
            if 0 < gap_bins < bw:
                levels = self._order_book.get_levels()
                bbo = self._order_book.bbo
                cvd = self._order_book.get_volume_delta()
                for k in range(1, gap_bins + 1):
                    bin_ts_sec = (last_ts + k * bin_duration) / 1_000_000_000.0
                    self.heatmap.push_snapshot(levels, bbo, bin_ts_sec, cvd=cvd)
            # else: gap ≥ screen width — keep hist as-is; live feed will scroll it

        if hasattr(self, 'volume_profile') and self.volume_profile is not None:
            self.volume_profile.update()

        try:
            n_ev = len(events)
            self._status.showMessage(
                f"Historical preload: {n_ev} events over {historical_hours:g}h  |  "
                f"{symbol}  |  press Start / auto-start for live"
            )
        except Exception:
            pass

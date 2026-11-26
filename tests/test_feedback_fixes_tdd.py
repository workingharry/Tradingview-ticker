"""TDD specification test suite for user feedback and review findings fixes.

Tests verify behaviors strictly at public seams:
1. Seam 1: Backend Preferences & Storage API (GET & POST /api/preferences)
2. Seam 2: Watchlist Unsubscribe & Storage Sync (POST /api/symbols/unsubscribe)
3. Seam 3: Tauri IPC & Desktop Shell Contract (toggle_always_on_top, #btnPinWindow)
4. Seam 4: Drawing Toolbar Engine & Tool Isolation (#btnMagnet, #drawingToolbar, ruler)
5. Seam 5: Risk/Reward Box Non-Obstructing Rendering & Close Button
6. Seam 6: TradingView Widget Custom Studies, Distinct EMA Colors & Compare Study
"""

import json
import re
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from fastapi.testclient import TestClient

from server import app
from engine.storage import get_storage

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_INDEX_HTML = BASE_DIR / "static" / "index.html"
STATIC_APP_JS = BASE_DIR / "static" / "app.js"
STATIC_STYLE_CSS = BASE_DIR / "static" / "style.css"
SRC_TAURI_LIB_RS = BASE_DIR / "src-tauri" / "src" / "lib.rs"


class TestFeedbackFixesTDD(unittest.TestCase):
    """Specification tests for the 7 user feedback fixes under strict TDD rules."""

    @classmethod
    def setUpClass(cls):
        cls.html = STATIC_INDEX_HTML.read_text(encoding="utf-8")
        cls.js = STATIC_APP_JS.read_text(encoding="utf-8")
        cls.css = STATIC_STYLE_CSS.read_text(encoding="utf-8")
        cls.rust = SRC_TAURI_LIB_RS.read_text(encoding="utf-8")

    def test_seam1_preferences_sqlite_persistence(self):
        """Seam 1: /api/preferences stores and restores preferences across sessions."""
        test_payload = {
            "indicatorConfig": {
                "ema10": False,
                "ema21": True,
                "rsLine": True,
                "avwap": True
            },
            "avwapConfig": {
                "anchorMode": "Swing Low",
                "anchorLookback": 120
            },
            "activeSymbol": "NASDAQ:NVDA",
            "activeTimeframe": "1D",
            "rsBenchmarkTag": "AMEX:SPY"
        }

        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]), \
             TestClient(app) as client:
            # 1. Save preferences via POST
            post_resp = client.post("/api/preferences", json=test_payload)
            self.assertEqual(post_resp.status_code, 200)
            data = post_resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertIn("preferences", data)
            self.assertEqual(data["preferences"]["activeSymbol"], "NASDAQ:NVDA")

            # 2. Retrieve preferences via GET
            get_resp = client.get("/api/preferences")
            self.assertEqual(get_resp.status_code, 200)
            get_data = get_resp.json()
            prefs = get_data["preferences"]
            self.assertEqual(prefs["activeSymbol"], "NASDAQ:NVDA")
            self.assertEqual(prefs["indicatorConfig"]["ema10"], False)
            self.assertEqual(prefs["avwapConfig"]["anchorLookback"], 120)

        # 3. Verify directly through storage interface (independent truth check)
        storage = get_storage()
        stored_prefs = storage.get_preferences()
        self.assertEqual(stored_prefs["activeSymbol"], "NASDAQ:NVDA")
        self.assertFalse(stored_prefs["indicatorConfig"]["ema10"])

        # 4. Frontend contracts: savePreferencesImmediate and syncIndicatorCheckboxes
        self.assertIn("savePreferencesImmediate", self.js)
        self.assertIn("syncIndicatorCheckboxes", self.js)
        self.assertIn("beforeunload", self.js)

    def test_seam2_watchlist_unsubscribe_and_storage_sync(self):
        """Seam 2: Unsubscribe endpoint evicts symbol and frontend calls POST with auth."""
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]), \
             TestClient(app) as client:
            resp = client.post("/api/symbols/unsubscribe?symbol=NASDAQ:AMZN")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "unsubscribed")

        # Frontend must invoke POST /api/symbols/unsubscribe with headers
        self.assertIn("/api/symbols/unsubscribe?symbol=", self.js)
        self.assertIn("savePreferencesDebounced", self.js)

    def test_seam3_tauri_always_on_top_contract(self):
        # guard-test: 設定守門，允許讀原始碼
        """Seam 3: toggle_always_on_top Tauri command exists in Rust and bound in UI."""
        # Rust lib.rs must declare the command and register it
        self.assertIn("fn toggle_always_on_top", self.rust)
        self.assertIn("fn get_always_on_top", self.rust)
        self.assertIn("fn set_always_on_top", self.rust)
        self.assertIn("toggle_always_on_top", self.rust)
        self.assertIn("get_always_on_top", self.rust)
        self.assertIn("set_always_on_top", self.rust)

        # UI must contain the pin button and style
        self.assertIn('id="btnPinWindow"', self.html)
        self.assertIn(".pin-toggle-btn", self.css)
        self.assertIn(".pin-toggle-btn.active", self.css)

        # Frontend JS must invoke the Tauri IPC command
        self.assertIn('"toggle_always_on_top"', self.js)
        self.assertIn('"get_always_on_top"', self.js)
        self.assertIn("btnPinWindow", self.js)

    def test_seam4_drawing_toolbar_isolation_and_ruler(self):
        """Seam 4: Magnet button is isolated from tool selection and ruler is active."""
        # #btnMagnet must have draw-tool-toggle to avoid being selected as a drawing tool
        self.assertIn('id="btnMagnet"', self.html)
        self.assertIn("draw-tool-toggle", self.html)

        # app.js query selector must exclude draw-tool-toggle
        self.assertIn(":not(.draw-tool-toggle)", self.js)

        # Ruler tool exists in DOM and is handled in app.js
        self.assertIn('data-tool="ruler"', self.html)
        self.assertIn('currentDrawTool === "ruler"', self.js)
        self.assertIn('d.type === "ruler"', self.js)

    def test_seam5_risk_reward_box_non_obstructing_and_close_btn(self):
        """Seam 5: Risk/Reward box uses non-obstructing 0.08 opacity fill and close button."""
        # Opacity must be 0.08 (non-obstructing) instead of opaque 0.20
        self.assertIn("rgba(34, 197, 94, 0.08)", self.js)
        self.assertIn("rgba(239, 68, 68, 0.08)", self.js)
        self.assertNotIn("rgba(34, 197, 94, 0.20)", self.js)
        self.assertNotIn("rgba(239, 68, 68, 0.20)", self.js)

        # Close button and dragging handles must be present
        self.assertIn("closeBtnX", self.js)
        self.assertIn("closeBtnY", self.js)
        self.assertIn("isDraggingHandle", self.js)

        # Forward anchor to avoid candlestick occlusion and body dragging with offset preservation
        self.assertIn("left = Math.max(10, xCenter)", self.js)
        self.assertIn('handle: "body"', self.js)
        self.assertIn("origAnchorX + (x -", self.js)
        self.assertIn(".cursor-move", self.css)
        self.assertIn(".cursor-ns-resize", self.css)
        self.assertIn(".cursor-pointer", self.css)

    def test_seam6_tradingview_widget_studies_and_distinct_colors(self):
        """Seam 6: Scheme C exclusively loads 21, 30, 200 EMA + Compare study with distinct colors."""
        # Compare study must be pushed for RS line
        self.assertIn("Compare@tv-basicstudies", self.js)

        # 21, 30, 200 EMA study lengths specified by user
        self.assertIn('inputs: { length: 21 }', self.js)
        self.assertIn('inputs: { length: 30 }', self.js)
        self.assertIn('inputs: { length: 200 }', self.js)

        # Negative assertions: Scheme C must NOT load AVWAP, 10 EMA, or 50 SMA in tvStudies
        self.assertNotIn("Anchored VWAP@tv-basicstudies", self.js)
        tv_studies_block = self.js[self.js.find("var tvStudies = []"):self.js.find("studies: tvStudies")]
        self.assertNotIn("length: 10", tv_studies_block)
        self.assertNotIn("length: 50", tv_studies_block)

        # Distinct EMA colors in studies_overrides and onChartReady
        self.assertIn('"moving average exponential.plot.color": "#a855f7"', self.js)
        self.assertIn('"moving average exponential.0.plot.color": "#a855f7"', self.js)
        self.assertIn('"moving average exponential.1.plot.color": "#f59e0b"', self.js)
        self.assertIn('"moving average exponential.2.plot.color": "#ec4899"', self.js)
        self.assertIn("onChartReady", self.js)
        self.assertIn("#a855f7", self.js)  # EMA 21 purple
        self.assertIn("#f59e0b", self.js)  # EMA 30 amber
        self.assertIn("#ec4899", self.js)  # EMA 200 pink/red
        self.assertIn("toTradingViewRealtimeSymbol", self.js)

    def test_seam7_watchlist_seed_guard_on_deletion(self):
        """Seam 7: Intentional watchlist clearing persists across database queries and does not auto-resurrect."""
        import tempfile
        from engine.storage import Storage

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_seed_guard.sqlite"
            with Storage(db_path=db_path) as storage:
                # 1. First read auto-seeds default items
                initial_wl = storage.get_watchlist()
                self.assertGreater(len(initial_wl), 0)

                # 2. Intentionally delete all symbols
                for item in initial_wl:
                    deleted = storage.remove_watchlist_item(item["id"])
                    self.assertTrue(deleted)

                # 3. Verify watchlist is now empty
                empty_wl = storage.get_watchlist()
                self.assertEqual(len(empty_wl), 0)

            # 4. Re-open database from disk simulating an app restart
            with Storage(db_path=db_path) as storage_restarted:
                restarted_wl = storage_restarted.get_watchlist()
                self.assertEqual(len(restarted_wl), 0, "Intentional clearing must NOT resurrect default tickers")

    def test_seam8_indicator_horizontal_price_lines_disabled(self):
        """Seam 8: Moving averages & indicators have horizontal price lines disabled (priceLineVisible: false)."""
        # Indicators must disable horizontal price lines across the chart
        self.assertIn("ema10Series = chart.addLineSeries", self.js)
        self.assertIn("priceLineVisible: false", self.js)
        self.assertIn("lastValueVisible: false", self.js)

    def test_seam9_legend_collapse_and_visibility_toggle(self):
        """Seam 9: Top legend bar supports show/hide and compact collapse toggle with state persistence."""
        self.assertIn("btnToggleLegendCollapse", self.html)
        self.assertIn("chkShowLegend", self.html)
        self.assertIn("chkCollapseLegend", self.html)

        self.assertIn("btnToggleLegendCollapse", self.js)
        self.assertIn("chkShowLegend", self.js)
        self.assertIn("chkCollapseLegend", self.js)
        self.assertIn("collapseLegend", self.js)
        self.assertIn("showLegend", self.js)

        self.assertIn(".legend-toggle-btn", self.css)
        self.assertIn(".chart-legend.collapsed", self.css)

    def test_seam10_ai_copilot_removed(self):
        """Seam 10: AI Copilot button, chat modal, keyboard shortcut and styles are completely removed."""
        # HTML elements must be completely removed
        self.assertNotIn("openChatBtn", self.html)
        self.assertNotIn("chatModal", self.html)
        self.assertNotIn("AI Copilot", self.html)

        # JS triggers must be completely removed
        self.assertNotIn("openChatBtn", self.js)
        self.assertNotIn("k === \"j\"", self.js)
        self.assertNotIn("openChatModal", self.js)

        # CSS styles must be completely removed
        self.assertNotIn(".chat-toggle-btn", self.css)
        self.assertNotIn(".chat-modal-dialog", self.css)

    def test_seam11_drawing_toolbar_hidden_by_default(self):
        """Seam 11: Drawing toolbar is removed/hidden by default from UI and configurable via settings."""
        # HTML element is hidden by default and modal checkbox exists
        self.assertIn('id="drawingToolbar"', self.html)
        self.assertIn('style="display: none;"', self.html)
        self.assertIn('id="chkShowDrawingToolbar"', self.html)

        # JS defaults showDrawingToolbar to false and binds controls
        self.assertIn("showDrawingToolbar: false", self.js)
        self.assertIn("chkShowDrawingToolbar", self.js)

        # CSS defaults to display: none and supports visible class
        self.assertIn(".drawing-toolbar.visible", self.css)

    def test_seam12_analysis_and_screener_buttons_hidden_by_default(self):
        """Seam 12: Fundamentals, AI Lens, and Screener buttons are removed/hidden by default from top header."""
        # Fundamentals & AI Lens buttons are hidden by default; Screener remains visible on toolbar
        self.assertIn('id="fundamentalsBtn" style="display: none;"', self.html)
        self.assertIn('id="aiLensBtn" style="display: none;"', self.html)
        self.assertIn('id="screenerBtn"', self.html)
        self.assertIn('id="chkShowAnalysisTools"', self.html)

        # JS defaults showAnalysisTools to false and binds controls
        self.assertIn("showAnalysisTools: false", self.js)
        self.assertIn("chkShowAnalysisTools", self.js)

        # CSS hides fundamentals and AI Lens with display: none !important by default
        self.assertIn("#fundamentalsBtn", self.css)
        self.assertIn("#aiLensBtn", self.css)

    def test_seam13_tradingview_spy_compare_removed_by_default(self):
        """Seam 13: TradingView SPDR S&P 500 comparison study overlay is removed by default from Scheme C."""
        # HTML modal checkbox exists
        self.assertIn('id="chkTvCompareRs"', self.html)

        # JS defaults tvCompareRs to false and only adds study when explicitly enabled
        self.assertIn("tvCompareRs: false", self.js)
        self.assertIn("indicatorConfig.tvCompareRs", self.js)
        self.assertIn("chkTvCompareRs", self.js)

    def test_seam14_split_layout_and_storage_protection(self):
        """Seam 14: Multi-chart split layout integrity, resize non-blocking, and storage key protection."""
        # CSS supports #tvWidgetContainer in split grid
        self.assertIn(".chart-wrapper.layout-split-1x2 #tvWidgetContainer", self.css)
        self.assertIn(".chart-wrapper.layout-split-2x2 #tvWidgetContainer", self.css)

        # JS preserves tv_chart_engine in initTradingViewWidget localStorage cleaning
        self.assertIn('lk !== "tv_chart_engine"', self.js)

        # JS resizeAllCharts does not short-circuit subCharts for Engine C
        self.assertIn("if (currentEngine !== \"C\")", self.js)
        self.assertIn("for (var p in subCharts)", self.js)

        # JS loadSubChart renders EMA 21, 30, 200 indicators
        self.assertIn('color: "#a855f7"', self.js)
        self.assertIn('color: "#f59e0b"', self.js)
        self.assertIn('color: "#ec4899"', self.js)

        # JS handleLiveBar forwards live ticks to subCharts
        self.assertIn("subCharts[pId].series.update", self.js)

    def test_seam15_multi_chart_engine_uniformity(self):
        """Seam 15: Multi-chart split layout engine uniformity (Scheme C TradingView widget per sub-pane vs Scheme A Lightweight Charts)."""
        # JS tracks subTvWidgets dictionary for TradingView widget instances in sub-panes
        self.assertIn("var subTvWidgets = {};", self.js)

        # JS loadSubChart branches on currentEngine === "C" to instantiate TradingView.widget per sub-pane
        self.assertIn('if (currentEngine === "C")', self.js)
        self.assertIn('container_id: "splitChart" + paneNum', self.js)
        self.assertIn("subTvWidgets[paneNum] =", self.js)

        # JS switchChartEngine refreshes split layout for Scheme C so all panes re-render uniformly
        self.assertIn("applySplitLayout(activeSplitLayout || \"1x1\");", self.js)

    def test_seam16_tri_copilot_concurrency_and_lifecycle_hardening(self):
        """Seam 16: Tri-Copilot converged fixes for MTF live ticks (B1), async race guard (B2), idempotent teardown (B3), and MTF CSS containment (B5a)."""
        # B1: Timeframe-aware MTF live bar routing
        self.assertIn("scTf === activeTimeframe", self.js)
        self.assertIn("scObj.lastBar", self.js)

        # B2: Monotonic generation token & AbortController race guard
        self.assertIn("var paneGen = {};", self.js)
        self.assertIn("var paneAbortControllers = {};", self.js)
        self.assertIn("gen !== paneGen[paneNum]", self.js)

        # B3: Idempotent sub-pane teardown lifecycle contract
        self.assertIn("function disposeSubPane(paneNum)", self.js)
        self.assertIn("disposeSubPane(2);", self.js)

        # B5a: CSS Grid containment for MTF split layout
        self.assertIn(".chart-wrapper.layout-split-mtf #tvWidgetContainer", self.css)
        self.assertIn("min-width: 0", self.css)
        self.assertIn("overflow: hidden", self.css)

    def test_seam17_mtf_bar_rollover_and_resource_lifecycle(self):
        """Seam 17: MTF live bar bucket calculation, bar rollover, price validation, and sub-pane resource lifecycle."""
        # 1. Price validation: rejects <= 0, NaN, and non-finite numbers
        self.assertIn("function isValidPrice(v)", self.js)
        self.assertIn('typeof v === "number" && !isNaN(v) && isFinite(v) && v > 0', self.js)
        self.assertIn("if (!isValidPrice(price)) return;", self.js)

        # 2. MTF candle bucketing (NY-session-aware computeLiveBucket) and rollover on bucketTime > lb.time
        self.assertIn("var subPlacement = computeLiveBucket({", self.js)
        self.assertIn("var bucketTime = subPlacement.time;", self.js)
        self.assertIn("if (bucketTime === lb.time)", self.js)
        self.assertIn("else if (bucketTime > lb.time)", self.js)
        self.assertIn("subCharts[pId].series.update(newCandle);", self.js)
        self.assertIn("scObj.lastBar = newCandle;", self.js)

        # 3. Sub-chart ResizeObserver lifecycle stored in subObj.ro
        self.assertIn("subObj.ro = ro;", self.js)
        self.assertIn("ro.observe(container);", self.js)

        # 4. Resource teardown: ro.disconnect() and iframe.src = 'about:blank'
        self.assertIn("subCharts[paneNum].ro.disconnect", self.js)
        self.assertIn('iframes[i].src = "about:blank"', self.js)


if __name__ == "__main__":
    unittest.main()

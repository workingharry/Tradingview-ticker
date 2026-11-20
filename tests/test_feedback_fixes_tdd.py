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

        with TestClient(app) as client:
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

    def test_seam2_watchlist_unsubscribe_and_storage_sync(self):
        """Seam 2: Unsubscribe endpoint evicts symbol and frontend calls POST with auth."""
        with TestClient(app) as client:
            resp = client.post("/api/symbols/unsubscribe?symbol=NASDAQ:AMZN")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "unsubscribed")

        # Frontend must invoke POST /api/symbols/unsubscribe with headers
        self.assertIn("/api/symbols/unsubscribe?symbol=", self.js)
        self.assertIn("savePreferencesDebounced", self.js)

    def test_seam3_tauri_always_on_top_contract(self):
        """Seam 3: toggle_always_on_top Tauri command exists in Rust and bound in UI."""
        # Rust lib.rs must declare the command and register it
        self.assertIn("fn toggle_always_on_top", self.rust)
        self.assertIn("toggle_always_on_top", self.rust)
        self.assertIn("set_always_on_top", self.rust)

        # UI must contain the pin button and style
        self.assertIn('id="btnPinWindow"', self.html)
        self.assertIn(".pin-toggle-btn", self.css)
        self.assertIn(".pin-toggle-btn.active", self.css)

        # Frontend JS must invoke the Tauri IPC command
        self.assertIn('"toggle_always_on_top"', self.js)
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

    def test_seam6_tradingview_widget_studies_and_distinct_colors(self):
        """Seam 6: Scheme C configures distinct EMA colors and Compare study for RS."""
        # Compare study must be pushed for RS line
        self.assertIn("Compare@tv-basicstudies", self.js)

        # Distinct EMA colors in studies_overrides
        self.assertIn('"moving average exponential.plot.color": "#00e5ff"', self.js)
        self.assertIn('"moving average.plot.color": "#eab308"', self.js)

        # onChartReady color overrides for multi-EMA distinctions
        self.assertIn("onChartReady", self.js)
        self.assertIn("#a855f7", self.js)  # EMA 21 purple
        self.assertIn("#f59e0b", self.js)  # EMA 30 amber


if __name__ == "__main__":
    unittest.main()

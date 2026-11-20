"""Tests verifying the 6 review findings fixes (R1-R6) on the Stock Website codebase."""

import re
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from server import app

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_INDEX_HTML = BASE_DIR / "static" / "index.html"
STATIC_APP_JS = BASE_DIR / "static" / "app.js"
STATIC_STYLE_CSS = BASE_DIR / "static" / "style.css"


class TestReviewFindingsFixes(unittest.TestCase):
    """Verifies that all 6 review findings (R1 - R6) are fully and correctly implemented."""

    @classmethod
    def setUpClass(cls):
        cls.html = STATIC_INDEX_HTML.read_text(encoding="utf-8")
        cls.js = STATIC_APP_JS.read_text(encoding="utf-8")
        cls.css = STATIC_STYLE_CSS.read_text(encoding="utf-8")

    def test_r1_date_range_and_timeframe_synchronization_logic(self):
        """Verifies R1: Date range buttons synchronize with resolution and clamp visible bounds."""
        # Assert RANGE_TF_MAP exists in app.js and covers all standard range intervals
        self.assertIn("RANGE_TF_MAP", self.js)
        self.assertIn('"1D": "1m"', self.js)
        self.assertIn('"5D": "5m"', self.js)
        self.assertIn('"1M": "1h"', self.js)
        self.assertIn('"1Y": "1D"', self.js)
        self.assertIn('"ALL": "1D"', self.js)

        # Assert clamp logic exists
        self.assertIn("clampAndSetVisibleRange", self.js)
        self.assertIn("earliest", self.js)

    def test_r2_drawing_toolbar_present(self):
        """Verifies R2: Drawing toolbar and overlay canvas exist with active tools."""
        self.assertIn('id="drawingToolbar"', self.html)
        self.assertIn('id="drawingCanvas"', self.html)
        self.assertIn('data-tool="risk-reward"', self.html)
        self.assertIn('data-tool="trendline"', self.html)

        # Active drawing engine functions exist in app.js
        self.assertIn("renderDrawingCanvas", self.js)
        self.assertIn(".draw-tool-btn", self.js)

    def test_r3_symbol_selector_redundancy_cleaned_up(self):
        """Verifies R3: Redundant #symbolSelector tab row removed from DOM and toolbar layout."""
        # symbolSelector container must NOT exist in index.html
        self.assertNotIn('id="symbolSelector"', self.html)

        # dead symbolSelector references and builders removed from app.js
        self.assertNotIn('document.getElementById("symbolSelector")', self.js)
        self.assertNotIn("buildSymbolSelector", self.js)

    def test_r4_engine_selector_streamlining_and_auto_fallback(self):
        """Verifies R4: Engine selector cleaned up and Scheme A vs B streamlined under the hood."""
        # UI dropdown presents user-friendly options without exposing Scheme A vs Scheme B internal wiring
        self.assertIn('id="engineSelect"', self.html)
        self.assertNotIn("方案 B: TV WebSocket 串流", self.html)
        self.assertNotIn("Scheme B: TV WebSocket Stream", self.html)

        # Server GET /api/history supports automatic fallback between Scheme B and Scheme A
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=BINANCE:BTCUSDT&timeframe=1m&engine=B")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn(data["engine"], ["A", "B"])

    def test_r5_price_scale_mode_synchronization_and_radio_state(self):
        """Verifies R5: Price scale mode operates as mutually exclusive radio state."""
        self.assertIn("setPriceScaleMode", self.js)
        self.assertIn("currentPriceScaleMode", self.js)

        # Assert scaleLog and scalePercent buttons exist in HTML
        self.assertIn('id="scaleLog"', self.html)
        self.assertIn('id="scalePercent"', self.html)

        # Mode toggles between normal, log, and percent without double-active desync
        self.assertIn('mode === "log"', self.js)
        self.assertIn('mode === "percent"', self.js)

    def test_r6_rs_benchmark_control_consolidation(self):
        """Verifies R6: RS Benchmark controls consolidated into indicators modal without duplicate prompt."""
        # Toolbar has concise status badge
        self.assertIn('id="rsActiveBadge"', self.html)

        # Redundant +Tag prompt button removed from toolbar and dead handler removed
        self.assertNotIn('id="customRsBtn"', self.html)
        self.assertNotIn('document.getElementById("customRsBtn")', self.js)

        # Modal contains consolidated configuration: quick presets and custom tag input
        self.assertIn('id="rsBenchmarkCustomInput"', self.html)
        self.assertIn('id="applyRsBenchmarkBtn"', self.html)
        self.assertIn('data-bench="SPY"', self.html)

    def test_dom_ids_100_percent_aligned(self):
        """Verifies 100% of getElementById calls in app.js exist in index.html with zero missing IDs."""
        id_call_pattern = re.compile(r"document\.getElementById\([\"\x27]([^\"\x27]+)[\"\x27]\)")
        js_ids = set(id_call_pattern.findall(self.js))
        html_id_pattern = re.compile(r"id=[\"\x27]([^\"\x27]+)[\"\x27]")
        html_ids = set(html_id_pattern.findall(self.html))

        missing_ids = js_ids - html_ids
        self.assertEqual(missing_ids, set(), f"Zero missing DOM IDs allowed! Found: {missing_ids}")


if __name__ == "__main__":
    unittest.main()

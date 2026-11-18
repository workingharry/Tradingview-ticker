"""Unit tests for Pine Script ported indicators (AVWAP, ATR Toolkit),
Left Drawing Toolbar, Risk-Reward calculations, and Multi-Chart Active Focus.
"""

import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path


class TestPineIndicatorsAndDrawing(unittest.TestCase):
    """Verifies Anchored VWAP, ATR Toolkit, drawing HUD metrics, and DOM contracts."""

    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.indicators_js = cls.project_root / "static" / "indicators.js"
        cls.app_js = cls.project_root / "static" / "app.js"
        cls.index_html = cls.project_root / "static" / "index.html"
        cls.style_css = cls.project_root / "static" / "style.css"

    def test_dom_and_ui_contracts(self):
        """Verifies HTML and CSS have drawing toolbar, canvas overlay, and context menu."""
        html = self.index_html.read_text("utf-8")
        self.assertIn("drawingToolbar", html)
        self.assertIn("drawingCanvas", html)
        self.assertIn('data-tool="risk-reward"', html)
        self.assertIn('data-tool="avwap"', html)
        self.assertIn('data-tool="trendline"', html)
        self.assertIn('data-tool="hline"', html)
        self.assertIn('data-tool="ruler"', html)
        self.assertIn("wlContextMenu", html)
        self.assertIn("mtfSyncBtn", html)

        css = self.style_css.read_text("utf-8")
        self.assertIn(".drawing-toolbar", css)
        self.assertIn(".draw-tool-btn", css)
        self.assertIn(".drawing-overlay-canvas", css)
        self.assertIn(".desktop-context-menu", css)
        self.assertIn(".focused-pane", css)
        self.assertIn(".mtf-sync-btn.active", css)

    def test_app_js_indicator_and_pane_contracts(self):
        """Verifies static/app.js implements de-cluttered RS, drawing engine, and MTF sync."""
        app_js = self.app_js.read_text("utf-8")
        self.assertIn("rsOverlayMain: false", app_js)
        self.assertIn("renderDrawingCanvas", app_js)
        self.assertIn("setFocusedPane", app_js)
        self.assertIn("syncMTF", app_js)
        self.assertIn("openWlContextMenu", app_js)
        self.assertIn("unsubscribeSymbol", app_js)

    def test_anchored_vwap_node_execution(self):
        """Verifies calcAnchoredVWAP execution via Node.js for Swing Low, Swing High, Highest Volume, and Bands."""
        script = """
        const ind = require('./static/indicators.js');
        const bars = [];
        for (let i = 0; i < 50; i++) {
          bars.push({
            time: 1700000000 + i * 86400,
            open: 100 + i,
            high: 105 + i,
            low: 95 + i,
            close: 102 + i,
            volume: i === 15 ? 10000 : 1000
          });
        }
        // Force a clear swing low at index 20
        bars[20].low = 50;
        bars[20].close = 55;

        // Force a clear swing high at index 30
        bars[30].high = 200;

        const lowRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Swing Low', anchorLookback: 50, bandMult: 1.0 });
        const highRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Swing High', anchorLookback: 50 });
        const volRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Highest Volume', anchorLookback: 50 });

        console.log(JSON.stringify({
          lowAnchor: lowRes.anchorBar,
          lowLen: lowRes.avwap.length,
          highAnchor: highRes.anchorBar,
          volAnchor: volRes.anchorBar,
          firstAvwap: lowRes.avwap[0],
          lastUpper: lowRes.upper[lowRes.upper.length - 1],
          lastLower: lowRes.lower[lowRes.lower.length - 1]
        }));
        """
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node" or "/usr/local/bin/node"
        proc = subprocess.run(
            [node_bin, "-e", script],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            check=True
        )
        res = json.loads(proc.stdout)
        self.assertEqual(res["lowAnchor"]["index"], 20)
        self.assertEqual(res["highAnchor"]["index"], 30)
        self.assertEqual(res["volAnchor"]["index"], 15)
        self.assertEqual(res["lowLen"], 30)
        self.assertGreater(res["lastUpper"]["value"], res["lastLower"]["value"])

    def test_atr_toolkit_node_execution(self):
        """Verifies calcATRToolkit execution via Node.js for squeeze, breakout, sizing, and Mansfield RS."""
        script = """
        const ind = require('./static/indicators.js');
        const bars = [];
        const benchmark = [];
        for (let i = 0; i < 70; i++) {
          const t = 1700000000 + i * 86400;
          bars.push({
            time: t,
            open: 100 + i * 0.5,
            high: 102 + i * 0.5,
            low: 98 + i * 0.5,
            close: 101 + i * 0.5,
            volume: i === 65 ? 8000 : 1000
          });
          benchmark.push({
            time: t,
            close: 500 + i * 0.2
          });
        }
        // Force a wide breakout bar at index 65
        bars[65].high = 145;
        bars[65].close = 144;

        const res = ind.calcATRToolkit(bars, benchmark, {
          riskDollar: 500,
          stopMult: 2.0,
          squeezeThresh: 30,
          mansfieldLen: 52
        });

        console.log(JSON.stringify({
          hasAtr: res.atr.length > 0,
          posSize: res.positionSizing,
          mansfieldLen: res.mansfieldRS.length,
          hasBreakout: res.breakouts.some(b => b.isBreakout)
        }));
        """
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node" or "/usr/local/bin/node"
        proc = subprocess.run(
            [node_bin, "-e", script],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            check=True
        )
        res = json.loads(proc.stdout)
        self.assertTrue(res["hasAtr"])
        self.assertTrue(res["hasBreakout"])
        self.assertGreater(res["posSize"]["suggestedShares"], 0)
        self.assertEqual(res["posSize"]["riskDollar"], 500)
        self.assertGreater(res["mansfieldLen"], 0)

    def test_risk_reward_math_pure_python_invariants(self):
        """Verifies the Risk-Reward formula and position sizing invariants."""
        entry = 223.37
        target = entry * 1.15
        stop = entry * 0.94
        risk_dollar = 500.0

        target_pct = ((target - entry) / entry) * 100.0
        stop_pct = ((entry - stop) / entry) * 100.0
        rr_ratio = target_pct / stop_pct
        stop_dist = entry - stop
        suggested_shares = math.floor(risk_dollar / stop_dist)

        self.assertAlmostEqual(target_pct, 15.0, places=4)
        self.assertAlmostEqual(stop_pct, 6.0, places=4)
        self.assertAlmostEqual(rr_ratio, 2.50, places=2)
        self.assertEqual(suggested_shares, math.floor(500.0 / (223.37 * 0.06)))
        self.assertGreater(suggested_shares, 30)

    def test_dual_mode_rs_and_settings_persistence(self):
        """Verifies dual-mode RS (#rsPane) and indicator configuration persistence."""
        html = self.index_html.read_text("utf-8")
        self.assertIn("btnModeMansfield", html)
        self.assertIn("btnModeDeepvue", html)
        self.assertIn("rsAtrPctBadge", html)
        self.assertIn("rsSuggestedBadge", html)
        self.assertIn("rsRatingBadge", html)

        app_js = self.app_js.read_text("utf-8")
        self.assertIn("function syncIndicatorCheckboxes", app_js)
        self.assertIn("saveIndicatorConfig();", app_js)
        self.assertIn("tv_indicator_config", app_js)
        self.assertIn("btnModeMansfield.onclick", app_js)
        self.assertIn("btnModeDeepvue.onclick", app_js)
        self.assertIn("calcATRToolkit", app_js)
        self.assertIn("Mansfield RS (52)", app_js)
        self.assertIn("Deepvue Ratio", app_js)

    def test_avwap_dom_ui_and_settings_modal_contracts(self):
        """Verifies index.html and style.css contain complete AVWAP modal with 3 tabs and 11 controls."""
        html = self.index_html.read_text("utf-8")
        # Settings Modal & Tabs
        self.assertIn("avwapSettingsModal", html)
        self.assertIn("tabBtnInputs", html)
        self.assertIn("tabBtnStyle", html)
        self.assertIn("tabBtnVisibility", html)
        self.assertIn("btnParsePineScript", html)
        self.assertIn("avwapPineInputArea", html)

        # 11 Inputs
        self.assertIn("avwapInputAnchor", html)
        self.assertIn("avwapInputLookback", html)
        self.assertIn("avwapInputGapPct", html)
        self.assertIn("avwapInputGapVol", html)
        self.assertIn("avwapInputVolMaLen", html)
        self.assertIn("avwapInputDate", html)
        self.assertIn("avwapInputTime", html)
        self.assertIn("avwapInputSource", html)
        self.assertIn("avwapInputShowBands", html)
        self.assertIn("avwapInputBandMult", html)
        self.assertIn("avwapInputMarkAnchor", html)
        self.assertIn("avwapInputShowTable", html)

        # Bottom buttons & Visual Elements
        self.assertIn("btnAvwapResetDefaults", html)
        self.assertIn("btnAvwapCancel", html)
        self.assertIn("btnAvwapOk", html)
        self.assertIn("avwapInfoTable", html)
        self.assertIn("avwapTblAnchor", html)
        self.assertIn("avwapTblVwap", html)
        self.assertIn("avwapTblDiff", html)
        self.assertIn("avwapTblPos", html)
        self.assertIn("btnAvwapSettings", html)
        self.assertIn("chkAvwap", html)

        # CSS verification
        css = self.style_css.read_text("utf-8")
        self.assertIn(".avwap-settings-dialog", css)
        self.assertIn(".avwap-tab-btn.active", css)
        self.assertIn(".avwap-pine-box", css)
        self.assertIn(".avwap-info-table", css)
        self.assertIn(".legend-indicator.ind-avwap", css)

    def test_avwap_all_six_anchor_modes_node(self):
        """Verifies calcAnchoredVWAP across all 6 modes via Node.js."""
        script = """
        const ind = require('./static/indicators.js');
        const bars = [];
        for (let i = 0; i < 60; i++) {
          const t = 1767312000 + i * 3600; // Starting 2026-01-02 00:00 UTC hourly bars
          bars.push({
            time: t,
            open: 100 + i,
            high: 105 + i,
            low: 95 + i,
            close: 102 + i,
            volume: 1000
          });
        }

        // Set clear landmarks:
        // Swing Low at bar 15
        bars[15].low = 40;
        bars[15].close = 45;

        // Swing High at bar 25
        bars[25].high = 300;

        // Highest Volume at bar 10
        bars[10].volume = 99999;

        // Gap-Up at bar 35: open > high[34], gapPct >= 5%, volume ratio >= 2
        bars[34].high = 135;
        bars[34].close = 134;
        bars[35].open = 145; // gap: ((145-134)/134)*100 = 8.2%
        bars[35].volume = 5000; // prior volume SMA ~ 1000, ratio 5 > 2

        const lowRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Swing Low', anchorLookback: 60 });
        const highRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Swing High', anchorLookback: 60 });
        const volRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Highest Volume', anchorLookback: 60 });
        const gapRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Gap-Up', anchorLookback: 60, gapPctMin: 5.0, gapVolMin: 2.0, volMaLen: 10 });
        const manRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Manual Date', manualAnchor: '2026-01-03 10:00' });
        const sessRes = ind.calcAnchoredVWAP(bars, { anchorMode: 'Session (intraday)' });

        console.log(JSON.stringify({
          lowIndex: lowRes.anchorBar.index,
          highIndex: highRes.anchorBar.index,
          volIndex: volRes.anchorBar.index,
          gapIndex: gapRes.anchorBar.index,
          manIndex: manRes.anchorBar.index,
          sessAvwapLen: sessRes.avwap.length,
          sessHasReset: sessRes.anchorBar.index > 0,
          lowBarsAgo: lowRes.anchorBar.barsAgo
        }));
        """
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node" or "/usr/local/bin/node"
        proc = subprocess.run(
            [node_bin, "-e", script],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            check=True
        )
        res = json.loads(proc.stdout)
        self.assertEqual(res["lowIndex"], 15)
        self.assertEqual(res["highIndex"], 25)
        self.assertEqual(res["volIndex"], 10)
        self.assertEqual(res["gapIndex"], 35)
        self.assertGreater(res["manIndex"], 0)
        self.assertEqual(res["sessAvwapLen"], 60)
        self.assertTrue(res["sessHasReset"])
        self.assertEqual(res["lowBarsAgo"], 60 - 1 - 15)

    def test_avwap_standard_deviation_bands_and_fill(self):
        """Verifies standard deviation channel math: upper = avwap + mult*stdev, lower = avwap - mult*stdev."""
        script = """
        const ind = require('./static/indicators.js');
        const bars = [];
        for (let i = 0; i < 20; i++) {
          bars.push({
            time: 1700000000 + i * 86400,
            open: 100 + i * 2,
            high: 105 + i * 2,
            low: 95 + i * 2,
            close: 102 + i * 2,
            volume: 1000 + i * 100
          });
        }
        const res = ind.calcAnchoredVWAP(bars, {
          anchorMode: 'Swing Low',
          anchorLookback: 20,
          bandMult: 1.5,
          source: 'hlc3'
        });

        const first = res.avwap[0];
        const last = res.avwap[res.avwap.length - 1];
        console.log(JSON.stringify({
          firstVwap: first.value,
          firstUpper: first.upper,
          firstLower: first.lower,
          firstStdev: first.stdev,
          lastVwap: last.value,
          lastUpper: last.upper,
          lastLower: last.lower,
          lastStdev: last.stdev,
          isAbove: last.isAbove
        }));
        """
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node" or "/usr/local/bin/node"
        proc = subprocess.run(
            [node_bin, "-e", script],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            check=True
        )
        res = json.loads(proc.stdout)
        # At anchor bar (single bar), stdev is 0, so upper == avwap == lower
        self.assertEqual(res["firstStdev"], 0)
        self.assertEqual(res["firstUpper"], res["firstVwap"])
        self.assertEqual(res["firstLower"], res["firstVwap"])

        # As variance accumulates, stdev > 0 and upper > avwap > lower
        self.assertGreater(res["lastStdev"], 0)
        self.assertGreater(res["lastUpper"], res["lastVwap"])
        self.assertLess(res["lastLower"], res["lastVwap"])
        self.assertAlmostEqual(res["lastUpper"] - res["lastVwap"], 1.5 * res["lastStdev"], places=2)

    def test_pine_v5_input_parser_node_and_python(self):
        """Verifies parsePineInputs in both Node.js and Python for the 11 AVWAP input declarations."""
        pine_script = """
        //@version=5
        indicator("Anchored VWAP", overlay=true)

        anchorMode = input.string("Swing Low", "Anchor", options=["Swing Low", "Swing High", "Highest Volume", "Gap-Up", "Manual Date", "Session (intraday)"])
        anchorLookback = input.int(252, "Auto-anchor lookback (bars)", minval=5, maxval=1000)
        gapPctMin = input.float(5.0, "Gap-Up: min gap % (open vs prior close)", minval=0.1, step=0.5)
        gapVolMin = input.float(2.0, "Gap-Up: min Volume / prior Volume MA", minval=0.5, step=0.1)
        volMaLen = input.int(50, "Volume MA Length (for Gap-Up)", minval=1)
        manualAnchor = input.time(timestamp("02 Jan 2026 00:00"), "Manual anchor date")
        avSrc = input.source(hlc3, "Source")
        showBands = input.bool(true, "Show std-dev bands")
        bandMult = input.float(1.0, "Band multiplier (x vol-weighted stdev)", minval=0.1, step=0.1)
        showAnchorLbl = input.bool(true, "Mark anchor bar")
        showTable = input.bool(true, "Show info table")
        """

        # 1. Test Node.js parsePineInputs
        script = f"""
        const ind = require('./static/indicators.js');
        const code = `{pine_script}`;
        const parsed = ind.parsePineInputs(code);
        console.log(JSON.stringify(parsed));
        """
        node_bin = shutil.which("node") or "/opt/homebrew/bin/node" or "/usr/local/bin/node"
        proc = subprocess.run(
            [node_bin, "-e", script],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            check=True
        )
        js_res = json.loads(proc.stdout)
        self.assertIn("anchorMode", js_res)
        self.assertEqual(js_res["anchorMode"]["defval"], "Swing Low")
        self.assertEqual(len(js_res["anchorMode"]["options"]), 6)
        self.assertEqual(js_res["anchorLookback"]["defval"], 252)
        self.assertEqual(js_res["anchorLookback"]["minval"], 5)
        self.assertEqual(js_res["anchorLookback"]["maxval"], 1000)
        self.assertEqual(js_res["gapPctMin"]["defval"], 5.0)
        self.assertEqual(js_res["gapVolMin"]["defval"], 2.0)
        self.assertEqual(js_res["volMaLen"]["defval"], 50)
        self.assertEqual(js_res["manualAnchor"]["defval"], "02 Jan 2026 00:00")
        self.assertEqual(js_res["avSrc"]["defval"], "hlc3")
        self.assertEqual(js_res["showBands"]["defval"], True)
        self.assertEqual(js_res["bandMult"]["defval"], 1.0)
        self.assertEqual(js_res["showAnchorLbl"]["defval"], True)
        self.assertEqual(js_res["showTable"]["defval"], True)

        # 2. Test Python parse_pine_inputs
        from engine.pine_engine import parse_pine_inputs
        py_res = parse_pine_inputs(pine_script)
        self.assertIn("anchorMode", py_res)
        self.assertEqual(py_res["anchorMode"]["defval"], "Swing Low")
        self.assertEqual(len(py_res["anchorMode"]["options"]), 6)
        self.assertEqual(py_res["anchorLookback"]["defval"], 252)
        self.assertEqual(py_res["showBands"]["defval"], True)

    def test_pure_python_avwap_engine_six_modes(self):
        """Verifies engine.pine_engine.calc_anchored_vwap pure python calculation."""
        from engine.pine_engine import calc_anchored_vwap

        bars = []
        for i in range(50):
            bars.append({
                "time": 1704153600 + i * 3600,
                "open": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "close": 102.0 + i,
                "volume": 1000.0
            })

        # Set Swing Low at bar 18
        bars[18]["low"] = 42.0
        bars[18]["close"] = 46.0

        # Set Swing High at bar 28
        bars[28]["high"] = 250.0

        low_res = calc_anchored_vwap(bars, {"anchorMode": "Swing Low", "anchorLookback": 50})
        high_res = calc_anchored_vwap(bars, {"anchorMode": "Swing High", "anchorLookback": 50})

        self.assertEqual(low_res["anchorBar"]["index"], 18)
        self.assertEqual(high_res["anchorBar"]["index"], 28)
        self.assertGreater(low_res["latest"]["upper"], low_res["latest"]["lower"])
        self.assertTrue(low_res["latest"]["isAbove"])

    def test_watchlist_reorder_and_storage_persistence(self):
        """Verifies storage.reorder_watchlist() and POST /api/symbols/reorder contract."""
        from engine.storage import Storage
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            db_file = Path(temp_dir) / "test_reorder.sqlite"
            st = Storage(db_file)

            wl = st.get_watchlist()
            self.assertGreater(len(wl), 3)

            # Test reordering first 3 items
            orig_ids = [w["id"] for w in wl]
            new_order = [orig_ids[2], orig_ids[0], orig_ids[1]] + orig_ids[3:]
            success = st.reorder_watchlist(new_order)
            self.assertTrue(success)

            reordered_wl = st.get_watchlist()
            reordered_ids = [w["id"] for w in reordered_wl]
            self.assertEqual(reordered_ids[:3], [orig_ids[2], orig_ids[0], orig_ids[1]])
            st.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_hollow_candlestick_and_scale_auto_dom_contracts(self):
        """Verifies static assets contain Hollow Candlestick toggles, Auto-Scale mode, and DnD hooks."""
        repo_root = Path(__file__).resolve().parent.parent
        index_html = (repo_root / "static" / "index.html").read_text(encoding="utf-8")
        style_css = (repo_root / "static" / "style.css").read_text(encoding="utf-8")
        app_js = (repo_root / "static" / "app.js").read_text(encoding="utf-8")

        # HTML contracts
        self.assertIn('id="candleStyleBtn"', index_html)
        self.assertIn('id="scaleAuto"', index_html)
        self.assertIn('id="screenerSearchWrap"', index_html)
        self.assertIn('id="screenerAutocompleteDropdown"', index_html)

        # CSS contracts
        self.assertIn(".candle-style-btn", style_css)
        self.assertIn(".wl-item.dragging", style_css)
        self.assertIn(".wl-item.drag-over", style_css)
        self.assertIn(".wl-drag-handle", style_css)

        # App.js contracts
        self.assertIn('safeGetStorage("tv_candle_style"', app_js)
        self.assertIn('setAutoScale', app_js)
        self.assertIn('draggedItemIndex', app_js)
        self.assertIn('/api/symbols/reorder', app_js)
        self.assertIn('DEFAULT_WATCHLIST_SEEDS', app_js)

    def test_prch_exchange_migration_and_realtime_presence(self):
        """Verifies PRCH is consistently mapped to NASDAQ:PRCH across watchlist, screener, and migrations."""
        from engine.storage import DEFAULT_WATCHLIST_SEEDS, Storage
        from engine.screener import US_MOMENTUM_UNIVERSE
        import tempfile
        import shutil

        # Storage seeds check
        prch_seeds = [s for s in DEFAULT_WATCHLIST_SEEDS if "PRCH" in s["id"]]
        self.assertEqual(len(prch_seeds), 1)
        self.assertEqual(prch_seeds[0]["id"], "NASDAQ:PRCH")
        self.assertEqual(prch_seeds[0]["exchange"], "NASDAQ")

        # Screener universe check
        prch_screener = [s for s in US_MOMENTUM_UNIVERSE if s["symbol"] == "PRCH"]
        self.assertEqual(len(prch_screener), 1)
        self.assertEqual(prch_screener[0]["id"], "NASDAQ:PRCH")

        # Database migration check
        temp_dir = tempfile.mkdtemp()
        try:
            db_file = Path(temp_dir) / "test_migration.sqlite"
            st1 = Storage(db_file)
            # Manually inject legacy NYSE:PRCH
            with st1._lock:
                st1._conn.execute("INSERT OR REPLACE INTO watchlist (id, name, exchange, pair, type, sort_order) VALUES ('NYSE:PRCH', 'Porch Group Inc.', 'NYSE', 'PRCH', 'stock', 99);")
                st1._conn.commit()
            st1.close()

            # Re-open storage on the same file; __init__ executes idempotent migrations
            st2 = Storage(db_file)
            wl = st2.get_watchlist()
            prch_items = [w for w in wl if "PRCH" in w["id"]]
            self.assertEqual(len(prch_items), 1)
            self.assertEqual(prch_items[0]["id"], "NASDAQ:PRCH")
            self.assertEqual(prch_items[0]["exchange"], "NASDAQ")
            st2.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_screener_global_search_and_custom_warning(self):
        """Verifies search endpoint indexes full universe and custom screener handles empty inputs safely."""
        from fastapi.testclient import TestClient
        from server import app

        with TestClient(app) as client:
            # 1. Search endpoint includes momentum universe (e.g. MRNA, PRCH, CART)
            res = client.get("/api/symbols/search?q=PRCH")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            results = data.get("results", [])
            self.assertTrue(any(r.get("pair") == "PRCH" or "PRCH" in r.get("id", "") for r in results))

            # 2. Screener scan with empty custom list returns warning
            scan_res = client.post("/api/screener/scan", json={"universe": "custom", "custom_symbols": ""})
            self.assertEqual(scan_res.status_code, 200)
            scan_data = scan_res.json()
            self.assertEqual(scan_data["total_scanned"], 0)
            self.assertIn("warning", scan_data)


if __name__ == "__main__":
    unittest.main()


import json
import os
import re
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestStaticAppFixes(unittest.TestCase):
    """Verifies fixes in static/app.js and static/style.css:
    1. Weekly (1W) timeframe bucket alignment to Monday 00:00:00 UTC.
    2. Dynamic string translation for position sizing, squeeze, and empty search results.
    3. Language switch triggers renderPositionSizingBadge.
    4. Top bar narrow viewport responsive rules in static/style.css.
    """

    def setUp(self):
        self.app_js_path = PROJECT_ROOT / "static" / "app.js"
        self.style_css_path = PROJECT_ROOT / "static" / "style.css"
        self.assertTrue(self.app_js_path.exists())
        self.assertTrue(self.style_css_path.exists())

    def test_align_bar_bucket_weekly_and_intraday(self):
        """Verifies alignBarBucket calculates Monday 00:00 UTC for 1W and aligns other timeframes."""
        jsc_path = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.exists(jsc_path):
            self.skipTest("JavaScriptCore jsc binary not found on macOS")

        test_script = f"""
        var window = {{
            addEventListener: function() {{}},
            removeEventListener: function() {{}}
        }};
        var globalThis = window;
        var document = {{
            addEventListener: function() {{}},
            getElementById: function() {{ return null; }},
            querySelector: function() {{ return null; }},
            querySelectorAll: function() {{ return []; }},
            createElement: function() {{ return {{ textContent: "", style: {{}}, appendChild: function() {{}} }}; }}
        }};
        load('{self.app_js_path}');

        var mon0 = Date.UTC(2026, 10, 16, 0, 0, 0) / 1000;
        var wedMid = Date.UTC(2026, 10, 18, 14, 30, 0) / 1000;
        var sunLate = Date.UTC(2026, 10, 22, 23, 59, 59) / 1000;

        var results = {{
            mon1W: alignBarBucket(mon0, "1W"),
            wed1W: alignBarBucket(wedMid, "1W"),
            sun1W: alignBarBucket(sunLate, "1W"),
            wed1D: alignBarBucket(wedMid, "1D"),
            wed15m: alignBarBucket(wedMid, "15m"),
            msCheck: alignBarBucket(wedMid * 1000, "1W")
        }};
        print(JSON.stringify(results));
        """
        res = subprocess.run([jsc_path, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"JSC execution error:\nStdout: {res.stdout}\nStderr: {res.stderr}")
        data = json.loads(res.stdout.strip())

        mon0_expected = 1794787200
        self.assertEqual(data["mon1W"], mon0_expected)
        self.assertEqual(data["wed1W"], mon0_expected)
        self.assertEqual(data["sun1W"], mon0_expected)
        self.assertEqual(data["msCheck"], mon0_expected)

        wed0_expected = 1794960000
        self.assertEqual(data["wed1D"], wed0_expected)

        wed1430_expected = 1795012200
        self.assertEqual(data["wed15m"], wed1430_expected)

    def test_i18n_dynamic_strings_and_search_empty(self):
        """Verifies that all required dynamic keys are defined in i18n and used in DOM rendering."""
        content = self.app_js_path.read_text(encoding="utf-8")

        for key in ("volSqueeze", "posSuggest", "posSuggestEmpty", "noResults"):
            self.assertIn(f"{key}:", content)

        self.assertIn("emptyDiv.textContent = dict.noResults ||", content)
        self.assertIn("renderPositionSizingBadge();", content)

    def test_narrow_viewport_css_anti_crowding(self):
        """Verifies responsive media queries in static/style.css prevent top nav overflow."""
        css_content = self.style_css_path.read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 1200px)", css_content)
        self.assertIn("@media (max-width: 1080px)", css_content)
        self.assertIn("max-width: 180px", css_content)
        self.assertIn("max-width: 140px", css_content)
        self.assertIn("flex-wrap: nowrap", css_content)
        self.assertIn(".engine-tag", css_content)

    def test_calc_rs_new_high_markers(self):
        """Verifies calcRsNewHighMarkers identifies leading RS breakouts, enforces 5-bar cooldown, and suppresses benchmark identity."""
        indicators_js_path = PROJECT_ROOT / "static" / "indicators.js"
        self.assertTrue(indicators_js_path.exists())

        node_script = """
        const Indicators = require('./static/indicators.js');
        const targetBars = [];
        const benchBars = [];
        const baseTime = 1700000000;

        for (let i = 0; i < 60; i++) {
            const t = baseTime + i * 86400;
            benchBars.push({ time: t, close: 400 - i * 2 });
            const high = i === 10 ? 115 : 105;
            targetBars.push({ time: t, open: 100, high: high, low: 95, close: 100 });
        }

        const markers = Indicators.calcRsNewHighMarkers(targetBars, benchBars, { minWarmup: 50, cooldown: 5 });
        const benchSelf = Indicators.calcRsNewHighMarkers(benchBars, benchBars);

        console.log(JSON.stringify({
            count: markers.length,
            firstType: markers.length > 0 ? markers[0].type : null,
            firstColor: markers.length > 0 ? markers[0].color : null,
            selfCount: benchSelf.length
        }));
        """
        node_env = os.environ.copy()
        node_env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{node_env.get('PATH', '')}"
        res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=node_env)
        self.assertEqual(res.returncode, 0, f"Node script error:\nStdout: {res.stdout}\nStderr: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertGreater(data["count"], 0)
        self.assertEqual(data["firstType"], "rs_before_price")
        self.assertEqual(data["firstColor"], "#2962ff")
        self.assertEqual(data["selfCount"], 0)

    def test_unused_step_variable_removed(self):
        """Verifies static/app.js no longer contains the redundant var step variable."""
        content = self.app_js_path.read_text(encoding="utf-8")
        self.assertNotIn("var step = TF_SECONDS[scTf] || 60;", content)

    def test_watchlist_groups_and_rs_nh_keys(self):
        """Verifies tv_watchlist_groups is protected from cleanup and i18n contains all group & RS NH keys."""
        content = self.app_js_path.read_text(encoding="utf-8")
        html_content = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('lk !== "tv_watchlist_groups"', content)
        for key in ("rsNewHighBadge", "rsNewHighTooltip", "grpAll", "grpLeaders", "grpVcp", "grpPullback", "grpEarnings", "ctxGroupHeader"):
            self.assertIn(f"{key}:", content)

        self.assertIn("wl-group-bar", html_content)
        self.assertIn("rsNewHighBadge", html_content)
        self.assertIn("data-assign-group", html_content)

    def test_rs_disabled_suppresses_markers_and_immediate_restore(self):
        """Part A1: Verifies RS disable suppresses blue dots and re-enable immediately restores."""
        content = self.app_js_path.read_text(encoding="utf-8")

        # 1. Early return with marker clearing when !indicatorConfig.rsLine
        self.assertIn("if (!indicatorConfig.rsLine) {", content)
        self.assertIn("rsLinePaneSeries.setMarkers([]);", content)
        self.assertIn("candleSeries.setMarkers([]);", content)
        self.assertIn('nhBadge.style.display = "none";', content)

        # 2. rsToggleBtn restores immediately on enable
        self.assertIn("if (indicatorConfig.rsLine && currentRawBars && currentRawBars.length > 0) {", content)
        self.assertIn("updateRsPane(currentRawBars, benchmarkRawBars);", content)

    def test_avwap_and_heatmap_modals_intercept_watchlist_keys(self):
        """Part A2: Verifies AVWAP and Heatmap modals intercept watchlist flipping keys."""
        content = self.app_js_path.read_text(encoding="utf-8")

        self.assertIn('var avwapModalEl = document.getElementById("avwapSettingsModal");', content)
        self.assertIn('var heatmapModalEl = document.getElementById("heatmapModal");', content)
        self.assertIn('avwapModalEl && avwapModalEl.style.display !== "none" && avwapModalEl.style.display !== ""', content)
        self.assertIn('heatmapModalEl && heatmapModalEl.style.display !== "none" && heatmapModalEl.style.display !== ""', content)

    def test_stock_theme_heatmap_aggregation_backend(self):
        """Part B1: Verifies build_theme_heatmap pure aggregation logic in engine/screener.py."""
        from engine.screener import build_theme_heatmap, STOCK_THEME_DEFINITIONS

        mock_candidates = [
            {
                "symbol": "NVDA",
                "id": "NASDAQ:NVDA",
                "name": "NVIDIA",
                "close": 140.0,
                "change_pct": 4.5,
                "rs_rating": 98,
                "volume_ratio_50": 1.6,
                "stage2": True,
                "sector": "Semiconductors"
            },
            {
                "symbol": "AMD",
                "id": "NASDAQ:AMD",
                "name": "Advanced Micro Devices",
                "close": 120.0,
                "change_pct": 2.5,
                "rs_rating": 85,
                "volume_ratio_50": 1.2,
                "stage2": True,
                "sector": "Semiconductors"
            },
            {
                "symbol": "PLTR",
                "id": "NYSE:PLTR",
                "name": "Palantir Technologies",
                "close": 65.0,
                "change_pct": 5.0,
                "rs_rating": 99,
                "volume_ratio_50": 2.1,
                "stage2": True,
                "sector": "Technology"
            },
            {
                "symbol": "QQQ",
                "id": "NASDAQ:QQQ",
                "name": "Invesco QQQ Trust",
                "close": 500.0,
                "change_pct": 0.8,
                "rs_rating": 70,
                "volume_ratio_50": 1.0,
                "stage2": False,
                "sector": "ETF"
            }
        ]

        themes = build_theme_heatmap(mock_candidates, benchmark_change_pct=0.5)
        self.assertIsInstance(themes, list)
        self.assertGreater(len(themes), 0)

        # Themes must be sorted descending by avg_change_pct
        for i in range(len(themes) - 1):
            self.assertGreaterEqual(themes[i]["avg_change_pct"], themes[i + 1]["avg_change_pct"])

        # Check theme properties
        first = themes[0]
        self.assertIn("id", first)
        self.assertIn("name_en", first)
        self.assertIn("name_zh", first)
        self.assertIn("avg_change_pct", first)
        self.assertIn("avg_rs_rating", first)
        self.assertIn("advancing", first)
        self.assertIn("total", first)
        self.assertIn("items", first)

        # Empty array safety test
        empty_res = build_theme_heatmap([])
        self.assertEqual(empty_res, [])

    def test_heatmap_frontend_dom_and_integration(self):
        """Part B2: Verifies HTML, CSS, and JS integration for Stock Theme Heatmap."""
        html_content = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css_content = self.style_css_path.read_text(encoding="utf-8")
        js_content = self.app_js_path.read_text(encoding="utf-8")

        # HTML DOM verification
        self.assertIn('id="heatmapBtn"', html_content)
        self.assertIn('id="heatmapModal"', html_content)
        self.assertIn('id="heatmapGrid"', html_content)
        self.assertIn('id="heatmapBenchBadge"', html_content)
        self.assertIn('data-universe="themes"', html_content)
        self.assertIn('data-universe="watchlist"', html_content)
        self.assertIn('data-metric="change"', html_content)
        self.assertIn('data-metric="rs"', html_content)
        self.assertIn('data-sort="change"', html_content)
        self.assertIn('data-sort="rs"', html_content)

        # CSS verification
        self.assertIn(".heatmap-dialog", css_content)
        self.assertIn(".heatmap-grid", css_content)
        self.assertIn(".heatmap-card", css_content)
        self.assertIn(".heatmap-item-box", css_content)
        self.assertIn(':root[data-theme="light"] .heatmap-dialog', css_content)

        # JS logic & i18n verification
        for key in ("heatmapBtn", "heatmapModalTitle", "hmUnivThemes", "hmUnivWatchlist", "hmMetricChange", "hmMetricRs", "hmSortChange", "hmSortRs", "heatmapLoading", "heatmapEmpty", "heatmapFooterTip"):
            self.assertIn(f"{key}:", js_content)

        self.assertIn("function openHeatmapModal(", js_content)
        self.assertIn("function closeHeatmapModalHandler(", js_content)
        self.assertIn("function toggleHeatmapModal(", js_content)
        self.assertIn("function fetchHeatmapData(", js_content)
        self.assertIn("function renderHeatmap(", js_content)
        self.assertIn("function getHeatmapColor(", js_content)
        self.assertIn('k === "m"', js_content)
        self.assertIn("closeHeatmapModalHandler();", js_content)

    def test_evaluate_canslim_stage_and_heatmap_change_pct(self):
        """P0 Test-First: Verifies evaluate_canslim_stage and fetch_heatmap_universe compute non-zero change_pct."""
        import asyncio
        from engine.screener import evaluate_canslim_stage, fetch_heatmap_universe

        # 1. Direct evaluate_canslim_stage with 25 bars (100.0 -> 105.0 => +5.0%)
        bars = [{"open": 100.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000000} for _ in range(24)]
        bars.append({"open": 101.0, "high": 107.0, "low": 100.0, "close": 105.0, "volume": 1500000})

        stage_res = evaluate_canslim_stage(bars)
        self.assertIn("change_pct", stage_res)
        self.assertEqual(stage_res["change_pct"], 5.0)

        # Minimal 2-bar check
        two_bars = [{"close": 50.0}, {"close": 55.0}]
        two_res = evaluate_canslim_stage(two_bars)
        self.assertEqual(two_res.get("change_pct"), 10.0)

        # 2. Async fetch_heatmap_universe check
        universe_item = [{"symbol": "NVDA", "id": "NASDAQ:NVDA", "name": "NVIDIA", "sector": "Semiconductors"}]
        async def mock_fetch(sym, timeframe="1D", limit=300):
            return bars

        items = asyncio.run(fetch_heatmap_universe(universe_item, storage_obj=None, benchmark_bars=bars, fetch_func=mock_fetch))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["change_pct"], 5.0)
        self.assertEqual(items[0]["close"], 105.0)

    def test_heatmap_light_mode_rs_text_contrast(self):
        """UI Contrast Test-First: Verifies app.js avoids washed-out #d1d4dc for mid RS and supports light/dark themes."""
        content = self.app_js_path.read_text(encoding="utf-8")
        self.assertNotIn('avgEl.style.color = rVal >= 80 ? "#38bdf8" : (rVal >= 60 ? "#d1d4dc" : "#f59e0b");', content)
        self.assertIn("var(--text-main)", content)

    def test_heatmap_api_endpoint(self):
        """Part B3: Verifies /api/heatmap FastAPI endpoint response structure, change_pct and advancing counts."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from server import app
        from api.screener import _HEATMAP_CACHE

        _HEATMAP_CACHE.clear()

        # Mock fetch_universal_history so that SPY and symbols have non-zero change_pct
        mock_spy_bars = [{"close": 500.0, "high": 505.0, "low": 498.0, "volume": 1000000} for _ in range(25)]
        mock_spy_bars.append({"close": 505.0, "high": 506.0, "low": 500.0, "volume": 1200000})

        mock_sym_bars = [{"close": 100.0, "high": 105.0, "low": 98.0, "volume": 500000} for _ in range(25)]
        mock_sym_bars.append({"close": 104.0, "high": 106.0, "low": 100.0, "volume": 800000})

        async def fake_fetch(symbol, timeframe="1D", limit=300):
            if "SPY" in symbol:
                return mock_spy_bars
            return mock_sym_bars

        client = TestClient(app)
        fake_storage = unittest.mock.MagicMock()
        fake_storage.get_bars.return_value = []
        fake_storage.get_watchlist.return_value = []

        with patch("api.deps.get_storage", return_value=fake_storage), \
             patch("api.history.fetch_universal_history", side_effect=fake_fetch):
            res = client.get("/api/heatmap?universe=themes&refresh=true")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("status"), "ok")
            self.assertEqual(data.get("universe"), "themes")
            self.assertIn("benchmark", data)
            self.assertEqual(data["benchmark"]["symbol"], "SPY")
            self.assertEqual(data["benchmark"]["change_pct"], 1.0)

            themes = data.get("themes", [])
            self.assertGreater(len(themes), 0)
            first_theme = themes[0]
            self.assertGreater(first_theme["advancing"], 0)
            self.assertGreater(first_theme["avg_change_pct"], 0.0)
            self.assertEqual(first_theme["items"][0]["change_pct"], 4.0)

    def test_watchlist_pin_and_ordering_logic(self):
        """Step 4 Part 1: Verifies watchlist pin state, storage whitelist, togglePinSymbol, and ordered symbols."""
        js_content = self.app_js_path.read_text(encoding="utf-8")
        html_content = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

        # 1. tv_pinned_symbols protected in initTradingViewWidget and preference sync
        self.assertIn('lk !== "tv_pinned_symbols"', js_content)
        self.assertIn("pinnedSymbols", js_content)
        self.assertIn("tv_pinned_symbols", js_content)

        # 2. togglePinSymbol and getOrderedWatchlistSymbols functions exist
        self.assertIn("function togglePinSymbol(", js_content)
        self.assertIn("function getOrderedWatchlistSymbols(", js_content)
        self.assertIn("getActiveWatchlistSymbols()", js_content)

        # 3. Context menu #ctxPinWl and button .wl-pin-btn
        self.assertIn('id="ctxPinWl"', html_content)
        self.assertIn("wl-pin-btn", js_content)
        self.assertIn("wl-pin-divider", js_content)

    def test_watchlist_drag_drop_indicators_and_group_bar_wrap(self):
        """Step 4 Part 1.2 & 1.3: Verifies drag-over-top/bottom insertion styles and group bar wrap."""
        css_content = self.style_css_path.read_text(encoding="utf-8")
        js_content = self.app_js_path.read_text(encoding="utf-8")

        # Insertion line indicators
        self.assertIn(".drag-over-top", css_content)
        self.assertIn(".drag-over-bottom", css_content)
        self.assertIn("drag-over-top", js_content)
        self.assertIn("drag-over-bottom", js_content)

        # Group bar anti-clipping wrap
        self.assertIn(".wl-group-bar", css_content)
        self.assertIn("flex-wrap: wrap", css_content)

    def test_split_pane_synchronized_rs_sub_panes(self):
        """Step 4 Part 2: Verifies multi-pane split layout has synchronized RS sub-panes and cleanup."""
        html_content = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css_content = self.style_css_path.read_text(encoding="utf-8")
        js_content = self.app_js_path.read_text(encoding="utf-8")

        # Split RS DOM elements in splitPane2..4
        for p in (2, 3, 4):
            self.assertIn(f'id="splitRsPane{p}"', html_content)
            self.assertIn(f'id="splitRsChart{p}"', html_content)
            self.assertIn(f'id="splitRsRating{p}"', html_content)
            self.assertIn(f'id="splitRsTitle{p}"', html_content)

        # CSS classes
        self.assertIn(".split-rs-pane", css_content)
        self.assertIn(".split-rs-header", css_content)
        self.assertIn(".split-rs-chart", css_content)

        # JS RS renderer and cleanup logic
        self.assertIn("function renderSubPaneRs(", js_content)
        self.assertIn("rsChart.remove()", js_content)

    def test_deepvue_hud_in_price_pane_and_compact_rs_header(self):
        """Step 4 Part 3: Verifies #deepvueHud is inside .price-pane and compact responsive RS header in split layouts."""
        html_content = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css_content = self.style_css_path.read_text(encoding="utf-8")

        # #deepvueHud must be inside price-pane
        price_pane_pos = html_content.find('class="price-pane"')
        hud_pos = html_content.find('id="deepvueHud"')
        chart_pos = html_content.find('id="chart"')
        self.assertGreater(price_pane_pos, -1)
        self.assertGreater(hud_pos, price_pane_pos)
        self.assertLess(hud_pos, chart_pos)

        # Compact split layout styles for RS header
        self.assertIn(".layout-split-1x2 #rsPane", css_content)
        self.assertIn("#rsSuggestedBadge", css_content)

    def test_step4_split_rs_bugfixes(self):
        """Part A: Verifies renderSubPaneRs uses array-based calcRsNewHighMarkers and loadBenchmarkHistory cascades."""
        js_content = self.app_js_path.read_text(encoding="utf-8")

        # 1. renderSubPaneRs must NOT look for nhRes.hasRecentBreakout or nhRes.markers
        self.assertNotIn("nhRes.hasRecentBreakout", js_content)
        self.assertNotIn("nhRes.markers", js_content)

        # 2. Must use last5Time and rsMarkers.some logic for split RS NH badge
        self.assertIn("var last5Time =", js_content)
        self.assertIn("rsMarkers.some", js_content)

        # 3. subObj.series and subObj.rsLineSeries markers must be cleared on rsLine disable
        self.assertIn("subObj.series.setMarkers", js_content)

        # 4. loadBenchmarkHistory must cascade to subCharts
        bench_func_idx = js_content.find("function loadBenchmarkHistory(")
        self.assertGreater(bench_func_idx, -1)
        bench_body = js_content[bench_func_idx:bench_func_idx + 1500]
        self.assertIn("subCharts", bench_body)
        self.assertIn("renderSubPaneRs", bench_body)

    def test_step5_all_us_screener_frontend_contracts(self):
        """Part C: Verifies #screenerBtn visible by default, all_us default universe, custom presets whitelist and table headers."""
        html_content = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js_content = self.app_js_path.read_text(encoding="utf-8")

        # 1. #screenerBtn visible by default (no style="display: none;")
        sc_btn_idx = html_content.find('id="screenerBtn"')
        self.assertGreater(sc_btn_idx, -1)
        sc_btn_tag = html_content[sc_btn_idx - 30:sc_btn_idx + 80]
        self.assertNotIn('display: none', sc_btn_tag)

        # 2. screenerUniverseSelect has all_us as default selected option
        self.assertIn('<option value="all_us" selected', html_content)

        # 3. Custom presets storage whitelist in initTradingViewWidget
        self.assertIn('lk !== "tv_custom_screener_presets"', js_content)

        # 4. Batch add button
        self.assertIn('id="screenerBatchAddBtn"', html_content)

    def test_run_stock_screen_race_condition_and_abort_guards(self):
        """Verifies runStockScreen protects against race conditions, rapid clicks, and request hangs via AbortController and sequence counter."""
        js_content = self.app_js_path.read_text(encoding="utf-8")

        self.assertIn("var _scanSeq = 0", js_content)
        self.assertIn("var _scanAbort = null", js_content)
        self.assertIn("new AbortController()", js_content)
        self.assertIn("signal: ctrl.signal", js_content)
        self.assertIn("seq !== _scanSeq", js_content)
        self.assertIn("AbortError", js_content)

    def test_desktop_app_perf_and_drag_fixes(self):
        """Verifies #screenerBtn CSS display, Tauri dragDropEnabled: false, and drawing/watchlist perf optimizations."""
        css_content = self.style_css_path.read_text(encoding="utf-8")
        js_content = self.app_js_path.read_text(encoding="utf-8")
        tauri_conf_path = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
        tauri_conf = json.loads(tauri_conf_path.read_text(encoding="utf-8"))

        # 1. #screenerBtn is not hidden by CSS display: none !important
        self.assertNotIn("#screenerBtn", css_content[css_content.find("/* Hide Analysis & Screener Buttons"):css_content.find("/* Hide Analysis & Screener Buttons") + 300])

        # 2. tauri.conf.json has dragDropEnabled: false on main window
        windows = tauri_conf.get("app", {}).get("windows", [])
        self.assertTrue(len(windows) > 0 and windows[0].get("dragDropEnabled") is False)

        # 3. static/app.js has scheduleDrawingRender and pointer events in Watchlist
        self.assertIn("function scheduleDrawingRender(", js_content)
        self.assertIn("pointerdown", js_content)
        self.assertIn("pointermove", js_content)
        self.assertIn("pointerup", js_content)

        # 4. subscribeCrosshairMove does NOT call renderDrawingCanvas()
        crosshair_idx = js_content.find("chart.subscribeCrosshairMove(")
        self.assertGreater(crosshair_idx, -1)
        crosshair_block = js_content[crosshair_idx:crosshair_idx + 250]
        self.assertNotIn("renderDrawingCanvas()", crosshair_block)


if __name__ == "__main__":
    unittest.main()

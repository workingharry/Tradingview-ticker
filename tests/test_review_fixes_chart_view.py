import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server
from fastapi.testclient import TestClient


def _mk_bars(n, t0=1795448820, step=60):
    return [
        {
            "time": t0 + i * step,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100.0,
        }
        for i in range(n)
    ]


class _FakeAggregator:
    def __init__(self, bars):
        self.bars = list(bars)
        self.seeded = []

    def get_history(self, symbol):
        return list(self.bars)

    def seed_history(self, symbol, bars):
        self.seeded.append(len(bars))
        merged = {b["time"]: b for b in bars}
        for b in self.bars:
            merged[b["time"]] = b
        self.bars = [merged[t] for t in sorted(merged)]


class TestReviewFixesChartView(unittest.TestCase):
    """Verifies all 7 review fix requirements (F1 through F6 plus Cache 304)."""

    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
        cls.ind_js_path = PROJECT_ROOT / "static" / "indicators.js"
        if not cls.app_js_path.exists():
            raise FileNotFoundError(f"static/app.js not found at {cls.app_js_path}")

        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )

    def setUp(self):
        if not self.node_bin:
            self.skipTest("Node.js binary not found; skipping JS evaluation")

    def _execute_node_eval(self, eval_js_snippet: str, include_indicators: bool = False):
        """Loads static/app.js (and optionally static/indicators.js) in Node.js VM sandbox."""
        ind_load = ""
        if include_indicators and self.ind_js_path.exists():
            ind_load = f"const indCode = fs.readFileSync({json.dumps(str(self.ind_js_path))}, 'utf8'); vm.runInContext(indCode, sandbox);"

        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            fetch: () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ bars: [] }}) }}),
            window: {{}},
            document: {{
                getElementById: (id) => {{
                    if (id === 'scaleAuto') {{
                        return {{
                            classList: {{
                                toggle: (cls, force) => {{
                                    sandbox._scaleAutoActive = Boolean(force);
                                }}
                            }}
                        }};
                    }}
                    return null;
                }},
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{
                _store: {{}},
                getItem: function(k) {{ return this._store[k] !== undefined ? this._store[k] : null; }},
                setItem: function(k, v) {{ this._store[k] = String(v); }},
                removeItem: function(k) {{ delete this._store[k]; }}
            }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);
        {ind_load}
        vm.runInContext(appCode, sandbox);

        const result = vm.runInContext({json.dumps(eval_js_snippet)}, sandbox);
        console.log(JSON.stringify(result));
        process.exit(0);
        """
        env = os.environ.copy()
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
        proc = subprocess.run(
            [self.node_bin, "-e", harness],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Node VM execution failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )
        return json.loads(proc.stdout.strip().splitlines()[0])

    def test_1_f1_main_time_axis_and_no_raw_truncation(self):
        """F1: getMainTimeAxis() mounted, clampAndSetVisibleRange and resetChartView use main time axis, and no shift() truncation."""
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            app_code = f.read()

        self.assertNotIn("currentRawBars.length > 500", app_code)

        js = """
        (function() {
            var bars305 = [];
            for (var i = 0; i < 305; i++) {
                bars305.push({ time: 1000 + i * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            }
            candleSeries = {
                data: function() { return bars305; }
            };
            currentRawBars = bars305.slice(5); // length 300
            
            var axis = getMainTimeAxis();
            var axisLength = axis.length;

            var setLogicalCalls = [];
            chart = {
                timeScale: function() {
                    return {
                        setVisibleLogicalRange: function(r) { setLogicalCalls.push(r); },
                        fitContent: function() {}
                    };
                }
            };

            clampAndSetVisibleRange("ALL");
            var allRange = setLogicalCalls[setLogicalCalls.length - 1];

            clampAndSetVisibleRange("1D");
            var d1Range = setLogicalCalls[setLogicalCalls.length - 1];

            var resetRange = resetChartView(chart, getMainTimeAxis().length);

            return {
                mountedWindow: typeof window.getMainTimeAxis === "function",
                mountedGlobalThis: typeof globalThis.getMainTimeAxis === "function",
                axisLength: axisLength,
                allRange: allRange,
                d1Range: d1Range,
                resetRange: resetRange
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertTrue(res["mountedWindow"])
        self.assertTrue(res["mountedGlobalThis"])
        self.assertEqual(res["axisLength"], 305)
        # ALL range: from 0, to 305 - 1 + 10 = 314
        self.assertEqual(res["allRange"], {"from": 0, "to": 314})
        self.assertEqual(res["d1Range"], {"from": 0, "to": 314})
        # resetChartView with 305 bars: from = 305 - 150 = 155, to = 305 - 1 + 10 = 314
        self.assertEqual(res["resetRange"], {"from": 155, "to": 314})

    def test_2_f2_compute_preserved_logical_range_and_backfill(self):
        """F2: computePreservedLogicalRange truth table (6 rows) and renderChartData 200-bar backfill restoration."""
        js = """
        (function() {
            var fn = computePreservedLogicalRange;
            var p300 = [];
            for (var i = 0; i < 300; i++) p300.push(1000 + 60 * i);
            var n500 = [];
            for (var j = 0; j < 500; j++) n500.push((1000 - 60 * 200) + 60 * j);
            var n301 = [];
            for (var k = 0; k < 301; k++) n301.push(1000 + 60 * k);

            var r1 = fn(p300, p300, { from: 100.5, to: 180.5 });
            var r2 = fn(p300, n500, { from: 100, to: 160 });
            var r3 = fn(p300, n301, { from: 150, to: 309 });
            var r4 = fn(p300, n301, { from: 100, to: 160 });
            var r5 = fn([], n301, { from: 100, to: 160 });
            var r6 = fn(p300, n301, null);

            // Simulation of renderChartData with 200 bars backfilled
            var curRange = null;
            var seriesBars = [];
            candleSeries = {
                setData: function(d) { seriesBars = d; },
                data: function() { return seriesBars; }
            };
            volumeSeries = { setData: function() {} };
            chart = {
                timeScale: function() {
                    return {
                        getVisibleLogicalRange: function() { return curRange; },
                        setVisibleLogicalRange: function(r) { curRange = r; },
                        fitContent: function() {}
                    };
                }
            };
            activeSymbol = "NASDAQ:NVDA";
            activeTimeframe = "1D";
            currentEngine = "A";
            lastChartViewKey = null;

            var bars300 = [];
            for (var b = 0; b < 300; b++) bars300.push({ time: 1000 + 60 * b, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartData(bars300);
            curRange = { from: 100, to: 160 };

            var bars500 = [];
            for (var b2 = 0; b2 < 500; b2++) bars500.push({ time: (1000 - 60 * 200) + 60 * b2, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartData(bars500);
            var restoredRange = curRange;

            return {
                mountedWindow: typeof window.computePreservedLogicalRange === "function",
                mountedGlobalThis: typeof globalThis.computePreservedLogicalRange === "function",
                r1: r1,
                r2: r2,
                r3: r3,
                r4: r4,
                r5: r5,
                r6: r6,
                restoredRange: restoredRange
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertTrue(res["mountedWindow"])
        self.assertTrue(res["mountedGlobalThis"])
        self.assertEqual(res["r1"], {"from": 100.5, "to": 180.5})
        self.assertEqual(res["r2"], {"from": 300, "to": 360})
        self.assertEqual(res["r3"], {"from": 151, "to": 310})
        self.assertEqual(res["r4"], {"from": 100, "to": 160})
        self.assertIsNone(res["r5"])
        self.assertIsNone(res["r6"])
        self.assertEqual(res["restoredRange"], {"from": 300, "to": 360})

    def test_3_f3_viewkey_engine_state_and_keep_view(self):
        """F3: viewKey default includes currentEngine, options.keepView preserves view and doesn't update lastChartViewKey."""
        js = """
        (function() {
            var curRange = null;
            var autoCalls = [];
            var seriesBars = [];
            candleSeries = {
                setData: function(d) { seriesBars = d; },
                data: function() { return seriesBars; }
            };
            volumeSeries = { setData: function() {} };
            chart = {
                timeScale: function() {
                    return {
                        getVisibleLogicalRange: function() { return curRange; },
                        setVisibleLogicalRange: function(r) { curRange = r; },
                        fitContent: function() {}
                    };
                },
                priceScale: function() {
                    return {
                        applyOptions: function(o) {
                            if (o && typeof o.autoScale === "boolean") autoCalls.push(o.autoScale);
                        }
                    };
                }
            };

            var bars300 = [];
            for (var i = 0; i < 300; i++) bars300.push({ time: 1000 + i * 86400, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });

            // 1. Initial render with engine A
            activeSymbol = "AAPL";
            activeTimeframe = "1D";
            currentEngine = "A";
            lastChartViewKey = null;
            renderChartData(bars300);
            var keyA = lastChartViewKey;

            // User moves viewport
            curRange = { from: 50, to: 100 };
            setAutoScale(false);
            autoCalls = [];

            // 2. Switch engine from A to B: viewKey mismatch triggers resetChartView & setAutoScale(true)
            currentEngine = "B";
            renderChartData(bars300);
            var keyB = lastChartViewKey;
            var rangeAfterEngineSwitch = curRange;
            var autoAfterEngineSwitch = autoCalls.slice();

            // 3. KeepView call: style change or RS benchmark change
            curRange = { from: 70, to: 120 };
            autoCalls = [];
            activeTimeframe = "1h"; // simulate timeframe state drift
            var bars200 = bars300.slice(100);
            renderChartData(bars200, { keepView: true });
            var keyAfterKeepView = lastChartViewKey;
            var autoAfterKeepView = autoCalls.slice();
            var rangeAfterKeepView = curRange;

            // 4. Subsequent render with viewKey AAPL|1h|B triggers clean view reset
            autoCalls = [];
            var bars500 = [];
            for (var j = 0; j < 500; j++) bars500.push({ time: 1000 + j * 3600, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartData(bars500, { viewKey: "AAPL|1h|B" });
            var rangeAfterTfReset = curRange;
            var autoAfterTfReset = autoCalls.slice();

            return {
                keyA: keyA,
                keyB: keyB,
                rangeAfterEngineSwitch: rangeAfterEngineSwitch,
                autoAfterEngineSwitch: autoAfterEngineSwitch,
                keyAfterKeepView: keyAfterKeepView,
                autoAfterKeepView: autoAfterKeepView,
                rangeAfterKeepView: rangeAfterKeepView,
                rangeAfterTfReset: rangeAfterTfReset,
                autoAfterTfReset: autoAfterTfReset
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertEqual(res["keyA"], "AAPL|1D|A")
        self.assertEqual(res["keyB"], "AAPL|1D|B")
        # 300 bars reset -> from: 300 - 150 = 150, to: 300 - 1 + 10 = 309
        self.assertEqual(res["rangeAfterEngineSwitch"], {"from": 150, "to": 309})
        self.assertEqual(res["autoAfterEngineSwitch"], [True])
        # keepView doesn't update lastChartViewKey and doesn't call autoScale
        self.assertEqual(res["keyAfterKeepView"], "AAPL|1D|B")
        self.assertEqual(res["autoAfterKeepView"], [])
        # Explicit range assertion for keepView:
        # Mathematical derivation: bars300 has indices 0..299 (time = 1000 + i * 86400).
        # bars200 = bars300.slice(100) has indices 0..199 (time = 1000 + (100 + k) * 86400).
        # Prior range was {from: 70, to: 120}. prevLast = 299, and 120 < 299 - 0.5, so anchorIdx = 120
        # with anchorTime = prevTimes[120] = 1000 + 120 * 86400. Binary search in nextTimes matches k = 20.
        # shift = 20 - 120 = -100, giving preserved range {from: 70 - 100, to: 120 - 100} = {from: -30, to: 20}.
        self.assertEqual(res["rangeAfterKeepView"], {"from": -30, "to": 20})
        # TF reset with 500 bars -> from: 500 - 150 = 350, to: 500 - 1 + 10 = 509
        self.assertEqual(res["rangeAfterTfReset"], {"from": 350, "to": 509})
        self.assertEqual(res["autoAfterTfReset"], [True])

        # Verify static call sites of { keepView: true }
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            app_code = f.read()
        keep_view_matches = re.findall(r"renderChartData\([^,]+,\s*\{\s*keepView:\s*true\s*\}\)", app_code)
        self.assertEqual(len(keep_view_matches), 3)

    def test_4_f4_rs_pane_blank_alignment_when_insufficient_bars(self):
        """F4: RS pane pre-pads empty series padToTimeAxis([], rsAxis) to align with main chart time axis."""
        js = """
        (function() {
            var rsLineData = null;
            var rsEmaData = null;
            rsLinePaneSeries = { setData: function(d) { rsLineData = d; } };
            rsEmaPaneSeries = { setData: function(d) { rsEmaData = d; } };

            // 1. Initial 300 daily bars + benchmark
            var dailyBars = [];
            var bmBars = [];
            for (var i = 0; i < 300; i++) {
                var t = 1000 + i * 86400;
                dailyBars.push({ time: t, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
                bmBars.push({ time: t, open: 100, high: 105, low: 95, close: 102, volume: 1000 });
            }
            candleSeries = { data: function() { return dailyBars; } };
            updateRsPane(dailyBars, bmBars);

            // 2. Switch to 40 1m bars with empty benchmark
            var m1Bars = [];
            for (var j = 0; j < 40; j++) {
                m1Bars.push({ time: 2000000 + j * 60, open: 15, high: 16, low: 14, close: 15.5, volume: 50 });
            }
            candleSeries = { data: function() { return m1Bars; } };
            updateRsPane(m1Bars, []);

            return {
                lineCount: rsLineData ? rsLineData.length : -1,
                lineFirst: rsLineData && rsLineData[0] ? rsLineData[0] : null,
                lineLast: rsLineData && rsLineData[rsLineData.length - 1] ? rsLineData[rsLineData.length - 1] : null,
                emaCount: rsEmaData ? rsEmaData.length : -1,
                emaFirst: rsEmaData && rsEmaData[0] ? rsEmaData[0] : null,
                emaLast: rsEmaData && rsEmaData[rsEmaData.length - 1] ? rsEmaData[rsEmaData.length - 1] : null
            };
        })()
        """
        res = self._execute_node_eval(js, include_indicators=True)
        self.assertEqual(res["lineCount"], 40)
        self.assertEqual(res["lineFirst"], {"time": 2000000})
        self.assertEqual(res["lineLast"]["time"], 2000000 + 39 * 60)
        self.assertNotIn("value", res["lineFirst"])
        self.assertEqual(res["emaCount"], 40)
        self.assertEqual(res["emaFirst"]["time"], 2000000)
        self.assertEqual(res["emaLast"]["time"], 2000000 + 39 * 60)

    def test_5_f5_backend_deduplication_local_backfill_and_seed_control(self):
        """F5: Backend in-flight deduplication, local cache fallback, and seed control."""
        async def run_f5_suite():
            server._last_1m_seed_attempt.clear()
            server._inflight_history.clear()

            # Part A: Concurrent in-flight deduplication
            agg1 = _FakeAggregator(_mk_bars(1, t0=1795448820 + 300 * 60))
            fake_storage1 = MagicMock()
            fake_storage1.get_bars.return_value = []

            fetch_calls = 0
            event_start = asyncio.Event()
            event_hold = asyncio.Event()

            async def slow_fetch(symbol, *args, **kwargs):
                nonlocal fetch_calls
                fetch_calls += 1
                event_start.set()
                await event_hold.wait()
                return _mk_bars(300)

            with patch.object(server, "aggregator", agg1), \
                 patch.object(server, "fetch_historical_bars", side_effect=slow_fetch), \
                 patch.object(server, "get_storage", return_value=fake_storage1):

                t1 = asyncio.create_task(
                    server.get_history(symbol="NASDAQ:MANH", timeframe="1m", seed=True, backfill=False, engine="A", limit=None)
                )
                await event_start.wait()

                t2 = asyncio.create_task(
                    server.get_history(symbol="NASDAQ:MANH", timeframe="1m", seed=True, backfill=False, engine="A", limit=None)
                )
                await asyncio.sleep(0.01)
                event_hold.set()

                r1 = await t1
                r2 = await t2

                self.assertEqual(fetch_calls, 1, "fetch_historical_bars must be called exactly once during flight")
                self.assertEqual(len(r1["bars"]), 301)
                self.assertEqual(len(r2["bars"]), 301)

            # Part B: Local cache fallback
            server._last_1m_seed_attempt.clear()
            server._inflight_history.clear()
            agg2 = _FakeAggregator(_mk_bars(1, t0=1795448820 + 300 * 60))
            fake_storage2 = MagicMock()
            fake_storage2.get_bars.return_value = _mk_bars(50)

            with patch.object(server, "aggregator", agg2), \
                 patch.object(server, "fetch_historical_bars", new=AsyncMock(return_value=[])), \
                 patch.object(server, "get_storage", return_value=fake_storage2):

                r_cache = await server.get_history(
                    symbol="NASDAQ:MANH", timeframe="1m", seed=True, backfill=False, engine="A", limit=None
                )
                self.assertEqual(len(r_cache["bars"]), 51)

            # Part C: Seed control
            server._last_1m_seed_attempt.clear()
            server._inflight_history.clear()
            agg3 = _FakeAggregator(_mk_bars(1, t0=1795448820 + 300 * 60))
            fake_storage3 = MagicMock()
            fake_storage3.get_bars.return_value = []
            fetch_mock3 = AsyncMock(return_value=_mk_bars(300))

            with patch.object(server, "aggregator", agg3), \
                 patch.object(server, "fetch_universal_history", new=fetch_mock3), \
                 patch.object(server, "get_storage", return_value=fake_storage3):

                # seed=False on equity symbol with 1 bar -> no network fetch
                r_no_seed = await server.get_history(
                    symbol="NASDAQ:MANH", timeframe="1m", seed=False, backfill=False, engine="A", limit=None
                )
                self.assertEqual(fetch_mock3.await_count, 0)
                self.assertEqual(len(r_no_seed["bars"]), 1)

                # seed=True -> network fetch executed
                r_seed = await server.get_history(
                    symbol="NASDAQ:MANH", timeframe="1m", seed=True, backfill=False, engine="A", limit=None
                )
                self.assertEqual(fetch_mock3.await_count, 1)
                self.assertEqual(len(r_seed["bars"]), 301)

        asyncio.run(run_f5_suite())

    def test_6_f6_subchart_seed_flag_static_check(self):
        """F6: loadSubChart history request URL contains seed=true, resetChartView right-aligns from = n - INITIAL_VISIBLE_BARS."""
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            app_code = f.read()

        self.assertIn('url = "/api/history?seed=true&symbol="', app_code)
        self.assertIn("var from = n - INITIAL_VISIBLE_BARS;", app_code)
        self.assertIn("Lightweight Charts 4.2.0 只在拖動開始時檢查 autoScale", app_code)

    def test_7_cache_304_and_no_cache_headers(self):
        """Cache 304: TestClient GET static/app.js returns 200 with ETag & no-cache, and 304 when If-None-Match matches."""
        client = TestClient(server.app)

        res200 = client.get("/static/app.js")
        self.assertEqual(res200.status_code, 200)
        self.assertIn("no-cache", res200.headers.get("cache-control", ""))
        etag = res200.headers.get("etag")
        self.assertTrue(etag, "ETag header must be present on 200 response")

        res304 = client.get("/static/app.js", headers={"if-none-match": etag})
        self.assertEqual(res304.status_code, 304)
        self.assertIn("no-cache", res304.headers.get("cache-control", ""))

    def test_8_phase3_range_preservation_and_boundaries(self):
        """Phase 3 (3-1 & 3-2): computePreservedLogicalRange boundaries, non-overlap resetChartView fallback, and renderChartDataKeepView."""
        js = """
        (function() {
            var p10 = [];
            for (var i = 0; i < 10; i++) p10.push(1000 + i * 60);
            var n10Future = [];
            for (var j = 0; j < 10; j++) n10Future.push(5000 + j * 60);
            var n10Past = [];
            for (var k = 0; k < 10; k++) n10Past.push(100 + k * 60);
            var n15 = [];
            for (var m = 0; m < 15; m++) n15.push(1000 + m * 60);

            var nanRange = computePreservedLogicalRange(p10, p10, { from: NaN, to: 5 });
            var infRange = computePreservedLogicalRange(p10, p10, { from: 0, to: Infinity });
            var nonOverlapFuture = computePreservedLogicalRange(p10, n10Future, { from: 1, to: 5 });
            var nonOverlapPast = computePreservedLogicalRange(p10, n10Past, { from: 1, to: 5 });

            // prevLast = 9; range.to = 8.5 >= 9 - 0.5 -> right-edge anchored (offset = -0.5, span = 4.5, nextLast = 14 -> to = 13.5, from = 9.0)
            var edgeHalf = computePreservedLogicalRange(p10, n15, { from: 4.0, to: 8.5 });
            // prevLast = 9; range.to = 8.4 < 9 - 0.5 -> anchorIdx = 8 -> same time in n15 at idx 8 -> shift 0
            var belowHalf = computePreservedLogicalRange(p10, n15, { from: 3.9, to: 8.4 });

            // renderChartData non-overlap fallback to resetChartView
            var curRange = null;
            var seriesBars = [];
            candleSeries = {
                setData: function(d) { seriesBars = d; },
                data: function() { return seriesBars; }
            };
            volumeSeries = { setData: function() {} };
            chart = {
                timeScale: function() {
                    return {
                        getVisibleLogicalRange: function() { return curRange; },
                        setVisibleLogicalRange: function(r) { curRange = r; },
                        fitContent: function() {}
                    };
                }
            };
            activeSymbol = "NASDAQ:AAPL";
            activeTimeframe = "1D";
            currentEngine = "A";
            lastChartViewKey = null;

            var barsA = [];
            for (var a = 0; a < 200; a++) barsA.push({ time: 1000 + a * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartData(barsA);
            curRange = { from: 20, to: 80 };

            // Non-overlapping bars with keepView: true must reset view (from: 200-150=50, to: 200-1+10=209)
            var barsNonOverlap = [];
            for (var b = 0; b < 200; b++) barsNonOverlap.push({ time: 1000000 + b * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartData(barsNonOverlap, { keepView: true });
            var rangeAfterNonOverlapReset = curRange;

            // renderChartDataKeepView: time-anchored preservation on 50-bar backfill even if prevLen is wrong
            var barsBase = [];
            for (var c = 0; c < 200; c++) barsBase.push({ time: 50000 + c * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartData(barsBase, { resetView: true });
            curRange = { from: 40, to: 90 };
            var barsBackfilled = [];
            for (var d = 0; d < 250; d++) barsBackfilled.push({ time: (50000 - 50 * 60) + d * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            // Pass prevLen=199 (off by 1) to prove time-based computePreservedLogicalRange (+50) wins over length diff (+51)
            renderChartDataKeepView(barsBackfilled, 199);
            var rangeAfterKeepViewTimeAnchored = curRange;

            // renderChartDataKeepView fallback to length-diff when prevTimes is empty
            candleSeries.data = function() { return []; };
            curRange = { from: 10, to: 60 };
            var barsPlus5 = [];
            for (var e = 0; e < 255; e++) barsPlus5.push({ time: (50000 - 50 * 60) + e * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            renderChartDataKeepView(barsPlus5, 250);
            var rangeAfterKeepViewFallback = curRange;

            return {
                nanRange: nanRange,
                infRange: infRange,
                nonOverlapFuture: nonOverlapFuture,
                nonOverlapPast: nonOverlapPast,
                edgeHalf: edgeHalf,
                belowHalf: belowHalf,
                rangeAfterNonOverlapReset: rangeAfterNonOverlapReset,
                rangeAfterKeepViewTimeAnchored: rangeAfterKeepViewTimeAnchored,
                rangeAfterKeepViewFallback: rangeAfterKeepViewFallback
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertIsNone(res["nanRange"])
        self.assertIsNone(res["infRange"])
        self.assertIsNone(res["nonOverlapFuture"])
        self.assertIsNone(res["nonOverlapPast"])
        self.assertEqual(res["edgeHalf"], {"from": 9.0, "to": 13.5})
        self.assertEqual(res["belowHalf"], {"from": 3.9, "to": 8.4})
        self.assertEqual(res["rangeAfterNonOverlapReset"], {"from": 50, "to": 209})
        self.assertEqual(res["rangeAfterKeepViewTimeAnchored"], {"from": 90, "to": 140})
        self.assertEqual(res["rangeAfterKeepViewFallback"], {"from": 15, "to": 65})

    def test_9_phase3_render_subpane_rs_early_return_cleanup(self):
        """Phase 3 (3-3): renderSubPaneRs clears rsLineSeries and rsEmaSeries before returning early while benchmark fetch is in flight."""
        js = """
        (function() {
            var paneEl = { style: {}, classList: { remove: function() {}, add: function() {} } };
            var rsPaneEl = { style: {} };
            var rsChartEl = { clientWidth: 300, clientHeight: 65 };
            var origGetById = document.getElementById;
            document.getElementById = function(id) {
                if (id === "splitPane2") return paneEl;
                if (id === "splitRsPane2") return rsPaneEl;
                if (id === "splitRsChart2") return rsChartEl;
                return origGetById ? origGetById(id) : null;
            };

            var lineSetDataCalls = [];
            var emaSetDataCalls = [];
            var bars = [
                { time: 1000, open: 10, high: 11, low: 9, close: 10.5, volume: 100 },
                { time: 1060, open: 10.5, high: 11.5, low: 10, close: 11, volume: 120 }
            ];
            indicatorConfig.rsLine = true;
            currentEngine = "A";
            activeTimeframe = "1D";
            paneTimeframes[2] = "15m";
            rsBenchmarkTag = "SPY";
            subPaneBenchmarkCache["SPY|15m"] = { inFlight: true, failedAt: 0, bars: [] };
            subCharts[2] = {
                bars: bars,
                series: { data: function() { return bars; } },
                rsChart: {},
                rsLineSeries: { setData: function(d) { lineSetDataCalls.push(d); } },
                rsEmaSeries: { setData: function(d) { emaSetDataCalls.push(d); } }
            };

            renderSubPaneRs(2, bars);

            return {
                lineSetDataCalls: lineSetDataCalls,
                emaSetDataCalls: emaSetDataCalls
            };
        })()
        """
        res = self._execute_node_eval(js)
        expected_blank = [{"time": 1000}, {"time": 1060}]
        self.assertEqual(res["lineSetDataCalls"], [expected_blank])
        self.assertEqual(res["emaSetDataCalls"], [expected_blank])

    def test_10_phase3_inflight_seed_timeout(self):
        """Phase 3 (3-4): concurrent get_history times out waiting on a stalled inflight 1m seed without cancelling the inflight task."""
        async def run_timeout_test():
            server._last_1m_seed_attempt.clear()
            server._inflight_history.clear()
            agg = _FakeAggregator(_mk_bars(2, t0=1795448820 + 100 * 60))
            fake_storage = MagicMock()
            fake_storage.get_bars.return_value = []

            event_started = asyncio.Event()
            hold_event = asyncio.Event()

            async def stalled_fetch(symbol, *args, **kwargs):
                event_started.set()
                await hold_event.wait()
                return _mk_bars(50)

            t1 = None
            try:
                with patch.object(server, "ONE_MIN_INFLIGHT_WAIT_SEC", 0.05), \
                     patch.object(server, "aggregator", agg), \
                     patch.object(server, "fetch_historical_bars", side_effect=stalled_fetch), \
                     patch.object(server, "get_storage", return_value=fake_storage):
                    t1 = asyncio.create_task(
                        server.get_history(symbol="NASDAQ:MANH", timeframe="1m", seed=True, backfill=False, engine="A", limit=None)
                    )
                    await event_started.wait()

                    res2 = await asyncio.wait_for(
                        server.get_history(symbol="NASDAQ:MANH", timeframe="1m", seed=True, backfill=False, engine="A", limit=None),
                        timeout=0.5,
                    )
                    self.assertEqual(len(res2["bars"]), 2)
                    self.assertFalse(t1.done(), "First inflight request must still be running after second request times out")

                    hold_event.set()
                    res1 = await asyncio.wait_for(t1, timeout=1.0)
                    self.assertEqual(len(res1["bars"]), 52)
            finally:
                hold_event.set()
                if t1 is not None and not t1.done():
                    await t1
                server._last_1m_seed_attempt.clear()
                server._inflight_history.clear()

        self.assertGreater(server.ONE_MIN_INFLIGHT_WAIT_SEC, 0)
        asyncio.run(run_timeout_test())

    def test_11_phase3_non_finite_guards_and_keepview_edge_cases(self):
        """Phase 3 remediation: non-finite timestamps in computePreservedLogicalRange/renderChartData and non-finite range or empty bars in renderChartDataKeepView."""
        js = """
        (function() {
            var nanAnchor = computePreservedLogicalRange([100, NaN, 300], [100, 200, 300], { from: 0, to: 1.4 });
            var nanPrevEnd = computePreservedLogicalRange([100, 200, NaN], [400, 500, 600], { from: 0, to: 2 });
            var nanNextFirst = computePreservedLogicalRange([100, 200, 300], [NaN, 200, 300], { from: 0, to: 1 });
            var nanNextMid = computePreservedLogicalRange([100, 200, 300], [100, NaN, 300], { from: 0, to: 1.4 });

            var curRange = null;
            var setRangeHistory = [];
            var seriesBars = [];
            candleSeries = {
                setData: function(d) { seriesBars = d; },
                data: function() { return seriesBars; }
            };
            volumeSeries = { setData: function() {} };
            chart = {
                timeScale: function() {
                    return {
                        getVisibleLogicalRange: function() { return curRange; },
                        setVisibleLogicalRange: function(r) {
                            curRange = r;
                            setRangeHistory.push({ from: r.from, to: r.to, fromIsFinite: Number.isFinite(r.from), toIsFinite: Number.isFinite(r.to) });
                        },
                        fitContent: function() {}
                    };
                }
            };
            activeSymbol = "NASDAQ:AAPL";
            activeTimeframe = "1D";
            currentEngine = "A";
            lastChartViewKey = null;

            var mkBars = function(n, t0) {
                var out = [];
                for (var i = 0; i < n; i++) out.push({ time: (t0 || 1000) + i * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
                return out;
            };

            // 1. renderChartData with a NaN bar[0].time in non-overlapping bars must still reset via resetChartView
            renderChartData(mkBars(200, 1000));
            curRange = { from: 50, to: 100 };
            var barsNonOverlapWithNaN = mkBars(200, 1000000);
            barsNonOverlapWithNaN[0].time = NaN;
            renderChartData(barsNonOverlapWithNaN, { keepView: true });
            var rangeAfterNaNNonOverlap = curRange;

            // 2. renderChartDataKeepView with non-finite ranges must keep resetChartView result
            renderChartData(mkBars(100, 2000), { resetView: true });
            curRange = { from: NaN, to: 50 };
            setRangeHistory = [];
            renderChartDataKeepView(mkBars(105, 2000), 100);
            var afterKeepViewNaN = curRange;
            var historyAfterNaN = setRangeHistory.slice();

            curRange = { from: null, to: null };
            setRangeHistory = [];
            renderChartDataKeepView(mkBars(105, 2000), 100);
            var afterKeepViewNull = curRange;

            curRange = { from: 0, to: Infinity };
            setRangeHistory = [];
            renderChartDataKeepView(mkBars(105, 2000), 100);
            var afterKeepViewInf = curRange;

            // 3. renderChartDataKeepView with empty bars must not shift viewport
            curRange = { from: 20, to: 50 };
            setRangeHistory = [];
            renderChartDataKeepView([], 100);
            var afterKeepViewEmpty = curRange;
            var historyAfterEmpty = setRangeHistory.slice();

            return {
                nanAnchor: nanAnchor,
                nanPrevEnd: nanPrevEnd,
                nanNextFirst: nanNextFirst,
                nanNextMid: nanNextMid,
                rangeAfterNaNNonOverlap: rangeAfterNaNNonOverlap,
                afterKeepViewNaN: afterKeepViewNaN,
                historyAfterNaN: historyAfterNaN,
                afterKeepViewNull: afterKeepViewNull,
                afterKeepViewInf: afterKeepViewInf,
                afterKeepViewEmpty: afterKeepViewEmpty,
                historyAfterEmpty: historyAfterEmpty
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertIsNone(res["nanAnchor"])
        self.assertIsNone(res["nanPrevEnd"])
        self.assertIsNone(res["nanNextFirst"])
        self.assertIsNone(res["nanNextMid"])
        self.assertEqual(res["rangeAfterNaNNonOverlap"], {"from": 50, "to": 209})
        self.assertEqual(res["afterKeepViewNaN"], {"from": -45, "to": 114})
        self.assertTrue(all(item["fromIsFinite"] and item["toIsFinite"] for item in res["historyAfterNaN"]))
        self.assertEqual(res["afterKeepViewNull"], {"from": -45, "to": 114})
        self.assertEqual(res["afterKeepViewInf"], {"from": -45, "to": 114})
        self.assertEqual(res["afterKeepViewEmpty"], {"from": 20, "to": 50})
        self.assertEqual(res["historyAfterEmpty"], [])

    def test_12_phase3_inflight_reentry_no_hang(self):
        """Phase 3 remediation: stalled background/empty-start inflight 1m seed does not hang get_history on re-entry."""
        async def run_reentry_tests():
            # Case A: background fetch_universal_history stalled while aggregator has 2 bars
            server._last_1m_seed_attempt.clear()
            server._inflight_history.clear()
            agg_a = _FakeAggregator(_mk_bars(2, t0=1795448820 + 100 * 60))
            fake_storage = MagicMock()
            fake_storage.get_bars.return_value = []
            started_a = asyncio.Event()
            hold_a = asyncio.Event()

            async def stalled_fetch_a(symbol, *args, **kwargs):
                started_a.set()
                await hold_a.wait()
                return _mk_bars(40)

            bg_task = None
            try:
                with patch.object(server, "ONE_MIN_INFLIGHT_WAIT_SEC", 0.05), \
                     patch.object(server, "aggregator", agg_a), \
                     patch.object(server, "fetch_historical_bars", side_effect=stalled_fetch_a), \
                     patch.object(server, "get_storage", return_value=fake_storage):
                    bg_task = asyncio.create_task(server.fetch_universal_history("NASDAQ:TSLA", timeframe="1m"))
                    await started_a.wait()

                    res_a = await asyncio.wait_for(
                        server.get_history(symbol="NASDAQ:TSLA", timeframe="1m", seed=True, backfill=False, engine="A", limit=None),
                        timeout=0.5,
                    )
                    self.assertEqual(len(res_a["bars"]), 2)
                    self.assertIn("NASDAQ:TSLA", server._last_1m_seed_attempt)

                    # Also verify backfill=True does not hang while inflight is stalled
                    res_bf = await asyncio.wait_for(
                        server.get_history(symbol="NASDAQ:TSLA", timeframe="1m", seed=True, backfill=True, engine="A", limit=None),
                        timeout=0.5,
                    )
                    self.assertEqual(len(res_bf["bars"]), 2)
                    self.assertFalse(bg_task.done())
            finally:
                hold_a.set()
                if bg_task is not None and not bg_task.done():
                    await bg_task
                server._last_1m_seed_attempt.clear()
                server._inflight_history.clear()

            # Case B: initial get_history started with 0 bars & stalled; live bar arrives; second get_history(seed=True) does not hang
            agg_b = _FakeAggregator([])
            started_b = asyncio.Event()
            hold_b = asyncio.Event()

            async def stalled_fetch_b(symbol, *args, **kwargs):
                started_b.set()
                await hold_b.wait()
                return _mk_bars(40)

            t1 = None
            try:
                with patch.object(server, "ONE_MIN_INFLIGHT_WAIT_SEC", 0.05), \
                     patch.object(server, "aggregator", agg_b), \
                     patch.object(server, "fetch_historical_bars", side_effect=stalled_fetch_b), \
                     patch.object(server, "get_storage", return_value=fake_storage):
                    t1 = asyncio.create_task(
                        server.get_history(symbol="NASDAQ:NVDA", timeframe="1m", seed=True, backfill=False, engine="A", limit=None)
                    )
                    await started_b.wait()
                    agg_b.bars = _mk_bars(1, t0=1795448820 + 200 * 60)

                    res_b = await asyncio.wait_for(
                        server.get_history(symbol="NASDAQ:NVDA", timeframe="1m", seed=True, backfill=False, engine="A", limit=None),
                        timeout=0.5,
                    )
                    self.assertEqual(len(res_b["bars"]), 1)
                    self.assertIn("NASDAQ:NVDA", server._last_1m_seed_attempt)
                    self.assertFalse(t1.done())

                    hold_b.set()
                    res_t1 = await asyncio.wait_for(t1, timeout=1.0)
                    self.assertEqual(len(res_t1["bars"]), 41)
            finally:
                hold_b.set()
                if t1 is not None and not t1.done():
                    await t1
                server._last_1m_seed_attempt.clear()
                server._inflight_history.clear()

        asyncio.run(run_reentry_tests())


if __name__ == "__main__":
    unittest.main()

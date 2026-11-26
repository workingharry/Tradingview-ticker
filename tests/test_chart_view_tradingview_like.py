import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestChartViewTradingViewLike(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
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

    def _execute_node_eval(self, eval_js_snippet: str):
        """Loads static/app.js in Node.js VM sandbox and evaluates the provided expression."""
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
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
        vm.runInContext(appCode, sandbox);

        const result = vm.runInContext({json.dumps(eval_js_snippet)}, sandbox);
        console.log(JSON.stringify(result));
        """
        env = os.environ.copy()
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
        proc = subprocess.run(
            [self.node_bin, "-e", harness],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env
        )
        self.assertEqual(
            proc.returncode, 0,
            f"Node VM execution failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        return json.loads(proc.stdout.strip())

    def test_1_reset_chart_view(self):
        """1. resetChartView: F6 新預期值：n=500 -> {350, 509}；n=150 -> {0, 159}；n=50 -> {-100, 59}；n=40 -> {-110, 49}；n=1 -> {-149, 10}；n=0 -> null。假圖表沒有 setVisibleLogicalRange、只有 fitContent 時，會呼叫 fitContent 一次。"""
        js = """
        (function() {
            var calls = [];
            var fakeChart = {
                timeScale: function() {
                    return {
                        setVisibleLogicalRange: function(r) { calls.push(r); }
                    };
                }
            };
            var r500 = resetChartView(fakeChart, 500);
            var r150 = resetChartView(fakeChart, 150);
            var r50 = resetChartView(fakeChart, 50);
            var r40 = resetChartView(fakeChart, 40);
            var r1 = resetChartView(fakeChart, 1);
            var r0 = resetChartView(fakeChart, 0);
            var rNeg = resetChartView(fakeChart, -1);

            var fitCalls = 0;
            var fakeChartFallback = {
                timeScale: function() {
                    return {
                        fitContent: function() { fitCalls++; }
                    };
                }
            };
            var rFallback = resetChartView(fakeChartFallback, 500);

            return {
                r500: r500,
                r150: r150,
                r50: r50,
                r40: r40,
                r1: r1,
                r0: r0,
                rNeg: rNeg,
                calls: calls,
                fitCalls: fitCalls,
                rFallback: rFallback
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertEqual(res["r500"], {"from": 350, "to": 509})
        self.assertEqual(res["r150"], {"from": 0, "to": 159})
        self.assertEqual(res["r50"], {"from": -100, "to": 59})
        self.assertEqual(res["r40"], {"from": -110, "to": 49})
        self.assertEqual(res["r1"], {"from": -149, "to": 10})
        self.assertIsNone(res["r0"])
        self.assertIsNone(res["rNeg"])
        self.assertEqual(res["calls"], [
            {"from": 350, "to": 509},
            {"from": 0, "to": 159},
            {"from": -100, "to": 59},
            {"from": -110, "to": 49},
            {"from": -149, "to": 10}
        ])
        self.assertEqual(res["fitCalls"], 1)
        self.assertEqual(res["rFallback"], {"from": 350, "to": 509})

    def test_2_render_chart_data_view_preservation(self):
        """2. renderChartData: 第一次呼叫重設視圖；同標的同週期保留目前範圍且 fitContent 0 次；改週期重設；resetView: true 重設。"""
        js = """
        (function() {
            candleSeries = { setData: function() {} };
            volumeSeries = { setData: function() {} };
            var ranges = [];
            var fitContentCalls = 0;
            var curRange = null;

            chart = {
                timeScale: function() {
                    return {
                        getVisibleLogicalRange: function() { return curRange; },
                        setVisibleLogicalRange: function(r) { ranges.push(r); curRange = r; },
                        fitContent: function() { fitContentCalls++; }
                    };
                }
            };

            var mkBars = function(n) {
                var b = [];
                for (var i = 0; i < n; i++) {
                    b.push({ time: 1000 + i * 60, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
                }
                return b;
            };
            var bars500 = mkBars(500);

            activeSymbol = "AAPL";
            activeTimeframe = "1D";
            lastChartViewKey = null;

            // 1. 第一次呼叫（key 改變）
            renderChartData(bars500);
            var firstRange = ranges[ranges.length - 1];

            // 2. 第二次呼叫，同標的同週期，getVisibleLogicalRange 回傳 {from: 100.5, to: 180.5}
            curRange = { from: 100.5, to: 180.5 };
            var fitBefore = fitContentCalls;
            renderChartData(bars500);
            var secondRange = ranges[ranges.length - 1];
            var secondFitDelta = fitContentCalls - fitBefore;

            // 3. 改 activeTimeframe 後再呼叫
            activeTimeframe = "1h";
            renderChartData(bars500);
            var thirdRange = ranges[ranges.length - 1];

            // 4. renderChartData(bars, { resetView: true })
            curRange = { from: 200, to: 300 };
            renderChartData(bars500, { resetView: true });
            var fourthRange = ranges[ranges.length - 1];

            return {
                firstRange: firstRange,
                secondRange: secondRange,
                secondFitDelta: secondFitDelta,
                thirdRange: thirdRange,
                fourthRange: fourthRange
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertEqual(res["firstRange"], {"from": 350, "to": 509})
        self.assertEqual(res["secondRange"], {"from": 100.5, "to": 180.5})
        self.assertEqual(res["secondFitDelta"], 0)
        self.assertEqual(res["thirdRange"], {"from": 350, "to": 509})
        self.assertEqual(res["fourthRange"], {"from": 350, "to": 509})

    def test_3_static_checks(self):
        """3. 靜態檢查：rightOffset: RIGHT_WHITESPACE_BARS 在 app.js 出現 4 次；renderChartData 函式本體內沒有 fitContent(；原本的 dblclick 那段（含 fitContent）已不存在。"""
        content = self.app_js_path.read_text(encoding="utf-8")

        # 4x rightOffset
        right_offset_matches = len(re.findall(r"rightOffset:\s*RIGHT_WHITESPACE_BARS", content))
        self.assertEqual(right_offset_matches, 4, f"Expected 4 occurrences of rightOffset: RIGHT_WHITESPACE_BARS, found {right_offset_matches}")

        # renderChartData has no fitContent(
        rcd_match = re.search(r"function\s+renderChartData\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\n\n//\s*=+", content)
        self.assertIsNotNone(rcd_match, "renderChartData function block not found")
        rcd_body = rcd_match.group(1)
        self.assertNotIn("fitContent(", rcd_body, "renderChartData body must not call fitContent(")

        # Original dblclick block gone
        old_dblclick_pattern = r'pContainer\.addEventListener\("dblclick",\s*function\(\)\s*\{\s*setAutoScale\(true\);\s*if\s*\(chart\s*&&\s*chart\.timeScale\)\s*\{\s*chart\.timeScale\(\)\.fitContent\(\);'
        self.assertIsNone(re.search(old_dblclick_pattern, content), "Old dblclick fitContent block must be removed")

    def test_4_clamp_and_set_visible_range(self):
        """4. clampAndSetVisibleRange: 'ALL', n=300 -> {0, 309}; '1M' -> {269, 309}（直接由 K 棒計算，不呼叫 setVisibleRange 再讀回）。"""
        js = """
        (function() {
            var logicalRanges = [];
            var visibleRanges = [];
            var curLogicalRange = null;

            chart = {
                timeScale: function() {
                    return {
                        setVisibleLogicalRange: function(r) { logicalRanges.push(r); curLogicalRange = r; },
                        getVisibleLogicalRange: function() { return curLogicalRange; },
                        setVisibleRange: function(r) { visibleRanges.push(r); }
                    };
                }
            };

            var mkBars = function(n) {
                var b = [];
                for (var i = 0; i < n; i++) {
                    b.push({ time: 1000 + i * 86400, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
                }
                return b;
            };
            currentRawBars = mkBars(300);

            // Test 1: "ALL"
            clampAndSetVisibleRange("ALL");
            var allRange = logicalRanges[logicalRanges.length - 1];

            // Test 2: "1M"：直接從 K 棒算邏輯範圍（Lightweight Charts 的範圍設定要到下一個畫面才生效，
            // 不能先 setVisibleRange 再讀回）。假圖表故意讓 getVisibleLogicalRange 回傳舊範圍。
            curLogicalRange = { from: 0, to: 1 };
            var visibleBefore = visibleRanges.length;
            clampAndSetVisibleRange("1M");
            var oneMonthRange = logicalRanges[logicalRanges.length - 1];
            clampAndSetVisibleRange("1Y");
            var oneYearRange = logicalRanges[logicalRanges.length - 1];

            return {
                allRange: allRange,
                oneMonthRange: oneMonthRange,
                oneYearRange: oneYearRange,
                setVisibleRangeCalls: visibleRanges.length - visibleBefore,
                helper: [
                    computeLogicalRangeForTime(currentRawBars, 0, 10),
                    computeLogicalRangeForTime(currentRawBars, currentRawBars[299].time + 5, 10),
                    computeLogicalRangeForTime([], 0, 10),
                    computeLogicalRangeForTime(currentRawBars.slice(0, 5), currentRawBars[1].time + 1, 0)
                ]
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertEqual(res["allRange"], {"from": 0, "to": 309})
        # 300 根日線，最後一根往前 30 天 = 第 269 根；右邊留 10 根
        self.assertEqual(res["oneMonthRange"], {"from": 269, "to": 309})
        self.assertEqual(res["oneYearRange"], {"from": 0, "to": 309})
        self.assertEqual(res["setVisibleRangeCalls"], 0)
        self.assertEqual(res["helper"], [{"from": 0, "to": 309}, {"from": 299, "to": 309}, None, {"from": 2, "to": 4}])

    def test_4b_drag_handler_uses_current_draw_tool(self):
        """拖動處理讀取實際存在的 currentDrawTool（activeDrawingTool 不存在，畫線時會被誤當成游標而關掉 AUTO）。"""
        code = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("activeDrawingTool", code)
        self.assertIn('var toolState = (typeof currentDrawTool !== "undefined" && currentDrawTool) ? currentDrawTool : "cursor";', code)

    def test_5_is_point_in_price_axis_and_should_disable_auto_scale_on_drag(self):
        """5. isPointInPriceAxis 與 shouldDisableAutoScaleOnDrag: D 段列出的每個預期值。"""
        js = """
        (function() {
            var rect1 = { left: 0, width: 1000 };
            var rect2 = { left: 100, width: 1000 };

            var p1_950 = isPointInPriceAxis(950, rect1, 60);
            var p1_940 = isPointInPriceAxis(940, rect1, 60);
            var p1_939 = isPointInPriceAxis(939, rect1, 60);
            var p1_1000 = isPointInPriceAxis(1000, rect1, 60);
            var p1_1001 = isPointInPriceAxis(1001, rect1, 60);
            var p1_axis0 = isPointInPriceAxis(950, rect1, 0);

            var p2_1045 = isPointInPriceAxis(1045, rect2, 60);
            var p2_1039 = isPointInPriceAxis(1039, rect2, 60);

            var d1 = shouldDisableAutoScaleOnDrag(0, 7, { autoScale: true, tool: "cursor", draggingHandle: false });
            var d2 = shouldDisableAutoScaleOnDrag(10, 7, { autoScale: true, tool: "cursor", draggingHandle: false });
            var d3 = shouldDisableAutoScaleOnDrag(0, 6, { autoScale: true, tool: "cursor", draggingHandle: false });
            var d4 = shouldDisableAutoScaleOnDrag(0, -7, { autoScale: true, tool: "cursor", draggingHandle: false });
            var d5 = shouldDisableAutoScaleOnDrag(0, 7, { autoScale: true, tool: "trendline", draggingHandle: false });
            var d6 = shouldDisableAutoScaleOnDrag(0, 7, { autoScale: true, tool: "cursor", draggingHandle: true });
            var d7 = shouldDisableAutoScaleOnDrag(0, 7, { autoScale: false, tool: "cursor", draggingHandle: false });
            var d8 = shouldDisableAutoScaleOnDrag(0, 7, null);

            return {
                p1_950: p1_950,
                p1_940: p1_940,
                p1_939: p1_939,
                p1_1000: p1_1000,
                p1_1001: p1_1001,
                p1_axis0: p1_axis0,
                p2_1045: p2_1045,
                p2_1039: p2_1039,
                d1: d1,
                d2: d2,
                d3: d3,
                d4: d4,
                d5: d5,
                d6: d6,
                d7: d7,
                d8: d8
            };
        })()
        """
        res = self._execute_node_eval(js)
        # isPointInPriceAxis assertions
        self.assertTrue(res["p1_950"])
        self.assertTrue(res["p1_940"])
        self.assertFalse(res["p1_939"])
        self.assertTrue(res["p1_1000"])
        self.assertFalse(res["p1_1001"])
        self.assertFalse(res["p1_axis0"])
        self.assertTrue(res["p2_1045"])
        self.assertFalse(res["p2_1039"])

        # shouldDisableAutoScaleOnDrag assertions
        self.assertTrue(res["d1"])
        self.assertFalse(res["d2"])
        self.assertFalse(res["d3"])
        self.assertTrue(res["d4"])
        self.assertFalse(res["d5"])
        self.assertFalse(res["d6"])
        self.assertFalse(res["d7"])
        self.assertFalse(res["d8"])

    def test_6_sync_auto_scale_from_chart(self):
        """6. syncAutoScaleFromChart: 假圖表 priceScale('right').options() 回傳 {autoScale: false} -> isAutoScale === false，而且沒有呼叫 applyOptions；假圖表沒有 priceScale -> 回傳 null、不丟錯。"""
        js = """
        (function() {
            var applyOptionsCalls = 0;
            isAutoScale = true;

            chart = {
                priceScale: function(id) {
                    return {
                        options: function() { return { autoScale: false }; },
                        applyOptions: function() { applyOptionsCalls++; }
                    };
                },
                applyOptions: function() { applyOptionsCalls++; }
            };

            var resSync = syncAutoScaleFromChart();
            var autoScaleAfter = isAutoScale;

            chart = {}; // no priceScale
            var resNull = null;
            var errorThrown = false;
            try {
                resNull = syncAutoScaleFromChart();
            } catch (e) {
                errorThrown = true;
            }

            return {
                resSync: resSync,
                autoScaleAfter: autoScaleAfter,
                applyOptionsCalls: applyOptionsCalls,
                resNull: resNull,
                errorThrown: errorThrown
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertIs(res["resSync"], False)
        self.assertIs(res["autoScaleAfter"], False)
        self.assertEqual(res["applyOptionsCalls"], 0)
        self.assertIsNone(res["resNull"])
        self.assertFalse(res["errorThrown"])

    def test_7_install_range_debug(self):
        """7. installRangeDebug: 旗標不是 '1' 時原方法沒被替換；旗標是 '1' 時呼叫 setVisibleLogicalRange 記錄 log，且原方法仍被呼叫、參數相同。"""
        js = """
        (function() {
            // Case A: Flag not '1'
            localStorage.removeItem("tv_debug_range");
            var origCalledA = false;
            var origFnA = function(r) { origCalledA = true; };
            var chartA = {
                timeScale: function() {
                    return { setVisibleLogicalRange: origFnA };
                }
            };
            installRangeDebug(chartA, "chartA");
            var isReplacedA = (chartA.timeScale().setVisibleLogicalRange !== origFnA);

            // Case B: Flag is '1'
            localStorage.setItem("tv_debug_range", "1");
            var origArgsB = null;
            var origFnB = function(r) { origArgsB = r; };
            var chartB = {
                timeScale: function() {
                    return { setVisibleLogicalRange: origFnB };
                }
            };
            var logsB = [];
            var origConsoleLog = console.log;
            console.log = function() {
                var args = Array.prototype.slice.call(arguments);
                logsB.push(args.join(" "));
            };
            try {
                installRangeDebug(chartB, "chartB");
                chartB.timeScale().setVisibleLogicalRange({ from: 10, to: 20 });
            } finally {
                console.log = origConsoleLog;
            }

            return {
                isReplacedA: isReplacedA,
                origArgsB: origArgsB,
                hasRangeLogB: logsB.some(function(l) {
                    return l.indexOf("[range]") !== -1 && l.indexOf("chartB") !== -1 && l.indexOf("setVisibleLogicalRange") !== -1;
                })
            };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertFalse(res["isReplacedA"])
        self.assertEqual(res["origArgsB"], {"from": 10, "to": 20})
        self.assertTrue(res["hasRangeLogB"])

    def test_8_server_no_cache_headers(self):
        """8. server: fastapi.testclient.TestClient(server.app)（不要用 with）GET /static/app.js 與 /，回應的 Cache-Control 為 no-cache。"""
        import server
        from fastapi.testclient import TestClient

        client = TestClient(server.app)

        # GET /static/app.js
        resp_static = client.get("/static/app.js")
        self.assertEqual(resp_static.status_code, 200)
        self.assertEqual(resp_static.headers.get("Cache-Control"), "no-cache")

        # GET /
        resp_root = client.get("/")
        self.assertEqual(resp_root.status_code, 200)
        self.assertEqual(resp_root.headers.get("Cache-Control"), "no-cache")


if __name__ == "__main__":
    unittest.main()

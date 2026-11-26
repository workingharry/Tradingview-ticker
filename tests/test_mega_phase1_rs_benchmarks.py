import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestMegaPhase1RsBenchmarks(unittest.TestCase):
    """階段 1：RS 與基準（S5）

    S5-01 子圖用錯週期的基準（子圖 1W、主圖 1D 時，子圖基準請求含 timeframe=1W）
    S5-02 RS 副圖與主圖 X 軸改用時間範圍同步，initChart 內不再出現 setVisibleLogicalRange(newRange)
    S5-03 基準去重與冷卻：連續呼叫 10 次只發 1 個請求；失敗後 29 秒內不重試、31 秒後重試
    S5-04 基準請求代次保護：兩個請求反序回來時只套用最新的
    S5-05 基準代號對應：QQQ, IWM, DIA, BTCUSDT, 含冒號, 其他, 空值, SPY
    """

    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
        cls.indicators_js_path = PROJECT_ROOT / "static" / "indicators.js"
        if not cls.app_js_path.exists():
            raise FileNotFoundError(f"static/app.js not found at {cls.app_js_path}")

        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )

    def setUp(self):
        if not self.node_bin:
            self.skipTest("Node.js binary not found in PATH; skipping JS VM evaluation")

    def _execute_node_eval(self, eval_js_snippet: str, load_indicators: bool = False):
        """Loads static/app.js in Node.js VM sandbox and evaluates the provided expression."""
        load_ind_code = ""
        if load_indicators and self.indicators_js_path.exists():
            load_ind_code = f"const indCode = fs.readFileSync({json.dumps(str(self.indicators_js_path))}, 'utf8'); vm.runInContext(indCode, sandbox);"

        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: (id) => ({{
                    id: id,
                    textContent: '',
                    style: {{}},
                    classList: {{ add: () => {{}}, remove: () => {{}}, contains: () => false }}
                }}),
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl,
            Date: Date,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);
        {load_ind_code}
        vm.runInContext(appCode, sandbox);

        const result = vm.runInContext({json.dumps(eval_js_snippet)}, sandbox);
        console.log("__JSON_OUTPUT__" + JSON.stringify(result));
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
            f"Node evaluation failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        stdout = proc.stdout.strip()
        for line in reversed(stdout.splitlines()):
            if line.startswith("__JSON_OUTPUT__"):
                return json.loads(line[len("__JSON_OUTPUT__"):])
        return None

    def _execute_node_eval_async(self, eval_js_snippet: str, load_indicators: bool = False):
        """Same as _execute_node_eval, but awaits a Promise returned by snippet."""
        load_ind_code = ""
        if load_indicators and self.indicators_js_path.exists():
            load_ind_code = f"const indCode = fs.readFileSync({json.dumps(str(self.indicators_js_path))}, 'utf8'); vm.runInContext(indCode, sandbox);"

        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: (id) => ({{
                    id: id,
                    textContent: '',
                    style: {{}},
                    classList: {{ add: () => {{}}, remove: () => {{}}, contains: () => false }}
                }}),
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl,
            Date: Date,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);
        {load_ind_code}
        vm.runInContext(appCode, sandbox);

        Promise.resolve(vm.runInContext({json.dumps(eval_js_snippet)}, sandbox))
            .then((result) => {{ console.log("__JSON_OUTPUT__" + JSON.stringify(result)); }})
            .catch((err) => {{
                console.error(err);
                process.exit(1);
            }});
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
            f"Node async evaluation failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        stdout = proc.stdout.strip()
        for line in reversed(stdout.splitlines()):
            if line.startswith("__JSON_OUTPUT__"):
                return json.loads(line[len("__JSON_OUTPUT__"):])
        return None

    def test_s5_05_resolve_benchmark_symbol(self):
        """S5-05: resolveBenchmarkSymbol returns consistent symbol mapping."""
        js_code = """
        ({
            qqq: resolveBenchmarkSymbol("QQQ"),
            iwm: resolveBenchmarkSymbol("IWM"),
            dia: resolveBenchmarkSymbol("DIA"),
            btcusdt: resolveBenchmarkSymbol("BTCUSDT"),
            withColon: resolveBenchmarkSymbol("COINBASE:ETHUSD"),
            other: resolveBenchmarkSymbol("NVDA"),
            emptyStr: resolveBenchmarkSymbol(""),
            nullVal: resolveBenchmarkSymbol(null),
            spyVal: resolveBenchmarkSymbol("SPY")
        })
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["qqq"], "NASDAQ:QQQ")
        self.assertEqual(res["iwm"], "AMEX:IWM")
        self.assertEqual(res["dia"], "NYSE:DIA")
        self.assertEqual(res["btcusdt"], "BINANCE:BTCUSDT")
        self.assertEqual(res["withColon"], "COINBASE:ETHUSD")
        self.assertEqual(res["other"], "NASDAQ:NVDA")
        self.assertEqual(res["emptyStr"], "AMEX:SPY")
        self.assertEqual(res["nullVal"], "AMEX:SPY")
        self.assertEqual(res["spyVal"], "AMEX:SPY")

    def test_s5_02_static_check_init_chart_time_range_sync(self):
        """S5-02: initChart does not contain setVisibleLogicalRange(newRange) and uses time range."""
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        start = code.find("function initChart(")
        self.assertNotEqual(start, -1, "initChart function not found")
        end = code.find("chart.subscribeCrosshairMove", start)
        self.assertNotEqual(end, -1, "end of initChart not found")
        init_chart_code = code[start:end]

        # 修正（滑動時自動放大到最大）：改用邏輯範圍同步，RS 資料先補齊成主圖的時間軸。
        # 時間範圍同步會在捲到右側空白時把剩下的 K 棒撐滿寬度並互相回傳，所以這裡要求不再使用。
        self.assertNotIn("setVisibleLogicalRange(newRange)", init_chart_code)
        self.assertNotIn("subscribeVisibleTimeRangeChange", init_chart_code)
        self.assertIn("bindLogicalRangeSync(chart, rsChart", init_chart_code)

    def test_s5_03_benchmark_load_dedup_and_cooldown(self):
        """S5-03: Consecutive updateRsPane(bars, []) calls are deduped, and failures cooldown for 30s."""
        js_code = """
        (function() {
            var fetchCount = 0;
            var fetchUrls = [];
            var failFetch = false;

            fetch = function(url) {
                fetchCount++;
                fetchUrls.push(url);
                if (failFetch) {
                    return Promise.reject(new Error("Network error"));
                }
                return new Promise(function(resolve) {
                    // simulate pending in-flight request
                });
            };

            var fakeTime = 1000000;
            Date.now = function() { return fakeTime; };

            window.Indicators = {
                calcATRToolkit: function() { return {}; },
                calcRelativeStrength: function() { return []; }
            };

            var testBars = [{ time: 100, open: 10, high: 12, low: 9, close: 11, volume: 100 }];
            activeTimeframe = "1D";
            rsBenchmarkTag = "SPY";

            // 1. Call updateRsPane 10 times consecutively
            for (var i = 0; i < 10; i++) {
                updateRsPane(testBars, []);
            }
            var initialCount = fetchCount;

            // 2. Simulate failure
            failFetch = true;
            // Force reset inFlight by running a failing fetch
            benchmarkLoadState.inFlight = false;
            return loadBenchmarkHistory("SPY")
                .then(function() {}, function() {})
                .then(function() {
                    var countAfterFail = fetchCount;
                    var failedAt = benchmarkLoadState.failedAt;

                    // 3. At +29 seconds: should NOT retry
                    fakeTime = failedAt + 29000;
                    loadBenchmarkHistory("SPY");
                    var countAt29 = fetchCount;

                    // 4. At +31 seconds: should retry
                    fakeTime = failedAt + 31000;
                    loadBenchmarkHistory("SPY");
                    var countAt31 = fetchCount;

                    return {
                        initialCount: initialCount,
                        countAfterFail: countAfterFail,
                        countAt29: countAt29,
                        countAt31: countAt31
                    };
                });
        })()
        """
        res = self._execute_node_eval_async(js_code, load_indicators=True)
        self.assertEqual(res["initialCount"], 1, "10 consecutive calls should produce exactly 1 fetch")
        self.assertEqual(res["countAt29"], res["countAfterFail"], "Should not retry within 29s cooldown")
        self.assertGreater(res["countAt31"], res["countAt29"], "Should retry after 31s cooldown")

    def test_s5_04_benchmark_generational_protection(self):
        """S5-04: Out-of-order benchmark responses are discarded in favor of newest."""
        js_code = """
        (function() {
            var resolvers = [];
            fetch = function(url) {
                return new Promise(function(resolve) {
                    resolvers.push(resolve);
                });
            };

            activeTimeframe = "1D";
            rsBenchmarkTag = "SPY";
            benchmarkLoadState = { key: null, inFlight: false, failedAt: 0, promise: null };

            // Request 1
            var p1 = loadBenchmarkHistory("SPY");
            benchmarkLoadState.inFlight = false; // allow second req for test

            // Request 2
            var p2 = loadBenchmarkHistory("SPY");

            // Resolve Request 2 FIRST with new data
            resolvers[1]({
                ok: true,
                json: function() { return Promise.resolve({ bars: [{ time: 200, close: 500 }] }); }
            });

            return p2.then(function() {
                var barsAfterReq2 = benchmarkRawBars.slice();

                // Resolve Request 1 LATER with stale data
                resolvers[0]({
                    ok: true,
                    json: function() { return Promise.resolve({ bars: [{ time: 100, close: 100 }] }); }
                });

                return p1.then(function() {
                    var barsAfterReq1 = benchmarkRawBars.slice();
                    return {
                        barsAfterReq2: barsAfterReq2,
                        barsAfterReq1: barsAfterReq1
                    };
                });
            });
        })()
        """
        res = self._execute_node_eval_async(js_code, load_indicators=True)
        self.assertEqual(len(res["barsAfterReq2"]), 1)
        self.assertEqual(res["barsAfterReq2"][0]["time"], 200)
        self.assertEqual(res["barsAfterReq1"][0]["time"], 200, "Stale request 1 must not overwrite request 2")

    def test_s5_01_subpane_benchmark_timeframe(self):
        """S5-01: When subpane timeframe is 1W and main chart is 1D, subpane fetches timeframe=1W."""
        js_code = """
        (function() {
            var fetchUrls = [];
            fetch = function(url) {
                fetchUrls.push(url);
                return Promise.resolve({
                    ok: true,
                    json: function() { return Promise.resolve({ bars: [{ time: 100, close: 50 }] }); }
                });
            };

            activeTimeframe = "1D";
            rsBenchmarkTag = "SPY";
            paneTimeframes[2] = "1W";
            subCharts[2] = { bars: [{ time: 100, open: 10, high: 12, low: 9, close: 11, volume: 100 }] };
            subPaneBenchmarkCache = {};

            renderSubPaneRs(2, subCharts[2].bars, null);

            return {
                fetchUrls: fetchUrls
            };
        })()
        """
        res = self._execute_node_eval(js_code, load_indicators=True)
        matched = [u for u in res["fetchUrls"] if "timeframe=1W" in u]
        self.assertTrue(len(matched) > 0, f"Expected fetch with timeframe=1W, got: {res['fetchUrls']}")


if __name__ == "__main__":
    unittest.main()

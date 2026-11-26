import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 參考時間戳（UTC 秒），以 Python zoneinfo 計算
NY_2026_11_23_1047 = 1795448820   # 2026-11-23 10:47 ET
NY_2026_11_23_0900 = 1795442400   # 2026-11-23 09:00 ET（盤前）
NY_2026_11_23_1530 = 1795465800   # 2026-11-23 15:30 ET
UTC_2026_11_23_0000 = 1795392000  # 2026-11-23 00:00 UTC
UTC_2026_11_23_0300 = 1795402800  # 2026-11-23 03:00 UTC

JS_SETUP = """
var renderCalls = [], ranges = [], fetchUrls = [], payloadQueue = [];
renderChartData = function(b) { renderCalls.push(b.length); };
chart = { timeScale: function() { return { getVisibleLogicalRange: function() { return { from: 80, to: 120 }; }, setVisibleLogicalRange: function(r) { ranges.push(r); }, fitContent: function() {} }; } };
renderWatchlist = function() {};
fetch = function(url) { fetchUrls.push(url); var p = payloadQueue.shift(); return Promise.resolve({ ok: true, json: function() { return Promise.resolve(p); } }); };
indicatorConfig.rsLine = false;
currentEngine = "A";
function mkBars(n, t0, step) { var a = []; for (var i = 0; i < n; i++) a.push({ time: t0 + i * step, open: 10, high: 11, low: 9, close: 10.5, volume: 100 }); return a; }
"""


class TestRecheckRecoveryAndMarkets(unittest.TestCase):
    """複查修正（第 4 段補強與第 7 段恢復流程）：

    R1 只有美股交易所才套用紐約交易時段；外匯、期貨等市場用 UTC，不會被當成盤外而丟棄
    R2 十字線時間遇到非時間戳格式時顯示日期，不顯示空白
    R3 狀態恢復：週期已切換就丟棄回應；已有 K 棒時只合併新的部分；保留縮放位置；缺口恢復不覆蓋自選清單
    R4 seq 缺口恢復最多每 15 秒一次；重新連線時強制執行
    R5 focus 與 visibilitychange 2 秒內只恢復一次
    另：1m 分桶以最後一根 K 棒的起點判斷（修正第 4 段造成的既有測試失敗）
    """

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
            self.skipTest("Node.js binary not found in PATH or standard macOS paths; skipping JS VM evaluation")

    def _execute_node_eval(self, eval_js_snippet: str):
        """Loads static/app.js in Node.js VM sandbox and evaluates the provided expression."""
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: () => null,
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

    def _execute_node_eval_async(self, eval_js_snippet: str):
        """Same as _execute_node_eval, but awaits a Promise returned by the snippet before printing."""
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');
        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: () => null,
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
            setTimeout: setTimeout
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(appCode, sandbox);
        Promise.resolve(vm.runInContext({json.dumps(eval_js_snippet)}, sandbox))
            .then((result) => {{ console.log(JSON.stringify(result)); }})
            .catch((err) => {{ console.error(err && err.stack ? err.stack : String(err)); process.exit(3); }});
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

    def test_market_classification(self):
        """R1：美股交易所用紐約時間，其他市場用 UTC。"""
        res = self._execute_node_eval("""
        ({
            nyse: getMarketTimeZone("NYSE:KO"),
            amex: getMarketTimeZone("AMEX:SPY"),
            otc: getMarketTimeZone("OTC:TSLQ"),
            bare: getMarketTimeZone("AAPL"),
            fx: getMarketTimeZone("FX:EURUSD"),
            cme: getMarketTimeZone("CME_MINI:ES1!"),
            cboe: getMarketTimeZone("CBOE:VIX"),
            btc: getMarketTimeZone("BINANCE:BTCUSDT")
        })
        """)
        for key in ("nyse", "amex", "otc", "bare"):
            self.assertEqual(res[key], "America/New_York", key)
        for key in ("fx", "cme", "cboe", "btc"):
            self.assertEqual(res[key], "UTC", key)

    def test_non_us_markets_not_dropped_outside_us_session(self):
        """R1 回歸：外匯在美股盤外的報價不能被丟棄；美股盤前仍照規則忽略。"""
        res = self._execute_node_eval("""
        ({
            fxHour: computeLiveBucket({ symbol: "FX:EURUSD", timeframe: "1h", tickTime: %d, lastBarTime: %d }),
            fxDay: computeLiveBucket({ symbol: "FX:EURUSD", timeframe: "1D", tickTime: %d, lastBarTime: %d }),
            usPremarket: computeLiveBucket({ symbol: "NASDAQ:NVDA", timeframe: "1h", tickTime: %d, lastBarTime: %d })
        })
        """ % (UTC_2026_11_23_0300 + 1500, UTC_2026_11_23_0300,
               UTC_2026_11_23_0300, UTC_2026_11_23_0000,
               NY_2026_11_23_0900, NY_2026_11_23_1530))
        self.assertEqual((res["fxHour"]["action"], res["fxHour"]["time"]), ("update", UTC_2026_11_23_0300))
        self.assertEqual((res["fxDay"]["action"], res["fxDay"]["time"]), ("update", UTC_2026_11_23_0000))
        self.assertEqual((res["usPremarket"]["action"], res["usPremarket"]["reason"]), ("ignore", "outside_session"))

    def test_crosshair_time_fallback(self):
        """R2：BusinessDay 物件或日期字串顯示日期；時間戳照常顯示交易所時間。"""
        res = self._execute_node_eval("""
        ({
            obj: formatMarketTime({ year: 2026, month: 11, day: 3 }, "NASDAQ:NVDA", "1D"),
            str: formatMarketTime("2026-11-03", "NASDAQ:NVDA", "1D"),
            nul: formatMarketTime(null, "NASDAQ:NVDA", "1D"),
            num: formatMarketTime(%d, "NASDAQ:NVDA", "5m")
        })
        """ % NY_2026_11_23_1047)
        self.assertEqual(res["obj"], "2026-11-03")
        self.assertEqual(res["str"], "2026-11-03")
        self.assertEqual(res["nul"], "")
        self.assertEqual(res["num"], "2026-11-23 10:47 ET")

    def test_merge_recovery_delta_bars(self):
        """R3：同時間取代、較新的加在後面、較舊與無效的略過、不修改傳入的陣列。"""
        res = self._execute_node_eval("""
        (function() {
            var ex = [{ time: 100, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }, { time: 160, open: 1.5, high: 2.5, low: 1.4, close: 2, volume: 5 }];
            var before = JSON.stringify(ex);
            return {
                basic: mergeRecoveryDeltaBars(ex, [{ time: 220, open: 2, high: 3, low: 1.9, close: 2.8, volume: 7 }, { time: 160, open: 1.5, high: 2.6, low: 1.4, close: 2.1, volume: 9 }]),
                olderLen: mergeRecoveryDeltaBars(ex, [{ time: 40, open: 1, high: 1, low: 1, close: 1, volume: 1 }]).length,
                invalidLen: mergeRecoveryDeltaBars(ex, [{ time: 0, open: 1, high: 1, low: 1, close: 1 }, { time: 300, open: 0, high: 1, low: 1, close: 1 }, { time: 400, open: "x", high: 1, low: 1, close: 1 }]).length,
                msTime: mergeRecoveryDeltaBars(ex, [{ time: 1795448820000, open: 2, high: 3, low: 1.9, close: 2.8, volume: 7 }])[2].time,
                notMutated: JSON.stringify(ex) === before
            };
        })()
        """)
        self.assertEqual([b["time"] for b in res["basic"]], [100, 160, 220])
        self.assertEqual(res["basic"][1]["close"], 2.1)
        self.assertEqual(res["basic"][1]["volume"], 9)
        self.assertEqual(res["olderLen"], 2)
        self.assertEqual(res["invalidLen"], 2)
        # 毫秒時間戳（13 位數）要轉成秒；220000 這種 6 位數本來就是秒，不轉換
        self.assertEqual(res["msTime"], 1795448820)
        self.assertTrue(res["notMutated"])

    def test_recovery_merges_delta_keeps_view_and_watchlist(self):
        """R3：1m 缺口恢復只合併新 K 棒、最後一根一起更新、縮放跟著右移、不覆蓋自選清單。"""
        res = self._execute_node_eval_async(JS_SETUP + """
        (async function() {
            activeSymbol = "BINANCE:BTCUSDT"; activeTimeframe = "1m";
            currentRawBars = mkBars(120, 6000, 60);
            symbolsList = [{ id: "BINANCE:BTCUSDT" }, { id: "LOCAL:ONLY" }];
            var lastT = currentRawBars[119].time;
            payloadQueue.push({ status: "ok", bars: mkBars(300, 0, 60),
                delta_bars: [{ time: lastT, open: 10, high: 12, low: 9, close: 11.9, volume: 160 },
                             { time: lastT + 60, open: 11.9, high: 12.2, low: 11.8, close: 12, volume: 40 }],
                watchlist: [{ id: "SERVER:LIST" }] });
            await recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe, includeWatchlist: false });
            return { url: fetchUrls[0], lastT: lastT, len: currentRawBars.length, replacedClose: currentRawBars[119].close,
                     newClose: currentRawBars[120].close, watchlistLen: symbolsList.length, renders: renderCalls, range: ranges[0] };
        })()
        """)
        self.assertIn("since_time=" + str(res["lastT"] - 1), res["url"])
        self.assertEqual(res["len"], 121)
        self.assertEqual(res["replacedClose"], 11.9)
        self.assertEqual(res["newClose"], 12)
        self.assertEqual(res["watchlistLen"], 2)
        self.assertEqual(res["renders"], [121])
        self.assertEqual(res["range"], {"from": 81, "to": 121})

    def test_recovery_ignores_stale_timeframe_and_uses_exclusive_since_for_larger_tf(self):
        """R3：較大週期的 since_time 等於最後一根；回應回來時週期已變就不套用。"""
        res = self._execute_node_eval_async(JS_SETUP + """
        (async function() {
            activeSymbol = "BINANCE:BTCUSDT"; activeTimeframe = "1h";
            currentRawBars = mkBars(50, 3600, 3600);
            payloadQueue.push({ status: "ok", bars: mkBars(300, 0, 3600), delta_bars: [], watchlist: [] });
            await recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe });
            var hourUrl = fetchUrls[0];
            var hourLen = currentRawBars.length;

            activeTimeframe = "5m";
            currentRawBars = mkBars(10, 300, 300);
            payloadQueue.push({ status: "ok", bars: mkBars(300, 0, 300), delta_bars: mkBars(3, 3300, 300), watchlist: [] });
            var p = recoverTerminalState({ symbol: activeSymbol, timeframe: "5m" });
            activeTimeframe = "1h";
            await p;
            return { hourUrl: hourUrl, hourLen: hourLen, staleLen: currentRawBars.length, renders: renderCalls.length };
        })()
        """)
        self.assertIn("since_time=" + str(3600 + 49 * 3600), res["hourUrl"])
        self.assertEqual(res["hourLen"], 50)
        self.assertEqual(res["staleLen"], 10)
        self.assertEqual(res["renders"], 0)

    def test_recovery_full_replace_when_no_local_bars(self):
        """R3：本地沒有 K 棒時整批套用，並照舊更新自選清單。"""
        res = self._execute_node_eval_async(JS_SETUP + """
        (async function() {
            activeSymbol = "BINANCE:BTCUSDT"; activeTimeframe = "1D"; currentRawBars = [];
            symbolsList = [];
            payloadQueue.push({ status: "ok", bars: mkBars(250, 86400, 86400), delta_bars: [], watchlist: [{ id: "A:1" }, { id: "B:2" }, { id: "C:3" }] });
            await recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe });
            return { url: fetchUrls[0], len: currentRawBars.length, watchlistLen: symbolsList.length, ranges: ranges.length };
        })()
        """)
        self.assertNotIn("since_time", res["url"])
        self.assertEqual(res["len"], 250)
        self.assertEqual(res["watchlistLen"], 3)
        self.assertEqual(res["ranges"], 0)

    def test_one_minute_bucket_uses_last_bar_start(self):
        """第 4 段回歸：1m 以最後一根 K 棒自己的起點判斷，歷史時間沒有對齊整分鐘也不會把當根丟掉。

        對應既有測試 test_challenger_m3_1::test_live_stream_backwards_timestamp_rejection（1000 為當根、1060 為下一根）。
        """
        res = self._execute_node_eval("""
        ({
            same: computeLiveBucket({ symbol: "BINANCE:BTCUSDT", timeframe: "1m", tickTime: 1000, lastBarTime: 1000 }),
            quoteInside: computeLiveBucket({ symbol: "BINANCE:BTCUSDT", timeframe: "1m", tickTime: 1045, lastBarTime: 1000 }),
            next: computeLiveBucket({ symbol: "BINANCE:BTCUSDT", timeframe: "1m", tickTime: 1060, lastBarTime: 1000 }),
            skipped: computeLiveBucket({ symbol: "BINANCE:BTCUSDT", timeframe: "1m", tickTime: 1150, lastBarTime: 1000 }),
            stale: computeLiveBucket({ symbol: "BINANCE:BTCUSDT", timeframe: "1m", tickTime: 900, lastBarTime: 1000 }),
            noHistory: computeLiveBucket({ symbol: "BINANCE:BTCUSDT", timeframe: "1m", tickTime: 1045, lastBarTime: null })
        })
        """)
        self.assertEqual((res["same"]["action"], res["same"]["time"]), ("update", 1000))
        self.assertEqual((res["quoteInside"]["action"], res["quoteInside"]["time"]), ("update", 1000))
        self.assertEqual((res["next"]["action"], res["next"]["time"]), ("new", 1060))
        self.assertEqual((res["skipped"]["action"], res["skipped"]["time"]), ("new", 1120))
        self.assertEqual((res["stale"]["action"], res["stale"]["reason"]), ("ignore", "stale"))
        self.assertEqual((res["noHistory"]["action"], res["noHistory"]["time"]), ("new", 1020))

    def test_gap_recovery_throttle_and_focus_dedupe(self):
        """R4／R5：缺口恢復 15 秒內只一次（強制除外）；focus 與 visibilitychange 2 秒內只一次。"""
        res = self._execute_node_eval_async("""
        (async function() {
            var calls = 0;
            recoverTerminalState = function() { calls++; return Promise.resolve(null); };
            activeSymbol = "NASDAQ:NVDA"; activeTimeframe = "1D";
            _lastGapRecoveryAt = 0; _gapRecoveryInFlight = false;
            var wait = function() { return new Promise(function(r) { setTimeout(r, 5); }); };
            triggerGapRecovery(); await wait();
            triggerGapRecovery(); await wait();
            triggerGapRecovery(true); await wait();
            var gapCalls = calls;
            calls = 0; _focusRecoveryAt = 0;
            requestFocusRecovery(); requestFocusRecovery();
            return { gapCalls: gapCalls, focusCalls: calls };
        })()
        """)
        self.assertEqual(res["gapCalls"], 2)
        self.assertEqual(res["focusCalls"], 1)

    def test_static_wiring(self):
        """R4／R5：重新連線強制恢復；focus、visibilitychange 都走去重函式；回到前景重畫保留縮放。"""
        content = self.app_js_path.read_text(encoding="utf-8")
        ws_start = content.find("ws.onopen = function()")
        ws_end = content.find("ws.onclose = function()", ws_start)
        self.assertIn("triggerGapRecovery(true);", content[ws_start:ws_end])
        self.assertIn('window.addEventListener("focus", function() {\n    requestFocusRecovery();', content)
        self.assertIn("renderChartDataKeepView(currentRawBars, currentRawBars.length);", content)
        self.assertEqual(content.count("recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe, includeWatchlist: false });"), 1)


if __name__ == "__main__":
    unittest.main()

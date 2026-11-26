import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Reference timestamps (UTC seconds) computed with Python zoneinfo, America/New_York and UTC.
E = {
    "ny_0306_0930": 1772807400,
    "ny_0309_0930": 1773063000,
    "ny_1102_0930": 1793629800,
    "ny_1123_1030": 1795447800,
    "ny_1123_1047": 1795448820,
    "ny_1123_1130": 1795451400,
    "ny_1123_1145": 1795452300,
    "ny_1123_1559": 1795467540,
    "ny_1123_1530": 1795465800,
    "ny_1123_0900": 1795442400,
    "ny_1123_1600": 1795467600,
    "ny_1123_1045": 1795448700,
    "ny_1120_0930": 1795185000,
    "ny_1123_1000": 1795446000,
    "ny_1123_0930": 1795444200,
    "ny_1120_0000": 1795150800,
    "ny_1123_0000": 1795410000,
    "ny_1123_1630": 1795469400,
    "ny_1116_0930": 1794839400,
    "ny_1120_1100": 1795190400,
    "ny_1122_1200": 1795366800,
    "ny_0309_1047": 1773067620,
    "ny_0309_1030": 1773066600,
    "utc_1123_1547": 1795448820,
    "utc_1123_1500": 1795446000,
    "utc_1123_1605": 1795449900,
    "utc_1123_1600": 1795449600,
    "utc_1123_2359": 1795478340,
    "utc_1123_0000": 1795392000,
    "utc_1124_0001": 1795478460,
    "utc_1124_0000": 1795478400,
    "utc_1125_1200": 1795608000,
    "utc_1123_mon0000": 1795392000
}


class TestSection4BarTime(unittest.TestCase):
    """第 4 段：前端 K 棒時間與分桶（static/app.js）。

    1. 美股依紐約交易時段分桶：1h K 從 :30 起算、日 K／週 K 依紐約交易日、盤前盤後不併入 >1m 的 K 棒
    2. 加密貨幣沿用 UTC 分桶
    3. 夏令時間切換前後都正確
    4. 1 分鐘 K 棒併入較大週期時，同一分鐘取代、換分鐘累加，不再用 Math.max，也不會重複計算
    5. 時間軸／十字線顯示交易所時間（ET 或 UTC）
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

    def _bucket(self, symbol, tf, tick, last):
        return self._execute_node_eval(
            "computeLiveBucket(" + json.dumps({"symbol": symbol, "timeframe": tf, "tickTime": tick, "lastBarTime": last}) + ")"
        )

    def test_zoned_wall_to_epoch_handles_dst(self):
        """紐約 09:30 在夏令時間切換前後對應不同的 UTC 時間。"""
        res = self._execute_node_eval("""
        ({
            pre: zonedWallToEpoch(2026, 3, 6, 9, 30, "America/New_York"),
            post: zonedWallToEpoch(2026, 3, 9, 9, 30, "America/New_York"),
            nov: zonedWallToEpoch(2026, 11, 2, 9, 30, "America/New_York")
        })
        """)
        self.assertEqual(res["pre"], E["ny_0306_0930"])
        self.assertEqual(res["post"], E["ny_0309_0930"])
        self.assertEqual(res["nov"], E["ny_1102_0930"])

    def test_us_intraday_buckets_follow_new_york_session(self):
        """1h K 從 :30 起算；盤前與 16:00 之後忽略；5m 正常對齊。"""
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_1123_1047"], E["ny_1123_1030"])
        self.assertEqual((r["action"], r["time"]), ("update", E["ny_1123_1030"]))
        # 回歸：舊版用 UTC 整點，11:45 會被放進 11:00；正確是 11:30
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_1123_1145"], E["ny_1123_1030"])
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_1123_1130"]))
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_1123_1559"], E["ny_1123_1530"])
        self.assertEqual((r["action"], r["time"]), ("update", E["ny_1123_1530"]))
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_1123_0900"], E["ny_1123_1530"])
        self.assertEqual((r["action"], r["reason"]), ("ignore", "outside_session"))
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_1123_1600"], E["ny_1123_1530"])
        self.assertEqual((r["action"], r["reason"]), ("ignore", "outside_session"))
        r = self._bucket("NASDAQ:NVDA", "5m", E["ny_1123_1047"], None)
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_1123_1045"]))
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_0309_1047"], None)
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_0309_1030"]))

    def test_us_daily_and_weekly_buckets(self):
        """日 K 依紐約交易日、沿用歷史時間戳慣例；盤後與週末忽略；週 K 依週一。"""
        r = self._bucket("NASDAQ:NVDA", "1D", E["ny_1123_1000"], E["ny_1120_0930"])
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_1123_0930"]))
        r = self._bucket("NASDAQ:NVDA", "1D", E["ny_1123_1559"], E["ny_1123_0930"])
        self.assertEqual((r["action"], r["time"]), ("update", E["ny_1123_0930"]))
        r = self._bucket("NASDAQ:NVDA", "1D", E["ny_1123_1000"], E["ny_1120_0000"])
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_1123_0000"]))
        r = self._bucket("NASDAQ:NVDA", "1D", E["ny_1123_1630"], E["ny_1123_0930"])
        self.assertEqual((r["action"], r["reason"]), ("ignore", "outside_session"))
        r = self._bucket("NASDAQ:NVDA", "1D", E["ny_1122_1200"], E["ny_1120_0930"])
        self.assertEqual((r["action"], r["reason"]), ("ignore", "outside_session"))
        r = self._bucket("NASDAQ:NVDA", "1W", E["ny_1120_1100"], E["ny_1116_0930"])
        self.assertEqual((r["action"], r["time"]), ("update", E["ny_1116_0930"]))
        r = self._bucket("NASDAQ:NVDA", "1W", E["ny_1123_1000"], E["ny_1116_0930"])
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_1123_0930"]))

    def test_crypto_and_one_minute_buckets(self):
        """加密貨幣用 UTC；1m 不受交易時段限制；無效與過舊的時間被忽略。"""
        r = self._bucket("BINANCE:BTCUSDT", "1h", E["utc_1123_1547"], E["utc_1123_1500"])
        self.assertEqual((r["action"], r["time"]), ("update", E["utc_1123_1500"]))
        r = self._bucket("BINANCE:BTCUSDT", "1h", E["utc_1123_1605"], E["utc_1123_1500"])
        self.assertEqual((r["action"], r["time"]), ("new", E["utc_1123_1600"]))
        r = self._bucket("BINANCE:BTCUSDT", "1D", E["utc_1123_2359"], E["utc_1123_0000"])
        self.assertEqual((r["action"], r["time"]), ("update", E["utc_1123_0000"]))
        r = self._bucket("BINANCE:BTCUSDT", "1D", E["utc_1124_0001"], E["utc_1123_0000"])
        self.assertEqual((r["action"], r["time"]), ("new", E["utc_1124_0000"]))
        r = self._bucket("NASDAQ:NVDA", "1m", E["ny_1123_1047"] + 60, E["ny_1123_1047"])
        self.assertEqual((r["action"], r["time"]), ("new", E["ny_1123_1047"] + 60))
        r = self._bucket("NASDAQ:NVDA", "1m", E["ny_1123_1047"] - 60, E["ny_1123_1047"])
        self.assertEqual((r["action"], r["reason"]), ("ignore", "stale"))
        r = self._bucket("NASDAQ:NVDA", "1m", E["ny_1123_0900"], None)
        self.assertEqual(r["action"], "new")
        r = self._bucket("NASDAQ:NVDA", "1h", 0, None)
        self.assertEqual((r["action"], r["reason"]), ("ignore", "invalid_time"))
        r = self._bucket("NASDAQ:NVDA", "1h", E["ny_1123_1047"] * 1000, E["ny_1123_1030"])
        self.assertEqual((r["action"], r["time"]), ("update", E["ny_1123_1030"]))

    def test_minute_bars_accumulate_volume(self):
        """同一分鐘取代、換分鐘累加；舊分鐘丟棄；1m 不重複計算歷史量。"""
        res = self._execute_node_eval("""
        (function() {
            var hist = { time: 1000, open: 100, high: 101, low: 99, close: 100.5, volume: 1000 };
            var s1 = applyMinuteBarToBucket(null, { bucketTime: 1000, timeframe: "5m", minute: { time: 1060, open: 100.5, high: 102, low: 100.4, close: 101.8, volume: 10 }, historyBar: hist });
            var s2 = applyMinuteBarToBucket(s1, { bucketTime: 1000, timeframe: "5m", minute: { time: 1060, open: 100.5, high: 102.2, low: 100.4, close: 102.1, volume: 25 }, historyBar: hist });
            var s3 = applyMinuteBarToBucket(s2, { bucketTime: 1000, timeframe: "5m", minute: { time: 1120, open: 102.1, high: 102.3, low: 98.5, close: 99, volume: 5 }, historyBar: hist });
            var s4 = applyMinuteBarToBucket(s3, { bucketTime: 1000, timeframe: "5m", minute: { time: 1060, open: 1, high: 1, low: 1, close: 1, volume: 999 }, historyBar: hist });
            var sNew = applyMinuteBarToBucket(null, { bucketTime: 1300, timeframe: "5m", minute: { time: 1300, open: 103, high: 104, low: 102.5, close: 103.5, volume: 7 }, historyBar: null });
            var sM1 = applyMinuteBarToBucket(null, { bucketTime: 1060, timeframe: "1m", minute: { time: 1060, open: 100, high: 101, low: 99.5, close: 100.7, volume: 60 }, historyBar: { time: 1060, open: 100, high: 100.9, low: 99.8, close: 100.6, volume: 50 } });
            return { s1: s1, s2: s2, s3: s3, s4: s4, sNew: sNew, sM1: sM1 };
        })()
        """)
        self.assertEqual(res["s1"]["volume"], 1010)
        self.assertEqual(res["s1"]["open"], 100)
        self.assertEqual(res["s1"]["high"], 102)
        self.assertEqual(res["s2"]["volume"], 1025)
        self.assertEqual(res["s3"]["volume"], 1030)
        self.assertEqual(res["s3"]["low"], 98.5)
        self.assertIsNone(res["s4"])
        self.assertEqual((res["sNew"]["open"], res["sNew"]["high"], res["sNew"]["low"], res["sNew"]["volume"]), (103, 104, 102.5, 7))
        self.assertEqual(res["sM1"]["volume"], 60)

    def test_market_time_formatting(self):
        """十字線與時間軸用交易所時間。"""
        res = self._execute_node_eval("""
        ({
            usIntraday: formatMarketTime(%d, "NASDAQ:NVDA", "5m"),
            usDaily: formatMarketTime(%d, "NASDAQ:NVDA", "1D"),
            usDailyMidnight: formatMarketTime(%d, "NASDAQ:NVDA", "1D"),
            cryptoIntraday: formatMarketTime(%d, "BINANCE:BTCUSDT", "1h"),
            tickTime: formatMarketTickMark(%d, 3, "NASDAQ:NVDA", "zh"),
            tickMonthZh: formatMarketTickMark(%d, 1, "NASDAQ:NVDA", "zh"),
            tickMonthEn: formatMarketTickMark(%d, 1, "NASDAQ:NVDA", "en"),
            tickNonNumeric: formatMarketTickMark({ year: 2026, month: 11, day: 23 }, 2, "NASDAQ:NVDA", "zh")
        })
        """ % (E["ny_1123_1047"], E["ny_1123_0930"], E["ny_1123_0000"], E["utc_1123_1547"],
               E["ny_1123_1047"], E["ny_1123_0930"], E["ny_1123_0930"]))
        self.assertEqual(res["usIntraday"], "2026-11-23 10:47 ET")
        self.assertEqual(res["usDaily"], "2026-11-23")
        self.assertEqual(res["usDailyMidnight"], "2026-11-23")
        self.assertEqual(res["cryptoIntraday"], "2026-11-23 15:47 UTC")
        self.assertEqual(res["tickTime"], "10:47")
        self.assertEqual(res["tickMonthZh"], "11月")
        self.assertEqual(res["tickMonthEn"], "Nov")
        self.assertIsNone(res["tickNonNumeric"])

    def test_static_invariants(self):
        """執行 updateQuote 與 formatMarketTickMark 驗證報價累計量不會覆蓋 K 棒量，且格式化函式可用。"""
        res = self._execute_node_eval("""
        (function() {
            var updatedBars = [];
            activeSymbol = "BINANCE:BTCUSDT";
            activeTimeframe = "1h";
            isHistoryLoading = false;
            currentCandleStyle = "solid";
            var barTime = %d;
            var initialBar = { time: barTime, open: 50000, high: 50100, low: 49900, close: 50050, volume: 12.5 };
            currentRawBars = [initialBar];
            lastBarTime = barTime;
            lastBarObj = Object.assign({}, initialBar);
            latestBarData = initialBar;
            candleSeries = {
                update: function(b) { updatedBars.push(b); },
                data: function() { return currentRawBars; }
            };
            updateQuote("BINANCE:BTCUSDT", { price: 50150, volume: 999999, time: barTime + 15 });
            return {
                updatedCount: updatedBars.length,
                updatedClose: updatedBars[0] ? updatedBars[0].close : null,
                lastBarVol: lastBarObj.volume,
                rawBarVol: currentRawBars[0].volume,
                tickLabel: formatMarketTickMark(barTime, 3, "BINANCE:BTCUSDT", "zh")
            };
        })()
        """ % E["utc_1123_1500"])
        self.assertEqual(res["updatedCount"], 1)
        self.assertEqual(res["updatedClose"], 50150)
        self.assertEqual(res["lastBarVol"], 12.5)
        self.assertEqual(res["rawBarVol"], 12.5)
        self.assertEqual(res["tickLabel"], "15:00")


if __name__ == "__main__":
    unittest.main()

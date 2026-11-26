import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestP1P2Hardening(unittest.TestCase):
    """TDD regression suite for the 4 P1/P2 hardening fixes:
    1. WebSocket reconnect gap-fill recovery and bar sequence gap detection.
    2. Intraday VWAP daily session reset (America/New_York for US equities, UTC for crypto).
    3. Deepvue HUD 52W High windowing (252D / 52W) and intraday Range High label accuracy.
    4. Removal of ?token= query parameter from REST /api/history requests in loadSubChart.
    """

    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
        cls.indicators_js_path = PROJECT_ROOT / "static" / "indicators.js"
        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )

    def _run_node(self, eval_js: str, load_app: bool = True, load_indicators: bool = True):
        if not self.node_bin:
            self.skipTest("Node.js binary not found")
        ind_path = json.dumps(str(self.indicators_js_path))
        app_path = json.dumps(str(self.app_js_path))
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: () => null,
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}}, setAttribute: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', host: '127.0.0.1:8000', protocol: 'http:' }},
            fetch: () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ status: 'ok', bars: [] }}) }}),
            AbortController: function() {{ this.signal = {{}}; this.abort = function() {{}}; }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            console: console,
            Intl: Intl
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;
        vm.createContext(sandbox);
        if ({'true' if load_indicators else 'false'}) {{
            vm.runInContext(fs.readFileSync({ind_path}, 'utf8'), sandbox);
        }}
        if ({'true' if load_app else 'false'}) {{
            vm.runInContext(fs.readFileSync({app_path}, 'utf8'), sandbox);
        }}
        const res = vm.runInContext({json.dumps(eval_js)}, sandbox);
        console.log(JSON.stringify(res));
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
            f"Node VM failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        return json.loads(proc.stdout.strip())

    def test_intraday_vwap_resets_across_us_trading_days(self):
        """Intraday VWAP must reset at each new NY trading day for US stocks, while daily VWAP stays cumulative."""
        res = self._run_node("""
        (function() {
            // Day 1: 2026-11-19 15:55 ET (20:55 UTC) -> 1795121700
            // Day 2: 2026-11-20 09:30 ET (14:30 UTC) -> 1795185000
            // Day 2: 2026-11-20 09:35 ET (14:35 UTC) -> 1795185300
            var bars = [
                { time: 1795121700, high: 100, low: 100, close: 100, volume: 10000 },
                { time: 1795185000, high: 200, low: 200, close: 200, volume: 100 },
                { time: 1795185300, high: 210, low: 210, close: 210, volume: 100 }
            ];
            var intraday = Indicators.calcVWAP(bars, { timeframe: "5m", symbol: "NASDAQ:NVDA" });
            var daily = Indicators.calcVWAP(bars, { timeframe: "1D", symbol: "NASDAQ:NVDA" });
            return { intraday: intraday, daily: daily };
        })()
        """, load_app=False, load_indicators=True)

        # Day 2 bar 1 must reset to 200.0 (not dragged down to ~100.99 by Day 1's 10000 vol)
        self.assertAlmostEqual(res["intraday"][0]["value"], 100.0, places=4)
        self.assertAlmostEqual(res["intraday"][1]["value"], 200.0, places=4)
        self.assertAlmostEqual(res["intraday"][2]["value"], 205.0, places=4)
        # Daily timeframe remains cumulative across bars
        self.assertLess(res["daily"][1]["value"], 105.0)

    def test_ws_sequence_gap_and_reconnect_recovery(self):
        """Verifies checkBarSequenceGap detects skipped seq and ws.onopen triggers recovery on reconnect."""
        res = self._run_node("""
        (function() {
            var s1 = checkBarSequenceGap("NASDAQ:NVDA", 10);
            var s2 = checkBarSequenceGap("NASDAQ:NVDA", 11);
            var s3 = checkBarSequenceGap("NASDAQ:NVDA", 14);
            var s4 = checkBarSequenceGap("NASDAQ:NVDA", 14);
            var s5 = checkBarSequenceGap("NASDAQ:NVDA", 1);
            return { s1: s1, s2: s2, s3: s3, s4: s4, s5: s5 };
        })()
        """, load_app=True, load_indicators=False)

        self.assertFalse(res["s1"]["gap"])
        self.assertFalse(res["s2"]["gap"])
        self.assertTrue(res["s3"]["gap"])
        self.assertEqual(res["s3"]["expected"], 12)
        self.assertEqual(res["s3"]["received"], 14)
        self.assertFalse(res["s4"]["gap"])
        self.assertFalse(res["s5"]["gap"])

        app_js = self.app_js_path.read_text(encoding="utf-8")
        ws_start = app_js.find("ws.onopen = function()")
        ws_end = app_js.find("ws.onclose = function()", ws_start)
        ws_body = app_js[ws_start:ws_end]
        self.assertIn("triggerGapRecovery(", ws_body)
        self.assertIn("checkBarSequenceGap(", ws_body)

    def test_hud_high_label_and_52w_windowing(self):
        """Verifies intraday HUD labels 'Range High'/'距區間高' and daily 52W High only scans last 252 bars."""
        res = self._run_node("""
        (function() {
            var labelEl = { textContent: "", title: "" };
            var valEl = { textContent: "", className: "" };
            document.getElementById = function(id) {
                if (id === "hud52wLabel") return labelEl;
                if (id === "hud52wVal") return valEl;
                return null;
            };

            // 1. Intraday 5m should switch label to Range High / 距區間高
            currentLang = "zh";
            activeTimeframe = "5m";
            updateDeepvueHud([
                { time: 1, high: 120, low: 100, close: 110, volume: 100 },
                { time: 2, high: 115, low: 105, close: 108, volume: 100 }
            ]);
            var intradayZh = { label: labelEl.textContent, val: valEl.textContent };

            currentLang = "en";
            updateDeepvueHud([
                { time: 1, high: 120, low: 100, close: 110, volume: 100 },
                { time: 2, high: 115, low: 105, close: 108, volume: 100 }
            ]);
            var intradayEn = { label: labelEl.textContent, val: valEl.textContent };

            // 2. Daily 1D with 300 bars: bar 0 has ancient high=500 (older than 252 trading days),
            // last 252 bars have max high=100 and latest close=100 -> must report NEW HIGH, not -80%!
            currentLang = "zh";
            activeTimeframe = "1D";
            var bars300 = [];
            for (var i = 0; i < 300; i++) {
                bars300.push({
                    time: 1700000000 + i * 86400,
                    high: i === 0 ? 500 : 100,
                    low: 90,
                    close: 100,
                    volume: 1000
                });
            }
            updateDeepvueHud(bars300);
            var daily52w = { label: labelEl.textContent, val: valEl.textContent };

            return { intradayZh: intradayZh, intradayEn: intradayEn, daily52w: daily52w };
        })()
        """, load_app=True, load_indicators=False)

        self.assertEqual(res["intradayZh"]["label"], "距區間高")
        self.assertEqual(res["intradayEn"]["label"], "Range High")
        self.assertEqual(res["daily52w"]["label"], "距52週高")
        self.assertEqual(res["daily52w"]["val"], "NEW HIGH")

    def test_load_sub_chart_does_not_leak_token_in_url(self):
        """Verifies loadSubChart relies on X-Session-Token header instead of appending &token= to /api/history URL."""
        app_js = self.app_js_path.read_text(encoding="utf-8")
        start = app_js.find("function loadSubChart(")
        end = app_js.find("function applySplitLayout(", start)
        sub_body = app_js[start:end]
        self.assertNotIn("&token=", sub_body)
        self.assertNotIn("?token=", sub_body)


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestSection11PositionSizing(unittest.TestCase):
    """Verifies Section 11 position sizing, single risk-dollar source, and R:R tool logic in static/app.js:
    1. parseRiskDollar & getRiskDollar validation and fallback behavior
    2. computeRiskRewardView long/short calculations, precision, and invalid-side rejection
       (including regression test for entry=100, target=115, stop=103 which previously clamped to 50,000 shares)
    3. computeAtrPositionSizing daily-only enforcement, stop-below-zero, and invalid input handling
    4. Static invariants on static/app.js (removal of legacy Math.max(0.01) clamping, hardcoded riskDollar,
       preset risk override, and local var timeScale declaration in mousemove handler)
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

    def test_parse_risk_dollar(self):
        """Validates parseRiskDollar accepts positive finite numbers/strings <= 10M and rejects invalid inputs."""
        res = self._execute_node_eval("""
        ({
            strInt: parseRiskDollar("750"),
            numFloat: parseRiskDollar(250.5),
            zeroStr: parseRiskDollar("0"),
            negStr: parseRiskDollar("-5"),
            alphaStr: parseRiskDollar("abc"),
            emptyStr: parseRiskDollar(""),
            boolTrue: parseRiskDollar(true),
            nullVal: parseRiskDollar(null),
            overflowStr: parseRiskDollar("1e309"),
            aboveCap: parseRiskDollar(20000000)
        })
        """)
        self.assertEqual(res["strInt"], 750)
        self.assertEqual(res["numFloat"], 250.5)
        self.assertIsNone(res["zeroStr"])
        self.assertIsNone(res["negStr"])
        self.assertIsNone(res["alphaStr"])
        self.assertIsNone(res["emptyStr"])
        self.assertIsNone(res["boolTrue"])
        self.assertIsNone(res["nullVal"])
        self.assertIsNone(res["overflowStr"])
        self.assertIsNone(res["aboveCap"])

    def test_get_risk_dollar_default(self):
        """Returns 500 when DOM input is absent and localStorage is empty."""
        res = self._execute_node_eval("getRiskDollar()")
        self.assertEqual(res, 500)

    def test_compute_risk_reward_view(self):
        """Verifies long/short R:R calculations, low-price precision, and invalid-side rejection.

        Regression note: entry=100, target=115, stop=103 previously clamped stopDist to 0.01
        and displayed 50,000 shares. It must now return side=null, shares=0, and a non-empty error.
        """
        res = self._execute_node_eval("""
        ({
            longStd: computeRiskRewardView({ entry: 100, target: 115, stop: 94, riskDollar: 500 }),
            shortStd: computeRiskRewardView({ entry: 100, target: 85, stop: 106, riskDollar: 500 }),
            lowPrice: computeRiskRewardView({ entry: 0.05, target: 0.06, stop: 0.045, riskDollar: 500 }),
            decimalPrecision: computeRiskRewardView({ entry: 2.02, target: 2.10, stop: 2.01, riskDollar: 500 }),
            invalidBothAbove: computeRiskRewardView({ entry: 100, target: 115, stop: 103, riskDollar: 500 }),
            invalidBothBelow: computeRiskRewardView({ entry: 100, target: 85, stop: 94, riskDollar: 500 }),
            invalidEntryZero: computeRiskRewardView({ entry: 0, target: 115, stop: 94, riskDollar: 500 }),
            longRisk750: computeRiskRewardView({ entry: 100, target: 115, stop: 94, riskDollar: 750 })
        })
        """)

        long_std = res["longStd"]
        self.assertEqual(long_std["side"], "long")
        self.assertIsNone(long_std["error"])
        self.assertAlmostEqual(long_std["rewardPct"], 15.0, places=6)
        self.assertAlmostEqual(long_std["riskPct"], 6.0, places=6)
        self.assertAlmostEqual(long_std["rr"], 2.5, places=6)
        self.assertEqual(long_std["shares"], 83)
        self.assertAlmostEqual(long_std["stopDistance"], 6.0, places=6)

        short_std = res["shortStd"]
        self.assertEqual(short_std["side"], "short")
        self.assertIsNone(short_std["error"])
        self.assertAlmostEqual(short_std["rewardPct"], 15.0, places=6)
        self.assertAlmostEqual(short_std["riskPct"], 6.0, places=6)
        self.assertAlmostEqual(short_std["rr"], 2.5, places=6)
        self.assertEqual(short_std["shares"], 83)
        self.assertAlmostEqual(short_std["stopDistance"], 6.0, places=6)

        low_price = res["lowPrice"]
        self.assertEqual(low_price["side"], "long")
        self.assertIsNone(low_price["error"])
        self.assertEqual(low_price["shares"], 100000)
        self.assertAlmostEqual(low_price["stopDistance"], 0.005, places=6)

        dec_prec = res["decimalPrecision"]
        self.assertEqual(dec_prec["side"], "long")
        self.assertIsNone(dec_prec["error"])
        self.assertEqual(dec_prec["shares"], 50000)
        self.assertAlmostEqual(dec_prec["stopDistance"], 0.01, places=6)

        for key in ("invalidBothAbove", "invalidBothBelow", "invalidEntryZero"):
            inv = res[key]
            self.assertIsNone(inv["side"], f"{key} should have side=None")
            self.assertTrue(bool(inv["error"]), f"{key} should have non-empty error")
            self.assertIsNone(inv["rewardPct"])
            self.assertIsNone(inv["riskPct"])
            self.assertIsNone(inv["rr"])
            self.assertEqual(inv["shares"], 0)
            self.assertIsNone(inv["stopDistance"])

        self.assertEqual(res["longRisk750"]["shares"], 125)

    def test_compute_atr_position_sizing(self):
        """Verifies computeAtrPositionSizing daily-only guard, stop-below-zero, and valid ATR sizing."""
        res = self._execute_node_eval("""
        ({
            std1D: computeAtrPositionSizing({ close: 100, atr: 2.5, stopMult: 2.0, riskDollar: 500, timeframe: "1D" }),
            lowPrice1D: computeAtrPositionSizing({ close: 0.5, atr: 0.004, stopMult: 2.0, riskDollar: 500, timeframe: "1D" }),
            floatAtr1D: computeAtrPositionSizing({ close: 123.45, atr: 2.345678, stopMult: 2.0, riskDollar: 500, timeframe: "1D" }),
            tf1m: computeAtrPositionSizing({ close: 100, atr: 2.5, stopMult: 2.0, riskDollar: 500, timeframe: "1m" }),
            tf1W: computeAtrPositionSizing({ close: 100, atr: 2.5, stopMult: 2.0, riskDollar: 500, timeframe: "1W" }),
            stopBelowZero: computeAtrPositionSizing({ close: 1, atr: 1, stopMult: 2.0, riskDollar: 500, timeframe: "1D" }),
            zeroAtr: computeAtrPositionSizing({ close: 100, atr: 0, stopMult: 2.0, riskDollar: 500, timeframe: "1D" })
        })
        """)

        self.assertEqual(res["std1D"]["shares"], 100)
        self.assertAlmostEqual(res["std1D"]["stopDistance"], 5.0, places=6)
        self.assertIsNone(res["std1D"]["error"])

        self.assertEqual(res["lowPrice1D"]["shares"], 62500)
        self.assertAlmostEqual(res["lowPrice1D"]["stopDistance"], 0.008, places=6)
        self.assertIsNone(res["lowPrice1D"]["error"])

        self.assertEqual(res["floatAtr1D"]["shares"], 106)
        self.assertAlmostEqual(res["floatAtr1D"]["stopDistance"], 4.691356, places=6)
        self.assertIsNone(res["floatAtr1D"]["error"])

        self.assertEqual(res["tf1m"]["shares"], 0)
        self.assertIsNone(res["tf1m"]["stopDistance"])
        self.assertEqual(res["tf1m"]["error"], "daily_only")

        self.assertEqual(res["tf1W"]["shares"], 0)
        self.assertIsNone(res["tf1W"]["stopDistance"])
        self.assertEqual(res["tf1W"]["error"], "daily_only")

        self.assertEqual(res["stopBelowZero"]["shares"], 0)
        self.assertEqual(res["stopBelowZero"]["error"], "stop_below_zero")

        self.assertEqual(res["zeroAtr"]["shares"], 0)
        self.assertEqual(res["zeroAtr"]["error"], "invalid_input")

    def test_static_app_js_invariants(self):
        """Checks static/app.js removed legacy clamping/hardcoded risk and declares var timeScale in mousemove."""
        content = self.app_js_path.read_text(encoding="utf-8")

        self.assertNotIn("Math.max(0.01, d.entryPrice - d.stopPrice)", content)
        self.assertNotIn("(d.riskDollar || 500)", content)
        self.assertNotIn("riskInput.value = cfg.risk_dollar", content)

        mm_start = content.find('drawingCanvas.addEventListener("mousemove"')
        self.assertNotEqual(mm_start, -1, 'drawingCanvas.addEventListener("mousemove" not found')
        mu_end = content.find('window.addEventListener("mouseup"', mm_start)
        self.assertNotEqual(mu_end, -1, 'window.addEventListener("mouseup" not found after mousemove')
        mousemove_slice = content[mm_start:mu_end]
        self.assertIn("var timeScale", mousemove_slice)


if __name__ == "__main__":
    unittest.main()

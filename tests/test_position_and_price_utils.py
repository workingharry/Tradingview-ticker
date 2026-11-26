import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestPositionAndPriceUtils(unittest.TestCase):
    """Verifies Section 0 shared numerical utilities in static/app.js:
    1. computePositionSize({entry, stop, riskDollar, side})
       - Long & short directions
       - Strict rejection of wrong-side stop (no Math.max(0.01) fake clamping)
       - Decimal-scaled stop distance + tolerant floor (1.1-1.0, 2.3-2.2, 2.02-2.01, 0.00001003-0.00001)
       - Raw stopDistance preserved (no premature toFixed)
       - Robust handling of invalid/zero/negative/NaN/Infinity inputs
    2. canonicalFormatPrice(value, symbol)
       - |value| >= 1: 2 decimal places + en-US thousands comma grouping (1234.5 -> "1,234.50", -1234.5 -> "-1,234.50")
       - 0.1 <= |value| < 1: 4 decimal places (0.5 -> "0.5000")
       - |value| < 0.1: 4 significant digits (0.05 -> "0.05000", 0.00001234 -> "0.00001234")
       - Negative numbers preserve negative sign and use absolute value for band selection
       - null, undefined, NaN, Infinity, 0 -> em-dash "—"
       - Implemented via Intl.NumberFormat("en-US", ...)
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

    # =========================================================================
    # 1. computePositionSize Tests
    # =========================================================================

    def test_compute_position_size_long_normal(self):
        """做多正常：entry=100, stop=95, riskDollar=500 -> shares=100, stopDistance=5, error=null"""
        snippet = "computePositionSize({ entry: 100, stop: 95, riskDollar: 500, side: 'long' })"
        res = self._execute_node_eval(snippet)
        self.assertEqual(res["shares"], 100)
        self.assertEqual(res["stopDistance"], 5)
        self.assertEqual(res["riskDollar"], 500)
        self.assertIsNone(res["error"])

    def test_compute_position_size_short_normal(self):
        """做空正常：entry=100, stop=105, riskDollar=500, side='short' -> shares=100, error=null"""
        snippet = "computePositionSize({ entry: 100, stop: 105, riskDollar: 500, side: 'short' })"
        res = self._execute_node_eval(snippet)
        self.assertEqual(res["shares"], 100)
        self.assertEqual(res["stopDistance"], 5)
        self.assertEqual(res["riskDollar"], 500)
        self.assertIsNone(res["error"])

    def test_compute_position_size_wrong_side(self):
        """做多停損高於進場價 / 做空停損低於進場價 → error 有值，shares=0"""
        # Long but stop > entry
        snippet_long_bad = "computePositionSize({ entry: 100, stop: 105, riskDollar: 500, side: 'long' })"
        res_long = self._execute_node_eval(snippet_long_bad)
        self.assertEqual(res_long["shares"], 0)
        self.assertEqual(res_long["stopDistance"], 0)
        self.assertIsNotNone(res_long["error"])
        self.assertIn("below entry price", res_long["error"])

        # Short but stop < entry
        snippet_short_bad = "computePositionSize({ entry: 100, stop: 95, riskDollar: 500, side: 'short' })"
        res_short = self._execute_node_eval(snippet_short_bad)
        self.assertEqual(res_short["shares"], 0)
        self.assertEqual(res_short["stopDistance"], 0)
        self.assertIsNotNone(res_short["error"])
        self.assertIn("above entry price", res_short["error"])

    def test_compute_position_size_equal_prices(self):
        """entry === stop → error 有值"""
        snippet = "computePositionSize({ entry: 100, stop: 100, riskDollar: 500 })"
        res = self._execute_node_eval(snippet)
        self.assertEqual(res["shares"], 0)
        self.assertEqual(res["stopDistance"], 0)
        self.assertIsNotNone(res["error"])
        self.assertIn("equal", res["error"])

    def test_compute_position_size_penny_stock(self):
        """低價股：entry=0.85, stop=0.80, riskDollar=500 → shares=10000，不因小數截成 0"""
        snippet = "computePositionSize({ entry: 0.85, stop: 0.80, riskDollar: 500 })"
        res = self._execute_node_eval(snippet)
        self.assertEqual(res["shares"], 10000)
        self.assertAlmostEqual(res["stopDistance"], 0.05, places=6)
        self.assertEqual(res["riskDollar"], 500)
        self.assertIsNone(res["error"])

    def test_compute_position_size_floating_point_corrections(self):
        """規格修正 1 浮點數誤差處理：1.1-1.0 -> 5000 股；2.3-2.2 -> 5000 股"""
        # Case 1: entry=1.1, stop=1.0, riskDollar=500 -> 5000 shares
        snippet_1 = "computePositionSize({ entry: 1.1, stop: 1.0, riskDollar: 500 })"
        res_1 = self._execute_node_eval(snippet_1)
        self.assertEqual(res_1["shares"], 5000)
        self.assertAlmostEqual(res_1["stopDistance"], 0.1, places=6)
        self.assertIsNone(res_1["error"])

        # Case 2: entry=2.3, stop=2.2, riskDollar=500 -> 5000 shares
        snippet_2 = "computePositionSize({ entry: 2.3, stop: 2.2, riskDollar: 500 })"
        res_2 = self._execute_node_eval(snippet_2)
        self.assertEqual(res_2["shares"], 5000)
        self.assertAlmostEqual(res_2["stopDistance"], 0.1, places=6)
        self.assertIsNone(res_2["error"])

    def test_compute_position_size_invalid_inputs(self):
        """無效輸入（null、NaN、Infinity、0、負數）→ error 有值，shares=0"""
        cases = [
            "{ entry: null, stop: 95, riskDollar: 500 }",
            "{ entry: 100, stop: NaN, riskDollar: 500 }",
            "{ entry: 100, stop: 95, riskDollar: Infinity }",
            "{ entry: 100, stop: 95, riskDollar: 0 }",
            "{ entry: 0, stop: 95, riskDollar: 500 }",
            "{ entry: -100, stop: 95, riskDollar: 500 }",
            "{ entry: 100, stop: -95, riskDollar: 500 }",
            "{ entry: 100, stop: 95, riskDollar: -500 }",
            "null",
            "{}",
            "{ entry: 100, stop: 95, riskDollar: 500, side: 'invalid_side' }"
        ]
        for c in cases:
            res = self._execute_node_eval(f"computePositionSize({c})")
            self.assertEqual(res["shares"], 0, f"Expected 0 shares for case: {c}")
            self.assertIsNotNone(res["error"], f"Expected error for case: {c}")

    def test_compute_position_size_float_regressions(self):
        """浮點誤差回歸：舊版 +1e-9 絕對容忍值在大股數時失效（例 2.02-2.01 得 49999）"""
        test_expr = """
        ([
            computePositionSize({ entry: 2.02, stop: 2.01, riskDollar: 500 }).shares,
            computePositionSize({ entry: 10.0111, stop: 10.0107, riskDollar: 500 }).shares,
            computePositionSize({ entry: 10.0333, stop: 10.0323, riskDollar: 500 }).shares,
            computePositionSize({ entry: 0.00002, stop: 0.00001, riskDollar: 500 }).shares,
            computePositionSize({ entry: 0.00001003, stop: 0.00001, riskDollar: 500 }).shares,
            computePositionSize({ entry: 100000.13, stop: 100000.14, riskDollar: 137.5, side: 'short' }).shares
        ])
        """
        res = self._execute_node_eval(test_expr)
        # 2.02-2.01 -> 500/0.01 = 50000
        self.assertEqual(res[0], 50000)
        # 10.0111-10.0107 -> 500/0.0004 = 1250000
        self.assertEqual(res[1], 1250000)
        # 10.0333-10.0323 -> 500/0.001 = 500000
        self.assertEqual(res[2], 500000)
        # 0.00002-0.00001 -> 500/0.00001 = 50000000
        self.assertEqual(res[3], 50000000)
        # 0.00001003-0.00001 -> 500/0.00000003 = 16666666666.67，必須向下取整，不可進位
        self.assertEqual(res[4], 16666666666)
        # 做空 BTC：距離 0.01、風險 137.5 -> 13750
        self.assertEqual(res[5], 13750)

    def test_compute_position_size_never_exceeds_risk(self):
        """隨機價格下 shares * stopDistance 不可超過 riskDollar"""
        test_expr = """
        (function() {
            var seed = 12345;
            function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
            var violations = 0;
            for (var i = 0; i < 5000; i++) {
                var entry = 0.0001 + rnd() * 1000;
                var stop = entry * (1 - (0.0001 + rnd() * 0.3));
                var r = computePositionSize({ entry: entry, stop: stop, riskDollar: 500 });
                if (r.error || r.shares * r.stopDistance > 500 * (1 + 1e-9)) violations++;
            }
            return violations;
        })()
        """
        self.assertEqual(self._execute_node_eval(test_expr), 0)

    def test_compute_position_size_rejects_non_numeric_types(self):
        """布林、陣列等非數字型別必須回傳 error（Number(true) 會變成 1，不可接受）"""
        test_expr = """
        ([
            computePositionSize({ entry: true, stop: 0.5, riskDollar: 500 }),
            computePositionSize({ entry: 100, stop: 95, riskDollar: [500] }),
            computePositionSize({ entry: '100', stop: '95', riskDollar: '500' })
        ])
        """
        res = self._execute_node_eval(test_expr)
        self.assertIsNotNone(res[0]["error"])
        self.assertIsNotNone(res[1]["error"])
        # 數字字串仍可接受
        self.assertIsNone(res[2]["error"])
        self.assertEqual(res[2]["shares"], 100)

    # =========================================================================
    # 2. canonicalFormatPrice Tests
    # =========================================================================

    def test_canonical_format_price_rules(self):
        """規格修正 2：驗證 canonicalFormatPrice 之有效位數、千分位與無效值"""
        test_expr = """
        ({
            largePrice: canonicalFormatPrice(1234.5),
            negativeLarge: canonicalFormatPrice(-1234.5),
            roundThousand: canonicalFormatPrice(1000),
            mediumPrice: canonicalFormatPrice(0.5),
            significantPointZeroFive: canonicalFormatPrice(0.05),
            microCrypto: canonicalFormatPrice(0.00001234),
            nullVal: canonicalFormatPrice(null),
            undefinedVal: canonicalFormatPrice(undefined),
            nanVal: canonicalFormatPrice(NaN),
            infVal: canonicalFormatPrice(Infinity),
            zeroVal: canonicalFormatPrice(0)
        })
        """
        res = self._execute_node_eval(test_expr)

        # |value| >= 1: 2 decimal places + en-US thousand comma
        self.assertEqual(res["largePrice"], "1,234.50")
        self.assertEqual(res["negativeLarge"], "-1,234.50")
        self.assertEqual(res["roundThousand"], "1,000.00")

        # 0.1 <= |value| < 1: 4 decimal places
        self.assertEqual(res["mediumPrice"], "0.5000")

        # |value| < 0.1: 4 significant digits
        self.assertEqual(res["significantPointZeroFive"], "0.05000")
        self.assertEqual(res["microCrypto"], "0.00001234")

        # Invalids must return em-dash '—'
        em_dash = "\u2014"
        self.assertEqual(res["nullVal"], em_dash)
        self.assertEqual(res["undefinedVal"], em_dash)
        self.assertEqual(res["nanVal"], em_dash)
        self.assertEqual(res["infVal"], em_dash)
        self.assertEqual(res["zeroVal"], em_dash)

    def test_canonical_format_price_band_boundaries(self):
        """區間邊界：依四捨五入後的值決定區間，0.99996 不可顯示為 '1.0000'"""
        test_expr = """
        ({
            nearOne: canonicalFormatPrice(0.99996),
            nearTenth: canonicalFormatPrice(0.099996),
            boolVal: canonicalFormatPrice(true)
        })
        """
        res = self._execute_node_eval(test_expr)
        self.assertEqual(res["nearOne"], "1.00")
        self.assertEqual(res["nearTenth"], "0.1000")
        self.assertEqual(res["boolVal"], "\u2014")


if __name__ == "__main__":
    unittest.main()

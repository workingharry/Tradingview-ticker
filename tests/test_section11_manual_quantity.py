import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestSection11ManualQuantityAndRrEdit(unittest.TestCase):
    """Verifies Section 11 follow-up features in static/app.js and static/index.html:
    1. parseManualQuantity strict decimal validation (up to 8 decimal places, <= 1e12)
    2. formatPriceForInput precision cleanup without thousand commas
    3. computeManualRiskView actual dollar risk and over-risk boundary tolerance
    4. describeRrEditInputs validation order (price -> quantity -> direction) and preview payload
    5. Static invariants (dblclick binding, no innerHTML in updateRrEditPreview, rrEditModal DOM IDs)
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

    def test_parse_manual_quantity(self):
        """Verifies parseManualQuantity accepts valid positive numbers/strings up to 8 decimals and rejects others."""
        res = self._execute_node_eval("""
        ({
            intStr: parseManualQuantity("100"),
            halfStr: parseManualQuantity("0.5"),
            paddedStr: parseManualQuantity(" 25 "),
            eightDec: parseManualQuantity("0.12345678"),
            minEightDec: parseManualQuantity("0.00000001"),
            nineDec: parseManualQuantity("0.123456789"),
            zeroStr: parseManualQuantity("0"),
            negStr: parseManualQuantity("-1"),
            alphaStr: parseManualQuantity("abc"),
            emptyStr: parseManualQuantity(""),
            sciStr: parseManualQuantity("1e3"),
            commaStr: parseManualQuantity("1,000"),
            boolTrue: parseManualQuantity(true),
            nullVal: parseManualQuantity(null)
        })
        """)
        self.assertEqual(res["intStr"], 100)
        self.assertEqual(res["halfStr"], 0.5)
        self.assertEqual(res["paddedStr"], 25)
        self.assertAlmostEqual(res["eightDec"], 0.12345678, places=10)
        self.assertAlmostEqual(res["minEightDec"], 1e-8, places=12)

        for key in ("nineDec", "zeroStr", "negStr", "alphaStr", "emptyStr", "sciStr", "commaStr", "boolTrue", "nullVal"):
            self.assertIsNone(res[key], f"{key} should return None")

    def test_format_price_for_input(self):
        """Verifies formatPriceForInput strips floating noise without adding commas."""
        res = self._execute_node_eval("""
        ({
            noisy: formatPriceForInput(142.37000000001),
            tiny: formatPriceForInput(0.00001234),
            integer: formatPriceForInput(100),
            zero: formatPriceForInput(0),
            neg: formatPriceForInput(-5),
            nanVal: formatPriceForInput(NaN),
            nullVal: formatPriceForInput(null)
        })
        """)
        self.assertEqual(res["noisy"], "142.37")
        self.assertEqual(res["tiny"], "0.00001234")
        self.assertEqual(res["integer"], "100")
        self.assertEqual(res["zero"], "")
        self.assertEqual(res["neg"], "")
        self.assertEqual(res["nanVal"], "")
        self.assertEqual(res["nullVal"], "")

    def test_compute_manual_risk_view(self):
        """Verifies manual quantity actual risk calculation and overRisk boundary tolerance."""
        res = self._execute_node_eval("""
        (function() {
            var vLong = computeRiskRewardView({ entry: 100, target: 115, stop: 94, riskDollar: 500 });
            var vShort = computeRiskRewardView({ entry: 100, target: 85, stop: 106, riskDollar: 500 });
            var vBtc = computeRiskRewardView({ entry: 60000, target: 66000, stop: 59000, riskDollar: 750 });
            var vDec = computeRiskRewardView({ entry: 2.02, target: 2.10, stop: 2.01, riskDollar: 500 });
            var vOne = computeRiskRewardView({ entry: 1.1, target: 1.2, stop: 1.0, riskDollar: 500 });
            var vInvalid = computeRiskRewardView({ entry: 100, target: 115, stop: 103, riskDollar: 500 });
            return {
                long83: computeManualRiskView({ view: vLong, quantity: 83, riskDollar: 500 }),
                long84: computeManualRiskView({ view: vLong, quantity: 84, riskDollar: 500 }),
                long100: computeManualRiskView({ view: vLong, quantity: 100, riskDollar: 500 }),
                short50: computeManualRiskView({ view: vShort, quantity: 50, riskDollar: 500 }),
                btcExact: computeManualRiskView({ view: vBtc, quantity: 0.75, riskDollar: 750 }),
                btcOver: computeManualRiskView({ view: vBtc, quantity: 0.76, riskDollar: 750 }),
                decExact: computeManualRiskView({ view: vDec, quantity: 50000, riskDollar: 500 }),
                decOver: computeManualRiskView({ view: vDec, quantity: 50001, riskDollar: 500 }),
                oneExact: computeManualRiskView({ view: vOne, quantity: 5000, riskDollar: 500 }),
                autoMode: computeManualRiskView({ view: vLong, quantity: null, riskDollar: 500 }),
                badView: computeManualRiskView({ view: vInvalid, quantity: 10, riskDollar: 500 }),
                badQty: computeManualRiskView({ view: vLong, quantity: -3, riskDollar: 500 })
            };
        })()
        """)

        self.assertEqual(res["long83"]["riskAmount"], 498)
        self.assertFalse(res["long83"]["overRisk"])
        self.assertIsNone(res["long83"]["error"])

        self.assertEqual(res["long84"]["riskAmount"], 504)
        self.assertTrue(res["long84"]["overRisk"])
        self.assertIsNone(res["long84"]["error"])

        self.assertEqual(res["long100"]["riskAmount"], 600)
        self.assertTrue(res["long100"]["overRisk"])
        self.assertIsNone(res["long100"]["error"])

        self.assertEqual(res["short50"]["riskAmount"], 300)
        self.assertFalse(res["short50"]["overRisk"])
        self.assertIsNone(res["short50"]["error"])

        self.assertEqual(res["btcExact"]["riskAmount"], 750)
        self.assertFalse(res["btcExact"]["overRisk"])
        self.assertIsNone(res["btcExact"]["error"])

        self.assertEqual(res["btcOver"]["riskAmount"], 760)
        self.assertTrue(res["btcOver"]["overRisk"])
        self.assertIsNone(res["btcOver"]["error"])

        self.assertEqual(res["decExact"]["riskAmount"], 500)
        self.assertFalse(res["decExact"]["overRisk"])
        self.assertIsNone(res["decExact"]["error"])

        self.assertAlmostEqual(res["decOver"]["riskAmount"], 500.01, places=6)
        self.assertTrue(res["decOver"]["overRisk"])
        self.assertIsNone(res["decOver"]["error"])

        self.assertEqual(res["oneExact"]["riskAmount"], 500)
        self.assertFalse(res["oneExact"]["overRisk"])
        self.assertIsNone(res["oneExact"]["error"])

        self.assertIsNone(res["autoMode"]["riskAmount"])
        self.assertFalse(res["autoMode"]["overRisk"])
        self.assertIsNone(res["autoMode"]["error"])

        self.assertIsNone(res["badView"]["riskAmount"])
        self.assertFalse(res["badView"]["overRisk"])
        self.assertEqual(res["badView"]["error"], "invalid_view")

        self.assertIsNone(res["badQty"]["riskAmount"])
        self.assertFalse(res["badQty"]["overRisk"])
        self.assertEqual(res["badQty"]["error"], "invalid_quantity")

    def test_describe_rr_edit_inputs(self):
        """Verifies validation order (price -> quantity -> direction) and preview calculations."""
        res = self._execute_node_eval("""
        ({
            autoLong: describeRrEditInputs({ entryText: "100", stopText: "94", targetText: "115", qtyText: "", riskDollar: 500 }),
            manualOver: describeRrEditInputs({ entryText: "100", stopText: "94", targetText: "115", qtyText: "100", riskDollar: 500 }),
            manualShort: describeRrEditInputs({ entryText: "100", stopText: "106", targetText: "85", qtyText: "50", riskDollar: 500 }),
            sameSide: describeRrEditInputs({ entryText: "100", stopText: "103", targetText: "115", qtyText: "", riskDollar: 500 }),
            badPrice: describeRrEditInputs({ entryText: "abc", stopText: "94", targetText: "115", qtyText: "", riskDollar: 500 }),
            badQty: describeRrEditInputs({ entryText: "100", stopText: "94", targetText: "115", qtyText: "1e3", riskDollar: 500 }),
            priceBeforeQty: describeRrEditInputs({ entryText: "abc", stopText: "94", targetText: "115", qtyText: "1e3", riskDollar: 500 })
        })
        """)

        self.assertTrue(res["autoLong"]["ok"])
        self.assertIsNone(res["autoLong"]["error"])
        self.assertIsNone(res["autoLong"]["quantity"])
        self.assertEqual(res["autoLong"]["view"]["shares"], 83)
        self.assertEqual(res["autoLong"]["view"]["side"], "long")

        self.assertTrue(res["manualOver"]["ok"])
        self.assertIsNone(res["manualOver"]["error"])
        self.assertEqual(res["manualOver"]["manual"]["riskAmount"], 600)
        self.assertTrue(res["manualOver"]["manual"]["overRisk"])

        self.assertTrue(res["manualShort"]["ok"])
        self.assertIsNone(res["manualShort"]["error"])
        self.assertEqual(res["manualShort"]["view"]["side"], "short")
        self.assertEqual(res["manualShort"]["manual"]["riskAmount"], 300)

        self.assertFalse(res["sameSide"]["ok"])
        self.assertEqual(res["sameSide"]["error"], "停損與目標必須在進場價兩側")

        self.assertFalse(res["badPrice"]["ok"])
        self.assertEqual(res["badPrice"]["error"], "價格必須是大於 0 的數字")

        self.assertFalse(res["badQty"]["ok"])
        self.assertEqual(res["badQty"]["error"], "數量必須是大於 0 的數字（最多 8 位小數），或留空")

        self.assertFalse(res["priceBeforeQty"]["ok"])
        self.assertEqual(res["priceBeforeQty"]["error"], "價格必須是大於 0 的數字")

    def test_static_invariants(self):
        """Checks dblclick binding, absence of innerHTML in updateRrEditPreview, and rrEditModal DOM IDs."""
        app_content = self.app_js_path.read_text(encoding="utf-8")
        self.assertIn('drawingCanvas.addEventListener("dblclick"', app_content)

        fn_start = app_content.find("function updateRrEditPreview")
        self.assertNotEqual(fn_start, -1, "function updateRrEditPreview not found in static/app.js")
        fn_end = app_content.find("\nfunction ", fn_start + 1)
        fn_body = app_content[fn_start:] if fn_end == -1 else app_content[fn_start:fn_end]
        self.assertNotIn("innerHTML", fn_body)

        html_path = PROJECT_ROOT / "static" / "index.html"
        html_content = html_path.read_text(encoding="utf-8")
        for dom_id in ("rrEditModal", "rrEditEntry", "rrEditStop", "rrEditTarget", "rrEditQty", "btnRrEditApply"):
            self.assertIn(f'id="{dom_id}"', html_content)


if __name__ == "__main__":
    unittest.main()

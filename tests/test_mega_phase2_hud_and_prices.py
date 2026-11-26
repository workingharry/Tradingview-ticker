import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestMegaPhase2HudAndPrices(unittest.TestCase):
    """階段 2：HUD、價格顯示與選股器數字（S6、S12、S9）

    S6-02 Vol/50D 與 ADR% 只在 activeTimeframe === "1D" 時顯示數值，其他週期顯示 "—"
    S6-06 頂部 24h Vol 在缺少 quote.volume 時顯示 "—"，不退而顯示單根 K 棒的量
    S12-01 頂部價格、自選清單、跑馬燈、十字線圖例改用 canonicalFormatPrice
    S12-02 computePriceFormatForPrice(price) 回傳 { precision, minMove }
    S9-03 computeYtdStart(nowSec, symbol) 美股回傳 1/1 00:00 ET，其他回傳 1/1 00:00 UTC
    S9-05 exportScreenerCsv 缺值輸出空欄位 ""，不以預設假數字代替
    S9-07 escapeCsvField 只有非數字才加前綴，保留負數/正數/百分比
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
            self.skipTest("Node.js binary not found in PATH; skipping JS VM evaluation")

    def _execute_node_eval(self, eval_js_snippet: str):
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const elements = {{}};
        function getOrCreateEl(id) {{
            if (!elements[id]) {{
                elements[id] = {{
                    id: id,
                    textContent: '',
                    title: '',
                    className: '',
                    style: {{}},
                    classList: {{
                        add: function(c) {{ elements[id].className += ' ' + c; }},
                        remove: function() {{}},
                        contains: function() {{ return false; }}
                    }},
                    appendChild: function() {{}},
                    setAttribute: function(k, v) {{ elements[id][k] = v; }},
                    getAttribute: function(k) {{ return elements[id][k] || null; }},
                    querySelector: function() {{ return null; }},
                    querySelectorAll: function() {{ return []; }}
                }};
            }}
            return elements[id];
        }}

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: (id) => getOrCreateEl(id),
                querySelector: (s) => null,
                querySelectorAll: (s) => [],
                createElement: (t) => getOrCreateEl('dynamic_' + Math.random()),
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

    def test_s6_02_hud_vol50_and_adr_by_timeframe(self):
        """S6-02: HUD Vol/50D and ADR% show values only on 1D, and '—' on intraday timeframes."""
        js_code = """
        (function() {
            var mockBars = [];
            for (var i = 0; i < 60; i++) {
                mockBars.push({
                    time: 1000 + i * 86400,
                    open: 100,
                    high: 110,
                    low: 95,
                    close: 105,
                    volume: 1000
                });
            }

            // 1. On 5m timeframe
            activeTimeframe = "5m";
            updateDeepvueHud(mockBars);
            var hudVol50Val_5m = document.getElementById("hudVol50Val").textContent;
            var hudVol50Title_5m = document.getElementById("hudVol50Val").title;
            var hudAdrVal_5m = document.getElementById("hudAdrVal").textContent;
            var hudAdrTitle_5m = document.getElementById("hudAdrVal").title;

            // 2. On 1D timeframe
            activeTimeframe = "1D";
            updateDeepvueHud(mockBars);
            var hudVol50Val_1d = document.getElementById("hudVol50Val").textContent;
            var hudAdrVal_1d = document.getElementById("hudAdrVal").textContent;

            return {
                hudVol50Val_5m: hudVol50Val_5m,
                hudVol50Title_5m: hudVol50Title_5m,
                hudAdrVal_5m: hudAdrVal_5m,
                hudAdrTitle_5m: hudAdrTitle_5m,
                hudVol50Val_1d: hudVol50Val_1d,
                hudAdrVal_1d: hudAdrVal_1d
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["hudVol50Val_5m"], "—")
        self.assertEqual(res["hudVol50Title_5m"], "Daily only — switch to 1D")
        self.assertEqual(res["hudAdrVal_5m"], "—")
        self.assertEqual(res["hudAdrTitle_5m"], "Daily only — switch to 1D")
        self.assertIn("x", res["hudVol50Val_1d"])
        self.assertIn("%", res["hudAdrVal_1d"])

    def test_s6_06_top_24h_volume_missing_quote(self):
        """S6-06: 24h Vol displays '—' if quote.volume is absent, does not use bar volume."""
        js_code = """
        (function() {
            activeSymbol = "NASDAQ:NVDA";
            activeVolumeEl = document.getElementById("activeVolume");
            activePriceEl = document.getElementById("activePrice");
            activeChangeEl = document.getElementById("activeChange");

            var mockBar = { open: 100, high: 105, low: 95, close: 102, volume: 999999 };

            // Quote with NO volume
            var quoteWithoutVol = { price: 102, change: 2, changePercentage: 2.0 };
            updateSymbolMetrics("NASDAQ:NVDA", quoteWithoutVol, mockBar);
            var volWithout = activeVolumeEl.textContent;

            // Quote WITH volume
            var quoteWithVol = { price: 102, volume: 50000000 };
            updateSymbolMetrics("NASDAQ:NVDA", quoteWithVol, mockBar);
            var volWith = activeVolumeEl.textContent;

            return {
                volWithout: volWithout,
                volWith: volWith
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["volWithout"], "—")
        self.assertNotEqual(res["volWith"], "—")

    def test_s12_01_canonical_format_price_display_locations(self):
        """S12-01: canonicalFormatPrice used in activePriceEl, watchlist, tape, and crosshair legend."""
        js_code = """
        (function() {
            activeSymbol = "BINANCE:DOGEUSDT";

            // 1. activePriceEl in updateSymbolMetrics
            activePriceEl = document.getElementById("activePrice");
            updateSymbolMetrics("BINANCE:DOGEUSDT", { price: 0.05 }, null);
            var topPrice = activePriceEl.textContent;

            // 2. updateWatchlistPrice
            sidebarElements["BINANCE:DOGEUSDT"] = {
                price: document.getElementById("wlPriceDoge"),
                chp: document.getElementById("wlChpDoge")
            };
            updateWatchlistPrice("BINANCE:DOGEUSDT", { price: 0.00001234 });
            var wlPrice = sidebarElements["BINANCE:DOGEUSDT"].price.textContent;

            // 3. updateTapeItem
            tapeElements["BINANCE:DOGEUSDT"] = {
                price: document.getElementById("tapePriceDoge"),
                change: document.getElementById("tapeChangeDoge"),
                item: document.getElementById("tapeItemDoge")
            };
            symbolStates["BINANCE:DOGEUSDT"] = { price: 1234.5 };
            updateTapeItem("BINANCE:DOGEUSDT");
            var tapePrice = tapeElements["BINANCE:DOGEUSDT"].price.textContent;

            // 4. updateCrosshairLegend
            legendCloseEl = document.getElementById("legendClose");
            updateCrosshairLegend({ open: 0.05, high: 0.06, low: 0.04, close: 0.05, volume: 100 });
            var legendClose = legendCloseEl.textContent;

            return {
                topPrice: topPrice,
                wlPrice: wlPrice,
                tapePrice: tapePrice,
                legendClose: legendClose
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["topPrice"], "0.05000", "<0.1 price should have 4 significant digits (0.05000)")
        self.assertEqual(res["wlPrice"], "0.00001234", "Micro price should have 4 significant digits")
        self.assertEqual(res["tapePrice"], "1,234.50", ">=1 price should have 2 decimal places with thousands comma")
        self.assertEqual(res["legendClose"], "0.05000")

    def test_s12_02_compute_price_format_for_price(self):
        """S12-02: computePriceFormatForPrice returns proper precision and minMove."""
        js_code = """
        ({
            p150: computePriceFormatForPrice(150),
            p05: computePriceFormatForPrice(0.5),
            pMicro: computePriceFormatForPrice(0.00001234)
        })
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["p150"], {"precision": 2, "minMove": 0.01})
        self.assertEqual(res["p05"], {"precision": 4, "minMove": 0.0001})
        self.assertEqual(res["pMicro"], {"precision": 8, "minMove": 1e-8})

    def test_s9_03_compute_ytd_start(self):
        """S9-03: computeYtdStart produces exact expected timestamps for US equities and 24/7 markets."""
        js_code = """
        ({
            nvdaNov23: computeYtdStart(1795446000, "NASDAQ:NVDA"),
            nvdaDec31: computeYtdStart(1798777800, "NASDAQ:NVDA"),
            nvdaJan01: computeYtdStart(1798779600, "NASDAQ:NVDA"),
            btcNov23: computeYtdStart(1795446000, "BINANCE:BTCUSDT")
        })
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["nvdaNov23"], 1767243600)
        self.assertEqual(res["nvdaDec31"], 1767243600)
        self.assertEqual(res["nvdaJan01"], 1798779600)
        self.assertEqual(res["btcNov23"], 1767225600)

    def test_s9_07_escape_csv_field(self):
        """S9-07: escapeCsvField handles negative numbers, formulas, commands, and numbers safely."""
        js_code = """
        ({
            negPct: escapeCsvField("-3.2%"),
            posNum: escapeCsvField("+5"),
            formula: escapeCsvField("=SUM(A1)"),
            cmd: escapeCsvField("-cmd"),
            nullVal: escapeCsvField(null),
            numVal: escapeCsvField(-3.2)
        })
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["negPct"], '"-3.2%"')
        self.assertEqual(res["posNum"], '"+5"')
        self.assertEqual(res["formula"], "\"'=SUM(A1)\"")
        self.assertEqual(res["cmd"], "\"'-cmd\"")
        self.assertEqual(res["nullVal"], '""')
        self.assertEqual(res["numVal"], "-3.2")


if __name__ == "__main__":
    unittest.main()

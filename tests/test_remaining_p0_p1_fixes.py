import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestRemainingP0P1Fixes(unittest.TestCase):
    """TDD regression suite for the 4 remaining P0/P1 bugs:
    1. Frontend DELAYED quote badge helper (formatQuoteDelayBadge) and DOM element #delayedQuoteBadge.
    2. Model source display helper (formatModelSourceLabel) avoiding fake 'gemini-3.8-flash' on rule_based fallback,
       plus AbortController + sequence guards in fetchFundamentals and fetchAiLens.
    3. server.py /api/screener/scan honoring both min_rev_growth_yoy and min_rev_yoy.
    4. src-tauri/src/lib.rs graceful SIGTERM shutdown helper before SIGKILL fallback.
    """

    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
        cls.index_html_path = PROJECT_ROOT / "static" / "index.html"
        cls.server_py_path = PROJECT_ROOT / "server.py"
        cls.lib_rs_path = PROJECT_ROOT / "src-tauri" / "src" / "lib.rs"
        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )

    def _execute_node_eval(self, eval_js_snippet: str):
        if not self.node_bin:
            self.skipTest("Node.js binary not found")
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
            fetch: () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ bars: [] }}) }}),
            AbortController: function() {{ this.signal = {{}}; this.abort = function() {{}}; }},
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

    def test_format_quote_delay_badge(self):
        """Verifies delayed quotes display DELAYED badge (with parsed delay minutes & i18n title) while realtime/unknown hide it."""
        res = self._execute_node_eval("""
        ({
            delayed900Zh: formatQuoteDelayBadge({ is_delayed: true, update_mode: "delayed_streaming_900" }, "zh"),
            delayed900En: formatQuoteDelayBadge({ is_delayed: true, update_mode: "delayed_streaming_900" }, "en"),
            delayedNoSec: formatQuoteDelayBadge({ is_delayed: true, update_mode: "delayed_streaming" }, "zh"),
            delayedModeOnly: formatQuoteDelayBadge({ update_mode: "delayed_streaming_1200" }, "en"),
            realtime: formatQuoteDelayBadge({ is_delayed: false, update_mode: "streaming" }, "zh"),
            explicitFalseOverrides: formatQuoteDelayBadge({ is_delayed: false, update_mode: "delayed_streaming_900" }, "zh"),
            unknown: formatQuoteDelayBadge({ is_delayed: null, update_mode: null }, "zh"),
            empty: formatQuoteDelayBadge(null, "zh")
        })
        """)
        self.assertTrue(res["delayed900Zh"]["visible"])
        self.assertEqual(res["delayed900Zh"]["text"], "DELAYED 15m")
        self.assertEqual(res["delayed900Zh"]["shortText"], "15m")
        self.assertEqual(res["delayed900Zh"]["minutes"], 15)
        self.assertIn("15 分鐘", res["delayed900Zh"]["title"])

        self.assertTrue(res["delayed900En"]["visible"])
        self.assertEqual(res["delayed900En"]["text"], "DELAYED 15m")
        self.assertIn("15 min", res["delayed900En"]["title"])

        self.assertTrue(res["delayedNoSec"]["visible"])
        self.assertEqual(res["delayedNoSec"]["text"], "DELAYED")
        self.assertEqual(res["delayedNoSec"]["shortText"], "D")
        self.assertIsNone(res["delayedNoSec"]["minutes"])

        self.assertTrue(res["delayedModeOnly"]["visible"])
        self.assertEqual(res["delayedModeOnly"]["text"], "DELAYED 20m")
        self.assertEqual(res["delayedModeOnly"]["minutes"], 20)

        self.assertFalse(res["realtime"]["visible"])
        self.assertFalse(res["explicitFalseOverrides"]["visible"])
        self.assertFalse(res["unknown"]["visible"])
        self.assertFalse(res["empty"]["visible"])

    def test_delayed_badge_lifecycle_and_watchlist_dom(self):
        """Verifies S1-06 layout wrapper, switchSymbol reset, merged state propagation, and watchlist delayed badge."""
        html = self.index_html_path.read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "static" / "style.css").read_text(encoding="utf-8")
        app_js = self.app_js_path.read_text(encoding="utf-8")

        # 1. Exchange & DELAYED badge share a horizontal row so 36px toolbar does not overflow
        self.assertIn('class="asset-exchange-row"', html)
        self.assertIn(".asset-exchange-row", css)
        self.assertIn(".wl-delayed-badge", css)

        # 2. updateQuote passes merged symbolStates[symbol] instead of partial quoteData
        uq_start = app_js.find("function updateQuote(")
        uq_end = app_js.find("var _liveIndicatorsTimer", uq_start)
        uq_body = app_js[uq_start:uq_end]
        self.assertIn("updateWatchlistPrice(symbol, mergedState)", uq_body)
        self.assertIn("updateSymbolMetrics(symbol, mergedState, latestBarData)", uq_body)

        # 3. switchSymbol immediately refreshes metrics/DELAYED badge so old symbol badge never lingers
        sw_start = app_js.find("function switchSymbol(")
        sw_end = app_js.find("function switchChartEngine(", sw_start)
        sw_body = app_js[sw_start:sw_end]
        self.assertIn("updateSymbolMetrics(symbol, symbolStates[symbol] || {}, null)", sw_body)

        # 4. Watchlist items render & update per-row delayed badge
        wl_start = app_js.find("function renderWatchlist(")
        wl_end = app_js.find("var activeContextSymbol", wl_start)
        wl_body = app_js[wl_start:wl_end]
        self.assertIn("wl-delayed-badge", wl_body)
        self.assertIn("delayedBadge:", wl_body)

        uwp_start = app_js.find("function updateWatchlistPrice(")
        uwp_end = app_js.find("var searchDebounceTimer", uwp_start)
        uwp_body = app_js[uwp_start:uwp_end]
        self.assertIn("els.delayedBadge", uwp_body)
        self.assertIn("formatQuoteDelayBadge(", uwp_body)

        # 5. Runtime Node VM check: partial quote tick preserves DELAYED badge, switching to realtime symbol hides it
        vm_res = self._execute_node_eval("""
        (function() {
          var badgeEl = { style: { display: "none" }, textContent: "", title: "" };
          document.getElementById = function(id) {
            if (id === "delayedQuoteBadge") return badgeEl;
            return null;
          };
          activeSymbol = "NASDAQ:NVDA";
          updateQuote("NASDAQ:NVDA", { price: 130.5, is_delayed: true, update_mode: "delayed_streaming_900" });
          var afterDelayed = { display: badgeEl.style.display, text: badgeEl.textContent };

          // Partial tick without is_delayed must preserve DELAYED badge via merged symbolStates
          updateQuote("NASDAQ:NVDA", { price: 131.0 });
          var afterPartial = { display: badgeEl.style.display, text: badgeEl.textContent };

          // Switching to a realtime crypto symbol must immediately clear DELAYED badge
          symbolStates["BINANCE:BTCUSDT"] = { price: 90000, is_delayed: false, update_mode: "streaming" };
          switchSymbol("BINANCE:BTCUSDT");
          var afterSwitchRealtime = { display: badgeEl.style.display, text: badgeEl.textContent };

          return { afterDelayed: afterDelayed, afterPartial: afterPartial, afterSwitchRealtime: afterSwitchRealtime };
        })()
        """)
        self.assertEqual(vm_res["afterDelayed"]["display"], "inline-flex")
        self.assertEqual(vm_res["afterDelayed"]["text"], "DELAYED 15m")
        self.assertEqual(vm_res["afterPartial"]["display"], "inline-flex")
        self.assertEqual(vm_res["afterPartial"]["text"], "DELAYED 15m")
        self.assertEqual(vm_res["afterSwitchRealtime"]["display"], "none")

    def test_format_model_source_label(self):
        """Verifies rule_based or missing model never fakes a Gemini model name."""
        res = self._execute_node_eval("""
        ({
            ruleBased: formatModelSourceLabel(null, "rule_based"),
            ruleBasedEvenWithName: formatModelSourceLabel("gemini-2.5-flash", "rule_based"),
            geminiReal: formatModelSourceLabel("gemini-2.5-flash", "gemini"),
            missingBoth: formatModelSourceLabel(null, null)
        })
        """)
        self.assertEqual(res["ruleBased"], "Rule-Based (Offline)")
        self.assertEqual(res["ruleBasedEvenWithName"], "Rule-Based (Offline)")
        self.assertEqual(res["geminiReal"], "gemini-2.5-flash")
        self.assertEqual(res["missingBoth"], "Rule-Based (Offline)")

    def test_frontend_delayed_dom_and_modal_race_guards(self):
        """Verifies #delayedQuoteBadge exists and fetchFundamentals/fetchAiLens have sequence + AbortController guards."""
        html = self.index_html_path.read_text(encoding="utf-8")
        app_js = self.app_js_path.read_text(encoding="utf-8")

        self.assertIn('id="delayedQuoteBadge"', html)
        self.assertIn("formatQuoteDelayBadge(", app_js)
        self.assertNotIn('|| "gemini-3.8-flash"', app_js)

        f_start = app_js.find("function fetchFundamentals(")
        f_end = app_js.find("function openAiLensModal(", f_start)
        fund_body = app_js[f_start:f_end]
        self.assertIn("_fundSeq", fund_body)
        self.assertIn("_fundAbort", fund_body)
        self.assertIn("AbortError", fund_body)

        a_start = app_js.find("function fetchAiLens(")
        a_end = app_js.find("function recoverTerminalState(", a_start)
        ai_body = app_js[a_start:a_end]
        self.assertIn("_aiLensSeq", ai_body)
        self.assertIn("_aiLensAbort", ai_body)
        self.assertIn("AbortError", ai_body)

    def test_server_screener_accepts_min_rev_yoy(self):
        """Verifies server.py falls back to min_rev_yoy when min_rev_growth_yoy is absent."""
        srv = self.server_py_path.read_text(encoding="utf-8")
        self.assertIn('raw_criteria.get("min_rev_yoy")', srv)
        idx = srv.find("min_rev_growth: Optional[float] = None")
        self.assertNotEqual(idx, -1)
        snippet = srv[idx:idx + 350]
        self.assertIn("min_rev_yoy", snippet)

    def test_tauri_graceful_sidecar_shutdown(self):
        """Verifies src-tauri/src/lib.rs sends SIGTERM and waits before SIGKILL fallback."""
        lib_rs = self.lib_rs_path.read_text(encoding="utf-8")
        self.assertIn("fn terminate_child_gracefully", lib_rs)
        self.assertIn("-TERM", lib_rs)
        self.assertIn("try_wait", lib_rs)


if __name__ == "__main__":
    unittest.main()

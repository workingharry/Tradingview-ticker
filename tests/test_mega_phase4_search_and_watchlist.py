"""
Mega Phase 4 Unit Tests: Watchlist & Search Pipeline Hardening (S8)
Strict TDD verification for:
  S8-03: executeSearch and screener autocomplete request sequence guards and error handling ("搜尋失敗")
  S8-02: subscribe buttons only update state on res.ok; handle error with "加入失敗"; exchange_guessed notification
  S8-01: switchSymbol checks symbolsList; unsubscribed symbols show #unsubscribedBadge with "加入自選" button; hides on subscribed
"""

import json
import subprocess
import unittest
from pathlib import Path


class TestMegaPhase4SearchAndWatchlist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.app_js_path = cls.project_root / "static" / "app.js"
        if not cls.app_js_path.exists():
            raise FileNotFoundError(f"Missing {cls.app_js_path}")

    def _execute_node_eval(self, eval_js_snippet: str):
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const elements = {{}};
        function getOrCreateEl(id) {{
            if (!elements[id]) {{
                const el = {{
                    _id: id,
                    _text: '',
                    title: '',
                    className: '',
                    style: {{}},
                    classList: {{
                        add: function(c) {{ el.className += ' ' + c; }},
                        remove: function(c) {{ el.className = el.className.replace(c, '').trim(); }},
                        contains: function(c) {{ return (el.className || '').indexOf(c) !== -1; }}
                    }},
                    children: [],
                    appendChild: function(c) {{ el.children.push(c); }},
                    insertBefore: function(c, ref) {{ el.children.push(c); }},
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}},
                    setAttribute: function(k, v) {{ el[k] = v; if (k === 'id') {{ el._id = v; elements[v] = el; }} }},
                    getAttribute: function(k) {{ return el[k] || null; }},
                    querySelector: function(s) {{
                        if (s === '#btnSubscribeActive' || s === '.btn-subscribe-active') {{
                            return elements['btnSubscribeActive'] || el.children.find(c => c.id === 'btnSubscribeActive') || null;
                        }}
                        return null;
                    }},
                    querySelectorAll: function() {{ return []; }}
                }};
                Object.defineProperty(el, 'id', {{
                    get: function() {{ return this._id; }},
                    set: function(v) {{ this._id = v; elements[v] = this; }}
                }});
                Object.defineProperty(el, 'innerHTML', {{
                    get: function() {{ return this._html || ''; }},
                    set: function(v) {{
                        this._html = v;
                        if (v === '') {{
                            this.children = [];
                            this._text = '';
                        }}
                    }}
                }});
                Object.defineProperty(el, 'textContent', {{
                    get: function() {{
                        if (this._text !== undefined && this._text !== '') return this._text;
                        if (this.children.length > 0) {{
                            return this.children.map(c => c.textContent).join(' ');
                        }}
                        return this._text || '';
                    }},
                    set: function(v) {{ this._text = v; }}
                }});
                elements[id] = el;
            }}
            return elements[id];
        }}

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: (id) => getOrCreateEl(id),
                querySelector: (s) => {{
                    if (s && s.startsWith('#')) return getOrCreateEl(s.slice(1));
                    return null;
                }},
                querySelectorAll: (s) => [],
                createElement: (t) => {{
                    const dynId = 'dynamic_' + Math.random();
                    return getOrCreateEl(dynId);
                }},
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            fetch: function() {{ return Promise.reject(new Error("fetch unmocked")); }},
            AbortController: globalThis.AbortController || function() {{ this.signal = {{}}; this.abort = function() {{}}; }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            sessionStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', protocol: 'http:', host: '127.0.0.1:8000', origin: 'http://127.0.0.1:8000' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl,
            Date: Date,
            setInterval: setInterval,
            clearInterval: clearInterval,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);
        vm.runInContext(appCode, sandbox);

        const result = vm.runInContext(`(async () => {{
            {eval_js_snippet}
        }})()`, sandbox);

        result.then(val => {{
            console.log("JSON_OUTPUT_START" + JSON.stringify(val) + "JSON_OUTPUT_END");
        }}).catch(err => {{
            console.error(err);
            process.exit(1);
        }});
        """

        p = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"Node execution failed with code {p.returncode}:\nStdout: {p.stdout}\nStderr: {p.stderr}")

        stdout = p.stdout
        start = stdout.find("JSON_OUTPUT_START")
        end = stdout.find("JSON_OUTPUT_END")
        if start == -1 or end == -1:
            raise ValueError(f"Could not parse JSON output from:\n{stdout}")
        return json.loads(stdout[start + len("JSON_OUTPUT_START"):end])

    def test_s8_03_execute_search_discard_out_of_order_and_handles_error(self):
        """S8-03: executeSearch drops out-of-order sequence responses and displays '搜尋失敗' on network error."""
        js_code = """
        var searchResultsList = document.getElementById("searchResultsList");
        var deferredCalls = [];

        fetch = function(url) {
            return new Promise(function(resolve, reject) {
                deferredCalls.push({ url: url, resolve: resolve, reject: reject });
            });
        };

        // First call: q=A
        executeSearch("A");
        // Second call: q=AAPL
        executeSearch("AAPL");

        // Resolve call 2 (AAPL) first
        deferredCalls[1].resolve({
            ok: true,
            json: function() {
                return Promise.resolve({
                    results: [{ id: "NASDAQ:AAPL", pair: "AAPL", name: "Apple Inc." }]
                });
            }
        });

        await new Promise(r => setTimeout(r, 10));

        var itemsAfterSecond = searchResultsList.children.length;
        var firstItemText = searchResultsList.children[0] ? searchResultsList.children[0].textContent : "";

        // Now resolve call 1 (A) late - should be discarded because searchReqSeq advanced
        deferredCalls[0].resolve({
            ok: true,
            json: function() {
                return Promise.resolve({
                    results: [{ id: "NYSE:A", pair: "A", name: "Agilent Technologies" }]
                });
            }
        });

        await new Promise(r => setTimeout(r, 10));

        var itemsAfterStaleResolve = searchResultsList.children.length;
        var staleItemText = searchResultsList.children[0] ? searchResultsList.children[0].textContent : "";

        // Now test error handling: trigger call 3 and reject it
        executeSearch("ERROR_SYM");
        deferredCalls[2].reject(new Error("Network failed"));

        await new Promise(r => setTimeout(r, 10));

        var errorEl = searchResultsList.children[0];
        var errorText = errorEl ? errorEl.textContent : "";

        return {
            itemsAfterSecond: itemsAfterSecond,
            firstItemText: firstItemText,
            itemsAfterStaleResolve: itemsAfterStaleResolve,
            staleItemText: staleItemText,
            errorText: errorText
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["itemsAfterSecond"], 1)
        self.assertIn("AAPL", res["firstItemText"])
        self.assertEqual(res["itemsAfterStaleResolve"], 1)
        self.assertIn("AAPL", res["staleItemText"])
        self.assertEqual(res["errorText"], "搜尋失敗")

    def test_s8_02_subscribe_only_updates_on_res_ok(self):
        """S8-02: selectAndSubscribeSymbol only appends to symbolsList when server returns 200 OK."""
        js_code = """
        symbolsList = [{ id: "NASDAQ:NVDA", pair: "NVDA" }];

        var failCalled = false;
        fetch = function(url, opts) {
            return Promise.resolve({
                ok: false,
                status: 500,
                json: function() { return Promise.resolve({ error: "DB Error" }); }
            });
        };

        var errCaught = null;
        try {
            await selectAndSubscribeSymbol({
                id: "NASDAQ:TSLA",
                pair: "TSLA",
                exchange: "NASDAQ"
            }, function(ok, data, err) {
                failCalled = !ok;
            });
        } catch (e) {
            errCaught = e.message;
        }

        var countAfterFail = symbolsList.length;

        // Now mock success
        fetch = function(url, opts) {
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "subscribed",
                        symbol: { id: "NASDAQ:TSLA", pair: "TSLA", exchange: "NASDAQ" }
                    });
                }
            });
        };

        var successCalled = false;
        await selectAndSubscribeSymbol({
            id: "NASDAQ:TSLA",
            pair: "TSLA",
            exchange: "NASDAQ"
        }, function(ok, data, err) {
            successCalled = ok;
        });

        var countAfterSuccess = symbolsList.length;

        return {
            failCalled: failCalled,
            errCaught: errCaught,
            countAfterFail: countAfterFail,
            successCalled: successCalled,
            countAfterSuccess: countAfterSuccess
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertTrue(res["failCalled"])
        self.assertIsNotNone(res["errCaught"])
        self.assertEqual(res["countAfterFail"], 1)
        self.assertTrue(res["successCalled"])
        self.assertEqual(res["countAfterSuccess"], 2)

    def test_s8_02_exchange_guessed_notification(self):
        """S8-02: When server returns exchange_guessed: true, notification is recorded."""
        js_code = """
        fetch = function(url, opts) {
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "subscribed",
                        symbol: { id: "NASDAQ:GUESSED", pair: "GUESSED", exchange: "NASDAQ" },
                        exchange_guessed: true
                    });
                }
            });
        };

        lastNotificationMessage = "";
        await selectAndSubscribeSymbol({
            id: "GUESSED",
            pair: "GUESSED"
        });

        return {
            lastNotification: lastNotificationMessage
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertIn("交易所是推測的", res["lastNotification"])

    def test_s8_01_switch_unsubscribed_symbol_shows_badge_and_button(self):
        """S8-01: switchSymbol for unsubscribed symbol displays badge with '未訂閱即時報價' and '+ 加入自選' button."""
        js_code = """
        symbolsList = [{ id: "NASDAQ:NVDA", pair: "NVDA" }];
        currentRawBars = [];

        // Mock chart & series methods to avoid errors during switchSymbol
        loadHistory = function() {};
        updateSymbolMetrics = function() {};
        renderWatchlist = function() {};

        switchSymbol("NASDAQ:AMD");

        var badge = document.getElementById("unsubscribedBadge");
        var btn = badge.children.find(c => c.id === "btnSubscribeActive");

        var initialBadgeDisplay = badge.style.display;
        var initialBadgeText = badge.textContent;
        var hasButton = !!btn;
        var buttonText = btn ? btn.textContent : "";

        // Now simulate clicking the subscribe button
        fetch = function(url, opts) {
            return Promise.resolve({
                ok: true,
                status: 200,
                json: function() {
                    return Promise.resolve({
                        status: "subscribed",
                        symbol: { id: "NASDAQ:AMD", pair: "AMD", exchange: "NASDAQ" }
                    });
                }
            });
        };

        if (btn && btn.onclick) {
            btn.onclick();
        }

        await new Promise(r => setTimeout(r, 10));

        var badgeDisplayAfterSubscribe = badge.style.display;
        var symbolsListLength = symbolsList.length;

        return {
            initialBadgeDisplay: initialBadgeDisplay,
            initialBadgeText: initialBadgeText,
            hasButton: hasButton,
            buttonText: buttonText,
            badgeDisplayAfterSubscribe: badgeDisplayAfterSubscribe,
            symbolsListLength: symbolsListLength
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["initialBadgeDisplay"], "inline-flex")
        self.assertIn("未訂閱即時報價", res["initialBadgeText"])
        self.assertTrue(res["hasButton"])
        self.assertEqual(res["buttonText"], "加入自選")
        self.assertEqual(res["badgeDisplayAfterSubscribe"], "none")
        self.assertEqual(res["symbolsListLength"], 2)

    def test_s8_01_switch_subscribed_symbol_hides_badge(self):
        """S8-01: switchSymbol for already subscribed symbol hides #unsubscribedBadge."""
        js_code = """
        symbolsList = [{ id: "NASDAQ:NVDA", pair: "NVDA" }, { id: "AMEX:SPY", pair: "SPY" }];
        currentRawBars = [];

        loadHistory = function() {};
        updateSymbolMetrics = function() {};
        renderWatchlist = function() {};

        var badge = document.getElementById("unsubscribedBadge");
        badge.style.display = "inline-flex";
        badge.textContent = "未訂閱即時報價";

        switchSymbol("AMEX:SPY");

        return {
            badgeDisplay: badge.style.display,
            badgeText: badge.textContent
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["badgeDisplay"], "none")
        self.assertEqual(res["badgeText"], "")


if __name__ == "__main__":
    unittest.main()

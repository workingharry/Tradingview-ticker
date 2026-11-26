"""
Mega Phase 6 Unit Tests: Drawings & Price Lines Pipeline Hardening (S11)
Strict TDD verification for:
  S11-04: Drawing coordinate shift detection loop and scale mode render triggers
  S11-06: Quick price lines scoped per symbol in localStorage with 'ALERT (未啟用提醒)' label
  S11-07: switchSymbol resets currentDrawTool to cursor and clears drawingInProgress
  S11-10: Alt/Option keyboard shortcuts check e.code for macOS dead-key immunity
"""

import json
import subprocess
import unittest
from pathlib import Path


class TestMegaPhase6DrawingsAndLines(unittest.TestCase):
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
                        contains: function(c) {{ return (el.className || '').indexOf(c) !== -1; }},
                        toggle: function(c, force) {{
                            if (force === undefined) force = !el.classList.contains(c);
                            if (force) el.classList.add(c); else el.classList.remove(c);
                        }}
                    }},
                    children: [],
                    appendChild: function(c) {{ el.children.push(c); }},
                    insertBefore: function(c, ref) {{ el.children.push(c); }},
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}},
                    setAttribute: function(k, v) {{ el[k] = v; if (k === 'id') {{ el._id = v; elements[v] = el; }} }},
                    getAttribute: function(k) {{ return el[k] || null; }},
                    querySelector: function(s) {{ return null; }},
                    querySelectorAll: function(s) {{ return []; }}
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

        const storageStore = {{}};
        const keyListeners = [];

        const sandbox = {{
            window: {{
                addEventListener: function(evt, handler) {{
                    if (evt === 'keydown') keyListeners.push(handler);
                }},
                removeEventListener: function() {{}},
                LightweightCharts: {{
                    LineStyle: {{ Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 }}
                }}
            }},
            document: {{
                hidden: false,
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
                addEventListener: function(evt, handler) {{
                    if (evt === 'keydown') keyListeners.push(handler);
                }},
                removeEventListener: () => {{}}
            }},
            fetch: function() {{ return Promise.reject(new Error("fetch unmocked")); }},
            AbortController: globalThis.AbortController || function() {{ this.signal = {{}}; this.abort = function() {{}}; }},
            localStorage: {{
                getItem: (k) => storageStore[k] || null,
                setItem: (k, v) => {{ storageStore[k] = String(v); }},
                removeItem: (k) => {{ delete storageStore[k]; }}
            }},
            sessionStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', protocol: 'http:', host: '127.0.0.1:8000', origin: 'http://127.0.0.1:8000' }},
            addEventListener: function(evt, handler) {{
                if (evt === 'keydown') keyListeners.push(handler);
            }},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl,
            Date: Date,
            setInterval: setInterval,
            clearInterval: clearInterval,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            _dispatchKeydown: function(evtObj) {{
                keyListeners.forEach(fn => fn(evtObj));
            }}
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;
        sandbox.document = sandbox.document;
        sandbox.LightweightCharts = {{
            LineStyle: {{ Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 }}
        }};

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

    def test_s11_04_drawing_coord_shift_and_scale_mode_render(self):
        """S11-04: Coordinate shift triggers scheduleDrawingRender; paused when hidden or 0 drawings; scale modes trigger render."""
        js_code = """
        var renderCallCount = 0;
        scheduleDrawingRender = function() {
            renderCallCount++;
        };

        // 1. When drawings is empty, checkDrawingCoordinateShift should do nothing
        drawings = [];
        latestBarData = { close: 100 };
        var coordY = 200;
        candleSeries = {
            priceToCoordinate: function(p) { return coordY * (p / 100); }
        };

        checkDrawingCoordinateShift();
        var callsWhenEmpty = renderCallCount;

        // 2. Add a drawing and run check: first run records coordinates
        drawings = [{ type: "trendline" }];
        checkDrawingCoordinateShift();
        var callsInitial = renderCallCount;

        // 3. Shift coordinate significantly (simulating y-scale shift or zoom)
        coordY = 250;
        checkDrawingCoordinateShift();
        var callsAfterShift = renderCallCount;

        // 4. Document hidden should pause checks
        document.hidden = true;
        coordY = 300;
        checkDrawingCoordinateShift();
        var callsWhenHidden = renderCallCount;
        document.hidden = false;

        // 5. Calling setPriceScaleMode and setAutoScale triggers scheduleDrawingRender
        chart = {
            priceScale: function() { return { applyOptions: function() {} }; }
        };
        var callsBeforeScale = renderCallCount;
        setPriceScaleMode("log");
        var callsAfterScaleMode = renderCallCount;
        setAutoScale(true);
        var callsAfterAutoScale = renderCallCount;

        return {
            callsWhenEmpty: callsWhenEmpty,
            callsInitial: callsInitial,
            callsAfterShift: callsAfterShift,
            callsWhenHidden: callsWhenHidden,
            callsBeforeScale: callsBeforeScale,
            callsAfterScaleMode: callsAfterScaleMode,
            callsAfterAutoScale: callsAfterAutoScale
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["callsWhenEmpty"], 0)
        self.assertEqual(res["callsInitial"], 0)
        self.assertGreater(res["callsAfterShift"], res["callsInitial"])
        self.assertEqual(res["callsWhenHidden"], res["callsAfterShift"])
        self.assertGreater(res["callsAfterScaleMode"], res["callsBeforeScale"])
        self.assertGreater(res["callsAfterAutoScale"], res["callsAfterScaleMode"])

    def test_s11_06_quick_price_lines_scoped_per_symbol(self):
        """S11-06: Lines scoped per symbol in localStorage with 'ALERT (未啟用提醒)' title and restored on switch."""
        js_code = """
        var createdLines = [];
        var removedLines = [];
        candleSeries = {
            createPriceLine: function(opts) {
                var handle = { opts: opts, id: Math.random() };
                createdLines.push(handle);
                return handle;
            },
            removePriceLine: function(handle) {
                removedLines.push(handle);
            },
            setData: function() {}
        };
        loadHistory = function() {};
        updateSymbolMetrics = function() {};
        renderWatchlist = function() {};

        activeSymbol = "NASDAQ:NVDA";
        lastCrosshairPrice = 145.50;

        // Add regular price line (isAlert = false)
        _addQuickPriceLine(false);
        // Add alert price line (isAlert = true)
        _addQuickPriceLine(true);

        var nvdaStored = JSON.parse(localStorage.getItem("tv_price_lines_NASDAQ:NVDA"));
        var alertTitle = createdLines[1].opts.title;

        // Now switch symbol to AMEX:SPY
        switchSymbol("AMEX:SPY");

        var removedCountOnSwitch = removedLines.length;
        var activeHandlesOnSpy = tvPriceLineHandles.length;

        // Add a line on SPY
        lastCrosshairPrice = 580.0;
        _addQuickPriceLine(false);

        var spyStored = JSON.parse(localStorage.getItem("tv_price_lines_AMEX:SPY"));

        // Switch back to NVDA: should restore 2 lines from NVDA storage
        createdLines = [];
        switchSymbol("NASDAQ:NVDA");
        var restoredNvdaHandlesCount = tvPriceLineHandles.length;

        return {
            nvdaStoredCount: nvdaStored.length,
            alertTitle: alertTitle,
            removedCountOnSwitch: removedCountOnSwitch,
            activeHandlesOnSpy: activeHandlesOnSpy,
            spyStoredCount: spyStored.length,
            restoredNvdaHandlesCount: restoredNvdaHandlesCount
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["nvdaStoredCount"], 2)
        self.assertEqual(res["alertTitle"], "ALERT (未啟用提醒)")
        self.assertEqual(res["removedCountOnSwitch"], 2)
        self.assertEqual(res["activeHandlesOnSpy"], 0)
        self.assertEqual(res["spyStoredCount"], 1)
        self.assertEqual(res["restoredNvdaHandlesCount"], 2)

    def test_s11_07_switch_symbol_resets_draw_tool_and_in_progress(self):
        """S11-07: switchSymbol clears drawingInProgress and resets currentDrawTool to cursor."""
        js_code = """
        candleSeries = { setData: function() {} };
        loadHistory = function() {};
        updateSymbolMetrics = function() {};
        renderWatchlist = function() {};

        currentDrawTool = "trendline";
        drawingInProgress = { type: "trendline", price1: 100, price2: 120 };

        switchSymbol("NASDAQ:AAPL");

        return {
            toolAfterSwitch: currentDrawTool,
            drawingInProgressAfterSwitch: drawingInProgress
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["toolAfterSwitch"], "cursor")
        self.assertIsNone(res["drawingInProgressAfterSwitch"])

    def test_s11_10_alt_option_shortcuts_use_code_for_macos_deadkeys(self):
        """S11-10: Alt/Option shortcuts check e.code to work even when macOS dead-key produces special chars."""
        js_code = """
        initEventListeners();

        candleSeries = {
            createPriceLine: function(opts) {
                return { opts: opts };
            }
        };
        lastCrosshairPrice = 200.0;
        var toggleScreenerCalled = false;
        toggleScreenerModal = function() {
            toggleScreenerCalled = true;
        };

        // 1. Simulate Option+S on macOS: key is 'ß', code is 'KeyS'
        _dispatchKeydown({
            altKey: true,
            metaKey: false,
            ctrlKey: false,
            shiftKey: false,
            key: "ß",
            code: "KeyS",
            preventDefault: function() {}
        });

        // 2. Simulate Option+A on macOS: key is 'å', code is 'KeyA'
        _dispatchKeydown({
            altKey: true,
            metaKey: false,
            ctrlKey: false,
            shiftKey: false,
            key: "å",
            code: "KeyA",
            preventDefault: function() {}
        });

        var lastPriceLine = tvPriceLineHandles[tvPriceLineHandles.length - 1];

        return {
            toggleScreenerCalled: toggleScreenerCalled,
            lastPriceLineTitle: lastPriceLine ? lastPriceLine.opts.title : ""
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertTrue(res["toggleScreenerCalled"])
        self.assertEqual(res["lastPriceLineTitle"], "ALERT (未啟用提醒)")


if __name__ == "__main__":
    unittest.main()

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
                const listeners = {{}};
                const el = {{
                    _id: id,
                    _text: '',
                    _listeners: listeners,
                    title: '',
                    className: '',
                    style: {{}},
                    classList: {{
                        add: function() {{
                            var parts = (el.className || '').split(/\\s+/).filter(Boolean);
                            for (var ai = 0; ai < arguments.length; ai++) {{
                                if (parts.indexOf(arguments[ai]) === -1) parts.push(arguments[ai]);
                            }}
                            el.className = parts.join(' ');
                        }},
                        remove: function() {{
                            var rem = Array.prototype.slice.call(arguments);
                            var parts = (el.className || '').split(/\\s+/).filter(Boolean);
                            el.className = parts.filter(function(p) {{ return rem.indexOf(p) === -1; }}).join(' ');
                        }},
                        contains: function(c) {{ return (el.className || '').split(/\\s+/).indexOf(c) !== -1; }},
                        toggle: function(c, force) {{
                            if (force === undefined) force = !el.classList.contains(c);
                            if (force) el.classList.add(c); else el.classList.remove(c);
                        }}
                    }},
                    children: [],
                    appendChild: function(c) {{ el.children.push(c); }},
                    insertBefore: function(c, ref) {{ el.children.push(c); }},
                    addEventListener: function(evt, fn) {{
                        listeners[evt] = listeners[evt] || [];
                        listeners[evt].push(fn);
                    }},
                    removeEventListener: function() {{}},
                    dispatchEvent: function(evt, obj) {{
                        (listeners[evt] || []).forEach(function(fn) {{ fn(obj); }});
                    }},
                    getBoundingClientRect: function() {{
                        return {{ left: 0, top: 0, width: 800, height: 600 }};
                    }},
                    getContext: function() {{ return null; }},
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
        const winListeners = {{}};

        const sandbox = {{
            window: {{
                addEventListener: function(evt, handler) {{
                    if (evt === 'keydown') keyListeners.push(handler);
                    winListeners[evt] = winListeners[evt] || [];
                    winListeners[evt].push(handler);
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
                winListeners[evt] = winListeners[evt] || [];
                winListeners[evt].push(handler);
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
            }},
            _dispatchWindowEvent: function(evt, evtObj) {{
                (winListeners[evt] || []).forEach(fn => fn(evtObj));
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

    def test_rr_box_coexistence_allows_drawing_lines_and_preserves_cursor_edit(self):
        """Regression test for P0 drawing bug:
        (a) With an existing risk-reward box on canvas, clicking inside the box with
            trendline / hline / ruler succeeds in adding new drawings.
        (b) In cursor mode, dragging R:R body and clicking close (X) still work.
        (c) In non-cursor mode, clicking directly on close (X) does NOT delete the R:R box.
        """
        js_code = """
        initEventListeners();

        chart = {
            timeScale: function() {
                return {
                    coordinateToTime: function(x) { return 1700000000 + Math.round(x) * 60; },
                    timeToCoordinate: function(t) { return (t - 1700000000) / 60; },
                    coordinateToLogical: function(x) { return Math.round(x / 10); }
                };
            }
        };
        candlestickSeries = {
            coordinateToPrice: function(y) { return 200 - y * 0.25; },
            priceToCoordinate: function(p) { return (200 - p) / 0.25; }
        };
        renderDrawingCanvas = function() {};
        activeSymbol = "NASDAQ:AXON";

        var rrBox = {
            type: "risk-reward",
            time: 1700012000,
            entryPrice: 150,
            targetPrice: 175,
            stopPrice: 125,
            boxLeft: 200,
            boxRight: 400,
            targetTop: 100,
            targetHeight: 100,
            stopTop: 200,
            stopHeight: 100,
            targetHandleY: 100,
            stopHandleY: 300,
            closeBtnX: 386,
            closeBtnY: 114
        };
        drawings = [rrBox];

        var canvasEl = document.getElementById("drawingCanvas");
        function clickAt(x, y) {
            canvasEl.dispatchEvent("mousedown", { clientX: x, clientY: y, ctrlKey: false, metaKey: false });
        }

        // (c) Non-cursor mode clicking directly on R:R close button X must NOT delete R:R box
        currentDrawTool = "trendline";
        clickAt(386, 114);
        var rrPreservedOnCloseClick = drawings.some(function(d) { return d.type === "risk-reward"; });
        var startedOverCloseBtn = Boolean(drawingInProgress && drawingInProgress.type === "trendline");

        // (a1) Complete trendline second click inside R:R box body
        clickAt(320, 220);
        var countAfterTrendline = drawings.length;
        var dragDuringTrendline = isDraggingHandle;

        // (a2) Horizontal line inside R:R box body
        currentDrawTool = "hline";
        clickAt(280, 180);
        var countAfterHline = drawings.length;

        // (a3) Ruler two clicks inside R:R box body
        currentDrawTool = "ruler";
        clickAt(240, 150);
        clickAt(340, 240);
        var countAfterRuler = drawings.length;
        var typesAdded = drawings.slice(1).map(function(d) { return d.type; });

        // (b1) Cursor mode dragging R:R body
        currentDrawTool = "cursor";
        clickAt(260, 160);
        var dragHandleInCursor = isDraggingHandle ? isDraggingHandle.handle : null;
        canvasEl.dispatchEvent("mousemove", { clientX: 280, clientY: 140 });
        var entryAfterDrag = rrBox.entryPrice;
        _dispatchWindowEvent("mouseup", {});
        var dragClearedAfterMouseup = isDraggingHandle === null;

        // (b2) Cursor mode clicking X deletes R:R box
        clickAt(386, 114);
        var rrCountAfterCursorClose = drawings.filter(function(d) { return d.type === "risk-reward"; }).length;

        return {
            rrPreservedOnCloseClick: rrPreservedOnCloseClick,
            startedOverCloseBtn: startedOverCloseBtn,
            countAfterTrendline: countAfterTrendline,
            dragDuringTrendline: dragDuringTrendline,
            countAfterHline: countAfterHline,
            countAfterRuler: countAfterRuler,
            typesAdded: typesAdded,
            dragHandleInCursor: dragHandleInCursor,
            entryMovedUp: entryAfterDrag > 150,
            dragClearedAfterMouseup: dragClearedAfterMouseup,
            rrCountAfterCursorClose: rrCountAfterCursorClose
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertTrue(res["rrPreservedOnCloseClick"])
        self.assertTrue(res["startedOverCloseBtn"])
        self.assertEqual(res["countAfterTrendline"], 2)
        self.assertIsNone(res["dragDuringTrendline"])
        self.assertEqual(res["countAfterHline"], 3)
        self.assertEqual(res["countAfterRuler"], 4)
        self.assertEqual(res["typesAdded"], ["trendline", "hline", "ruler"])
        self.assertEqual(res["dragHandleInCursor"], "body")
        self.assertTrue(res["entryMovedUp"])
        self.assertTrue(res["dragClearedAfterMouseup"])
        self.assertEqual(res["rrCountAfterCursorClose"], 0)

    def test_switch_tool_clears_hover_cursor_classes(self):
        """Fix 1: switching from cursor to a non-cursor drawing tool clears stale hover cursor classes."""
        js_code = """
        var origQsa = document.querySelectorAll;
        var cursorBtn = document.createElement('button');
        cursorBtn.setAttribute('data-tool', 'cursor');
        cursorBtn.classList.add('draw-tool-btn', 'active');
        var trendBtn = document.createElement('button');
        trendBtn.setAttribute('data-tool', 'trendline');
        trendBtn.classList.add('draw-tool-btn');
        document.querySelectorAll = function(sel) {
            if (sel && sel.indexOf('.draw-tool-btn') !== -1) return [cursorBtn, trendBtn];
            return origQsa ? origQsa(sel) : [];
        };

        initEventListeners();

        var canvasEl = document.getElementById('drawingCanvas');
        canvasEl.classList.add('interactive', 'cursor-pointer', 'cursor-move', 'cursor-ns-resize');

        // Clicking trendline button must remove all cursor-* hover classes immediately
        trendBtn.onclick();
        var afterBtnClick = {
            tool: currentDrawTool,
            hasDrawingActive: canvasEl.classList.contains('drawing-active'),
            hasPointer: canvasEl.classList.contains('cursor-pointer'),
            hasMove: canvasEl.classList.contains('cursor-move'),
            hasNsResize: canvasEl.classList.contains('cursor-ns-resize')
        };

        // Re-adding stale hover class and triggering chartContainer mousemove must also clear it
        canvasEl.classList.add('cursor-pointer');
        document.getElementById('chartContainer').dispatchEvent('mousemove', { clientX: 100, clientY: 100 });
        var afterMousemove = {
            hasPointer: canvasEl.classList.contains('cursor-pointer'),
            hasInteractive: canvasEl.classList.contains('interactive')
        };

        return { afterBtnClick: afterBtnClick, afterMousemove: afterMousemove };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["afterBtnClick"]["tool"], "trendline")
        self.assertTrue(res["afterBtnClick"]["hasDrawingActive"])
        self.assertFalse(res["afterBtnClick"]["hasPointer"])
        self.assertFalse(res["afterBtnClick"]["hasMove"])
        self.assertFalse(res["afterBtnClick"]["hasNsResize"])
        self.assertFalse(res["afterMousemove"]["hasPointer"])
        self.assertTrue(res["afterMousemove"]["hasInteractive"])

    def test_esc_cancels_in_progress_drawing_before_resetting_tool(self):
        """Fix 2: first Esc cancels drawingInProgress while keeping tool; second Esc resets to cursor."""
        js_code = """
        initEventListeners();

        var canvasEl = document.getElementById('drawingCanvas');
        currentDrawTool = 'trendline';
        canvasEl.classList.add('drawing-active');
        drawingInProgress = { type: 'trendline', step: 1, time1: 1700000000, price1: 150, currentX: 120, currentY: 200 };
        var renderCalls = 0;
        renderDrawingCanvas = function() { renderCalls++; };

        // First Escape: cancels drawingInProgress, preserves trendline tool and drawing-active
        _dispatchKeydown({ key: 'Escape', metaKey: false, ctrlKey: false, altKey: false });
        var afterFirstEsc = {
            drawingInProgress: drawingInProgress,
            tool: currentDrawTool,
            hasDrawingActive: canvasEl.classList.contains('drawing-active'),
            renderCalls: renderCalls
        };

        // Second Escape (when drawingInProgress is null): closes modals and resets tool to cursor
        document.getElementById('indicatorModal').style.display = 'flex';
        _dispatchKeydown({ key: 'Escape', metaKey: false, ctrlKey: false, altKey: false });
        var afterSecondEsc = {
            drawingInProgress: drawingInProgress,
            tool: currentDrawTool,
            hasDrawingActive: canvasEl.classList.contains('drawing-active'),
            modalDisplay: document.getElementById('indicatorModal').style.display
        };

        return { afterFirstEsc: afterFirstEsc, afterSecondEsc: afterSecondEsc };
        """
        res = self._execute_node_eval(js_code)
        self.assertIsNone(res["afterFirstEsc"]["drawingInProgress"])
        self.assertEqual(res["afterFirstEsc"]["tool"], "trendline")
        self.assertTrue(res["afterFirstEsc"]["hasDrawingActive"])
        self.assertGreaterEqual(res["afterFirstEsc"]["renderCalls"], 1)

        self.assertIsNone(res["afterSecondEsc"]["drawingInProgress"])
        self.assertEqual(res["afterSecondEsc"]["tool"], "cursor")
        self.assertFalse(res["afterSecondEsc"]["hasDrawingActive"])
        self.assertEqual(res["afterSecondEsc"]["modalDisplay"], "none")


if __name__ == "__main__":
    unittest.main()

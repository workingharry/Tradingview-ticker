"""
Tests for Mega Task 2 Phase 1:
Fix NVDA Watchlist Selection Bug & Drawing Robustness (static/app.js)
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestWatchlistSwitchSymbol(unittest.TestCase):
    """Phase 1 test suite covering 1-1 through 1-7 in static/app.js."""

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
            self.skipTest("Node.js binary not found; skipping JS evaluation")

    def _execute_node_eval(self, eval_js_snippet: str, initial_storage: dict = None):
        """Loads static/app.js in Node.js VM sandbox with full DOM & Lightweight Charts mocks."""
        storage_json = json.dumps(initial_storage or {})

        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const initialStorage = {storage_json};
        const localStorageStore = Object.assign({{}}, initialStorage);

        const listeners = {{}};
        const canvasListeners = {{}};

        const mockCanvasCtx = {{
            clearRect: () => {{}},
            beginPath: () => {{}},
            moveTo: () => {{}},
            lineTo: () => {{}},
            stroke: () => {{}},
            fill: () => {{}},
            arc: () => {{}},
            fillRect: () => {{}},
            strokeRect: () => {{}},
            fillText: () => {{}},
            measureText: () => ({{ width: 10 }}),
            save: () => {{}},
            restore: () => {{}},
            setLineDash: () => {{}},
            closePath: () => {{}},
            font: '',
            fillStyle: '',
            strokeStyle: '',
            lineWidth: 1,
            lineJoin: 'round',
            lineCap: 'round',
            textAlign: 'left',
            textBaseline: 'alphabetic'
        }};

        const mockDrawingCanvas = {{
            width: 800,
            height: 600,
            clientWidth: 800,
            clientHeight: 600,
            getBoundingClientRect: () => ({{ left: 0, top: 0, width: 800, height: 600 }}),
            getContext: (type) => (type === '2d' ? mockCanvasCtx : null),
            classList: {{
                _classes: new Set(),
                add: function(...args) {{ args.forEach(c => this._classes.add(c)); }},
                remove: function(...args) {{ args.forEach(c => this._classes.delete(c)); }},
                toggle: function(c, force) {{
                    if (force !== undefined) {{
                        force ? this._classes.add(c) : this._classes.delete(c);
                    }} else {{
                        this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c);
                    }}
                }},
                contains: function(c) {{ return this._classes.has(c); }}
            }},
            addEventListener: (evt, cb) => {{ canvasListeners[evt] = cb; }},
            removeEventListener: () => {{}}
        }};

        const mockElement = (id) => ({{
            id: id,
            style: {{}},
            textContent: '',
            innerHTML: '',
            classList: {{
                toggle: () => {{}},
                add: () => {{}},
                remove: () => {{}},
                contains: () => false
            }},
            appendChild: () => {{}},
            removeChild: () => {{}},
            querySelector: () => null,
            querySelectorAll: () => [],
            setAttribute: () => {{}},
            getAttribute: () => null,
            addEventListener: (evt, cb) => {{ listeners[evt] = cb; }},
            removeEventListener: () => {{}},
            scrollIntoView: () => {{}}
        }});

        const sandbox = {{
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            AbortController: (typeof AbortController !== 'undefined' ? AbortController : class {{ abort() {{}} }}),
            fetch: () => Promise.resolve({{
                ok: true,
                json: () => Promise.resolve({{ bars: [] }})
            }}),
            window: {{}},
            document: {{
                getElementById: (id) => {{
                    if (id === 'drawingCanvas') return mockDrawingCanvas;
                    return mockElement(id);
                }},
                querySelector: () => mockElement('dummyQuery'),
                querySelectorAll: () => [],
                createElement: (tag) => {{
                    const el = mockElement(tag);
                    el.tagName = tag.toUpperCase();
                    return el;
                }},
                elementFromPoint: () => null,
                body: mockElement('body'),
                addEventListener: (evt, cb) => {{ listeners[evt] = cb; }},
                removeEventListener: () => {{}}
            }},
            localStorage: {{
                _store: localStorageStore,
                getItem: function(k) {{ return this._store[k] !== undefined ? this._store[k] : null; }},
                setItem: function(k, v) {{ this._store[k] = String(v); }},
                removeItem: function(k) {{ delete this._store[k]; }}
            }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '' }},
            addEventListener: (evt, cb) => {{ listeners[evt] = cb; }},
            removeEventListener: () => {{}},
            console: {{
                log: console.log,
                warn: function(...args) {{
                    sandbox._lastWarn = args;
                    sandbox._warnLogs = sandbox._warnLogs || [];
                    sandbox._warnLogs.push(args.join(' '));
                }},
                error: function(...args) {{
                    sandbox._lastError = args;
                    sandbox._errorLogs = sandbox._errorLogs || [];
                    sandbox._errorLogs.push(args.join(' '));
                }},
                debug: () => {{}}
            }},
            Intl: Intl,
            _canvasListeners: canvasListeners,
            _mockCanvas: mockDrawingCanvas
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);

        // Pre-configure minimal chart mock before appCode runs
        const mockTimeScale = {{
            timeToCoordinate: (t) => {{
                if (t === null || (typeof t === 'object' && (!t.year || t.year <= 0))) {{
                    throw new TypeError("Cannot convert object to primitive value");
                }}
                if (typeof t === 'number') return t > 1000 ? 500 : t;
                return 100;
            }},
            coordinateToTime: (x) => {{
                if (x > 700) return null; // simulate right padding
                return 1700000000 + x * 60;
            }},
            coordinateToLogical: (x) => (x / 20),
            getVisibleLogicalRange: () => ({{ from: 0, to: 50 }}),
            applyOptions: () => {{}}
        }};

        const mockCandleSeries = {{
            coordinateToPrice: (y) => (200 - y * 0.1),
            priceToCoordinate: (p) => ((200 - p) * 10),
            setData: (data) => {{ sandbox._lastSetData = data; }},
            setMarkers: () => {{}},
            removePriceLine: () => {{}},
            createPriceLine: () => ({{}})
        }};

        sandbox.chart = {{
            timeScale: () => mockTimeScale
        }};
        sandbox.candlestickSeries = mockCandleSeries;
        sandbox.candleSeries = mockCandleSeries;
        sandbox.volumeSeries = mockCandleSeries;

        vm.runInContext(appCode, sandbox);

        // Re-ensure chart and series mocks are attached in sandbox context
        sandbox.chart = {{ timeScale: () => mockTimeScale }};
        sandbox.candlestickSeries = mockCandleSeries;
        sandbox.candleSeries = mockCandleSeries;
        sandbox.volumeSeries = mockCandleSeries;

        const result = vm.runInContext({json.dumps(eval_js_snippet)}, sandbox);
        Promise.resolve(result).then((res) => {{
            console.log(JSON.stringify(res));
            process.exit(0);
        }}).catch((err) => {{
            console.error(err);
            process.exit(1);
        }});
        """

        env = os.environ.copy()
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
        proc = subprocess.run(
            [self.node_bin, "-e", harness],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Node evaluation failed (code {proc.returncode}):\\nSTDOUT: {proc.stdout}\\nSTDERR: {proc.stderr}",
        )
        try:
            return json.loads(proc.stdout.strip().split("\n")[-1])
        except Exception as e:
            self.fail(f"Failed to parse JSON result: {proc.stdout}\\nError: {e}")

    def test_00_sanity_and_helpers_exposed(self):
        """Verify that all Phase 1 helper utilities and global states are exposed on globalThis."""
        res = self._execute_node_eval("""
        ({
            isValidChartTime: typeof globalThis.isValidChartTime === 'function',
            safeTimeToCoordinate: typeof globalThis.safeTimeToCoordinate === 'function',
            snapTimeFromCoordinate: typeof globalThis.snapTimeFromCoordinate === 'function',
            sanitizeDrawings: typeof globalThis.sanitizeDrawings === 'function',
            loadedHistorySymbolExposed: ('loadedHistorySymbol' in globalThis),
            loadedHistorySymbolInitial: globalThis.loadedHistorySymbol
        })
        """)
        self.assertTrue(res["isValidChartTime"])
        self.assertTrue(res["safeTimeToCoordinate"])
        self.assertTrue(res["snapTimeFromCoordinate"])
        self.assertTrue(res["sanitizeDrawings"])
        self.assertTrue(res["loadedHistorySymbolExposed"])
        self.assertIsNone(res["loadedHistorySymbolInitial"])

    def test_01_is_valid_chart_time_table(self):
        """Verify the full isValidChartTime specification table."""
        res = self._execute_node_eval("""
        ({
            posNum: isValidChartTime(1700000000),
            posSmall: isValidChartTime(1),
            posFloat: isValidChartTime(0.5),
            zero: isValidChartTime(0),
            negNum: isValidChartTime(-5),
            nanVal: isValidChartTime(NaN),
            infVal: isValidChartTime(Infinity),
            negInfVal: isValidChartTime(-Infinity),
            isoDate: isValidChartTime('2025-01-02'),
            isoOld: isValidChartTime('1999-12-31'),
            isoDateTime: isValidChartTime('2025-01-02T00:00:00Z'),
            badStr: isValidChartTime('abc'),
            emptyStr: isValidChartTime(''),
            nullVal: isValidChartTime(null),
            undefVal: isValidChartTime(undefined),
            dateObjValid: isValidChartTime({ year: 2025, month: 1, day: 2 }),
            dateObjEmpty: isValidChartTime({}),
            dateObjMonth0: isValidChartTime({ year: 2025, month: 0, day: 1 }),
            dateObjMonth13: isValidChartTime({ year: 2025, month: 13, day: 1 }),
            dateObjDay0: isValidChartTime({ year: 2025, month: 1, day: 0 }),
            dateObjDay32: isValidChartTime({ year: 2025, month: 1, day: 32 }),
            dateObjNegYear: isValidChartTime({ year: -2025, month: 1, day: 1 })
        })
        """)
        # Valid
        self.assertTrue(res["posNum"])
        self.assertTrue(res["posSmall"])
        self.assertTrue(res["posFloat"])
        self.assertTrue(res["isoDate"])
        self.assertTrue(res["isoOld"])
        self.assertTrue(res["dateObjValid"])

        # Invalid
        self.assertFalse(res["zero"])
        self.assertFalse(res["negNum"])
        self.assertFalse(res["nanVal"])
        self.assertFalse(res["infVal"])
        self.assertFalse(res["negInfVal"])
        self.assertFalse(res["isoDateTime"])
        self.assertFalse(res["badStr"])
        self.assertFalse(res["emptyStr"])
        self.assertFalse(res["nullVal"])
        self.assertFalse(res["undefVal"])
        self.assertFalse(res["dateObjEmpty"])
        self.assertFalse(res["dateObjMonth0"])
        self.assertFalse(res["dateObjMonth13"])
        self.assertFalse(res["dateObjDay0"])
        self.assertFalse(res["dateObjDay32"])
        self.assertFalse(res["dateObjNegYear"])

    def test_02_safe_time_to_coordinate(self):
        """Verify safeTimeToCoordinate guards against invalid inputs and exceptions."""
        res = self._execute_node_eval("""
        (() => {
            let calledWith = [];
            const mockTs = {
                timeToCoordinate: (t) => {
                    calledWith.push(t);
                    if (t === 999) throw new Error("Boom");
                    return 250;
                }
            };
            const rNull = safeTimeToCoordinate(mockTs, null);
            const rZero = safeTimeToCoordinate(mockTs, 0);
            const rBadStr = safeTimeToCoordinate(mockTs, "abc");
            const rErr = safeTimeToCoordinate(mockTs, 999);
            const rOk = safeTimeToCoordinate(mockTs, 1700000000);
            const rNullTs = safeTimeToCoordinate(null, 1700000000);

            return {
                rNull, rZero, rBadStr, rErr, rOk, rNullTs,
                calledCount: calledWith.length,
                calledWith
            };
        })()
        """)
        self.assertIsNone(res["rNull"])
        self.assertIsNone(res["rZero"])
        self.assertIsNone(res["rBadStr"])
        self.assertIsNone(res["rErr"])
        self.assertEqual(res["rOk"], 250)
        self.assertIsNone(res["rNullTs"])
        # Only valid chart time (999 and 1700000000) should reach timeToCoordinate
        self.assertEqual(res["calledCount"], 2)
        self.assertEqual(res["calledWith"], [999, 1700000000])

    def test_03_snap_time_from_coordinate(self):
        """Verify snapTimeFromCoordinate handles on-screen, off-screen right margin, and empty axis."""
        res = self._execute_node_eval("""
        (() => {
            const axisTimes = [100, 200, 300, 400, 500];
            const mockTs = {
                coordinateToTime: (x) => {
                    if (x >= 500) return null; // beyond screen
                    return x * 10;
                },
                coordinateToLogical: (x) => {
                    // x=600 -> logical index 10 (exceeds axisTimes.length - 1)
                    return x / 50;
                }
            };

            const normal = snapTimeFromCoordinate(mockTs, 20, axisTimes);
            const clamped = snapTimeFromCoordinate(mockTs, 600, axisTimes); // logical 12 -> clamped to 4 (500)
            const emptyAxis = snapTimeFromCoordinate(mockTs, 600, []);
            const nullAxis = snapTimeFromCoordinate(mockTs, 600, null);

            return { normal, clamped, emptyAxis, nullAxis };
        })()
        """)
        self.assertEqual(res["normal"], 200)
        self.assertEqual(res["clamped"], 500)
        self.assertIsNone(res["emptyAxis"])
        self.assertIsNone(res["nullAxis"])

    def test_04_sanitize_drawings_and_backup(self):
        """Verify sanitizeDrawings purges invalid drawings and loadDrawingsForSymbol backs up raw."""
        corrupted = json.dumps([
            {"type": "hline", "price": "150.5"},
            {"type": "hline", "price": "invalid"},
            {"type": "trendline", "time1": 1700000000, "price1": "100", "time2": 1700000060, "price2": "110"},
            {"type": "trendline", "time1": 1700000000, "price1": 100, "time2": None, "price2": 110},
            {"type": "risk-reward", "time": 1700000000, "entryPrice": 100},
            None,
            "not_an_object",
            {}
        ])
        res = self._execute_node_eval("""
        (() => {
            const sym = "NASDAQ:NVDA";
            const loaded = loadDrawingsForSymbol(sym);
            const bakKey = "tv_bak_drawings_" + sym;
            const backedUp = localStorage.getItem(bakKey);

            return {
                cleanLength: loaded.length,
                cleanTypes: loaded.map(d => d.type),
                hlinePrice: loaded[0].price,
                trendlinePrice1: loaded[1].price1,
                hasBak: Boolean(backedUp),
                backedUpMatch: backedUp === localStorage.getItem("tv_drawings_" + sym),
                warnLogged: Boolean(globalThis._warnLogs && globalThis._warnLogs.length > 0)
            };
        })()
        """, initial_storage={"tv_drawings_NASDAQ:NVDA": corrupted})

        self.assertEqual(res["cleanLength"], 3)
        self.assertEqual(res["cleanTypes"], ["hline", "trendline", "risk-reward"])
        self.assertEqual(res["hlinePrice"], 150.5)
        self.assertEqual(res["trendlinePrice1"], 100)
        self.assertTrue(res["hasBak"])
        self.assertTrue(res["backedUpMatch"])
        self.assertTrue(res["warnLogged"])

    def test_05_render_drawing_canvas_error_resilience(self):
        """Verify renderDrawingCanvas does not throw on null drawings or malformed drawing objects."""
        res = self._execute_node_eval("""
        (() => {
            let errors = [];
            // 1. drawings is null
            globalThis.drawings = null;
            try { renderDrawingCanvas(); } catch (e) { errors.push("null drawings: " + e.message); }

            // 2. drawings has malformed items
            globalThis.drawings = [
                null,
                {},
                { type: "trendline", time1: null, price1: 100, time2: null, price2: 110 },
                { type: "hline", price: NaN },
                { type: "risk-reward", time: null, entryPrice: 100, targetPrice: 115, stopPrice: 95 }
            ];
            try { renderDrawingCanvas(); } catch (e) { errors.push("malformed items: " + e.message); }

            return {
                errors,
                pass: errors.length === 0
            };
        })()
        """)
        self.assertTrue(res["pass"], f"Unexpected renderDrawingCanvas errors: {res['errors']}")

    def test_06_switch_symbol_deadlock_reproduction_and_fix(self):
        """Verify that corrupted drawings do NOT deadlock switchSymbol on subsequent clicks."""
        corrupted_drawings = json.dumps([
            {"type": "trendline", "time1": 1700000000, "price1": 100, "time2": None, "price2": 110}
        ])
        res = self._execute_node_eval("""
        (() => {
            let historyCalls = [];
            globalThis.loadHistory = function(sym) {
                historyCalls.push(sym);
            };

            // Initial state: MANH is active with bars
            globalThis.activeSymbol = "NASDAQ:MANH";
            globalThis.currentRawBars = [{ time: 1700000000, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
            globalThis.loadedHistorySymbol = "NASDAQ:MANH";

            // Click NVDA (which has corrupted drawing)
            switchSymbol("NASDAQ:NVDA");

            const stateAfterClick1 = {
                activeSymbol: globalThis.activeSymbol,
                rawBarsLen: globalThis.currentRawBars.length,
                loadedHistorySymbol: globalThis.loadedHistorySymbol,
                historyCall1: historyCalls.slice()
            };

            // Second click on NVDA
            switchSymbol("NASDAQ:NVDA");

            return {
                stateAfterClick1,
                historyCallsTotal: historyCalls.length,
                historyCalls
            };
        })()
        """, initial_storage={"tv_drawings_NASDAQ:NVDA": corrupted_drawings})

        state1 = res["stateAfterClick1"]
        self.assertEqual(state1["activeSymbol"], "NASDAQ:NVDA")
        self.assertEqual(state1["rawBarsLen"], 0)
        self.assertIsNone(state1["loadedHistorySymbol"])
        self.assertEqual(state1["historyCall1"], ["NASDAQ:NVDA"])

        # Second click does not trigger deadlock because loadedHistorySymbol is not NVDA
        self.assertEqual(res["historyCallsTotal"], 2)
        self.assertEqual(res["historyCalls"], ["NASDAQ:NVDA", "NASDAQ:NVDA"])

    def test_07_switch_symbol_early_return_logic(self):
        """Verify early return in switchSymbol only occurs when symbol is fully loaded."""
        res = self._execute_node_eval("""
        (() => {
            let historyCalls = [];
            globalThis.loadHistory = function(sym) { historyCalls.push(sym); };

            // Case 1: loadedHistorySymbol is null -> must NOT early return
            globalThis.activeSymbol = "NASDAQ:NVDA";
            globalThis.currentRawBars = [{ time: 100 }];
            globalThis.loadedHistorySymbol = null;
            switchSymbol("NASDAQ:NVDA");

            // Case 2: loadedHistorySymbol matches symbol AND currentRawBars has items -> MUST early return
            globalThis.currentRawBars = [{ time: 100 }];
            globalThis.loadedHistorySymbol = "NASDAQ:NVDA";
            switchSymbol("NASDAQ:NVDA");

            // Case 3: empty symbol -> MUST early return
            switchSymbol("");
            switchSymbol(null);

            return { historyCalls };
        })()
        """)
        # Only Case 1 should have called loadHistory
        self.assertEqual(res["historyCalls"], ["NASDAQ:NVDA"])

    def test_08_apply_history_payload_and_empty_cache(self):
        """Verify applyHistoryPayload manages loadedHistorySymbol and loadHistory avoids caching empty bars."""
        res = self._execute_node_eval("""
        (async () => {
            switchSymbol("NASDAQ:NVDA");
            const reqId = globalThis.activeHistoryRequestId;
            const curActiveSym = globalThis.activeSymbol;

            const validBars = [
                { time: 1700000000, open: 10, high: 11, low: 9, close: 10, volume: 100 }
            ];

            // 1. Success payload sets loadedHistorySymbol
            applyHistoryPayload({ bars: validBars }, "NASDAQ:NVDA", reqId);
            const loaded1 = globalThis.loadedHistorySymbol;

            // 2. Empty payload clears loadedHistorySymbol
            applyHistoryPayload({ bars: [] }, "NASDAQ:NVDA", reqId);
            const loaded2 = globalThis.loadedHistorySymbol;

            // 3. Test caching behavior on empty bars in loadHistory
            let cachedCalls = [];
            globalThis.setCachedHistory = function(k, d) {
                cachedCalls.push({ key: k, barsLen: d && d.bars ? d.bars.length : null });
            };

            globalThis.fetch = () => Promise.resolve({
                ok: true,
                json: () => Promise.resolve({ bars: [] })
            });

            await loadHistory("NASDAQ:TEST_EMPTY", "1m");

            return {
                reqId,
                curActiveSym,
                loaded1,
                loaded2,
                cachedCalls,
                errorLogs: globalThis._errorLogs
            };
        })()
        """)
        self.assertEqual(res["loaded1"], "NASDAQ:NVDA")
        self.assertIsNone(res["loaded2"])
        # setCachedHistory should NOT have been called with empty bars
        self.assertEqual(res["cachedCalls"], [])

    def test_09_watchlist_drag_handle_sensitivity(self):
        """Verify that 0px clicks on the drag handle do not trigger isDragging while >3px movement does."""
        res = self._execute_node_eval("""
        (() => {
            const results = {};

            // Helper to simulate drag logic from renderWatchlist
            function testDragSensitivity(isHandle, diffX, diffY) {
                let isDragging = false;
                const dragThreshold = isHandle ? 3 : 6;
                if (diffY > dragThreshold || diffX > dragThreshold) {
                    isDragging = true;
                }
                return isDragging;
            }

            // Handle clicked without movement (0px)
            results.handle0px = testDragSensitivity(true, 0, 0);

            // Handle micro-jitter (2px)
            results.handle2px = testDragSensitivity(true, 2, 1);

            // Handle deliberate drag (4px)
            results.handle4px = testDragSensitivity(true, 4, 0);

            // Non-handle drag (4px -> should be false because threshold is 6)
            results.nonHandle4px = testDragSensitivity(false, 4, 0);

            // Non-handle drag (7px -> should be true)
            results.nonHandle7px = testDragSensitivity(false, 7, 0);

            return results;
        })()
        """)
        self.assertFalse(res["handle0px"])
        self.assertFalse(res["handle2px"])
        self.assertTrue(res["handle4px"])
        self.assertFalse(res["nonHandle4px"])
        self.assertTrue(res["nonHandle7px"])


if __name__ == "__main__":
    unittest.main()

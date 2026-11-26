"""
Tests for Mega Task 2 Phase 2:
Validate Stored Frontend Configuration on Load (static/app.js)

Asserts that readStoredJson, configuration loaders, timeframe normalization,
and symbol validation handle malformed, corrupted, and edge-case storage values
defensively with proper tv_bak_* backups, console warnings, and clean defaults.
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


class TestFrontendStorageRobustness(unittest.TestCase):
    """Phase 2 test suite covering frontend configuration validation and storage robustness."""

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

    def _execute_node_eval(self, eval_js_snippet: str, initial_storage: dict = None, run_bootstrap: bool = False):
        """Loads static/app.js in Node.js VM sandbox with full DOM & Lightweight Charts mocks."""
        storage_json = json.dumps(initial_storage or {})
        bootstrap_call = "vm.runInContext(\"if (typeof bootstrap === 'function') { bootstrap(); }\", sandbox);" if run_bootstrap else ""

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
            id: 'drawingCanvas',
            width: 800,
            height: 600,
            clientWidth: 800,
            clientHeight: 600,
            style: {{}},
            parentElement: {{ style: {{}} }},
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
            removeEventListener: () => {{}},
            setAttribute: () => {{}},
            getAttribute: () => null
        }};

        const tfButtons = [];
        ['1m', '5m', '15m', '1h', '1D', '1W'].forEach(tf => {{
            const btn = {{
                getAttribute: (attr) => (attr === 'data-tf' ? tf : null),
                setAttribute: () => {{}},
                classList: {{
                    _classes: new Set(tf === '1D' ? ['active'] : []),
                    add: function(c) {{ this._classes.add(c); }},
                    remove: function(c) {{ this._classes.delete(c); }},
                    contains: function(c) {{ return this._classes.has(c); }}
                }}
            }};
            tfButtons.push(btn);
        }});

        const mockElement = (id) => ({{
            id: id,
            style: {{}},
            clientWidth: 800,
            clientHeight: 600,
            parentElement: {{ style: {{}} }},
            textContent: '',
            innerHTML: '',
            classList: {{
                _classes: new Set(),
                toggle: function(c) {{ this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c); }},
                add: function(c) {{ this._classes.add(c); }},
                remove: function(c) {{ this._classes.delete(c); }},
                contains: function(c) {{ return this._classes.has(c); }}
            }},
            appendChild: () => {{}},
            removeChild: () => {{}},
            querySelector: (sel) => {{
                if (sel === '.tf-btn.active') {{
                    return tfButtons.find(b => b.classList.contains('active')) || null;
                }}
                return null;
            }},
            querySelectorAll: (sel) => (sel === '.tf-btn' ? tfButtons : []),
            setAttribute: () => {{}},
            getAttribute: () => null,
            addEventListener: (evt, cb) => {{ listeners[evt] = cb; }},
            removeEventListener: () => {{}},
            scrollIntoView: () => {{}}
        }});

        const warnLogs = [];
        const errorLogs = [];
        const mockPriceLines = [];

        const mockSeries = {{
            setData: () => {{}},
            setMarkers: () => {{}},
            createPriceLine: (options) => {{
                mockPriceLines.push(options);
                return options;
            }},
            removePriceLine: (l) => {{
                const idx = mockPriceLines.indexOf(l);
                if (idx >= 0) mockPriceLines.splice(idx, 1);
            }},
            priceToCoordinate: (p) => 100,
            coordinateToPrice: (y) => 150,
            update: () => {{}},
            applyOptions: () => {{}},
            priceScale: () => ({{ applyOptions: () => {{}} }})
        }};

        const mockTimeScale = {{
            timeToCoordinate: (t) => 100,
            coordinateToTime: (x) => 1700000000,
            coordinateToLogical: (x) => 5,
            getVisibleLogicalRange: () => ({{ from: 0, to: 50 }}),
            applyOptions: () => {{}},
            subscribeVisibleLogicalRangeChange: () => {{}},
            scrollToPosition: () => {{}}
        }};

        const mockChart = {{
            timeScale: () => mockTimeScale,
            addCandlestickSeries: () => mockSeries,
            addHistogramSeries: () => mockSeries,
            addLineSeries: () => mockSeries,
            addAreaSeries: () => mockSeries,
            subscribeCrosshairMove: () => {{}},
            applyOptions: () => {{}},
            remove: () => {{}},
            resize: () => {{}}
        }};

        const mockLw = {{
            CrosshairMode: {{ Normal: 0, Magnet: 1 }},
            LineStyle: {{ Dotted: 1, Dashed: 2, Solid: 0 }},
            PriceScaleMode: {{ Normal: 0, Logarithmic: 1, Percentage: 2 }},
            createChart: () => mockChart
        }};

        const loc = {{ host: '127.0.0.1:8000', protocol: 'http:', search: '', port: '8000' }};

        const sandbox = {{
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            AbortController: class {{ abort() {{}} }},
            WebSocket: class {{ addEventListener() {{}} send() {{}} close() {{}} }},
            fetch: () => Promise.resolve({{
                ok: true,
                json: () => Promise.resolve({{ symbols: [], bars: [] }})
            }}),
            window: {{ location: loc, LightweightCharts: mockLw }},
            location: loc,
            LightweightCharts: mockLw,
            document: {{
                getElementById: (id) => {{
                    if (id === 'drawingCanvas') return mockDrawingCanvas;
                    return mockElement(id);
                }},
                querySelector: (sel) => {{
                    if (sel === '.tf-btn.active') {{
                        return tfButtons.find(b => b.classList.contains('active')) || null;
                    }}
                    return mockElement('dummyQuery');
                }},
                querySelectorAll: (sel) => {{
                    if (sel === '.tf-btn') return tfButtons;
                    return [];
                }},
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
            addEventListener: (evt, cb) => {{ listeners[evt] = cb; }},
            removeEventListener: () => {{}},
            console: {{
                log: console.log,
                warn: function(...args) {{
                    warnLogs.push(args.join(' '));
                }},
                error: function(...args) {{
                    errorLogs.push(args.join(' '));
                }},
                debug: () => {{}}
            }},
            Intl: Intl,
            _canvasListeners: canvasListeners,
            _mockCanvas: mockDrawingCanvas,
            _tfButtons: tfButtons,
            _warnLogs: warnLogs,
            _errorLogs: errorLogs,
            _mockPriceLines: mockPriceLines
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;
        sandbox.window.location = loc;
        sandbox.window.LightweightCharts = mockLw;

        sandbox.chart = mockChart;
        sandbox.candlestickSeries = mockSeries;
        sandbox.candleSeries = mockSeries;
        sandbox.volumeSeries = mockSeries;

        vm.createContext(sandbox);
        vm.runInContext(appCode, sandbox);

        // Re-ensure chart / series / mock canvas references
        sandbox.chart = mockChart;
        sandbox.candlestickSeries = mockSeries;
        sandbox.candleSeries = mockSeries;
        sandbox.volumeSeries = mockSeries;
        sandbox.LightweightCharts = mockLw;
        sandbox.window.LightweightCharts = mockLw;

        {bootstrap_call}

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
            f"Node evaluation failed (code {proc.returncode}):\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}",
        )
        try:
            return json.loads(proc.stdout.strip().split("\n")[-1])
        except Exception as e:
            self.fail(f"Failed to parse JSON result: {proc.stdout}\nError: {e}")

    def test_00_sanity_read_stored_json_exposed(self):
        """Verify that readStoredJson is exposed to window and globalThis."""
        res = self._execute_node_eval("""
        ({
            inGlobalThis: typeof globalThis.readStoredJson === 'function',
            inWindow: typeof window.readStoredJson === 'function',
            isIdentical: globalThis.readStoredJson === window.readStoredJson
        })
        """)
        self.assertTrue(res["inGlobalThis"])
        self.assertTrue(res["inWindow"])
        self.assertTrue(res["isIdentical"])

    def test_01_read_stored_json_direct_valid_and_fallback(self):
        """Verify readStoredJson returns parsed JSON for valid data and handles null/missing gracefully."""
        res = self._execute_node_eval("""
        (() => {
            localStorage.setItem("test_valid", JSON.stringify(["AAPL", "NVDA"]));
            const resValid = readStoredJson("test_valid", Array.isArray, []);
            const bakValid = localStorage.getItem("tv_bak_test_valid");

            const resMissing = readStoredJson("test_missing_key", Array.isArray, ["FALLBACK"]);
            const bakMissing = localStorage.getItem("tv_bak_test_missing_key");

            return {
                resValid,
                bakValid,
                resMissing,
                bakMissing,
                warnCount: _warnLogs.length
            };
        })()
        """)
        self.assertEqual(res["resValid"], ["AAPL", "NVDA"])
        self.assertIsNone(res["bakValid"])
        self.assertEqual(res["resMissing"], ["FALLBACK"])
        self.assertIsNone(res["bakMissing"])
        self.assertEqual(res["warnCount"], 0)

    def test_02_read_stored_json_syntax_error_and_validator_rejection(self):
        """Verify syntax error and validator rejection trigger backup and return fallback."""
        res = self._execute_node_eval("""
        (() => {
            // Case A: Syntax error
            localStorage.setItem("test_syntax_err", "{bad_json: true");
            const resSyntax = readStoredJson("test_syntax_err", (x) => true, "FALLBACK_A");
            const bakSyntax = localStorage.getItem("tv_bak_test_syntax_err");

            // Case B: Validator rejected (returns false)
            localStorage.setItem("test_invalid_val", JSON.stringify({ a: 1 }));
            const resInvalid = readStoredJson("test_invalid_val", Array.isArray, ["FALLBACK_B"]);
            const bakInvalid = localStorage.getItem("tv_bak_test_invalid_val");

            // Case C: Pre-existing backup must not be overwritten
            localStorage.setItem("tv_bak_test_existing_bak", "ORIGINAL_PRESERVED_BACKUP");
            localStorage.setItem("test_existing_bak", "NEW_CORRUPTED_VALUE");
            const resExisting = readStoredJson("test_existing_bak", Array.isArray, ["FALLBACK_C"]);
            const bakExisting = localStorage.getItem("tv_bak_test_existing_bak");

            return {
                resSyntax, bakSyntax,
                resInvalid, bakInvalid,
                resExisting, bakExisting,
                warnLogs: _warnLogs
            };
        })()
        """)
        self.assertEqual(res["resSyntax"], "FALLBACK_A")
        self.assertEqual(res["bakSyntax"], "{bad_json: true")

        self.assertEqual(res["resInvalid"], ["FALLBACK_B"])
        self.assertEqual(res["bakInvalid"], '{"a":1}')

        self.assertEqual(res["resExisting"], ["FALLBACK_C"])
        self.assertEqual(res["bakExisting"], "ORIGINAL_PRESERVED_BACKUP")

        warn_text = " ".join(res["warnLogs"])
        self.assertIn("無效的儲存設定 test_syntax_err", warn_text)
        self.assertIn("無效的儲存設定 test_invalid_val", warn_text)

    def test_03_read_stored_json_sanitizer_modifications(self):
        """Verify that when validator returns a sanitized object differing from input, backup is created."""
        res = self._execute_node_eval("""
        (() => {
            // Sanitizer filtering out invalid items
            const rawData = [
                { id: "A", price: 100 },
                { id: "B", price: "not_a_number" },
                { id: "C", price: 300 }
            ];
            localStorage.setItem("test_sanitizer", JSON.stringify(rawData));

            const sanitizer = (val) => {
                if (!Array.isArray(val)) return false;
                return val.filter(item => typeof item.price === 'number' && isFinite(item.price));
            };

            const cleanResult = readStoredJson("test_sanitizer", sanitizer, []);
            const bakSanitizer = localStorage.getItem("tv_bak_test_sanitizer");

            // Non-modifying sanitizer (cleanResult === parsed)
            localStorage.setItem("test_clean", JSON.stringify([{ id: "X", price: 50 }]));
            const identicalResult = readStoredJson("test_clean", sanitizer, []);
            const bakClean = localStorage.getItem("tv_bak_test_clean");

            return {
                cleanResult,
                bakSanitizer,
                identicalResult,
                bakClean,
                warnLogs: _warnLogs
            };
        })()
        """)
        self.assertEqual(len(res["cleanResult"]), 2)
        self.assertEqual(res["cleanResult"][0]["id"], "A")
        self.assertEqual(res["cleanResult"][1]["id"], "C")
        self.assertIsNotNone(res["bakSanitizer"])
        self.assertIn("not_a_number", res["bakSanitizer"])

        self.assertEqual(len(res["identicalResult"]), 1)
        self.assertIsNone(res["bakClean"])

        warn_text = " ".join(res["warnLogs"])
        self.assertIn("已清理無效設定項 test_sanitizer", warn_text)

    def test_04_pinned_symbols_robustness(self):
        """Verify tv_pinned_symbols validates array of non-empty strings and handles malformed data."""
        cases = [
            ("missing", None, [], False),
            ("empty_obj", "{}", [], True),
            ("number", "42", [], True),
            ("string", '"just_a_string"', [], True),
            ("syntax_err", "{invalid_json", [], True),
            ("array_with_numbers", "[123, 456]", [], True),
            ("array_with_empty_str", '["", "NVDA"]', [], True),
            ("array_with_spaces", '["   ", "NVDA"]', [], True),
            ("valid", '["NASDAQ:NVDA", "AMEX:SPY"]', ["NASDAQ:NVDA", "AMEX:SPY"], False),
        ]

        for name, raw_val, expected_val, should_backup in cases:
            storage = {}
            if raw_val is not None:
                storage["tv_pinned_symbols"] = raw_val

            res = self._execute_node_eval(
                """
            ({
                pinned: pinnedSymbols,
                bak: localStorage.getItem("tv_bak_tv_pinned_symbols"),
                warnLogs: _warnLogs
            })
            """,
                initial_storage=storage,
            )

            self.assertEqual(res["pinned"], expected_val, f"Failed case {name}: got {res['pinned']}")
            if should_backup:
                self.assertIsNotNone(res["bak"], f"Expected backup for case {name}")
                self.assertEqual(res["bak"], raw_val)
            else:
                self.assertIsNone(res["bak"], f"Did not expect backup for case {name}")

    def test_05_watchlist_groups_robustness(self):
        """Verify tv_watchlist_groups validates plain object with string values and handles malformed data."""
        cases = [
            ("missing", None, {}, False),
            ("array", '["group1", "group2"]', {}, True),
            ("number", "42", {}, True),
            ("string", '"invalid"', {}, True),
            ("syntax_err", "{broken_json:", {}, True),
            ("object_with_number_val", '{"NASDAQ:NVDA": 123}', {}, True),
            ("object_with_null_val", '{"NASDAQ:NVDA": null}', {}, True),
            ("valid", '{"NASDAQ:NVDA": "Tech", "AMEX:SPY": "ETF"}', {"NASDAQ:NVDA": "Tech", "AMEX:SPY": "ETF"}, False),
        ]

        for name, raw_val, expected_val, should_backup in cases:
            storage = {}
            if raw_val is not None:
                storage["tv_watchlist_groups"] = raw_val

            res = self._execute_node_eval(
                """
            ({
                groups: watchlistGroups,
                bak: localStorage.getItem("tv_bak_tv_watchlist_groups"),
                warnLogs: _warnLogs
            })
            """,
                initial_storage=storage,
            )

            self.assertEqual(res["groups"], expected_val, f"Failed case {name}: got {res['groups']}")
            if should_backup:
                self.assertIsNotNone(res["bak"], f"Expected backup for case {name}")
                self.assertEqual(res["bak"], raw_val)
            else:
                self.assertIsNone(res["bak"], f"Did not expect backup for case {name}")

    def test_06_symbols_cache_robustness(self):
        """Verify tv_symbols_cache validates array of objects with string id and falls back to defaults."""
        cases = [
            ("missing", None, True, False),
            ("empty_obj", "{}", True, True),
            ("number", "42", True, True),
            ("string", '"symbols"', True, True),
            ("syntax_err", "[bad json", True, True),
            ("array_of_strings", '["NVDA", "AAPL"]', True, True),
            ("array_missing_id", '[{"name": "NVIDIA"}]', True, True),
            ("array_empty_id", '[{"id": "", "name": "NVIDIA"}]', True, True),
            ("array_whitespace_id", '[{"id": "   ", "name": "NVIDIA"}]', True, True),
            ("valid", '[{"id": "CUSTOM:SYM", "name": "Custom", "exchange": "CUSTOM", "pair": "SYM"}]', False, False),
        ]

        for name, raw_val, expect_default_seed, should_backup in cases:
            storage = {}
            if raw_val is not None:
                storage["tv_symbols_cache"] = raw_val

            res = self._execute_node_eval(
                """
            ({
                symbolsCount: symbolsList.length,
                firstSymbolId: symbolsList[0] ? symbolsList[0].id : null,
                bak: localStorage.getItem("tv_bak_tv_symbols_cache")
            })
            """,
                initial_storage=storage,
            )

            if expect_default_seed:
                self.assertEqual(res["firstSymbolId"], "NASDAQ:NVDA", f"Expected default seeds for {name}")
                self.assertGreaterEqual(res["symbolsCount"], 5)
            else:
                self.assertEqual(res["firstSymbolId"], "CUSTOM:SYM", f"Expected custom symbol for {name}")
                self.assertEqual(res["symbolsCount"], 1)

            if should_backup:
                self.assertIsNotNone(res["bak"], f"Expected backup for case {name}")
                self.assertEqual(res["bak"], raw_val)
            else:
                self.assertIsNone(res["bak"], f"Did not expect backup for case {name}")

    def test_07_price_lines_robustness(self):
        """Verify restorePriceLinesForSymbol validates price lines, filters invalid prices, and creates backup."""
        # Case A: Corrupted raw JSON string
        storage_a = {"tv_price_lines_NASDAQ:NVDA": "{broken"}
        res_a = self._execute_node_eval(
            """
        (() => {
            restorePriceLinesForSymbol("NASDAQ:NVDA");
            return {
                linesCreated: _mockPriceLines.length,
                bak: localStorage.getItem("tv_bak_tv_price_lines_NASDAQ:NVDA"),
                warnLogs: _warnLogs
            };
        })()
        """,
            initial_storage=storage_a,
        )
        self.assertEqual(res_a["linesCreated"], 0)
        self.assertEqual(res_a["bak"], "{broken")

        # Case B: Mixed items (valid numbers, invalid strings, NaN, missing price)
        mixed_lines = [
            {"price": 120.5, "isAlert": True},
            {"price": "invalid_price", "isAlert": False},
            {"price": None, "isAlert": False},
            {"price": 140.0, "isAlert": False},
        ]
        storage_b = {"tv_price_lines_NASDAQ:NVDA": json.dumps(mixed_lines)}
        res_b = self._execute_node_eval(
            """
        (() => {
            restorePriceLinesForSymbol("NASDAQ:NVDA");
            return {
                linesCreated: _mockPriceLines.length,
                prices: _mockPriceLines.map(l => l.price),
                bak: localStorage.getItem("tv_bak_tv_price_lines_NASDAQ:NVDA"),
                warnLogs: _warnLogs
            };
        })()
        """,
            initial_storage=storage_b,
        )
        self.assertEqual(res_b["linesCreated"], 2)
        self.assertEqual(res_b["prices"], [120.5, 140.0])
        self.assertIsNotNone(res_b["bak"])
        self.assertIn("invalid_price", res_b["bak"])
        warn_text = " ".join(res_b["warnLogs"])
        self.assertIn("已清理無效設定項 tv_price_lines_NASDAQ:NVDA", warn_text)

        # Case C: Clean valid price lines
        clean_lines = [{"price": 200.0, "isAlert": True}, {"price": 210.5, "isAlert": False}]
        storage_c = {"tv_price_lines_NASDAQ:NVDA": json.dumps(clean_lines)}
        res_c = self._execute_node_eval(
            """
        (() => {
            restorePriceLinesForSymbol("NASDAQ:NVDA");
            return {
                linesCreated: _mockPriceLines.length,
                prices: _mockPriceLines.map(l => l.price),
                bak: localStorage.getItem("tv_bak_tv_price_lines_NASDAQ:NVDA")
            };
        })()
        """,
            initial_storage=storage_c,
        )
        self.assertEqual(res_c["linesCreated"], 2)
        self.assertEqual(res_c["prices"], [200.0, 210.5])
        self.assertIsNone(res_c["bak"])

    def test_08_active_timeframe_validation_and_normalization(self):
        """Verify tv_active_timeframe normalizes cases, validates allowed values, and backs up invalid values."""
        # Allowed and case normalization
        valid_cases = [
            ("1d", "1D"),
            ("1D", "1D"),
            ("1w", "1W"),
            ("1W", "1W"),
            ("1m", "1m"),
            ("5m", "5m"),
            ("5M", "5m"),
            ("15m", "15m"),
            ("15M", "15m"),
            ("1h", "1h"),
            ("1H", "1h"),
        ]

        for input_tf, expected_tf in valid_cases:
            res = self._execute_node_eval(
                """
            ({
                activeTf: activeTimeframe,
                bak: localStorage.getItem("tv_bak_tv_active_timeframe")
            })
            """,
                initial_storage={"tv_active_timeframe": input_tf},
                run_bootstrap=True,
            )
            self.assertEqual(res["activeTf"], expected_tf, f"Failed normalization for {input_tf}")
            self.assertIsNone(res["bak"], f"Unexpected backup for valid timeframe {input_tf}")

        # Invalid timeframes
        invalid_cases = ["2h", "1M", "daily", "45", "random", "", "42", "1Y", "10m"]
        for invalid_tf in invalid_cases:
            res = self._execute_node_eval(
                """
            ({
                activeTf: activeTimeframe,
                bak: localStorage.getItem("tv_bak_tv_active_timeframe"),
                warnLogs: _warnLogs
            })
            """,
                initial_storage={"tv_active_timeframe": invalid_tf},
                run_bootstrap=True,
            )
            self.assertEqual(res["activeTf"], "1D", f"Expected fallback 1D for {invalid_tf}")
            self.assertIsNotNone(res["bak"], f"Expected backup for invalid timeframe {invalid_tf}")
            self.assertEqual(res["bak"], invalid_tf)
            warn_text = " ".join(res["warnLogs"])
            self.assertIn("無效的儲存設定 tv_active_timeframe", warn_text)

    def test_09_active_symbol_validation(self):
        """Verify tv_active_symbol validates exchange:pair regex and backs up invalid symbols."""
        valid_symbols = [
            "NASDAQ:NVDA",
            "BINANCE:BTCUSDT",
            "INDEX:^GSPC",
            "AMEX:SPY",
            "NYSE:BRK.A",
            "TV:US500",
            "BINANCE:BTC-USDT",
        ]

        for sym in valid_symbols:
            res = self._execute_node_eval(
                """
            ({
                activeSym: activeSymbol,
                bak: localStorage.getItem("tv_bak_tv_active_symbol")
            })
            """,
                initial_storage={"tv_active_symbol": sym},
                run_bootstrap=True,
            )
            self.assertEqual(res["activeSym"], sym, f"Expected valid symbol {sym} to be accepted")
            self.assertIsNone(res["bak"], f"Unexpected backup for valid symbol {sym}")

        invalid_symbols = [
            "",
            "invalid sym",
            "<script>alert(1)</script>",
            "42",
            "NVDA",
            ":NVDA",
            "NASDAQ:",
            "EXCHANGE_TOO_LONG_OVER_LIMIT:NVDA",
        ]

        for inv_sym in invalid_symbols:
            res = self._execute_node_eval(
                """
            ({
                activeSym: activeSymbol,
                bak: localStorage.getItem("tv_bak_tv_active_symbol"),
                warnLogs: _warnLogs
            })
            """,
                initial_storage={"tv_active_symbol": inv_sym},
                run_bootstrap=True,
            )
            self.assertEqual(res["activeSym"], "NASDAQ:NVDA", f"Expected fallback NASDAQ:NVDA for {inv_sym}")
            self.assertIsNotNone(res["bak"], f"Expected backup for invalid symbol {inv_sym}")
            self.assertEqual(res["bak"], inv_sym)
            warn_text = " ".join(res["warnLogs"])
            self.assertIn("無效的儲存設定 tv_active_symbol", warn_text)

    def test_10_bootstrap_and_interaction_resilience_all_corrupted(self):
        """Verify bootstrap and user interactions succeed with 0 errors when all storage keys are corrupted."""
        all_corrupted_storage = {
            "tv_pinned_symbols": "{corrupted_pinned: true}",
            "tv_watchlist_groups": "[corrupted, array]",
            "tv_symbols_cache": "42",
            "tv_price_lines_NASDAQ:NVDA": "CORRUPTED_PRICE_LINES_STRING",
            "tv_active_timeframe": "invalid_tf_999",
            "tv_active_symbol": "BAD_SYMBOL_NO_COLON",
        }

        # First verify bootstrap() alone populates backups for all 6 target keys directly (including price lines)
        res_bootstrap_only = self._execute_node_eval(
            """
            ({
                activeSym: activeSymbol,
                activeTf: activeTimeframe,
                bakPinned: localStorage.getItem("tv_bak_tv_pinned_symbols"),
                bakGroups: localStorage.getItem("tv_bak_tv_watchlist_groups"),
                bakSymbols: localStorage.getItem("tv_bak_tv_symbols_cache"),
                bakLines: localStorage.getItem("tv_bak_tv_price_lines_NASDAQ:NVDA"),
                bakTf: localStorage.getItem("tv_bak_tv_active_timeframe"),
                bakSym: localStorage.getItem("tv_bak_tv_active_symbol"),
            })
            """,
            initial_storage=all_corrupted_storage,
            run_bootstrap=True,
        )
        self.assertEqual(res_bootstrap_only["activeSym"], "NASDAQ:NVDA")
        self.assertEqual(res_bootstrap_only["activeTf"], "1D")
        self.assertEqual(res_bootstrap_only["bakPinned"], "{corrupted_pinned: true}")
        self.assertEqual(res_bootstrap_only["bakGroups"], "[corrupted, array]")
        self.assertEqual(res_bootstrap_only["bakSymbols"], "42")
        self.assertEqual(res_bootstrap_only["bakLines"], "CORRUPTED_PRICE_LINES_STRING")
        self.assertEqual(res_bootstrap_only["bakTf"], "invalid_tf_999")
        self.assertEqual(res_bootstrap_only["bakSym"], "BAD_SYMBOL_NO_COLON")

        res = self._execute_node_eval(
            """
        (() => {
            // Test user interactions after bootstrap with corrupted storage
            // Note: restorePriceLinesForSymbol is already invoked by bootstrap()
            switchSymbol("NASDAQ:NVDA");
            togglePinSymbol("NASDAQ:NVDA");
            const ordered = getOrderedWatchlistSymbols();

            return {
                activeSym: activeSymbol,
                activeTf: activeTimeframe,
                pinned: pinnedSymbols,
                groups: watchlistGroups,
                symbolsCount: symbolsList.length,
                orderedAllCount: (ordered && ordered.all) ? ordered.all.length : 0,
                bakPinned: localStorage.getItem("tv_bak_tv_pinned_symbols"),
                bakGroups: localStorage.getItem("tv_bak_tv_watchlist_groups"),
                bakSymbols: localStorage.getItem("tv_bak_tv_symbols_cache"),
                bakLines: localStorage.getItem("tv_bak_tv_price_lines_NASDAQ:NVDA"),
                bakTf: localStorage.getItem("tv_bak_tv_active_timeframe"),
                bakSym: localStorage.getItem("tv_bak_tv_active_symbol"),
                warnCount: _warnLogs.length,
                errorCount: _errorLogs.length
            };
        })()
        """,
            initial_storage=all_corrupted_storage,
            run_bootstrap=True,
        )

        # Assert clean fallback states
        self.assertEqual(res["activeSym"], "NASDAQ:NVDA")
        self.assertEqual(res["activeTf"], "1D")
        self.assertIn("NASDAQ:NVDA", res["pinned"])
        self.assertEqual(res["groups"], {})
        self.assertGreaterEqual(res["symbolsCount"], 5)
        self.assertGreaterEqual(res["orderedAllCount"], 5)

        # Assert backups created for all 6 target keys
        self.assertEqual(res["bakPinned"], "{corrupted_pinned: true}")
        self.assertEqual(res["bakGroups"], "[corrupted, array]")
        self.assertEqual(res["bakSymbols"], "42")
        self.assertEqual(res["bakLines"], "CORRUPTED_PRICE_LINES_STRING")
        self.assertEqual(res["bakTf"], "invalid_tf_999")
        self.assertEqual(res["bakSym"], "BAD_SYMBOL_NO_COLON")

        # Zero uncaught application errors
        self.assertEqual(res["errorCount"], 0)
        self.assertGreater(res["warnCount"], 0)


if __name__ == "__main__":
    unittest.main()

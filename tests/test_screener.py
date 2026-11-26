"""Unit tests for Institutional Stock Screener (RS + ATR + CANSLIM Engine).

Verifies:
1. Mansfield RS Date Alignment & mathematical accuracy.
2. ATR Volatility Squeeze, Expansion Breakout, and ZeroDivisionError protection.
3. Stan Weinstein & Mark Minervini Stage 2 criteria and short IPO bars tolerance.
4. CANSLIM RS Rating (1-99) small-sample stability via benchmark anchoring.
5. FastAPI screener endpoints (/api/screener/presets, /api/screener/scan, /api/screener/ai-summary).
"""

import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from engine.screener import (
    ScreeningCriteria,
    _resample_1d_to_1w,
    calculate_atr_toolkit,
    calculate_canslim_rs_rating,
    calculate_mansfield_rs,
    calculate_weekly_stage2,
    evaluate_canslim_stage,
    scan_symbol_candidate,
    scan_universe,
)
from server import app


class TestStockScreenerEngine(unittest.TestCase):
    """Test suite verifying quant logic and API endpoints of the screener engine."""

    def setUp(self):
        self.client = TestClient(app)
        # Generate 300 synthetic daily bars
        self.base_time = 1700000000
        self.target_bars = []
        self.bench_bars = []

        for i in range(300):
            t = self.base_time + i * 86400
            # Target stock: uptrending from 100 to 250
            p = 100.0 + i * 0.5
            self.target_bars.append({
                "time": t,
                "open": p - 0.5,
                "high": p + 1.5,
                "low": p - 1.0,
                "close": p,
                "volume": 1_000_000.0 + (500_000.0 if i == 299 else 0.0),
            })
            # Benchmark (SPY): slower uptrending from 400 to 460
            bp = 400.0 + i * 0.2
            self.bench_bars.append({
                "time": t,
                "open": bp - 0.2,
                "high": bp + 0.5,
                "low": bp - 0.5,
                "close": bp,
                "volume": 50_000_000.0,
            })

    def test_mansfield_rs_alignment_and_math(self):
        """Verifies date-aligned Inner Join and Mansfield RS oscillator computation."""
        # 1. Standard 252-bar calculation on aligned data
        m_rs = calculate_mansfield_rs(self.target_bars, self.bench_bars, length=252)
        self.assertIsNotNone(m_rs)
        # Target grew ~150% while SPY grew ~15%, so Mansfield RS must be strongly positive
        self.assertGreater(m_rs, 0.0)

        # 2. Test Date Misalignment / Holiday Tolerance (Inner Join verification)
        misaligned_target = [b for idx, b in enumerate(self.target_bars) if idx % 7 != 0]
        m_rs_misaligned = calculate_mansfield_rs(misaligned_target, self.bench_bars, length=200)
        self.assertIsNotNone(m_rs_misaligned)
        self.assertGreater(m_rs_misaligned, 0.0)

        # 3. Empty or insufficient history returns None safely
        self.assertIsNone(calculate_mansfield_rs([], self.bench_bars))
        self.assertIsNone(calculate_mansfield_rs(self.target_bars[:20], self.bench_bars[:20], length=252))

    def test_atr_squeeze_and_zero_atr_guard(self):
        """Verifies Wilder RMA ATR, Squeeze Percentile, Position Sizing and ZeroDivision safety."""
        # 1. Standard calculation
        atr_res = calculate_atr_toolkit(
            self.target_bars,
            atr_len=14,
            pct_len=126,
            squeeze_thresh=20.0,
            risk_dollar=500.0,
            stop_mult=2.0
        )
        self.assertGreater(atr_res["atr"], 0.0)
        self.assertGreater(atr_res["atr_pct"], 0.0)
        self.assertIn("atr_percentile", atr_res)
        self.assertGreater(atr_res["suggested_shares"], 0)
        # Position sizing check: suggested_shares == floor(500 / (2.0 * atr))
        expected_shares = int(500.0 / (2.0 * atr_res["atr"]))
        self.assertEqual(atr_res["suggested_shares"], expected_shares)

        # 2. Zero-Division Guard: Halted / completely flat bars with 0 range
        flat_bars = [
            {"time": self.base_time + i * 86400, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0}
            for i in range(50)
        ]
        flat_res = calculate_atr_toolkit(flat_bars, atr_len=14, risk_dollar=500.0)
        # Must return default result gracefully without raising ZeroDivisionError
        self.assertEqual(flat_res["atr"], 0.0)
        self.assertEqual(flat_res["suggested_shares"], 0)
        self.assertFalse(flat_res["is_squeeze"])

    def test_canslim_stage2_and_short_ipo_bars(self):
        """Verifies Stage 2 Uptrend evaluation, 200MA slope, and sub-new stock handling."""
        # 1. Full 300-bar uptrending stock should satisfy Stage 2
        stage_info = evaluate_canslim_stage(self.target_bars)
        self.assertTrue(stage_info["stage2"])
        self.assertEqual(stage_info["trend_regime"], "STAGE_2_STRONG_UPTREND")
        self.assertTrue(stage_info["close"] > stage_info["sma50"] > stage_info["sma200"])
        self.assertGreater(stage_info["pct_above_52w_low"], 25.0)

        # 2. Short IPO stock (< 200 bars, e.g. 60 bars) rising 50% from IPO base
        short_bars = [
            {
                "time": self.base_time + i * 86400,
                "open": 100.0 + i * 0.8,
                "high": 101.0 + i * 0.8,
                "low": 99.0 + i * 0.8,
                "close": 100.0 + i * 0.8,
                "volume": 500_000.0
            }
            for i in range(60)
        ]
        short_stage = evaluate_canslim_stage(short_bars)
        self.assertIsNone(short_stage["sma200"])
        self.assertIsNotNone(short_stage["sma50"])
        # Should gracefully evaluate with available MAs without IndexError
        self.assertTrue(short_stage["stage2"])

    def test_rs_rating_small_watchlist_consistency(self):
        """Verifies CANSLIM RS Rating (1-99) does not distort in small universes."""
        # 1. Strong uptrending stock
        score_strong = calculate_canslim_rs_rating(self.target_bars, self.bench_bars)
        self.assertGreaterEqual(score_strong, 75)
        self.assertLessEqual(score_strong, 99)

        # 2. Downtrending stock: price dropping from 200 to 50
        down_bars = []
        for i in range(300):
            t = self.base_time + i * 86400
            p = max(10.0, 200.0 - i * 0.5)
            down_bars.append({
                "time": t, "open": p, "high": p + 1.0, "low": p - 1.0, "close": p, "volume": 100000.0
            })
        score_weak = calculate_canslim_rs_rating(down_bars, self.bench_bars)
        self.assertLess(score_weak, 40)
        self.assertGreaterEqual(score_weak, 1)

        # 3. Symmetrical Benchmark Parity Check: Identical performance to benchmark yields exact median score 50
        score_parity = calculate_canslim_rs_rating(self.bench_bars, self.bench_bars)
        self.assertEqual(score_parity, 50)

        # 4. Symmetrical Sub-New Stock Parity Check: Sub-new stock (60 bars) tracking benchmark yields exact 50
        short_bench = self.bench_bars[:60]
        score_short_parity = calculate_canslim_rs_rating(short_bench, self.bench_bars)
        self.assertEqual(score_short_parity, 50)

    def test_screener_remote_backfill_when_short_cache(self):
        """Verifies fetch_func is invoked to backfill a short cache, and (S1-01) also when the cache already has 252+ bars."""
        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = self.target_bars[:50]
        mock_storage.get_fundamental_report.return_value = None

        mock_fetch = AsyncMock(return_value=self.target_bars)

        universe = [{"id": "NASDAQ:TSLA", "symbol": "TSLA", "name": "Tesla"}]
        criteria = ScreeningCriteria(
            min_rs_rating=1,
            mansfield_above_zero=False,
            stage2_only=False,
            atr_squeeze_only=False,
            max_pct_from_52w_high=100.0
        )

        results = asyncio.run(scan_universe(
            universe=universe,
            criteria=criteria,
            storage_obj=mock_storage,
            benchmark_bars=self.bench_bars,
            fetch_func=mock_fetch,
            max_concurrency=2
        ))

        # fetch_func must have been called to backfill the missing bars
        mock_fetch.assert_called_once_with("NASDAQ:TSLA", timeframe="1D")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "TSLA")

        # S1-01 (第 1 段): a long cache can still be stale, so fetch_func MUST be called even with >= 252 bars.
        # fetch_func applies the S1-02 freshness rules, so this does not cause extra remote calls when data is fresh.
        # (Previously this asserted assert_not_called(), which encoded the stale-data bug.)
        mock_storage.get_bars.return_value = self.target_bars
        mock_fetch.reset_mock()

        results_cached = asyncio.run(scan_universe(
            universe=universe,
            criteria=criteria,
            storage_obj=mock_storage,
            benchmark_bars=self.bench_bars,
            fetch_func=mock_fetch,
            max_concurrency=2
        ))
        mock_fetch.assert_called_once_with("NASDAQ:TSLA", timeframe="1D")
        self.assertEqual(len(results_cached), 1)

    @patch("server.call_gemini_generate", new_callable=AsyncMock)
    @patch("server.fetch_universal_history", new_callable=AsyncMock)
    def test_screener_api_endpoints(self, mock_fetch_history, mock_gemini):
        """Verifies GET /api/screener/presets, POST /api/screener/scan, and /api/screener/ai-summary with offline mocks."""
        mock_fetch_history.return_value = self.target_bars
        mock_gemini.return_value = "### Institutional AI Copilot Synthesis\nStage 2 market confirmation with disciplined risk sizing."

        # 1. GET Presets
        res = self.client.get("/api/screener/presets")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "ok")
        self.assertGreaterEqual(len(data["presets"]), 4)
        preset_ids = [p["id"] for p in data["presets"]]
        self.assertIn("stage2_vcp", preset_ids)
        self.assertIn("rs_leaders", preset_ids)
        self.assertIn("canslim_growth", preset_ids)

        canslim_preset = next(p for p in data["presets"] if p["id"] == "canslim_growth")
        self.assertEqual(canslim_preset["criteria"].get("min_rev_growth_yoy"), 20.0)

        # 2. POST Scan (dry run with custom universe of 2 symbols, mocked offline)
        scan_payload = {
            "universe": "custom",
            "custom_symbols": ["AAPL", "NVDA"],
            "risk_dollar": 600.0,
            "criteria": {
                "min_rs_rating": 50,
                "mansfield_above_zero": False,
                "stage2_only": False,
                "atr_squeeze_only": False,
                "max_pct_from_52w_high": 50.0,
                "min_rev_growth_yoy": 20.0
            }
        }
        scan_res = self.client.post("/api/screener/scan", json=scan_payload)
        self.assertEqual(scan_res.status_code, 200)
        scan_data = scan_res.json()
        self.assertEqual(scan_data["status"], "ok")
        self.assertIn("elapsed_ms", scan_data)
        self.assertIn("benchmark_status", scan_data)
        self.assertIsInstance(scan_data["items"], list)

        # 3. POST AI Summary with risk_dollar parameter
        ai_payload = {
            "items": [
                {
                    "symbol": "NVDA",
                    "name": "NVIDIA Corp.",
                    "close": 156.14,
                    "mansfield_rs": 14.2,
                    "rs_rating": 94,
                    "pct_from_52w_high": -4.2,
                    "atr_pct": 2.5,
                    "is_squeeze": True,
                    "suggested_shares": 52
                }
            ],
            "benchmark_status": {
                "symbol": "SPY",
                "close": 585.20,
                "regime": "STAGE_2_STRONG_UPTREND"
            },
            "risk_dollar": 750.0
        }
        ai_res = self.client.post("/api/screener/ai-summary", json=ai_payload)
        self.assertEqual(ai_res.status_code, 200)
        ai_data = ai_res.json()
        self.assertEqual(ai_data["status"], "ok")
        self.assertIn("summary", ai_data)
        self.assertIn("Institutional AI Copilot Synthesis", ai_data["summary"])

        # 4. Verify AI Summary fallback deterministic behavior when Gemini returns empty/fails
        mock_gemini.side_effect = Exception("Offline test simulation")
        ai_fallback_res = self.client.post("/api/screener/ai-summary", json=ai_payload)
        self.assertEqual(ai_fallback_res.status_code, 200)
        ai_fallback_data = ai_fallback_res.json()
        self.assertEqual(ai_fallback_data["status"], "ok")
        self.assertIn("$750", ai_fallback_data["summary"])

        # 5. Robustness against non-numeric or negative risk_dollar in AI summary
        ai_invalid_risk_payload = {
            "items": ai_payload["items"],
            "benchmark_status": ai_payload["benchmark_status"],
            "risk_dollar": "invalid_not_a_number"
        }
        ai_inv_res = self.client.post("/api/screener/ai-summary", json=ai_invalid_risk_payload)
        self.assertEqual(ai_inv_res.status_code, 200)
        self.assertIn("$500", ai_inv_res.json()["summary"])

        # 6. Robustness against non-numeric min_rev_growth_yoy in scan criteria
        inv_scan_payload = {
            "universe": "custom",
            "custom_symbols": ["AAPL"],
            "risk_dollar": -100,
            "criteria": {
                "min_rev_growth_yoy": "non_numeric"
            }
        }
        inv_scan_res = self.client.post("/api/screener/scan", json=inv_scan_payload)
        self.assertEqual(inv_scan_res.status_code, 200)
        self.assertEqual(inv_scan_res.json()["status"], "ok")

    def test_multi_timeframe_weekly_stage2_resampling(self):
        """Verifies in-memory 1D to 1W bar resampling and Stan Weinstein Weekly Stage 2 logic."""
        # 1. 1D -> 1W Resampling: 300 daily bars should yield ~43-44 weekly bars
        weekly_bars = _resample_1d_to_1w(self.target_bars)
        self.assertGreaterEqual(len(weekly_bars), 40)
        self.assertLessEqual(len(weekly_bars), 65)

        # 2. Resampling boundary values & OHLCV invariants
        w0 = weekly_bars[0]
        self.assertGreater(w0["high"], w0["low"])
        self.assertGreater(w0["volume"], 0)

        # 3. Weekly Stage 2 uptrend evaluation on target bars (price 100 -> 250)
        self.assertTrue(calculate_weekly_stage2(weekly_bars))
        stage_info = evaluate_canslim_stage(self.target_bars)
        self.assertTrue(stage_info["weekly_stage2"])

        # 4. Downtrend stock should fail weekly Stage 2 (price 250 -> 50)
        down_bars = []
        for i in range(300):
            t = self.base_time + i * 86400
            p = max(10.0, 250.0 - i * 0.6)
            down_bars.append({
                "time": t, "open": p, "high": p + 1.0, "low": p - 1.0, "close": p, "volume": 100_000.0
            })
        down_weekly = _resample_1d_to_1w(down_bars)
        self.assertFalse(calculate_weekly_stage2(down_weekly))
        down_stage = evaluate_canslim_stage(down_bars)
        self.assertFalse(down_stage["weekly_stage2"])

        # 5. Insufficient history (< 30 weeks) returns False safely
        short_weekly = weekly_bars[:25]
        self.assertFalse(calculate_weekly_stage2(short_weekly))

        # 6. Edge cases for resampling: empty list, string date format, slash dates, and datetime objects
        self.assertEqual(_resample_1d_to_1w([]), [])
        str_date_bars = [
            {"time": "2024/01/08", "open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "volume": 1000.0},
            {"time": "2024-01-09", "open": 104.0, "high": 108.0, "low": 103.0, "close": 107.0, "volume": 1500.0},
            {"time": datetime(2024, 1, 10, tzinfo=timezone.utc), "open": 107.0, "high": 110.0, "low": 106.0, "close": 109.0, "volume": 2000.0},
        ]
        res_str = _resample_1d_to_1w(str_date_bars)
        self.assertEqual(len(res_str), 1)
        self.assertEqual(res_str[0]["open"], 100.0)
        self.assertEqual(res_str[0]["high"], 110.0)
        self.assertEqual(res_str[0]["low"], 98.0)
        self.assertEqual(res_str[0]["close"], 109.0)
        self.assertEqual(res_str[0]["volume"], 4500.0)

        # 7. Resampling deduplication: duplicate days should be ignored and not inflate weekly volume
        duplicate_day_bars = [
            {"time": "2024-01-08", "open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "volume": 1000.0},
            {"time": "2024-01-08", "open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "volume": 1000.0},  # duplicate
            {"time": "2024-01-09", "open": 104.0, "high": 108.0, "low": 103.0, "close": 107.0, "volume": 1500.0},
        ]
        res_dedup = _resample_1d_to_1w(duplicate_day_bars)
        self.assertEqual(len(res_dedup), 1)
        self.assertEqual(res_dedup[0]["volume"], 2500.0)

        # 8. Boundary condition: Exactly 30 weekly bars should evaluate slope as False (only 1 SMA point)
        exact_30_weekly = weekly_bars[:30]
        self.assertEqual(len(exact_30_weekly), 30)
        self.assertFalse(calculate_weekly_stage2(exact_30_weekly))

        # 9. scan_symbol_candidate filtering with weekly_stage2_only
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        sem = asyncio.Semaphore(1)
        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = self.target_bars
        mock_storage.get_fundamental_report.return_value = None

        criteria_pass = ScreeningCriteria(
            weekly_stage2_only=True,
            stage2_only=False,
            min_rs_rating=1,
            mansfield_above_zero=False,
            atr_squeeze_only=False,
            max_pct_from_52w_high=100.0
        )
        cand_pass = asyncio.run(scan_symbol_candidate(
            sym_info={"id": "NASDAQ:NVDA", "symbol": "NVDA", "has_prefix": True},
            criteria=criteria_pass,
            storage_obj=mock_storage,
            benchmark_bars=self.bench_bars,
            fetch_func=None,
            semaphore=sem
        ))
        self.assertIsNotNone(cand_pass)
        self.assertTrue(cand_pass["weekly_stage2"])
        self.assertEqual(cand_pass["export_symbol"], "NASDAQ:NVDA")

        # Custom symbol without prefix: export_symbol should be plain symbol
        cand_custom = asyncio.run(scan_symbol_candidate(
            sym_info={"id": "NASDAQ:LLY", "symbol": "LLY", "has_prefix": False},
            criteria=criteria_pass,
            storage_obj=mock_storage,
            benchmark_bars=self.bench_bars,
            fetch_func=None,
            semaphore=sem
        ))
        self.assertIsNotNone(cand_custom)
        self.assertEqual(cand_custom["export_symbol"], "LLY")

        # Failing candidate: downtrending stock should be filtered out when weekly_stage2_only=True
        mock_storage.get_bars.return_value = down_bars
        cand_fail = asyncio.run(scan_symbol_candidate(
            sym_info={"id": "NASDAQ:WEAK", "symbol": "WEAK", "has_prefix": True},
            criteria=criteria_pass,
            storage_obj=mock_storage,
            benchmark_bars=self.bench_bars,
            fetch_func=None,
            semaphore=sem
        ))
        self.assertIsNone(cand_fail)

    def test_custom_symbols_normalization_and_prefix_resolution(self):
        """Verifies custom universe inputs handle lists, whitespace, semicolons, and preserve prefixes."""
        # 1. Preset API contains weekly_stage2_only in all preset criteria
        presets_res = self.client.get("/api/screener/presets")
        self.assertEqual(presets_res.status_code, 200)
        for p in presets_res.json()["presets"]:
            self.assertIn("weekly_stage2_only", p["criteria"])

        # 2. Custom input with semicolon and comma separated strings
        with patch("server.fetch_universal_history", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = self.target_bars
            payload_str = {
                "universe": "custom",
                "custom_symbols": "lly; jpm, NYSE:V",
                "criteria": {"stage2_only": False, "mansfield_above_zero": False, "min_rs_rating": 1, "max_pct_from_52w_high": 100.0}
            }
            res = self.client.post("/api/screener/scan", json=payload_str)
            self.assertEqual(res.status_code, 200)
            data = res.json()
            symbols = [it["export_symbol"] for it in data["items"]]
            self.assertIn("LLY", symbols)
            self.assertIn("JPM", symbols)
            self.assertIn("NYSE:V", symbols)

        # 3. Custom input with list of lowercase strings and non-string elements
        with patch("server.fetch_universal_history", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = self.target_bars
            payload_list = {
                "universe": "custom",
                "custom_symbols": ["  aapl  ", "nvda", 12345],
                "criteria": {
                    "stage2_only": False,
                    "mansfield_above_zero": False,
                    "min_rs_rating": 1,
                    "max_pct_from_52w_high": 100.0,
                    "min_volume_ratio": 0.0
                }
            }
            res_list = self.client.post("/api/screener/scan", json=payload_list)
            self.assertEqual(res_list.status_code, 200)
            symbols_list = [it["export_symbol"] for it in res_list.json()["items"]]
            self.assertIn("AAPL", symbols_list)
            self.assertIn("NVDA", symbols_list)

    def test_frontend_screener_exports_via_jsc(self):
        """Executes frontend export functions in Apple JavaScriptCore (jsc) to verify CSV & TradingView export logic."""
        import json
        from pathlib import Path
        import shutil
        import subprocess

        jsc_candidates = [
            shutil.which("jsc"),
            "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc",
            "/System/Library/Frameworks/JavaScriptCore.framework/Resources/jsc",
        ]
        jsc_path = next((c for c in jsc_candidates if c and Path(c).is_file()), None)
        if not jsc_path:
            self.skipTest("JSC binary not available on this platform")

        app_js_posix = (Path(__file__).resolve().parent.parent / "static" / "app.js").as_posix()

        test_script = f"""
        var capturedBlobs = [];
        var capturedCopies = [];

        var mockElements = {{}};
        function MockElement(tag) {{
            this.tagName = (tag || 'div').toUpperCase();
            this.style = {{}};
            this.children = [];
            this.attributes = {{}};
            this.classList = {{
                add: function() {{}},
                remove: function() {{}}
            }};
            this._textContent = '';
        }}
        MockElement.prototype.appendChild = function(c) {{ this.children.push(c); }};
        MockElement.prototype.removeChild = function(c) {{}};
        MockElement.prototype.setAttribute = function(k, v) {{ this.attributes[k] = String(v); }};
        MockElement.prototype.getAttribute = function(k) {{ return this.attributes[k]; }};
        MockElement.prototype.focus = function() {{}};
        MockElement.prototype.select = function() {{}};
        MockElement.prototype.click = function() {{}};
        Object.defineProperty(MockElement.prototype, 'textContent', {{
            get: function() {{ return this._textContent; }},
            set: function(v) {{ this._textContent = String(v); }}
        }});

        var document = {{
            getElementById: function(id) {{
                if (!mockElements[id]) mockElements[id] = new MockElement('div');
                return mockElements[id];
            }},
            createElement: function(tag) {{ return new MockElement(tag); }},
            addEventListener: function() {{}},
            body: new MockElement('body'),
            querySelectorAll: function() {{ return []; }}
        }};

        var window = {{
            addEventListener: function() {{}},
            location: {{ protocol: 'http:', host: 'localhost:8000' }}
        }};

        var navigator = {{
            clipboard: {{
                writeText: function(txt) {{
                    capturedCopies.push(txt);
                    return Promise.resolve();
                }}
            }}
        }};

        var URL = {{
            createObjectURL: function(b) {{ return 'blob://' + Math.random(); }},
            revokeObjectURL: function(u) {{}}
        }};

        function Blob(parts, opts) {{
            this.content = parts.join('');
            this.opts = opts;
            capturedBlobs.push(this);
        }}

        // Load static/app.js
        load('{app_js_posix}');

        // 1. Verify escapeCsvField
        var esc1 = escapeCsvField("Tesla, Inc.");
        var esc2 = escapeCsvField('He said "Hello"');
        var esc3 = escapeCsvField(123.45);
        var esc4 = escapeCsvField(null);
        var esc5 = escapeCsvField(undefined);

        // 2. Setup mock screener results including Tesla, Inc. with comma
        lastScreenerResults = [
            {{
                id: "NASDAQ:NVDA",
                symbol: "NVDA",
                name: "NVIDIA Corp.",
                sector: "Technology",
                close: 156.14,
                change_pct: 2.34,
                trend_regime: "STAGE_2_STRONG_UPTREND",
                weekly_stage2: true,
                power_trend: true,
                is_squeeze: false,
                mansfield_rs: 14.2,
                rs_rating: 94,
                atr: 4.12,
                atr_pct: 2.64,
                atr_percentile: 18.0,
                stop_distance: 8.24,
                suggested_shares: 60,
                volume_ratio_50: 1.45,
                revenue_yoy: "+94%",
                has_prefix: true,
                export_symbol: "NASDAQ:NVDA"
            }},
            {{
                id: "NASDAQ:TSLA",
                symbol: "TSLA",
                name: "Tesla, Inc.",
                sector: "Consumer Cyclical",
                close: 250.00,
                change_pct: -1.20,
                trend_regime: "STAGE_2_STRONG_UPTREND",
                weekly_stage2: true,
                power_trend: false,
                is_squeeze: true,
                mansfield_rs: 8.5,
                rs_rating: 88,
                atr: 6.50,
                atr_pct: 2.60,
                atr_percentile: 12.0,
                stop_distance: 13.00,
                suggested_shares: 38,
                volume_ratio_50: 1.20,
                revenue_yoy: "+15%",
                has_prefix: true,
                export_symbol: "NASDAQ:TSLA"
            }},
            {{
                id: "NYSE:LLY",
                symbol: "LLY",
                name: "Eli Lilly and Co.",
                sector: "Healthcare",
                close: 800.00,
                change_pct: 0.50,
                trend_regime: "STAGE_2_UPTREND_PULLBACK",
                weekly_stage2: false,
                power_trend: false,
                is_squeeze: false,
                mansfield_rs: 5.1,
                rs_rating: 82,
                atr: 12.00,
                atr_pct: 1.50,
                atr_percentile: 40.0,
                stop_distance: 24.00,
                suggested_shares: 20,
                volume_ratio_50: 0.95,
                revenue_yoy: "+28%",
                has_prefix: false,
                export_symbol: "LLY"
            }}
        ];

        // Run TradingView export
        exportScreenerTradingView();
        var tvBlob = capturedBlobs[capturedBlobs.length - 1];

        // Run Copy Watchlist
        copyScreenerWatchlist();
        var copiedText = capturedCopies[capturedCopies.length - 1];

        // Run CSV export
        exportScreenerCsv();
        var csvBlob = capturedBlobs[capturedBlobs.length - 1];

        // Test section grouping when ONLY otherItems exist (Bug 1 regression guard)
        lastScreenerResults = [
            {{
                id: "NASDAQ:AAPL",
                symbol: "AAPL",
                name: "Apple Inc.",
                power_trend: false,
                is_squeeze: false,
                has_prefix: true,
                export_symbol: "NASDAQ:AAPL"
            }}
        ];
        exportScreenerTradingView();
        var tvOnlyOtherBlob = capturedBlobs[capturedBlobs.length - 1];

        print(JSON.stringify({{
            esc1: esc1,
            esc2: esc2,
            esc3: esc3,
            esc4: esc4,
            esc5: esc5,
            tvContent: tvBlob.content,
            copiedText: copiedText,
            csvContent: csvBlob.content,
            tvOnlyOtherContent: tvOnlyOtherBlob.content
        }}));
        """

        res = subprocess.run([jsc_path, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"JSC execution error:\nStdout: {res.stdout}\nStderr: {res.stderr}")
        data = json.loads(res.stdout.strip())

        # 1. Check escapeCsvField output
        self.assertEqual(data["esc1"], '"Tesla, Inc."')
        self.assertEqual(data["esc2"], '"He said ""Hello"""')
        self.assertEqual(data["esc3"], "123.45")
        self.assertEqual(data["esc4"], '""')
        self.assertEqual(data["esc5"], '""')

        # 2. Check TradingView export content
        tv_content = data["tvContent"]
        self.assertIn("###POWER TREND LEADERS", tv_content)
        self.assertIn("NASDAQ:NVDA", tv_content)
        self.assertIn("###STAGE 2 VCP", tv_content)
        self.assertIn("NASDAQ:TSLA", tv_content)
        self.assertIn("###STAGE 2 MOMENTUM", tv_content)
        self.assertIn("LLY", tv_content)  # Custom symbol exported without NASDAQ: prefix!

        # 3. Check TradingView export when only stage 2 momentum items exist (Bug 1 regression check)
        self.assertIn("###STAGE 2 MOMENTUM", data["tvOnlyOtherContent"])
        self.assertIn("NASDAQ:AAPL", data["tvOnlyOtherContent"])

        # 4. Check copy watchlist
        self.assertEqual(data["copiedText"], "NASDAQ:NVDA, NASDAQ:TSLA, LLY")

        # 5. Check CSV content & UTF-8 BOM
        csv_content = data["csvContent"]
        self.assertTrue(csv_content.startswith("\ufeff"), "CSV must start with UTF-8 BOM")
        lines = csv_content[1:].split("\r\n")
        self.assertGreaterEqual(len(lines), 4)

        # Check headers (18 columns)
        header_line = lines[0]
        self.assertIn("Suggested Stop Price (Close - 2xATR)", header_line)
        self.assertIn("Position Value ($)", header_line)
        self.assertIn("Vol Ratio 50D", header_line)

        # Check Tesla row: "Tesla, Inc." is quoted so comma does not split into extra columns!
        tsla_line = next(line for line in lines if "TSLA" in line)
        self.assertIn('"Tesla, Inc."', tsla_line)
        # Parse CSV line respecting quotes to verify column count
        import csv
        reader = csv.reader([tsla_line])
        row_fields = next(reader)
        self.assertEqual(len(row_fields), 18, f"Expected 18 CSV columns, got {len(row_fields)}: {row_fields}")
        self.assertEqual(row_fields[0], "TSLA")
        self.assertEqual(row_fields[1], "Tesla, Inc.")
        self.assertEqual(row_fields[3], "250")  # Close as number
        self.assertEqual(row_fields[12], "13")  # Stop distance
        self.assertEqual(row_fields[13], "237")  # Suggested stop price (250 - 13 = 237)
        self.assertEqual(row_fields[14], "38")  # Suggested shares
        self.assertEqual(row_fields[15], "9500")  # Position value (38 * 250 = 9500)


if __name__ == "__main__":
    unittest.main()


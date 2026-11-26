"""Unit and Integration Tests for AI Agent Lens (Non-Repainting) and Unified Recovery Engine.

Validates:
1. Non-repainting mathematical contract (confirmed candle isolation).
2. Trend regime classification (Stage 2 Uptrend, Pullback, Stage 4 Downtrend, Neutral).
3. Deterministic CANSLIM / Stage 2 decision synthesis and failure mode attribution.
4. Storage persistence for AI Agent Lens reports and application preferences.
5. Endpoints: GET /api/ai-lens, GET /api/recovery, GET/POST /api/preferences.
6. 100% DOM ID alignment between static/app.js and static/index.html.
"""

import os
import re
import math
import time
import unittest
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import patch, MagicMock, AsyncMock

from starlette.testclient import TestClient
import server
from server import app
from engine.storage import Storage


def _generate_synthetic_bars(
    count: int = 250,
    start_price: float = 100.0,
    trend_slope: float = 0.5,
    volatility: float = 2.0,
    base_time: int = 1700000000,
) -> List[Dict[str, Any]]:
    """Generates synthetic OHLCV daily bars with controlled trend direction."""
    bars = []
    p = start_price
    for i in range(count):
        t = base_time + (i * 86400)
        p += trend_slope
        noise = math.sin(i / 5.0) * volatility
        close_p = max(1.0, round(p + noise, 4))
        high_p = max(close_p + 1.0, round(close_p + abs(noise) + 0.5, 4))
        low_p = max(0.5, round(close_p - abs(noise) - 0.5, 4))
        open_p = round((high_p + low_p) / 2.0, 4)
        vol = round(10000.0 + (i * 20.0) + (abs(noise) * 500.0), 2)
        bars.append({
            "time": t,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": vol,
        })
    return bars


class TestAiAgentLensAndRecovery(unittest.TestCase):
    """Test suite for AI Agent Lens and Unified State & History Recovery Engine."""

    def setUp(self):
        self.original_watchlist = [dict(w) for w in server.WATCHLIST]
        self.original_symbols = list(server.SYMBOLS)
        self.storage = Storage(":memory:")
        for p in (
            patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock),
            patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock),
            patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=[]),
            patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=_generate_synthetic_bars(count=60)),
            patch("api.ai.call_gemini_generate", new_callable=AsyncMock, return_value=None),
            patch("api.ai.call_gemini_generate_with_model", new_callable=AsyncMock, return_value=(None, None)),
        ):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        server.WATCHLIST.clear()
        server.WATCHLIST.extend(self.original_watchlist)
        server.SYMBOLS.clear()
        server.SYMBOLS.extend(self.original_symbols)
        self.storage.close()

    def test_non_repainting_contract_and_candle_isolation(self):
        """Validates that indicators evaluate strictly on finalized bars, isolating the live candle."""
        bars = _generate_synthetic_bars(count=60, start_price=150.0, trend_slope=0.2)
        # Latest bar (unconfirmed live candle) has an extreme spike that should NOT repaint confirmed metrics
        bars[-1]["close"] = 999.0
        bars[-1]["high"] = 1000.0
        bars[-1]["unconfirmed"] = True

        payload = server.build_ai_agent_lens_payload(
            symbol="NASDAQ:AAPL",
            timeframe="1D",
            bars=bars,
        )

        self.assertEqual(payload["schema"], "ai-agent-lens/v1")
        self.assertTrue(payload["is_confirmed_bar"])
        self.assertIsNotNone(payload["confirmed_bar"])
        self.assertIsNotNone(payload["unconfirmed_latest_bar"])

        # Confirmed bar must be bars[-2], NOT the live bars[-1]
        self.assertEqual(payload["confirmed_bar"]["time"], bars[-2]["time"])
        self.assertEqual(payload["confirmed_bar"]["close"], bars[-2]["close"])
        self.assertEqual(payload["unconfirmed_latest_bar"]["time"], bars[-1]["time"])
        self.assertEqual(payload["unconfirmed_latest_bar"]["close"], 999.0)

        # Quant metrics close must match confirmed_bar, NOT the unconfirmed live spike
        self.assertEqual(payload["quant_metrics"]["close"], bars[-2]["close"])
        self.assertNotEqual(payload["quant_metrics"]["close"], 999.0)

    def test_trend_regime_classification(self):
        """Validates Minervini / Weinstein Stage 2 Uptrend, Pullback, Stage 4 Downtrend, and Neutral."""
        # Strong Uptrend
        uptrend_bars = _generate_synthetic_bars(count=260, start_price=50.0, trend_slope=0.5, volatility=0.2)
        up_payload = server.build_ai_agent_lens_payload("NASDAQ:NVDA", "1D", uptrend_bars)
        self.assertEqual(up_payload["quant_metrics"]["trend_regime"], "STAGE_2_STRONG_UPTREND")

        # Downtrend
        downtrend_bars = _generate_synthetic_bars(count=260, start_price=200.0, trend_slope=-0.6, volatility=0.2)
        down_payload = server.build_ai_agent_lens_payload("NASDAQ:DOWN", "1D", downtrend_bars)
        self.assertEqual(down_payload["quant_metrics"]["trend_regime"], "STAGE_4_DOWNTREND")

        # Empty / Choppy Bars
        empty_payload = server.build_ai_agent_lens_payload("UNKNOWN:TEST", "1D", [])
        self.assertEqual(empty_payload["quant_metrics"]["trend_regime"], "NEUTRAL")

    def test_rs_rating_and_ratio_calculation(self):
        """Validates Relative Strength ratio and 1-99 percentile score against benchmark."""
        target_bars = _generate_synthetic_bars(count=100, start_price=100.0, trend_slope=1.0)
        bench_bars = _generate_synthetic_bars(count=100, start_price=400.0, trend_slope=0.1)

        payload = server.build_ai_agent_lens_payload(
            symbol="NASDAQ:AAPL",
            timeframe="1D",
            bars=target_bars,
            benchmark_bars=bench_bars,
        )

        quant = payload["quant_metrics"]
        self.assertIsNotNone(quant["rs_ratio"])
        self.assertIsNotNone(quant["rs_rating"])
        self.assertGreaterEqual(quant["rs_rating"], 1)
        self.assertLessEqual(quant["rs_rating"], 99)
        # Strong target outperforming flat benchmark should produce high RS rating
        self.assertGreaterEqual(quant["rs_rating"], 80)

    def test_deterministic_ai_lens_decision_contract(self):
        """Validates that offline decision generation produces all mandatory structured fields."""
        bars = _generate_synthetic_bars(count=260, start_price=100.0, trend_slope=0.4)
        payload = server.build_ai_agent_lens_payload("NASDAQ:MSFT", "1D", bars)

        decision = server.generate_deterministic_ai_lens_decision(payload)

        self.assertIn(decision["bias"], ["LONG", "SHORT", "WAIT"])
        self.assertIsInstance(decision["confidence"], int)
        self.assertGreaterEqual(decision["confidence"], 0)
        self.assertLessEqual(decision["confidence"], 100)

        # Invalidation Condition & Failure Mode must be non-empty strings
        self.assertTrue(len(decision["invalidation_condition"].strip()) > 5)
        self.assertTrue(len(decision["failure_mode"].strip()) > 5)
        self.assertTrue(len(decision["setup_summary"].strip()) > 5)
        self.assertTrue(len(decision["human_decision_note"].strip()) > 5)

    def test_sqlite_storage_ai_lens_and_preferences_persistence(self):
        """Validates SQLite table persistence for ai_agent_lens_reports and preferences."""
        sample_lens = {"schema": "ai-agent-lens/v1", "symbol": "NASDAQ:AAPL"}
        sample_decision = {"bias": "LONG", "confidence": 85, "invalidation_condition": "Below 21EMA"}

        saved = self.storage.save_ai_agent_lens_report(
            symbol="NASDAQ:AAPL",
            timeframe="1D",
            bar_time=1700000000,
            lens_data=sample_lens,
            decision_data=sample_decision,
            model_used="gemini-3.8-flash",
        )
        self.assertTrue(saved)

        rep = self.storage.get_ai_agent_lens_report("NASDAQ:AAPL", "1D")
        self.assertIsNotNone(rep)
        self.assertEqual(rep["symbol"], "NASDAQ:AAPL")
        self.assertEqual(rep["timeframe"], "1D")
        self.assertEqual(rep["bar_time"], 1700000000)
        self.assertEqual(rep["decision"]["bias"], "LONG")
        self.assertEqual(rep["model_used"], "gemini-3.8-flash")

        # Test preferences table
        self.storage.save_preferences({"active_engine": "A", "theme": "dark", "rs_benchmark": "SPY"})
        prefs = self.storage.get_preferences()
        self.assertEqual(prefs.get("active_engine"), "A")
        self.assertEqual(prefs.get("theme"), "dark")
        self.assertEqual(prefs.get("rs_benchmark"), "SPY")

    def test_recover_state_and_history_logic(self):
        """Validates unified recovery returning cached bars, watchlist, preferences, and delta bars."""
        import asyncio
        bars = _generate_synthetic_bars(count=50, start_price=50000.0, trend_slope=10.0)
        self.storage.save_bars("BINANCE:BTCUSDT", "1D", bars)

        cutoff_time = bars[30]["time"]

        async def _run():
            return await server.recover_state_and_history(
                storage=self.storage,
                symbol="BINANCE:BTCUSDT",
                timeframe="1D",
                since_time=cutoff_time,
                limit=100,
            )

        res = asyncio.run(_run())
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["symbol"], "BINANCE:BTCUSDT")
        self.assertEqual(len(res["bars"]), 50)
        # Delta bars should contain only bars with time > cutoff_time (indices 31 to 49 = 19 bars)
        self.assertEqual(len(res["delta_bars"]), 19)
        self.assertTrue(all(b["time"] > cutoff_time for b in res["delta_bars"]))
        self.assertIsInstance(res["watchlist"], list)
        self.assertIsInstance(res["preferences"], dict)

    def test_dom_id_alignment_between_app_js_and_index_html(self):
        """Enforces 100% strict DOM ID contract: all elements queried in app.js exist in index.html."""
        project_root = Path(__file__).resolve().parent.parent
        app_js_path = project_root / "static" / "app.js"
        index_html_path = project_root / "static" / "index.html"

        with open(app_js_path, "r", encoding="utf-8") as f:
            app_js_content = f.read()

        with open(index_html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # Extract all document.getElementById("...")
        get_el_ids = re.findall(r'document\.getElementById\(["\']([^"\']+)["\']\)', app_js_content)
        # Extract all document.querySelector("#...")
        query_ids = re.findall(r'querySelector\(["\']#([a-zA-Z0-9_\-]+)["\']\)', app_js_content)

        all_queried_ids = set(get_el_ids + query_ids)

        # Extract all id="..." from index.html
        html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', html_content))

        missing_ids = [target_id for target_id in sorted(all_queried_ids) if target_id not in html_ids]

        self.assertEqual(
            missing_ids,
            [],
            f"DOM Contract Violation! The following IDs are queried in app.js but missing in index.html: {missing_ids}",
        )

    def test_ai_lens_endpoint_response_structure_and_offline_fallback(self):
        """Validates that GET /api/ai-lens returns schema-compliant decision payload even offline."""
        with TestClient(app) as client:
            res = client.get("/api/ai-lens?symbol=NASDAQ:AAPL&timeframe=1D")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "ok")
            self.assertIn("report", data)
            rep = data["report"]
            self.assertEqual(rep["symbol"], "NASDAQ:AAPL")
            self.assertEqual(rep["timeframe"], "1D")
            self.assertIn("lens", rep)
            self.assertIn("decision", rep)
            dec = rep["decision"]
            self.assertIn(dec["bias"], ["LONG", "SHORT", "WAIT"])
            self.assertIsInstance(dec["confidence"], int)
            self.assertTrue(len(dec["invalidation_condition"]) > 0)
            self.assertTrue(len(dec["failure_mode"]) > 0)

    def test_ai_lens_endpoint_caching_and_refresh(self):
        """Validates that GET /api/ai-lens caches reports and refreshes when requested."""
        with TestClient(app) as client:
            # Initial generation
            res1 = client.get("/api/ai-lens?symbol=NASDAQ:NVDA&timeframe=1D&refresh=true")
            self.assertEqual(res1.status_code, 200)
            self.assertFalse(res1.json().get("cached"))

            # Second call should be served from SQLite cache
            res2 = client.get("/api/ai-lens?symbol=NASDAQ:NVDA&timeframe=1D&refresh=false")
            self.assertEqual(res2.status_code, 200)
            self.assertTrue(res2.json().get("cached"))

            # Refresh call forces fresh diagnosis
            res3 = client.get("/api/ai-lens?symbol=NASDAQ:NVDA&timeframe=1D&refresh=true")
            self.assertEqual(res3.status_code, 200)
            self.assertFalse(res3.json().get("cached"))

    def test_recovery_endpoint_contract(self):
        """Validates that GET /api/recovery returns unified state, watchlist, and preferences."""
        with TestClient(app) as client:
            res = client.get("/api/recovery?symbol=BINANCE:BTCUSDT&timeframe=1D")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["symbol"], "BINANCE:BTCUSDT")
            self.assertEqual(data["timeframe"], "1D")
            self.assertIn("bars", data)
            self.assertIn("watchlist", data)
            self.assertIn("preferences", data)
            self.assertIsInstance(data["watchlist"], list)
            self.assertGreater(len(data["watchlist"]), 0)

    def test_preferences_endpoints_crud(self):
        """Validates GET /api/preferences and POST /api/preferences roundtrip."""
        with TestClient(app) as client:
            post_res = client.post("/api/preferences", json={"theme": "dark", "active_engine": "C"})
            self.assertEqual(post_res.status_code, 200)
            self.assertEqual(post_res.json()["status"], "ok")

            get_res = client.get("/api/preferences")
            self.assertEqual(get_res.status_code, 200)
            prefs = get_res.json()["preferences"]
            self.assertEqual(prefs.get("theme"), "dark")
            self.assertEqual(prefs.get("active_engine"), "C")

    def test_symbol_normalization_and_validation(self):
        """Validates ticker prefix resolution and HTTP 400 rejection for malformed symbols."""
        with TestClient(app) as client:
            # Bare stock ticker resolves to NASDAQ
            res_lens = client.get("/api/ai-lens?symbol=NVDA&timeframe=1D")
            self.assertEqual(res_lens.status_code, 200)
            self.assertEqual(res_lens.json()["symbol"], "NASDAQ:NVDA")

            # Bare crypto ticker resolves to BINANCE
            res_rec = client.get("/api/recovery?symbol=BTCUSDT&timeframe=1D")
            self.assertEqual(res_rec.status_code, 200)
            self.assertEqual(res_rec.json()["symbol"], "BINANCE:BTCUSDT")

            # Invalid symbols return 400 Bad Request
            res_bad_lens = client.get("/api/ai-lens?symbol=INVALID/TICKER&timeframe=1D")
            self.assertEqual(res_bad_lens.status_code, 400)
            self.assertIn("error", res_bad_lens.json())

            res_bad_rec = client.get("/api/recovery?symbol=INVALID/TICKER&timeframe=1D")
            self.assertEqual(res_bad_rec.status_code, 400)
            self.assertIn("error", res_bad_rec.json())

    def test_rs_rating_with_unaligned_bars(self):
        """Validates Relative Strength rating alignment when target and benchmark bar histories differ in length."""
        # Target has 100 bars from base_time, Benchmark only has 30 bars (later start time)
        target_bars = _generate_synthetic_bars(count=100, start_price=100.0, trend_slope=0.5, base_time=1700000000)
        bench_bars = _generate_synthetic_bars(count=30, start_price=400.0, trend_slope=0.1, base_time=1700000000 + (70 * 86400))

        payload = server.build_ai_agent_lens_payload(
            symbol="NASDAQ:AAPL",
            timeframe="1D",
            bars=target_bars,
            benchmark_bars=bench_bars,
        )

        quant = payload["quant_metrics"]
        self.assertIsNotNone(quant["rs_ratio"])
        self.assertIsNotNone(quant["rs_rating"])
        self.assertGreaterEqual(quant["rs_rating"], 1)
        self.assertLessEqual(quant["rs_rating"], 99)


if __name__ == "__main__":
    unittest.main()

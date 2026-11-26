"""Unit and Integration Tests for 10 Code Review & Architectural Audit Fixes.

Covers:
1.1 Cache-first fast path threshold (prevents single-bar starvation).
1.2 Dotenv loading precedence & dynamic provider credentials.
1.3 Gemini header authentication (x-goog-api-key) and valid models cascade.
2.1 Non-repainting contract with closed session handling.
2.2 Storage timeframe casing normalization (lowercase minutes/hours, uppercase daily/weekly).
2.3 CANSLIM screener multi-key revenue parsing and strict filter enforcement.
2.4 Watchlist eviction notification (symbol_removed) and auto-incrementing sort_order.
2.5 TradingView client series mapping cleanup across reconnects.
3.1 Packaging & repo hygiene (no redundant src-tauri/static symlink, no nested sidecar).
3.2 ticker.py robust qsd packet parsing without 'v' key and thread-safe SQLite operations.
"""

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import server
from engine.storage import Storage, normalize_storage_timeframe
from engine.providers import PolygonDataProvider, AlpacaDataProvider
from engine.screener import ScreeningCriteria, evaluate_canslim_stage, scan_symbol_candidate
from engine.client import TradingViewAsyncClient
from ticker import ticker


class TestAuditTenFindingsFixes(unittest.TestCase):
    """Test suite verifying all 10 architectural review findings are resolved."""

    def setUp(self):
        self.storage = Storage(":memory:")

    def tearDown(self):
        self.storage.close()

    # 1.1 Cache-First Fast Path Threshold
    def test_item_1_1_cache_first_threshold_prevents_single_bar_starvation(self):
        """Verifies that 1 live bar does not mask the need to fetch historical bars."""
        storage = self.storage
        now_ts = int(time.time())
        # Seed only 1 recent bar
        single_bar = [{"time": now_ts, "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 1000.0}]
        storage.save_bars("NASDAQ:TEST11", "1m", single_bar)

        with patch("api.deps.get_storage", return_value=storage), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo:
            mock_yahoo.return_value = [
                {"time": now_ts - (i * 60), "open": 100.0, "high": 105.0, "low": 99.0, "close": 100.0, "volume": 500.0}
                for i in range(50)
            ]
            import asyncio
            # With limit=300 and clean_tf="1m", min_expected is 30. 1 cached bar < 30, so it must fetch remote
            bars = asyncio.run(server.fetch_historical_bars("NASDAQ:TEST11", timeframe="1m", limit=300))
            self.assertTrue(mock_yahoo.called)
            self.assertEqual(len(bars), 50)

    # 1.2 Dotenv Loading Order & Dynamic Providers
    def test_item_1_2_dynamic_provider_configuration_and_env_setdefault(self):
        """Verifies that providers dynamically reflect API keys set in os.environ after init."""
        poly = PolygonDataProvider()
        alpaca = AlpacaDataProvider()

        orig_poly = os.environ.get("POLYGON_API_KEY")
        orig_apca_id = os.environ.get("APCA_API_KEY_ID")
        orig_apca_sec = os.environ.get("APCA_API_SECRET_KEY")

        try:
            os.environ.pop("POLYGON_API_KEY", None)
            os.environ.pop("APCA_API_KEY_ID", None)
            os.environ.pop("APCA_API_SECRET_KEY", None)

            self.assertFalse(poly.is_configured)
            self.assertFalse(alpaca.is_configured)

            os.environ["POLYGON_API_KEY"] = "test_poly_key"
            os.environ["APCA_API_KEY_ID"] = "test_apca_id"
            os.environ["APCA_API_SECRET_KEY"] = "test_apca_sec"

            # Must dynamically report configured without needing re-instantiation
            self.assertTrue(poly.is_configured)
            self.assertEqual(poly.api_key, "test_poly_key")
            self.assertTrue(alpaca.is_configured)
            self.assertEqual(alpaca.key_id, "test_apca_id")
            self.assertEqual(alpaca.secret_key, "test_apca_sec")
        finally:
            if orig_poly:
                os.environ["POLYGON_API_KEY"] = orig_poly
            else:
                os.environ.pop("POLYGON_API_KEY", None)
            if orig_apca_id:
                os.environ["APCA_API_KEY_ID"] = orig_apca_id
            else:
                os.environ.pop("APCA_API_KEY_ID", None)
            if orig_apca_sec:
                os.environ["APCA_API_SECRET_KEY"] = orig_apca_sec
            else:
                os.environ.pop("APCA_API_SECRET_KEY", None)

    # 1.3 Gemini Header Auth & Valid Models
    def test_item_1_3_gemini_header_auth_and_valid_cascade(self):
        """Verifies that call_gemini_generate passes key in headers and cascade has valid models."""
        self.assertIn("gemini-2.5-flash", server.GEMINI_MODELS_CASCADE)
        for model_id in server.GEMINI_MODELS_CASCADE:
            # Must not contain non-existent 3.5 series
            self.assertNotIn("3.5", model_id)

        import asyncio
        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyFakeKey123"}), \
             patch("api.deps.get_shared_client", return_value=None), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{"content": {"parts": [{"text": "Bullish Stage 2"}]}}]
            }
            mock_post.return_value = mock_resp

            result = asyncio.run(server.call_gemini_generate("Analyze NVDA"))
            self.assertEqual(result, "Bullish Stage 2")

            # Verify the call URL did NOT leak the key in query params
            called_url = mock_post.call_args[0][0]
            self.assertNotIn("key=", called_url)
            self.assertNotIn("AIzaSyFakeKey123", called_url)

            # Verify the header auth was passed
            called_headers = mock_post.call_args[1].get("headers", {})
            self.assertEqual(called_headers.get("x-goog-api-key"), "AIzaSyFakeKey123")

    # 2.1 Non-Repainting Contract for Closed Session
    def test_item_2_1_ai_agent_lens_closed_session(self):
        """Verifies that when is_live=False, all bars are confirmed and no unconfirmed bar is sliced."""
        base_t = 1700000000
        bars = [
            {"time": base_t + (i * 86400), "open": 100.0 + i, "high": 105.0 + i, "low": 99.0 + i, "close": 104.0 + i, "volume": 1000.0}
            for i in range(30)
        ]

        # 1. Explicitly closed session (e.g. weekend or daily after-hours)
        payload_closed = server.build_ai_agent_lens_payload("NASDAQ:NVDA", "1D", bars, is_live=False)
        self.assertIsNone(payload_closed["unconfirmed_latest_bar"])
        self.assertEqual(payload_closed["confirmed_bar"]["time"], bars[-1]["time"])
        self.assertEqual(payload_closed["quant_metrics"]["close"], bars[-1]["close"])

        # 2. Live intraday session
        payload_live = server.build_ai_agent_lens_payload("NASDAQ:NVDA", "1D", bars, is_live=True)
        self.assertIsNotNone(payload_live["unconfirmed_latest_bar"])
        self.assertEqual(payload_live["unconfirmed_latest_bar"]["time"], bars[-1]["time"])
        self.assertEqual(payload_live["confirmed_bar"]["time"], bars[-2]["time"])
        self.assertEqual(payload_live["quant_metrics"]["close"], bars[-2]["close"])

    # 2.2 Storage Timeframe Casing Normalization
    def test_item_2_2_storage_timeframe_casing_normalization(self):
        """Verifies that SQLite storage preserves lowercase minutes/hours and uppercase daily/weekly."""
        self.assertEqual(normalize_storage_timeframe("1m"), "1m")
        self.assertEqual(normalize_storage_timeframe("1M"), "1m")
        self.assertEqual(normalize_storage_timeframe("5m"), "5m")
        self.assertEqual(normalize_storage_timeframe("5M"), "5m")
        self.assertEqual(normalize_storage_timeframe("1h"), "1h")
        self.assertEqual(normalize_storage_timeframe("1H"), "1h")
        self.assertEqual(normalize_storage_timeframe("1d"), "1D")
        self.assertEqual(normalize_storage_timeframe("1D"), "1D")
        self.assertEqual(normalize_storage_timeframe("1w"), "1W")
        self.assertEqual(normalize_storage_timeframe("1W"), "1W")

        # Save and retrieve with mixed casing
        storage = self.storage
        lens_data = {"trend_regime": "STAGE_2_UPTREND"}
        decision_data = {"action": "BUY"}
        storage.save_ai_agent_lens_report("NASDAQ:AAPL", "1m", 1700000000, lens_data, decision_data)

        # Retrieve using "1m" and "1M" should both hit the same normalized record
        rep_lower = storage.get_ai_agent_lens_report("NASDAQ:AAPL", "1m")
        rep_upper = storage.get_ai_agent_lens_report("NASDAQ:AAPL", "1M")
        self.assertIsNotNone(rep_lower)
        self.assertIsNotNone(rep_upper)
        self.assertEqual(rep_lower["timeframe"], "1m")
        self.assertEqual(rep_upper["timeframe"], "1m")

    # 2.3 CANSLIM Screener Multi-Key Revenue and Filter Enforcement
    def test_item_2_3_canslim_screener_revenue_keys_and_filtering(self):
        """Verifies that alternative revenue YoY keys are parsed and missing data fails min_rev_growth_yoy."""
        base_t = 1700000000
        bars = [
            {"time": base_t + (i * 86400), "open": 50.0 + i, "high": 52.0 + i, "low": 49.0 + i, "close": 51.0 + i, "volume": 10000.0}
            for i in range(250)
        ]

        # Test key aliases
        stage1 = evaluate_canslim_stage(bars, fundamentals={"revenueGrowth": "35.5%"})
        self.assertEqual(stage1["revenue_yoy"], "35.5%")

        stage2 = evaluate_canslim_stage(bars, fundamentals={"revenue_growth": "42.0%"})
        self.assertEqual(stage2["revenue_yoy"], "42.0%")

        # Test scan filtering when min_rev_growth_yoy is enforced
        criteria = ScreeningCriteria(min_rev_growth_yoy=25.0)

        # Missing revenue data must return None
        import asyncio
        async def _test_scan():
            sym_info = {"id": "NASDAQ:TESTREV", "symbol": "TESTREV"}
            sem = asyncio.Semaphore(1)
            return await scan_symbol_candidate(
                sym_info, criteria, self.storage, benchmark_bars=[], fetch_func=None, semaphore=sem
            )
        res_missing = asyncio.run(_test_scan())
        self.assertIsNone(res_missing)

    # 2.4 Watchlist Eviction Broadcast and Auto-Increment Sort Order
    def test_item_2_4_watchlist_auto_increment_sort_order_and_eviction_broadcast(self):
        """Verifies that add_watchlist_item auto-increments sort_order and subscribe_symbol broadcasts symbol_removed."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        storage = self.storage
        storage.add_watchlist_item({"id": "NASDAQ:SYM1", "name": "Sym 1"})
        storage.add_watchlist_item({"id": "NASDAQ:SYM2", "name": "Sym 2"})

        wl = storage.get_watchlist()
        order_map = {item["id"]: item["sort_order"] for item in wl}
        self.assertEqual(order_map["NASDAQ:SYM1"], 0)
        self.assertEqual(order_map["NASDAQ:SYM2"], 1)

        # Verify backend actually broadcasts symbol_removed on eviction
        orig_wl = [dict(w) for w in server.WATCHLIST]
        orig_syms = list(server.SYMBOLS)
        try:
            server.WATCHLIST.clear()
            server.SYMBOLS.clear()
            for i in range(50):
                sym_id = f"{'CORE' if i < 5 else 'DYN'}:EV{i}"
                server.WATCHLIST.append({"id": sym_id, "name": sym_id, "exchange": "DYN", "pair": f"EV{i}", "type": "stock"})
                server.SYMBOLS.append(sym_id)
            expected_evicted = server.WATCHLIST[5]["id"]
            broadcasts = []
            with patch.object(server.manager, "broadcast_nowait", side_effect=lambda m: broadcasts.append(m)), \
                 patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]):
                asyncio.run(server.subscribe_symbol({"symbol": "NASDAQ:EVICTED99"}))
            self.assertTrue(
                any(m.get("type") == "symbol_removed" and m.get("symbol") == expected_evicted for m in broadcasts),
                f"Expected symbol_removed broadcast for {expected_evicted}, got {broadcasts}",
            )
        finally:
            server.WATCHLIST.clear()
            server.WATCHLIST.extend(orig_wl)
            server.SYMBOLS.clear()
            server.SYMBOLS.extend(orig_syms)

    # 2.5 TradingView Client Reconnect Cleanup
    def test_item_2_5_tv_client_reconnect_cleanup(self):
        """Verifies that TradingViewAsyncClient cleans series mapping and cancels futures on disconnect."""
        client = TradingViewAsyncClient(["BINANCE:BTCUSDT"])
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            client.series_futures["sds_test"] = ("NASDAQ:AAPL_1D", fut)
            client.series_map["sds_test"] = "NASDAQ:AAPL_1D"
            client.key_to_series_id["NASDAQ:AAPL_1D"] = "sds_test"

            # Call stop()
            loop.run_until_complete(client.stop())

            self.assertEqual(len(client.series_map), 0)
            self.assertEqual(len(client.key_to_series_id), 0)
            self.assertEqual(len(client.series_futures), 0)
            self.assertTrue(fut.cancelled())
        finally:
            loop.close()

    # 3.1 Packaging & Repo Hygiene
    def test_item_3_1_src_tauri_hygiene(self):
        """Verifies that src-tauri/static duplicate symlink is not present and no nested sidecar exists."""
        tauri_static = Path("src-tauri/static")
        self.assertFalse(tauri_static.exists())

        nested_sidecar = Path("src-tauri/bin/server-sidecar/server-sidecar")
        self.assertFalse(nested_sidecar.exists())

    # 3.2 ticker.py Robust QSD Parsing & Thread Safety
    def test_item_3_2_ticker_robust_qsd_parsing_and_thread_safe_sqlite(self):
        """Verifies that ticker.py handles packets without 'v' key and uses db_lock for SQLite."""
        t = ticker(symbols=["BINANCE:BTCUSDT"], save=False)

        # Status packet with missing 'v' key
        status_msg = {"m": "qsd", "p": ["qs_123", {"s": "ok", "n": "BINANCE:BTCUSDT"}]}
        try:
            t.parseMessage(status_msg)
        except Exception as e:
            self.fail(f"parseMessage raised unexpected exception on status packet: {e}")

        # Packet with malformed 'p'
        malformed_msg = {"m": "qsd", "p": ["qs_123"]}
        try:
            t.parseMessage(malformed_msg)
        except Exception as e:
            self.fail(f"parseMessage raised unexpected exception on malformed packet: {e}")

        # Verify db_lock exists
        self.assertTrue(hasattr(t, "db_lock"))


if __name__ == "__main__":
    unittest.main()

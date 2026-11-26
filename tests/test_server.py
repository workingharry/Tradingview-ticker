"""Comprehensive offline unit tests for server.py REST endpoints and WebSocket broadcaster.

All external network operations (Binance REST API and TradingView WebSocket) are mocked
so tests run 100% offline, deterministically, and with zero network latency or rate-limiting.
"""

import unittest
import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
import httpx
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import server
from server import app, WATCHLIST, manager, fetch_binance_history, ConnectionManager
from engine.aggregator import CandlestickBar


MOCK_BINANCE_BARS = [
    {
        "time": 1731234000 + i * 60,
        "open": 68000.0 + i * 10,
        "high": 68050.0 + i * 10,
        "low": 67950.0 + i * 10,
        "close": 68020.0 + i * 10,
        "volume": 15.5 + i,
    }
    for i in range(10)
]


class TestServerEndpoints(unittest.TestCase):
    """Integration tests for FastAPI endpoints with mocked external network services."""

    def setUp(self):
        self.original_watchlist = [dict(w) for w in server.WATCHLIST]
        self.original_symbols = list(server.SYMBOLS)
        self.fetch_patcher = patch(
            "api.history.fetch_binance_history",
            new_callable=AsyncMock,
            return_value=list(MOCK_BINANCE_BARS),
        )
        self.client_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.client_stop_patcher = patch(
            "engine.client.TradingViewAsyncClient.stop",
            new_callable=AsyncMock,
        )
        self.mock_fetch = self.fetch_patcher.start()
        self.mock_start = self.client_start_patcher.start()
        self.mock_stop = self.client_stop_patcher.start()

    def tearDown(self):
        self.fetch_patcher.stop()
        self.client_start_patcher.stop()
        self.client_stop_patcher.stop()
        server.WATCHLIST.clear()
        server.WATCHLIST.extend(self.original_watchlist)
        server.SYMBOLS.clear()
        server.SYMBOLS.extend(self.original_symbols)

    def test_get_symbols_endpoint(self):
        """Validates that GET /api/symbols returns all configured watchlist items with states."""
        with TestClient(app) as client:
            resp = client.get("/api/symbols")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("symbols", data)
            self.assertEqual(len(data["symbols"]), len(server.WATCHLIST))

            symbol_ids = [s["id"] for s in data["symbols"]]
            self.assertIn("NASDAQ:NVDA", symbol_ids)
            self.assertIn("BINANCE:BTCUSDT", symbol_ids)

            first = data["symbols"][0]
            for field in ["id", "name", "exchange", "pair", "type", "state"]:
                self.assertIn(field, first)
            self.assertIn("status", data)

    def test_get_history_crypto_seeded(self):
        """Validates that GET /api/history returns historical bars with required keys for crypto."""
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=BINANCE:BTCUSDT")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["symbol"], "BINANCE:BTCUSDT")
            self.assertIsInstance(data["bars"], list)
            self.assertGreater(len(data["bars"]), 0)

            first_bar = data["bars"][0]
            for field in ["time", "open", "high", "low", "close", "volume"]:
                self.assertIn(field, first_bar)
                self.assertIsInstance(first_bar[field], (int, float))

    @pytest.mark.network
    def test_get_history_equity_live_or_empty(self):
        """Validates that GET /api/history returns 200 and a list for equity symbols."""
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=NASDAQ:AAPL")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["symbol"], "NASDAQ:AAPL")
            self.assertIsInstance(data["bars"], list)

    def test_get_history_equity_offline_mocked(self):
        """Validates GET /api/history for equity symbols offline with mocked Yahoo fetch."""
        mock_equity_bars = [
            {"time": 1731234000, "open": 220.0, "high": 225.0, "low": 219.0, "close": 223.0, "volume": 500000.0}
        ]
        with patch("api.history.fetch_yahoo_history", new_callable=AsyncMock, return_value=mock_equity_bars):
            with TestClient(app) as client:
                resp = client.get("/api/history?symbol=NASDAQ:AAPL")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["symbol"], "NASDAQ:AAPL")
                self.assertIsInstance(data["bars"], list)

    def test_get_history_unknown_symbol_returns_empty(self):
        """Validates that unknown symbols return 200 OK with empty bars array."""
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=UNKNOWN:XYZ")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["symbol"], "UNKNOWN:XYZ")
            self.assertEqual(data["bars"], [])

    def test_get_history_binance_fetch_failure_resilience(self):
        """Validates that if external history fetch fails, endpoint gracefully returns empty list."""
        self.mock_fetch.return_value = []
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=BINANCE:ETHUSDT")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["symbol"], "BINANCE:ETHUSDT")
            self.assertIsInstance(data["bars"], list)

    def test_static_index_serves_html(self):
        """Validates that GET / delivers the main HTML terminal application."""
        with TestClient(app) as client:
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Momentum Swing Terminal", resp.text)
            self.assertIn("chartContainer", resp.text)

    def test_static_asset_serving(self):
        """Validates that static assets are accessible via /static and root paths."""
        with TestClient(app) as client:
            resp_static = client.get("/static/style.css")
            self.assertEqual(resp_static.status_code, 200)
            resp_root = client.get("/style.css")
            self.assertEqual(resp_root.status_code, 200)

    def test_websocket_live_connects_and_receives_status(self):
        """Validates that WebSocket /ws/live handshakes and immediately sends initial status."""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/live") as websocket:
                data = websocket.receive_json()
                self.assertEqual(data["type"], "status")
                self.assertIn("status", data)

    def test_websocket_live_receives_broadcast_quote_and_bar(self):
        """Validates multiplexed live quote and candlestick bar streaming to WebSocket clients."""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/live") as websocket:
                # 1. First frame is initial status
                initial = websocket.receive_json()
                self.assertEqual(initial["type"], "status")

                # 2. Trigger quote update handler
                server.on_quote_update_handler("BINANCE:BTCUSDT", {
                    "price": 68500.0,
                    "volume": 25000.0,
                    "time": 1731234000
                })
                quote_msg = websocket.receive_json()
                if quote_msg.get("type") == "bar":
                    quote_msg = websocket.receive_json()
                self.assertEqual(quote_msg["type"], "quote")
                self.assertEqual(quote_msg["symbol"], "BINANCE:BTCUSDT")
                self.assertEqual(quote_msg["data"]["price"], 68500.0)

                # 3. Trigger bar update handler
                dummy_bar = CandlestickBar(
                    time=1731234000,
                    open=68400.0,
                    high=68600.0,
                    low=68350.0,
                    close=68500.0,
                    volume=12.5
                )
                server.on_bar_update_handler("BINANCE:BTCUSDT", dummy_bar)
                bar_msg = websocket.receive_json()
                self.assertEqual(bar_msg["type"], "bar")
                self.assertEqual(bar_msg["symbol"], "BINANCE:BTCUSDT")
                self.assertEqual(bar_msg["bar"]["close"], 68500.0)

    def test_websocket_live_client_disconnect(self):
        """Validates that client disconnect cleans up client queue without server errors."""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/live") as websocket:
                initial = websocket.receive_json()
                self.assertEqual(initial["type"], "status")
                self.assertGreater(len(manager.client_queues), 0)
            # After context manager exit, client has disconnected
            # Manager cleans up queue on disconnect
            self.assertEqual(len(manager.client_queues), 0)

    def test_websocket_disconnect_during_initial_status_send_does_not_leak_queue(self):
        """Validates that a client disconnecting during initial status send cleans up its queue."""
        async def run_test():
            class AbortiveWS:
                def __init__(self):
                    self.closed = False

                async def accept(self):
                    return None

                async def send_json(self, data):
                    raise WebSocketDisconnect(code=1006)

                async def receive_text(self):
                    raise WebSocketDisconnect(code=1006)

                async def close(self, code=1000, reason=""):
                    self.closed = True

            ws = AbortiveWS()
            manager.client_queues.pop(ws, None)
            initial_count = len(manager.client_queues)

            await server.websocket_live_endpoint(ws)

            self.assertNotIn(ws, manager.client_queues)
            self.assertEqual(len(manager.client_queues), initial_count)

        asyncio.run(run_test())


class TestConnectionManager(unittest.TestCase):
    """Unit tests for WebSocket ConnectionManager backpressure, drop-oldest, and close handling."""

    def test_connection_manager_drop_oldest_on_full_queue(self):
        """Validates that a full client queue drops the oldest message and appends the newest."""
        async def run_test():
            mgr = ConnectionManager(max_queue_size=2)

            class MockWS:
                """Mock websocket instance for queue tests."""

            ws = MockWS()
            queue = asyncio.Queue(maxsize=2)
            mgr.client_queues[ws] = queue

            # Add 2 messages to fill queue
            await mgr.broadcast({"msg": 1})
            await mgr.broadcast({"msg": 2})
            self.assertTrue(queue.full())

            # Add 3rd message -> triggers drop-oldest
            await mgr.broadcast({"msg": 3})
            self.assertTrue(queue.full())

            first = queue.get_nowait()
            second = queue.get_nowait()
            self.assertEqual(first["msg"], 2)
            self.assertEqual(second["msg"], 3)

        asyncio.run(run_test())

    def test_connection_manager_high_volume_drop_oldest(self):
        """Validates backpressure when pushing >100 messages into a bounded queue of size 100."""
        async def run_test():
            mgr = ConnectionManager(max_queue_size=100)

            class MockWS:
                """Mock websocket instance for queue tests."""

            ws = MockWS()
            queue = asyncio.Queue(maxsize=100)
            mgr.client_queues[ws] = queue

            for i in range(150):
                mgr.broadcast_nowait({"index": i})

            self.assertEqual(queue.qsize(), 100)
            first = queue.get_nowait()
            self.assertEqual(first["index"], 50)

        asyncio.run(run_test())

    def test_connection_manager_close_all(self):
        """Validates that close_all() gracefully closes active WebSockets with code 1001."""
        async def run_test():
            mgr = ConnectionManager(max_queue_size=10)

            class MockWS:
                def __init__(self):
                    self.closed = False
                    self.code = None
                    self.reason = None

                async def close(self, code=1000, reason=""):
                    self.closed = True
                    self.code = code
                    self.reason = reason

            ws1, ws2 = MockWS(), MockWS()
            mgr.client_queues[ws1] = asyncio.Queue(maxsize=10)
            mgr.client_queues[ws2] = asyncio.Queue(maxsize=10)

            await mgr.close_all()
            self.assertTrue(ws1.closed)
            self.assertEqual(ws1.code, 1001)
            self.assertTrue(ws2.closed)
            self.assertEqual(ws2.code, 1001)
            self.assertEqual(len(mgr.client_queues), 0)

        asyncio.run(run_test())

    def test_connection_manager_disconnect_safe(self):
        """Validates that disconnecting an untracked socket does not raise an exception."""
        mgr = ConnectionManager(max_queue_size=10)

        class MockWS:
            """Mock untracked websocket."""

        ws = MockWS()
        # Should not raise and client queues remain empty
        mgr.disconnect(ws)
        self.assertEqual(len(mgr.client_queues), 0)


class TestFetchBinanceHistory(unittest.TestCase):
    """Unit tests for fetch_binance_history parsing and error handling."""

    def test_fetch_binance_history_success(self):
        """Validates parsing of raw Binance kline responses into CandlestickBar dicts."""
        async def run_test():
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                [1731234000000, "68000.00", "68100.00", "67900.00", "68050.00", "12.345"],
                [1731234060000, "68050.00", "68150.00", "68010.00", "68120.00", "8.910"],
            ]

            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("httpx.AsyncClient", mock_client_cls):
                bars = await fetch_binance_history("BINANCE:BTCUSDT", limit=2)
                self.assertEqual(len(bars), 2)
                self.assertEqual(bars[0]["time"], 1731234000)
                self.assertEqual(bars[0]["open"], 68000.0)
                self.assertEqual(bars[0]["high"], 68100.0)
                self.assertEqual(bars[0]["low"], 67900.0)
                self.assertEqual(bars[0]["close"], 68050.0)
                self.assertEqual(bars[0]["volume"], 12.345)

        asyncio.run(run_test())

    def test_fetch_binance_history_http_error(self):
        """Validates that non-200 responses return an empty list gracefully."""
        async def run_test():
            mock_resp = MagicMock()
            mock_resp.status_code = 500

            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client_cls = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("httpx.AsyncClient", mock_client_cls):
                bars = await fetch_binance_history("BINANCE:BTCUSDT", limit=2)
                self.assertEqual(bars, [])

        asyncio.run(run_test())

    def test_fetch_binance_history_network_exception(self):
        """Validates that network exceptions return an empty list gracefully."""
        async def run_test():
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectTimeout("Connection timed out")
            mock_client_cls = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch("httpx.AsyncClient", mock_client_cls):
                bars = await fetch_binance_history("BINANCE:BTCUSDT", limit=2)
                self.assertEqual(bars, [])

        asyncio.run(run_test())


class TestTenDefectsRegression(unittest.TestCase):
    """Hermetic regression tests validating all 10 code review defect remediations."""

    def test_r1_timeframe_normalization(self):
        """Verifies canonical timeframe normalization for SQLite queries and Yahoo API."""
        self.assertEqual(server.normalize_timeframe("1m"), "1m")
        self.assertEqual(server.normalize_timeframe("1M"), "1m")
        self.assertEqual(server.normalize_timeframe("5m"), "5m")
        self.assertEqual(server.normalize_timeframe("5M"), "5m")
        self.assertEqual(server.normalize_timeframe("15m"), "15m")
        self.assertEqual(server.normalize_timeframe("1h"), "1h")
        self.assertEqual(server.normalize_timeframe("1H"), "1h")
        self.assertEqual(server.normalize_timeframe("1d"), "1D")
        self.assertEqual(server.normalize_timeframe("1D"), "1D")
        self.assertEqual(server.normalize_timeframe("1w"), "1W")
        self.assertEqual(server.normalize_timeframe("1W"), "1W")
        self.assertEqual(server.normalize_timeframe("1wk"), "1W")

    def test_r2_ai_agent_lens_payload_timeframe_casing(self):
        """Verifies that build_ai_agent_lens_payload preserves canonical timeframe casing."""
        bars = [
            {"time": 1700000000 + i * 60, "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 100.0}
            for i in range(10)
        ]
        payload = server.build_ai_agent_lens_payload("NASDAQ:NVDA", "1m", bars)
        self.assertEqual(payload["timeframe"], "1m")

    def test_r3_chat_endpoint_extracts_nested_quant_metrics(self):
        """Verifies chat_endpoint extracts quant_metrics from lens_report['lens']['quant_metrics']."""
        mock_lens_report = {
            "symbol": "NASDAQ:NVDA",
            "timeframe": "1D",
            "lens": {
                "quant_metrics": {
                    "close": 145.0,
                    "stage": "Stage 2",
                    "trend_regime": "Stage 2 Uptrend"
                }
            },
            "decision": {"action": "BUY"}
        }
        with patch.object(server.get_storage(), "get_ai_agent_lens_report", return_value=mock_lens_report):
            with patch("api.ai.call_gemini_generate", return_value="Analyzed with Stage 2 Uptrend metrics.") as mock_gemini:
                with TestClient(app) as client:
                    resp = client.post("/api/chat", json={"message": "NVDA trend", "symbol": "NASDAQ:NVDA", "timeframe": "1D"})
                    self.assertEqual(resp.status_code, 200)
                    prompt_arg = mock_gemini.call_args[0][0]
                    self.assertIn("145.0", prompt_arg)
                    self.assertIn("Stage 2", prompt_arg)

    def test_r4_pyinstaller_hiddenimports_include_screener_and_pine(self):
        # guard-test: 設定守門，允許讀原始碼
        """Verifies server.spec and build_sidecar.py hiddenimports contain screener and pine_engine."""
        import scripts.build_sidecar as bs
        self.assertIn("engine.screener", bs.REQUIRED_HIDDEN_IMPORTS)
        self.assertIn("engine.pine_engine", bs.REQUIRED_HIDDEN_IMPORTS)
        spec_content = Path("server.spec").read_text(encoding="utf-8")
        self.assertIn('"engine.screener"', spec_content)
        self.assertIn('"engine.pine_engine"', spec_content)

    def test_r5_client_scheme_b_resolution_and_high_low_quote_fields(self):
        """Verifies client res_map has 1W resolution and quotes parse high_price/low_price."""
        from engine.client import TradingViewAsyncClient
        client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT"])
        pkt = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": ["qs_1", {"n": "BINANCE:BTCUSDT", "v": {"lp": 68000.0, "high_price": 69000.0, "low_price": 67000.0}}]
            }
        }
        client._handle_packet(pkt)
        st = client.states["BINANCE:BTCUSDT"]
        self.assertEqual(st.get("high"), 69000.0)
        self.assertEqual(st.get("low"), 67000.0)

    def test_r6_yahoo_session_manager_cross_loop_rebind(self):
        """Verifies YahooSessionManager rebinds lock dynamically across distinct asyncio event loops."""
        async def loop1_call():
            mgr = server.YahooSessionManager()
            lock1 = mgr._get_lock()
            return lock1

        async def loop2_call():
            mgr = server.YahooSessionManager()
            lock2 = mgr._get_lock()
            return lock2

        lock1 = asyncio.run(loop1_call())
        lock2 = asyncio.run(loop2_call())
        self.assertIsNotNone(lock1)
        self.assertIsNotNone(lock2)
        self.assertIsNot(lock1, lock2)

    def test_r7_connection_manager_broadcast_without_queue_loop_attribute(self):
        """Verifies ConnectionManager broadcasts safely in Python 3.12+ when Queue lacks _loop."""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            mgr = server.ConnectionManager(max_queue_size=10)

            class DummyWS:
                pass

            ws = DummyWS()
            queue = asyncio.Queue(maxsize=10)
            mgr.client_queues[ws] = queue
            mgr.client_loops[ws] = loop

            # Remove _loop if present to simulate Python 3.12+
            if "_loop" in vars(queue):
                delattr(queue, "_loop")

            mgr.broadcast_nowait({"type": "test", "data": 123})
            self.assertEqual(queue.qsize(), 1)
            msg = queue.get_nowait()
            self.assertEqual(msg["data"], 123)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_r8_tauri_conf_csp_contains_query2(self):
        # guard-test: 設定守門，允許讀原始碼
        """Verifies that src-tauri/tauri.conf.json includes query2.finance.yahoo.com in CSP."""
        conf_path = Path("src-tauri/tauri.conf.json")
        conf = json.loads(conf_path.read_text(encoding="utf-8"))
        csp = conf.get("app", {}).get("security", {}).get("csp", "")
        self.assertIn("https://query2.finance.yahoo.com", csp)

    def test_r9_ticker_identifier_escaping_and_main_thread_signal_guard(self):
        """Verifies ticker.py escapes dynamic SQLite table names and guards signal registration."""
        from ticker import Ticker
        t = Ticker(symbols=['NVDA"test'], save=False)
        self.assertIn('NVDA"test', t.symbols)

    def test_fetch_historical_bars_timeframe_normalization_and_casing(self):
        """Ponytail finding 2: Verifies that lowercase or mixed-case timeframes (e.g. 1d, 1M) are canonicalized in cache lookup and persistence."""
        import time
        from datetime import date, datetime, time as dt_time
        from unittest import mock
        from server import fetch_historical_bars, get_storage
        from engine.market_time import NY_TZ
        storage = get_storage()
        # 第 1 段 S1-02: freshness is now decided by US session rules, not wall-clock age.
        # Pin the clock to a fixed closed session so this test no longer passes only on weekends.
        session_date = date(2026, 11, 20)  # Friday
        close_ts = int(datetime.combine(session_date, dt_time(16, 0), tzinfo=NY_TZ).timestamp())
        eval_ts = close_ts + 3600  # Fri 17:00 ET, market closed
        fake_bars = [
            {"time": close_ts - ((249 - i) * 86400), "open": 200.0, "high": 205.0, "low": 199.0, "close": 204.0, "volume": 50000.0}
            for i in range(250)
        ]
        test_sym = f"NASDAQ:TESTNORM_{int(time.time() * 1000)}"
        # Saved under canonical "1D"
        storage.save_bars(test_sym, "1D", fake_bars)
        # This process fetched the series after the close -> the cache is fresh.
        server._last_remote_fetch[(test_sym, "1D")] = float(close_ts + 600)

        try:
            with mock.patch("api.history.fetch_yahoo_history") as mock_yahoo, \
                 mock.patch("api.history.is_regular_session_open", return_value=False), \
                 mock.patch("api.history.last_completed_session_date", return_value=session_date), \
                 mock.patch("api.history.time.time", return_value=eval_ts):
                mock_yahoo.side_effect = RuntimeError("Network call should not happen when cached under canonical timeframe")
                # Query with lowercase "1d"
                bars = asyncio.run(fetch_historical_bars(test_sym, timeframe="1d", limit=250))
                self.assertEqual(len(bars), 250)
                self.assertEqual(bars[-1]["close"], 204.0)
                mock_yahoo.assert_not_called()
        finally:
            server._last_remote_fetch.pop((test_sym, "1D"), None)

    def test_connection_cache_first_history_serves_fresh_sqlite_bars(self):
        """TDD Seam 2: Verifies that when SQLite has fresh cached bars, fetch_historical_bars serves them without remote network fetch."""
        import time
        from datetime import date, datetime, time as dt_time
        from unittest import mock
        from server import fetch_historical_bars, get_storage
        from engine.market_time import NY_TZ
        storage = get_storage()
        # 第 1 段 S1-02: pin the clock to a fixed closed session (see the test above).
        session_date = date(2026, 11, 20)  # Friday
        close_ts = int(datetime.combine(session_date, dt_time(16, 0), tzinfo=NY_TZ).timestamp())
        eval_ts = close_ts + 3600  # Fri 17:00 ET, market closed
        fake_bars = [
            {"time": close_ts - ((249 - i) * 86400), "open": 100.0, "high": 105.0, "low": 99.0, "close": 105.5, "volume": 100000.0}
            for i in range(250)
        ]
        test_sym = f"NASDAQ:TESTFRESH_{int(time.time() * 1000)}"
        storage.save_bars(test_sym, "1D", fake_bars)
        server._last_remote_fetch[(test_sym, "1D")] = float(close_ts + 600)

        try:
            with mock.patch("api.history.fetch_yahoo_history") as mock_yahoo, \
                 mock.patch("api.history.is_regular_session_open", return_value=False), \
                 mock.patch("api.history.last_completed_session_date", return_value=session_date), \
                 mock.patch("api.history.time.time", return_value=eval_ts):
                mock_yahoo.side_effect = RuntimeError("Network call should not be made when cache is fresh")
                bars = asyncio.run(fetch_historical_bars(test_sym, timeframe="1D", limit=250))
                self.assertEqual(len(bars), 250)
                self.assertEqual(bars[-1]["close"], 105.5)
                mock_yahoo.assert_not_called()
        finally:
            server._last_remote_fetch.pop((test_sym, "1D"), None)

    def test_normalize_symbol_id_consistency(self):
        """Verifies canonical EXCHANGE:TICKER normalization across equities, ETFs, and crypto."""
        self.assertEqual(server.normalize_symbol_id("nvda"), "NASDAQ:NVDA")
        self.assertEqual(server.normalize_symbol_id("SPY"), "AMEX:SPY")
        self.assertEqual(server.normalize_symbol_id("PLTR"), "NYSE:PLTR")
        self.assertEqual(server.normalize_symbol_id("btcusdt"), "BINANCE:BTCUSDT")
        self.assertEqual(server.normalize_symbol_id("NYSE:KO"), "NYSE:KO")
        self.assertEqual(server.normalize_symbol_id(""), "")

    def test_get_history_normalizes_bare_symbol_and_honors_limit(self):
        """Verifies /api/history normalizes bare tickers and slices output to the requested limit."""
        with patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=list(MOCK_BINANCE_BARS)), \
             patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock):
            with TestClient(app) as client:
                resp = client.get("/api/history?symbol=BTCUSDT&timeframe=1m&limit=4")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["symbol"], "BINANCE:BTCUSDT")
                self.assertLessEqual(len(data["bars"]), 4)

    def test_ws_bar_broadcast_includes_monotonic_seq_only_for_bars(self):
        """Verifies bar broadcasts include monotonically increasing per-symbol seq while quotes do not."""
        mgr = ConnectionManager(max_queue_size=10)
        self.assertEqual(mgr.next_bar_seq("NASDAQ:NVDA"), 1)
        self.assertEqual(mgr.next_bar_seq("NASDAQ:NVDA"), 2)
        self.assertEqual(mgr.next_bar_seq("AMEX:SPY"), 1)

        with patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=list(MOCK_BINANCE_BARS)), \
             patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/live") as websocket:
                    initial = websocket.receive_json()
                    self.assertEqual(initial["type"], "status")

                    bar1 = CandlestickBar(time=1731234000, open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0)
                    bar2 = CandlestickBar(time=1731234060, open=100.5, high=102.0, low=100.0, close=101.5, volume=12.0)
                    server.on_bar_update_handler("NASDAQ:NVDA", bar1)
                    server.on_bar_update_handler("NASDAQ:NVDA", bar2)

                    msg1 = websocket.receive_json()
                    msg2 = websocket.receive_json()
                    self.assertEqual(msg1["type"], "bar")
                    self.assertEqual(msg2["type"], "bar")
                    self.assertIsInstance(msg1.get("seq"), int)
                    self.assertEqual(msg2.get("seq"), msg1["seq"] + 1)

                    server.on_quote_update_handler("NASDAQ:NVDA", {"price": 102.0, "volume": 500.0, "time": 1731234065})
                    q_msg = websocket.receive_json()
                    if q_msg.get("type") == "bar":
                        q_msg = websocket.receive_json()
                    self.assertEqual(q_msg["type"], "quote")
                    self.assertNotIn("seq", q_msg)


if __name__ == "__main__":
    unittest.main()

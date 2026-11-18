"""Full-Stack End-to-End Live Integration Test Suite.

Exercises the entire FastAPI backend, unified asyncio lifespan management,
REST endpoints (/api/symbols, /api/history), real-time WebSocket multiplexed
broadcaster (/ws/live), static frontend asset delivery, in-memory aggregation,
and clean connection disconnection and shutdown.
"""

import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

import server
from server import app, manager
from engine.aggregator import CandlestickBar
from engine.storage import close_storage


MOCK_HISTORICAL_KLINES = [
    {
        "time": 1731234000 + i * 60,
        "open": 68000.0 + i * 15.0,
        "high": 68050.0 + i * 15.0,
        "low": 67980.0 + i * 15.0,
        "close": 68030.0 + i * 15.0,
        "volume": 20.0 + i * 2.5,
    }
    for i in range(20)
]


class TestFullStackE2ELiveIntegration(unittest.TestCase):
    """Full-stack end-to-end integration test harness for the TradingView real-time terminal."""

    def setUp(self):
        """Set up offline deterministic mocks for external network calls and isolated SQLite storage."""
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp_db.close()
        self.orig_env = os.environ.get("TERMINAL_DB_PATH")
        os.environ["TERMINAL_DB_PATH"] = self.tmp_db.name
        close_storage()

        self.fetch_patcher = patch(
            "server.fetch_binance_history",
            new_callable=AsyncMock,
            return_value=list(MOCK_HISTORICAL_KLINES),
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
        """Tear down patchers, isolated storage, and verify no leaked state."""
        self.fetch_patcher.stop()
        self.client_start_patcher.stop()
        self.client_stop_patcher.stop()
        close_storage()
        if self.orig_env is not None:
            os.environ["TERMINAL_DB_PATH"] = self.orig_env
        else:
            os.environ.pop("TERMINAL_DB_PATH", None)
        try:
            os.unlink(self.tmp_db.name)
        except OSError:
            pass

    def test_e2e_static_html_and_assets(self):
        """Verifies GET / returns HTML containing <div id="chart"></div> and required UI containers."""
        with TestClient(app) as client:
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("text/html", resp.headers.get("content-type", ""))
            body = resp.text

            # Critical assertions per dispatch requirements
            self.assertIn('<div id="chart"></div>', body, "Root page must contain <div id=\"chart\"></div>")
            self.assertIn('id="chartContainer"', body, "Root page must contain #chartContainer canvas target")
            self.assertIn('id="tickerTape"', body, "Root page must contain #tickerTape container")
            self.assertIn('id="statusBadge"', body, "Root page must contain #statusBadge indicator")
            self.assertIn('id="chartLegend"', body, "Root page must contain #chartLegend crosshair readout")

            # Verify static asset delivery
            js_resp = client.get("/static/app.js")
            self.assertEqual(js_resp.status_code, 200)
            self.assertIn("javascript", js_resp.headers.get("content-type", ""))

            css_resp = client.get("/static/style.css")
            self.assertEqual(css_resp.status_code, 200)
            self.assertIn("text/css", css_resp.headers.get("content-type", ""))

    def test_e2e_rest_symbols_and_history(self):
        """Verifies GET /api/symbols and GET /api/history deliver expected schemas and invariants."""
        with TestClient(app) as client:
            # 1. Test /api/symbols endpoint
            sym_resp = client.get("/api/symbols")
            self.assertEqual(sym_resp.status_code, 200)
            sym_data = sym_resp.json()
            self.assertIn("symbols", sym_data)
            self.assertIn("status", sym_data)
            self.assertEqual(len(sym_data["symbols"]), 9)

            symbols_list = sym_data["symbols"]
            symbol_ids = [s["id"] for s in symbols_list]
            self.assertIn("BINANCE:BTCUSDT", symbol_ids)
            self.assertIn("BINANCE:ETHUSDT", symbol_ids)
            self.assertIn("BINANCE:SOLUSDT", symbol_ids)
            self.assertIn("NASDAQ:AAPL", symbol_ids)
            self.assertIn("NASDAQ:NVDA", symbol_ids)

            for item in symbols_list:
                self.assertIn("name", item)
                self.assertIn("exchange", item)
                self.assertIn("pair", item)
                self.assertIn("type", item)
                self.assertIn("state", item)
                self.assertIsInstance(item["state"], dict)

            # 2. Test /api/history?symbol=BINANCE:BTCUSDT
            hist_resp = client.get("/api/history?symbol=BINANCE:BTCUSDT")
            self.assertEqual(hist_resp.status_code, 200)
            hist_data = hist_resp.json()
            self.assertEqual(hist_data["symbol"], "BINANCE:BTCUSDT")
            self.assertIn("bars", hist_data)
            bars = hist_data["bars"]
            self.assertGreater(len(bars), 0)

            for bar in bars:
                self.assertIn("time", bar)
                self.assertIn("open", bar)
                self.assertIn("high", bar)
                self.assertIn("low", bar)
                self.assertIn("close", bar)
                self.assertIn("volume", bar)
                # OHLC Invariants
                self.assertGreaterEqual(bar["high"], bar["open"], "High must be >= open")
                self.assertGreaterEqual(bar["high"], bar["close"], "High must be >= close")
                self.assertLessEqual(bar["low"], bar["open"], "Low must be <= open")
                self.assertLessEqual(bar["low"], bar["close"], "Low must be <= close")
                self.assertGreaterEqual(bar["volume"], 0.0, "Volume must be non-negative")

            # 3. Test equity symbol history (empty until live ticks or seeded)
            equity_resp = client.get("/api/history?symbol=NASDAQ:AAPL")
            self.assertEqual(equity_resp.status_code, 200)
            self.assertEqual(equity_resp.json()["symbol"], "NASDAQ:AAPL")
            self.assertIsInstance(equity_resp.json()["bars"], list)

            # 4. Test unknown symbol
            unknown_resp = client.get("/api/history?symbol=FOREX:EURUSD")
            self.assertEqual(unknown_resp.status_code, 200)
            self.assertEqual(unknown_resp.json()["bars"], [])

    def test_e2e_websocket_lifecycle_and_live_messages(self):
        """Verifies WebSocket /ws/live initial handshake, quote & bar streaming, and disconnection."""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/live") as ws:
                # 1. Initial message is status
                initial_msg = ws.receive_json()
                self.assertEqual(initial_msg["type"], "status")
                self.assertIn("status", initial_msg)
                self.assertIn(initial_msg["status"], ["initializing", "connected", "connecting", "reconnecting", "disconnected"])

                # 2. Ingest quote tick through server pipeline for a new bar bucket
                future_time = 1731234000 + 50 * 60  # Far ahead of seeded history
                test_quote = {
                    "price": 68999.50,
                    "volume": 32100.0,
                    "time": future_time,
                    "change": 1.25,
                    "change_pct": 0.018,
                    "high": 69500.0,
                    "low": 67800.0,
                }
                server.on_quote_update_handler("BINANCE:BTCUSDT", test_quote)

                # Expect bar update generated by aggregator
                bar_packet = ws.receive_json()
                self.assertEqual(bar_packet["type"], "bar")
                self.assertEqual(bar_packet["symbol"], "BINANCE:BTCUSDT")
                self.assertIn("bar", bar_packet)
                self.assertEqual(bar_packet["bar"]["close"], 68999.50)

                # Expect quote update dispatched by on_quote_update_handler
                quote_packet = ws.receive_json()
                self.assertEqual(quote_packet["type"], "quote")
                self.assertEqual(quote_packet["symbol"], "BINANCE:BTCUSDT")
                self.assertIn("data", quote_packet)
                self.assertEqual(quote_packet["data"]["price"], 68999.50)
                self.assertEqual(quote_packet["data"]["volume"], 32100.0)

                # 3. Direct bar packet injection via on_bar_update_handler
                test_bar = CandlestickBar(
                    time=future_time,
                    open=68900.0,
                    high=69100.0,
                    low=68850.0,
                    close=68999.50,
                    volume=18.75,
                )
                server.on_bar_update_handler("BINANCE:BTCUSDT", test_bar)

                direct_bar_packet = ws.receive_json()
                self.assertEqual(direct_bar_packet["type"], "bar")
                self.assertEqual(direct_bar_packet["symbol"], "BINANCE:BTCUSDT")
                self.assertIn("bar", direct_bar_packet)
                bar_dict = direct_bar_packet["bar"]
                self.assertEqual(bar_dict["time"], future_time)
                self.assertEqual(bar_dict["open"], 68900.0)
                self.assertEqual(bar_dict["high"], 69100.0)
                self.assertEqual(bar_dict["low"], 68850.0)
                self.assertEqual(bar_dict["close"], 68999.50)
                self.assertEqual(bar_dict["volume"], 18.75)

                # Verify OHLC invariants on received bar
                self.assertGreaterEqual(bar_dict["high"], bar_dict["open"])
                self.assertGreaterEqual(bar_dict["high"], bar_dict["close"])
                self.assertLessEqual(bar_dict["low"], bar_dict["open"])
                self.assertLessEqual(bar_dict["low"], bar_dict["close"])

            # 4. Clean disconnection verification
            # Manager should have 0 active client queues after exiting websocket context
            self.assertEqual(len(manager.client_queues), 0, "Client queue must be deregistered on disconnect")

    def test_e2e_websocket_multiplex_multiple_clients(self):
        """Verifies multiple concurrent WebSocket clients receive identical synchronized broadcast."""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/live") as ws1:
                with client.websocket_connect("/ws/live") as ws2:
                    # Both get status
                    s1 = ws1.receive_json()
                    s2 = ws2.receive_json()
                    self.assertEqual(s1["type"], "status")
                    self.assertEqual(s2["type"], "status")

                    # Ingest quote tick through server pipeline
                    server.on_quote_update_handler("NASDAQ:NVDA", {
                        "price": 145.20,
                        "volume": 500000.0,
                        "time": 1731234120,
                    })

                    # Both clients receive the generated bar update
                    b1 = ws1.receive_json()
                    b2 = ws2.receive_json()
                    self.assertEqual(b1, b2)
                    self.assertEqual(b1["type"], "bar")
                    self.assertEqual(b1["symbol"], "NASDAQ:NVDA")
                    self.assertEqual(b1["bar"]["close"], 145.20)

                    # Both clients receive the quote update
                    q1 = ws1.receive_json()
                    q2 = ws2.receive_json()
                    self.assertEqual(q1, q2)
                    self.assertEqual(q1["type"], "quote")
                    self.assertEqual(q1["symbol"], "NASDAQ:NVDA")
                    self.assertEqual(q1["data"]["price"], 145.20)

                    # Direct bar injection
                    bar = CandlestickBar(
                        time=1731234120,
                        open=144.50,
                        high=145.80,
                        low=144.20,
                        close=145.20,
                        volume=420.0,
                    )
                    server.on_bar_update_handler("NASDAQ:NVDA", bar)

                    direct_b1 = ws1.receive_json()
                    direct_b2 = ws2.receive_json()
                    self.assertEqual(direct_b1, direct_b2)
                    self.assertEqual(direct_b1["bar"]["close"], 145.20)

            # Both disconnected
            self.assertEqual(len(manager.client_queues), 0)

    def test_e2e_tick_to_bar_aggregation_to_history_roundtrip(self):
        """Verifies that ticks ingested through the server pipeline genuinely update history in REST."""
        with TestClient(app) as client:
            test_sym = "NASDAQ:AAPL"
            base_time = 1731240000  # Distinct epoch

            # Verify initial state has 0 bars
            initial_resp = client.get(f"/api/history?symbol={test_sym}")
            self.assertEqual(initial_resp.status_code, 200)
            self.assertEqual(initial_resp.json()["bars"], [])

            # Feed a sequence of ticks simulating a 1-minute candle formation
            ticks = [
                {"price": 220.0, "volume": 1000.0, "time": base_time + 5},
                {"price": 225.0, "volume": 1050.0, "time": base_time + 15},
                {"price": 218.0, "volume": 1120.0, "time": base_time + 35},
                {"price": 224.0, "volume": 1200.0, "time": base_time + 55},
            ]

            for tick in ticks:
                server.on_quote_update_handler(test_sym, tick)

            # Fetch history via REST API
            resp = client.get(f"/api/history?symbol={test_sym}")
            self.assertEqual(resp.status_code, 200)
            bars = resp.json()["bars"]
            self.assertEqual(len(bars), 1, "Aggregated bar must appear in historical bars query")

            latest_bar = bars[0]
            self.assertEqual(latest_bar["time"], base_time)
            self.assertEqual(latest_bar["open"], 220.0)
            self.assertEqual(latest_bar["high"], 225.0)
            self.assertEqual(latest_bar["low"], 218.0)
            self.assertEqual(latest_bar["close"], 224.0)
            self.assertEqual(latest_bar["volume"], 200.0)  # Delta: 1200 - 1000 = 200

            # Advance to minute 2 (triggers bar completion in aggregator)
            next_tick = {"price": 226.5, "volume": 1280.0, "time": base_time + 65}
            server.on_quote_update_handler(test_sym, next_tick)

            resp2 = client.get(f"/api/history?symbol={test_sym}")
            self.assertEqual(resp2.status_code, 200)
            bars2 = resp2.json()["bars"]
            self.assertEqual(len(bars2), 2, "Second minute candle must appear in historical bars")
            self.assertEqual(bars2[0]["time"], base_time)
            self.assertEqual(bars2[0]["close"], 224.0)
            self.assertEqual(bars2[1]["time"], base_time + 60)
            self.assertEqual(bars2[1]["open"], 226.5)
            self.assertEqual(bars2[1]["close"], 226.5)
            self.assertEqual(bars2[1]["volume"], 80.0)  # Delta: 1280 - 1200 = 80

    def test_e2e_lifespan_startup_and_shutdown(self):
        """Verifies full lifespan context manager startup, background tasks, and clean teardown."""
        # Entering TestClient runs lifespan startup
        with TestClient(app) as client:
            self.assertTrue(self.mock_start.called, "TradingViewAsyncClient.start must be awaited on startup")
            self.assertIsNotNone(server.aggregator, "TickToBarAggregator must be initialized on startup")
            self.assertIsNotNone(server.tv_client, "TradingViewAsyncClient must be created on startup")

        # Exiting TestClient runs lifespan shutdown
        self.assertTrue(self.mock_stop.called, "TradingViewAsyncClient.stop must be awaited on shutdown")
        self.assertEqual(len(manager.client_queues), 0, "All client queues must be closed and cleared")


if __name__ == "__main__":
    unittest.main()

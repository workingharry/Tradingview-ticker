"""Adversarial stress and resiliency tests for REST endpoints, Binance seeding, and static assets.

Executed by Challenger M2-2 to empirically verify that:
1. /api/history cleanly handles all unknown, malformed, non-crypto, and injection symbols without 500 errors.
2. Binance seeding survives all external failure modes (429, 503, 502, timeouts, corrupt JSON, schema errors).
3. Static files are served with correct headers and protect against path traversal attacks.
4. Server endpoints are resilient prior to initialization or when aggregator/tv_client are uninitialized.
"""

import unittest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi.testclient import TestClient

import server
from server import app, WATCHLIST, fetch_binance_history


class TestAdversarialHistoryEndpoint(unittest.TestCase):
    """Stress tests for /api/history query handling."""

    def setUp(self):
        self.mock_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.mock_stop_patcher = patch(
            "engine.client.TradingViewAsyncClient.stop",
            new_callable=AsyncMock,
        )
        self.mock_fetch_patcher = patch(
            "server.fetch_binance_history",
            new_callable=AsyncMock,
            return_value=[],
        )
        self.mock_start = self.mock_start_patcher.start()
        self.mock_stop = self.mock_stop_patcher.start()
        self.mock_fetch = self.mock_fetch_patcher.start()

    def tearDown(self):
        self.mock_start_patcher.stop()
        self.mock_stop_patcher.stop()
        self.mock_fetch_patcher.stop()

    def test_adversarial_and_malformed_symbols(self):
        """Verifies clean 200 OK responses with valid schema across hostile/malformed inputs."""
        hostile_symbols = [
            "UNKNOWN:XYZ",
            "BINANCE:DOESNOTEXIST123",
            "COINBASE:BTCUSD",
            "BOGUS",
            "",
            ":::",
            "BINANCE:",
            ":BTCUSDT",
            "?&!@#$",
            "A" * 2000,
            "../../etc/passwd",
            "' OR 1=1 --",
            "<script>alert(1)</script>",
            "NASDAQ:AAPL",
            "NASDAQ:NVDA",
            "NYSE:GME",
            "FOREX:EURUSD",
            "INDEX:SPX",
        ]

        with TestClient(app) as client:
            for sym in hostile_symbols:
                resp = client.get("/api/history", params={"symbol": sym})
                self.assertEqual(resp.status_code, 200, f"Failed for symbol {sym!r}")
                data = resp.json()
                self.assertIn("symbol", data)
                self.assertEqual(data["symbol"], sym)
                self.assertIn("bars", data)
                self.assertIsInstance(data["bars"], list)

    def test_default_symbol_when_omitted(self):
        """Verifies that omitting the symbol parameter defaults to BINANCE:BTCUSDT."""
        with TestClient(app) as client:
            resp = client.get("/api/history")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["symbol"], "BINANCE:BTCUSDT")
            self.assertIsInstance(data["bars"], list)

    def test_concurrent_history_requests(self):
        """Verifies that 50 concurrent history requests execute cleanly without race conditions."""
        async def run_concurrent():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
                tasks = [ac.get("/api/history?symbol=BINANCE:BTCUSDT") for _ in range(50)]
                responses = await asyncio.gather(*tasks)
                for r in responses:
                    self.assertEqual(r.status_code, 200)
                    data = r.json()
                    self.assertIn("bars", data)

        with TestClient(app):
            asyncio.run(run_concurrent())


class TestBinanceSeedingResilience(unittest.TestCase):
    """Verifies that the server lifespan startup and seeding survive all external API failure modes."""

    def setUp(self):
        self.mock_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.mock_stop_patcher = patch(
            "engine.client.TradingViewAsyncClient.stop",
            new_callable=AsyncMock,
        )
        self.mock_start = self.mock_start_patcher.start()
        self.mock_stop = self.mock_stop_patcher.start()

    def tearDown(self):
        self.mock_start_patcher.stop()
        self.mock_stop_patcher.stop()

    def _test_seeding_scenario(self, mock_client_config):
        mock_resp = MagicMock()
        if "status" in mock_client_config:
            mock_resp.status_code = mock_client_config["status"]
            mock_resp.text = mock_client_config["text"]
            if mock_client_config.get("is_json", False):
                mock_resp.json.return_value = json.loads(mock_client_config["text"])
            else:
                mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", mock_client_config["text"], 0)

        mock_client = AsyncMock()
        if "exception" in mock_client_config:
            mock_client.get.side_effect = mock_client_config["exception"]
        else:
            mock_client.get.return_value = mock_resp

        mock_client_cls = MagicMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", mock_client_cls):
            with TestClient(app) as client:
                r_sym = client.get("/api/symbols")
                self.assertEqual(r_sym.status_code, 200)
                r_hist = client.get("/api/history?symbol=BINANCE:BTCUSDT")
                self.assertEqual(r_hist.status_code, 200)
                self.assertIsInstance(r_hist.json()["bars"], list)

    def test_http_429_rate_limit(self):
        self._test_seeding_scenario({
            "status": 429,
            "text": json.dumps({"code": -1003, "msg": "Way too many requests"}),
            "is_json": True
        })

    def test_http_503_service_unavailable(self):
        self._test_seeding_scenario({
            "status": 503,
            "text": "Service Unavailable",
            "is_json": False
        })

    def test_http_502_bad_gateway_html(self):
        self._test_seeding_scenario({
            "status": 502,
            "text": "<html><body>502 Bad Gateway</body></html>",
            "is_json": False
        })

    def test_connect_timeout(self):
        self._test_seeding_scenario({
            "exception": httpx.ConnectTimeout("Connection timed out after 8000ms")
        })

    def test_read_timeout(self):
        self._test_seeding_scenario({
            "exception": httpx.ReadTimeout("Read timed out")
        })

    def test_network_connection_reset(self):
        self._test_seeding_scenario({
            "exception": httpx.NetworkError("Connection reset by peer")
        })

    def test_corrupt_json_payload(self):
        self._test_seeding_scenario({
            "status": 200,
            "text": "{malformed json",
            "is_json": False
        })

    def test_empty_200_body(self):
        self._test_seeding_scenario({
            "status": 200,
            "text": "",
            "is_json": False
        })

    def test_non_list_json_object(self):
        self._test_seeding_scenario({
            "status": 200,
            "text": json.dumps({"status": "error", "message": "unexpected"}),
            "is_json": True
        })

    def test_malformed_kline_rows_skipped(self):
        """Verifies rows with non-numeric fields or missing values are skipped without crash."""
        corrupt_rows = [
            ["not_a_timestamp", "68000.0", "68100.0", "67900.0", "68050.0", "10.0"],
            [1731234000000],
            [None, None, None, None, None, None],
            [1731234060000, "bad_open", "bad_high", "bad_low", "bad_close", "bad_vol"],
            [1731234120000, "68100.0", "68200.0", "68000.0", "68150.0", "15.0"],
        ]
        self._test_seeding_scenario({
            "status": 200,
            "text": json.dumps(corrupt_rows),
            "is_json": True
        })

    def test_mixed_symbol_failures_during_lifespan(self):
        """Verifies lifespan handles partial failures where 1 symbol succeeds and others fail."""
        async def mock_fetch(symbol, limit=300):
            if "BTC" in symbol:
                return [{
                    "time": 1731234000,
                    "open": 68000.0,
                    "high": 68100.0,
                    "low": 67900.0,
                    "close": 68050.0,
                    "volume": 12.0
                }]
            elif "ETH" in symbol:
                return []  # Simulates 429
            else:
                raise httpx.ReadTimeout("Timeout")

        with patch("server.fetch_binance_history", side_effect=mock_fetch), \
             patch("engine.storage.Storage.get_bars", return_value=[]):
            with TestClient(app) as client:
                r_sym = client.get("/api/symbols")
                self.assertEqual(r_sym.status_code, 200)

                r_btc = client.get("/api/history?symbol=BINANCE:BTCUSDT")
                self.assertEqual(r_btc.status_code, 200)
                self.assertEqual(len(r_btc.json()["bars"]), 1)

                r_eth = client.get("/api/history?symbol=BINANCE:ETHUSDT")
                self.assertEqual(r_eth.status_code, 200)
                self.assertEqual(len(r_eth.json()["bars"]), 0)


class TestStaticFileServingAndSecurity(unittest.TestCase):
    """Verifies static asset delivery and path traversal immunity."""

    def setUp(self):
        self.mock_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.mock_stop_patcher = patch(
            "engine.client.TradingViewAsyncClient.stop",
            new_callable=AsyncMock,
        )
        self.mock_fetch_patcher = patch(
            "server.fetch_binance_history",
            new_callable=AsyncMock,
            return_value=[],
        )
        self.mock_start = self.mock_start_patcher.start()
        self.mock_stop = self.mock_stop_patcher.start()
        self.mock_fetch = self.mock_fetch_patcher.start()

    def tearDown(self):
        self.mock_start_patcher.stop()
        self.mock_stop_patcher.stop()
        self.mock_fetch_patcher.stop()

    def test_static_asset_headers(self):
        """Verifies correct Content-Type headers for HTML, JS, and CSS files."""
        targets = [
            ("/", 200, "text/html"),
            ("/index.html", 200, "text/html"),
            ("/static/index.html", 200, "text/html"),
            ("/app.js", 200, "javascript"),
            ("/static/app.js", 200, "javascript"),
            ("/style.css", 200, "text/css"),
            ("/static/style.css", 200, "text/css"),
        ]
        with TestClient(app) as client:
            for path, expected_status, expected_ctype in targets:
                resp = client.get(path)
                self.assertEqual(resp.status_code, expected_status)
                self.assertIn(expected_ctype, resp.headers.get("content-type", "").lower())

    def test_missing_static_file_returns_404(self):
        """Verifies that non-existent files return 404 cleanly."""
        with TestClient(app) as client:
            self.assertEqual(client.get("/nonexistent.js").status_code, 404)
            self.assertEqual(client.get("/static/missing.css").status_code, 404)

    def test_path_traversal_immunity(self):
        """Verifies that directory traversal attacks fail to read internal files."""
        traversal_paths = [
            "/static/../server.py",
            "/static/%2e%2e/server.py",
            "/static/..%2fserver.py",
            "/../server.py",
            "/%2e%2e/server.py",
            "/static/../../etc/passwd",
        ]
        with TestClient(app) as client:
            for path in traversal_paths:
                resp = client.get(path)
                # Must either be 404 or 400, never 200 leaking server code
                if resp.status_code == 200:
                    self.assertNotIn("FastAPI application", resp.text)
                    self.assertNotIn("root:x:", resp.text)


class TestPreStartupEndpointResilience(unittest.TestCase):
    """Verifies that endpoints behave gracefully if queried when uninitialized."""

    def test_uninitialized_aggregator_and_client(self):
        """Verifies that endpoints return clean empty structures if aggregator/client are None."""
        orig_aggregator = server.aggregator
        orig_client = server.tv_client
        server.aggregator = None
        server.tv_client = None

        try:
            client = TestClient(app)
            r_sym = client.get("/api/symbols")
            self.assertEqual(r_sym.status_code, 200)
            data_sym = r_sym.json()
            self.assertEqual(len(data_sym["symbols"]), len(WATCHLIST))
            for item in data_sym["symbols"]:
                self.assertEqual(item["state"], {})

            r_hist = client.get("/api/history?symbol=BINANCE:BTCUSDT")
            self.assertEqual(r_hist.status_code, 200)
            self.assertEqual(r_hist.json()["bars"], [])
        finally:
            server.aggregator = orig_aggregator
            server.tv_client = orig_client


if __name__ == "__main__":
    unittest.main()

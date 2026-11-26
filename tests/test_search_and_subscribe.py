"""Tests for dynamic symbol search, subscription, and universal history seeding."""

import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from server import app, POPULAR_SEARCH_INDEX, WATCHLIST, SYMBOLS


class TestSearchAndSubscribe(unittest.TestCase):
    """Verifies symbol search, dynamic subscription, and multi-asset routing."""

    def setUp(self):
        self._orig_watchlist = [dict(w) for w in WATCHLIST]
        self._orig_symbols = list(SYMBOLS)
        for p in (
            patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock),
            patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock),
            patch("engine.client.TradingViewAsyncClient.subscribe", new_callable=AsyncMock),
            patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=[]),
            patch("server.fetch_universal_history", new_callable=AsyncMock, return_value=[]),
        ):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        WATCHLIST.clear()
        WATCHLIST.extend(self._orig_watchlist)
        SYMBOLS.clear()
        SYMBOLS.extend(self._orig_symbols)

    def test_search_symbols_empty_returns_popular(self):
        with TestClient(app) as client:
            resp = client.get("/api/symbols/search")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("results", data)
            self.assertTrue(len(data["results"]) > 0)

    def test_search_symbols_fuzzy_matching(self):
        with TestClient(app) as client:
            resp = client.get("/api/symbols/search?q=spy")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            results = data["results"]
            pairs = [r["pair"] for r in results]
            self.assertIn("SPY", pairs)

    def test_search_symbols_dynamic_synthesis_for_custom_ticker(self):
        with TestClient(app) as client:
            resp = client.get("/api/symbols/search?q=MYCUSTOMSTOCK")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            results = data["results"]
            self.assertTrue(any(r["pair"] == "MYCUSTOMSTOCK" for r in results))

    def test_search_symbols_does_not_synthesize_hostile_input(self):
        with TestClient(app) as client:
            for bad_q in ["<script>alert(1)</script>", "../../etc/passwd", "?&!@#$", "A" * 100]:
                resp = client.get("/api/symbols/search", params={"q": bad_q})
                self.assertEqual(resp.status_code, 200)
                results = resp.json()["results"]
                self.assertEqual(results, [])

    @patch("server.tv_client")
    def test_subscribe_symbol_endpoint(self, mock_tv):
        mock_tv.subscribe = AsyncMock()
        with TestClient(app) as client:
            resp = client.post("/api/symbols/subscribe", json={
                "symbol": "NASDAQ:TSLA",
                "name": "Tesla, Inc.",
                "exchange": "NASDAQ",
                "type": "stock"
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "subscribed")
            self.assertEqual(data["symbol"]["id"], "NASDAQ:TSLA")

    def test_subscribe_symbol_rejects_malformed_or_hostile_symbols(self):
        with TestClient(app) as client:
            for bad_sym in ["", "   ", "BINANCE:", ":BTCUSDT", "../../etc/passwd", "NASDAQ:<script>", "A" * 100]:
                resp = client.post("/api/symbols/subscribe", json={"symbol": bad_sym})
                self.assertEqual(resp.status_code, 400, f"Expected 400 for {bad_sym!r}, got {resp.status_code}")
                self.assertIn("error", resp.json())


if __name__ == "__main__":
    unittest.main()

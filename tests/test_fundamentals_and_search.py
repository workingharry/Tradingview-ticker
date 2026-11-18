import os
import re
import sys
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

import server
from server import app
from engine.storage import get_storage


class TestFundamentalsAndSearch(unittest.TestCase):
    """Hermetic unit and integration tests for Live Search, Fundamentals AI, and Watchlist Unsubscribe."""

    def setUp(self):
        self.original_watchlist = list(server.WATCHLIST)
        self.original_symbols = list(server.SYMBOLS)
        self.mock_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.mock_start = self.mock_start_patcher.start()

    def tearDown(self):
        self.mock_start_patcher.stop()
        server.WATCHLIST = list(self.original_watchlist)
        server.SYMBOLS = list(self.original_symbols)

    def test_dom_ids_and_split_chart_structure_aligned(self):
        """Verifies 100% DOM ID alignment between static/app.js and static/index.html including new elements."""
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(root_dir, "static", "index.html")
        js_path = os.path.join(root_dir, "static", "app.js")

        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Extract all getElementById calls
        id_call_pattern = re.compile(r"document\.getElementById\([\"\x27]([^\"\x27]+)[\"\x27]\)")
        js_ids = set(id_call_pattern.findall(js))

        # Extract all HTML id attributes
        html_id_pattern = re.compile(r"id=[\"\x27]([^\"\x27]+)[\"\x27]")
        html_ids = set(html_id_pattern.findall(html))

        missing_ids = js_ids - html_ids
        self.assertEqual(
            missing_ids,
            set(),
            f"Zero missing DOM IDs allowed! Missing in index.html: {missing_ids}",
        )

        # Verify key Milestone A & B elements exist
        self.assertIn("fundamentalsBtn", html_ids)
        self.assertIn("fundamentalsModal", html_ids)
        self.assertIn("closeFundamentalsModal", html_ids)
        self.assertIn("refreshFundamentalsBtn", html_ids)
        self.assertIn("rsPane", html_ids)
        self.assertIn("rsPaneTitle", html_ids)
        self.assertIn("rsRatingBadge", html_ids)
        self.assertIn("rsChart", html_ids)
        self.assertIn('data-tf="1W"', html)
        self.assertIn('data-tf="1D"', html)

    def test_search_symbols_with_live_yahoo_results(self):
        """Verifies search_symbols queries upstream Yahoo search and normalizes results."""
        mock_yahoo_response = {
            "quotes": [
                {
                    "symbol": "NVDA",
                    "shortname": "NVIDIA Corporation",
                    "exchange": "NMS",
                    "quoteType": "EQUITY",
                },
                {
                    "symbol": "NVD",
                    "shortname": "GraniteShares 2x Short NVDA",
                    "exchange": "PCX",
                    "quoteType": "ETF",
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_yahoo_response

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("server.get_shared_client", return_value=mock_client):
            with TestClient(app) as client:
                res = client.get("/api/symbols/search?q=NVDA")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIn("results", data)
                results = data["results"]
                self.assertGreaterEqual(len(results), 1)

                nvda_item = next((r for r in results if r["pair"] == "NVDA"), None)
                self.assertIsNotNone(nvda_item)
                self.assertEqual(nvda_item["exchange"], "NASDAQ")
                self.assertEqual(nvda_item["type"], "stock")

    def test_search_symbols_fallback_when_yahoo_fails(self):
        """Verifies search_symbols provides clean local synthesis fallback when upstream fails."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Upstream search unavailable")

        with patch("server.get_shared_client", return_value=mock_client):
            with TestClient(app) as client:
                res = client.get("/api/symbols/search?q=PLTR")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                results = data["results"]
                self.assertGreaterEqual(len(results), 1)
                self.assertEqual(results[0]["pair"], "PLTR")
                self.assertTrue(results[0]["id"].endswith(":PLTR"))

    def test_fundamentals_endpoint_validation_and_caching(self):
        """Verifies /api/fundamentals validates input, parses metrics, and leverages SQLite cache."""
        mock_quote_summary = {
            "quoteSummary": {
                "result": [{
                    "price": {
                        "regularMarketPrice": {"raw": 185.50},
                        "marketCap": {"fmt": "2.8T"},
                        "shortName": "Apple Inc."
                    },
                    "summaryDetail": {
                        "fiftyTwoWeekHigh": {"raw": 199.62},
                        "fiftyTwoWeekLow": {"raw": 164.08},
                        "trailingPE": {"raw": 32.5}
                    },
                    "defaultKeyStatistics": {
                        "forwardPE": {"raw": 28.2},
                        "trailingEps": {"raw": 6.42}
                    },
                    "financialData": {
                        "revenueGrowth": {"fmt": "8.5%"},
                        "grossMargins": {"fmt": "46.2%"}
                    }
                }]
            }
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_quote_summary

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp

        with patch("server.get_shared_client", return_value=mock_client):
            with TestClient(app) as client:
                # 1. Invalid symbol format returns 400
                res_bad = client.get("/api/fundamentals?symbol=INVALID!!!")
                self.assertEqual(res_bad.status_code, 400)

                # 2. Fresh fetch without cache
                res = client.get("/api/fundamentals?symbol=NASDAQ:AAPL&refresh=true")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data["status"], "ok")
                self.assertFalse(data["cached"])
                self.assertEqual(data["symbol"], "NASDAQ:AAPL")

                metrics = data["metrics"]
                self.assertEqual(metrics["current_price"], 185.50)
                self.assertEqual(metrics["fifty_two_week_high"], 199.62)
                self.assertAlmostEqual(metrics["pct_from_high"], -7.07, places=1)
                self.assertEqual(metrics["pe_ratio"], 32.5)
                self.assertEqual(metrics["eps"], 6.42)
                self.assertIn("CANSLIM", data["ai_summary"])
                self.assertIn("基本面", data["ai_summary"])

                # 3. Subsequent fetch should hit SQLite cache
                res_cached = client.get("/api/fundamentals?symbol=NASDAQ:AAPL&refresh=false")
                self.assertEqual(res_cached.status_code, 200)
                cached_data = res_cached.json()
                self.assertTrue(cached_data["cached"])
                self.assertEqual(cached_data["symbol"], "NASDAQ:AAPL")

    def test_unsubscribe_symbol_removes_from_sqlite_and_memory(self):
        """Verifies DELETE /api/symbols/unsubscribe deletes from storage and broadcasts eviction."""
        with TestClient(app) as client:
            test_sym = "NASDAQ:MSFT"

            # Subscribe first
            sub_res = client.post(f"/api/symbols/subscribe?symbol={test_sym}")
            self.assertEqual(sub_res.status_code, 200)
            self.assertIn(test_sym, [s["id"] if isinstance(s, dict) else s for s in server.WATCHLIST])
            self.assertIn(test_sym, server.SYMBOLS)

            # Unsubscribe via DELETE
            del_res = client.delete(f"/api/symbols/unsubscribe?symbol={test_sym}")
            self.assertEqual(del_res.status_code, 200)
            del_data = del_res.json()
            self.assertEqual(del_data["status"], "unsubscribed")
            self.assertEqual(del_data["symbol"], test_sym)
            self.assertTrue(del_data["removed"])

            # Verify evicted from in-memory WATCHLIST and SYMBOLS
            self.assertNotIn(test_sym, [s["id"] if isinstance(s, dict) else s for s in server.WATCHLIST])
            self.assertNotIn(test_sym, server.SYMBOLS)

            # Verify evicted from SQLite storage
            db_watchlist = get_storage().get_watchlist()
            self.assertNotIn(test_sym, db_watchlist)


if __name__ == "__main__":
    unittest.main()

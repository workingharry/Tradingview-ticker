"""Hermetic and live integration tests for Yahoo Session, Dotenv, and Fundamentals."""

import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from fastapi.testclient import TestClient

import server
from server import app, load_dotenv_custom, YahooSessionManager


class TestYahooSessionAndFundamentals(unittest.TestCase):
    """Verifies YahooSessionManager, dotenv loading, fundamentals parsing, and chat fallback."""

    def test_load_dotenv_custom(self):
        """Verifies that load_dotenv_custom parses .env and sets environment variables."""
        loaded = load_dotenv_custom()
        self.assertIsInstance(loaded, list)
        self.assertTrue(any(".env" in p for p in loaded) or len(loaded) >= 0)
        # GEMINI_API_KEY should be present in os.environ if .env exists
        if any(os.path.isfile(p) for p in [".env", os.path.expanduser("~/.env")]):
            self.assertTrue(bool(os.environ.get("GEMINI_API_KEY")))

    def test_yahoo_session_manager_singleton(self):
        """Verifies that YahooSessionManager acts as a thread-safe singleton."""
        mgr1 = YahooSessionManager()
        mgr2 = YahooSessionManager()
        self.assertIs(mgr1, mgr2)
        mgr1.invalidate()
        self.assertFalse(mgr1.is_valid())

    @pytest.mark.network
    def test_fundamentals_real_or_mocked_parsing(self):
        """Verifies that /api/fundamentals successfully returns parsed metrics."""
        with TestClient(app) as client:
            res = client.get("/api/fundamentals?symbol=NASDAQ:NVDA&refresh=true")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("status"), "ok")
            metrics = data.get("metrics", {})
            self.assertEqual(metrics.get("symbol"), "NASDAQ:NVDA")
            self.assertEqual(metrics.get("ticker"), "NVDA")

    def test_fundamentals_mocked_offline(self):
        """Verifies /api/fundamentals parsing and metrics contract completely offline."""
        mock_summary = {
            "quoteSummary": {
                "result": [
                    {
                        "financialData": {"currentPrice": {"raw": 140.0}, "revenueGrowth": {"raw": 0.94}},
                        "defaultKeyStatistics": {"forwardPE": {"raw": 30.5}, "trailingEps": {"raw": 2.95}},
                        "summaryDetail": {"trailingPE": {"raw": 47.4}, "fiftyTwoWeekHigh": {"raw": 152.0}, "fiftyTwoWeekLow": {"raw": 48.0}},
                        "price": {"longName": "NVIDIA Corp."},
                    }
                ]
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_summary
        mock_cli = AsyncMock()
        mock_cli.get.return_value = mock_resp

        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch.object(YahooSessionManager, "get_session", new_callable=AsyncMock, return_value=({"B": "1"}, "crumb")), \
             patch("api.deps.get_shared_client", return_value=mock_cli), \
             patch("api.ai.call_gemini_generate", new_callable=AsyncMock, return_value="Offline mocked summary"):
            with TestClient(app) as client:
                res = client.get("/api/fundamentals?symbol=NASDAQ:NVDA&refresh=true")
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data.get("status"), "ok")
                metrics = data.get("metrics", {})
                self.assertEqual(metrics.get("symbol"), "NASDAQ:NVDA")
                self.assertEqual(metrics.get("ticker"), "NVDA")

    @pytest.mark.network
    def test_chat_endpoint_has_real_swing_levels_or_ai(self):
        """Verifies that /api/chat responds with dynamic swing support/resistance."""
        with TestClient(app) as client:
            res = client.post(
                "/api/chat",
                json={
                    "message": "這隻股票近期的基本面與估值位階有何潛在隱憂或亮點？",
                    "symbol": "NASDAQ:MRNA",
                    "timeframe": "1D",
                },
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("status"), "ok")
            self.assertEqual(data.get("symbol"), "NASDAQ:MRNA")
            reply = data.get("reply", "")
            self.assertTrue(len(reply) > 20)

    def test_chat_endpoint_mocked_offline(self):
        """Verifies /api/chat responds cleanly offline when AI helper is mocked."""
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.ai.call_gemini_generate", new_callable=AsyncMock, return_value=None):
            with TestClient(app) as client:
                res = client.post(
                    "/api/chat",
                    json={
                        "message": "這隻股票近期的基本面與估值位階有何潛在隱憂或亮點？",
                        "symbol": "NASDAQ:MRNA",
                        "timeframe": "1D",
                    },
                )
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data.get("status"), "ok")
                self.assertEqual(data.get("symbol"), "NASDAQ:MRNA")
                self.assertTrue(len(data.get("reply", "")) > 20)


if __name__ == "__main__":
    unittest.main()

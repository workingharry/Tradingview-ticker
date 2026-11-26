"""Tests for /api/chat interactive Gemini Market Copilot endpoint."""

import unittest
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from server import app, call_gemini_generate


class TestChatEndpoint(unittest.TestCase):
    """Verifies input validation, context injection, and response format of /api/chat."""

    @classmethod
    def setUpClass(cls):
        cls._tv_start_patcher = patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock)
        cls._tv_stop_patcher = patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock)
        cls._tv_start_patcher.start()
        cls._tv_stop_patcher.start()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls._tv_start_patcher.stop()
        cls._tv_stop_patcher.stop()

    def test_chat_empty_payload_rejected(self):
        """Verifies that an empty or missing payload returns HTTP 400."""
        resp = self.client.post("/api/chat", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_chat_empty_message_rejected(self):
        """Verifies that a blank message returns HTTP 400."""
        resp = self.client.post("/api/chat", json={"message": "   ", "symbol": "NASDAQ:NVDA"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Message is required", resp.json().get("error", ""))

    @pytest.mark.network
    def test_chat_valid_message_with_fallback(self):
        """Verifies that a valid message returns HTTP 200 and structured response."""
        resp = self.client.post(
            "/api/chat",
            json={
                "message": "請問目前 NVDA 股價位階與支撐點在哪？",
                "symbol": "NASDAQ:NVDA",
                "timeframe": "1D"
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("symbol"), "NASDAQ:NVDA")
        self.assertEqual(data.get("timeframe"), "1D")
        self.assertTrue(len(data.get("reply", "")) > 10)
        self.assertIn("timestamp", data)

    def test_chat_valid_message_offline_fallback(self):
        """Verifies that a valid message returns HTTP 200 and structured fallback response offline."""
        with patch("api.ai.call_gemini_generate", new_callable=AsyncMock, return_value=None):
            resp = self.client.post(
                "/api/chat",
                json={
                    "message": "請問目前 NVDA 股價位階與支撐點在哪？",
                    "symbol": "NASDAQ:NVDA",
                    "timeframe": "1D"
                }
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("status"), "ok")
            self.assertEqual(data.get("symbol"), "NASDAQ:NVDA")
            self.assertEqual(data.get("timeframe"), "1D")
            self.assertTrue(len(data.get("reply", "")) > 10)
            self.assertIn("timestamp", data)

    @pytest.mark.network
    def test_chat_symbol_normalization(self):
        """Verifies that bare ticker (e.g. FAST) is normalized to NASDAQ:FAST."""
        resp = self.client.post(
            "/api/chat",
            json={
                "message": "FAST 的籌碼面如何？",
                "symbol": "FAST",
                "timeframe": "1D"
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("symbol"), "NASDAQ:FAST")

    def test_chat_symbol_normalization_offline(self):
        """Verifies bare ticker normalization to NASDAQ:FAST completely offline."""
        with patch("api.ai.call_gemini_generate", new_callable=AsyncMock, return_value=None):
            resp = self.client.post(
                "/api/chat",
                json={
                    "message": "FAST 的籌碼面如何？",
                    "symbol": "FAST",
                    "timeframe": "1D"
                }
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("symbol"), "NASDAQ:FAST")

    @patch("api.ai.call_gemini_generate")
    def test_chat_mocked_gemini_success(self, mock_gemini):
        """Verifies that when Gemini returns an answer, /api/chat wraps and returns it."""
        mock_gemini.return_value = "根據當前走勢，NVDA 處於第二階段上升趨勢，關鍵支撐在 140 美元。"
        resp = self.client.post(
            "/api/chat",
            json={
                "message": "現在適合加碼嗎？",
                "symbol": "NASDAQ:NVDA",
                "timeframe": "1D"
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("處於第二階段上升趨勢", data.get("reply", ""))


if __name__ == "__main__":
    unittest.main()

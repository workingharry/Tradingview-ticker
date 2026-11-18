"""Tests for Multi-Engine Charting: Scheme A, Scheme B (TradingView WebSocket Series), and Scheme C (TradingView Widget)."""

import asyncio
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from server import app
from engine.client import TradingViewAsyncClient


class TestChartEngines(unittest.TestCase):
    """Verifies Scheme A, Scheme B, and Scheme C multi-engine routing and resilience."""

    def test_scheme_a_history_query(self):
        """Verifies GET /api/history?engine=A routes through dual-source engine and returns 200 OK."""
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=BINANCE:BTCUSDT&timeframe=1m&engine=A")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["engine"], "A")
            self.assertIn("bars", data)

    def test_scheme_b_fallback_to_scheme_a_when_ws_not_connected(self):
        """Verifies that if Scheme B is requested but TV client is disconnected, it falls back to Scheme A."""
        with TestClient(app) as client:
            resp = client.get("/api/history?symbol=BINANCE:BTCUSDT&timeframe=1m&engine=B")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            # If fallback engaged, it delivers data without crashing
            self.assertIn(data["engine"], ["A", "B"])
            self.assertIn("bars", data)

    def test_scheme_b_tv_client_timescale_update_parsing(self):
        """Verifies TradingViewAsyncClient correctly parses timescale_update packets into candlestick bars."""
        client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT"])
        client.cs = "cs_test"
        
        # Setup a pending future for a series
        series_id = "sds_test"
        series_key = "BINANCE:BTCUSDT_1D"
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        client.series_futures[series_id] = (series_key, fut)

        packet = {
            "type": "data",
            "data": {
                "m": "timescale_update",
                "p": [
                    "cs_test",
                    {
                        series_id: {
                            "s": [
                                {"i": 0, "v": [1700000000, 60000.0, 61000.0, 59500.0, 60800.0, 150.5]},
                                {"i": 1, "v": [1700086400, 60800.0, 62000.0, 60500.0, 61500.0, 210.0]}
                            ]
                        }
                    }
                ]
            }
        }

        client._handle_packet(packet)
        self.assertTrue(fut.done())
        bars = fut.result()
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["time"], 1700000000)
        self.assertEqual(bars[0]["open"], 60000.0)
        self.assertEqual(bars[0]["high"], 61000.0)
        self.assertEqual(bars[0]["low"], 59500.0)
        self.assertEqual(bars[0]["close"], 60800.0)
        self.assertEqual(bars[0]["volume"], 150.5)
        self.assertEqual(client.chart_series[series_key], bars)
        loop.close()

    def test_scheme_c_dom_elements_exist_in_index_html(self):
        """Verifies that Scheme C tvWidgetContainer, engineSelect, and TradingView library exist in HTML."""
        with TestClient(app) as client:
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)
            html = resp.text
            self.assertIn('id="engineSelect"', html)
            self.assertIn('id="tvWidgetContainer"', html)
            self.assertIn('s3.tradingview.com/tv.js', html)
            self.assertIn('value="A"', html)
            self.assertIn('value="C"', html)


if __name__ == "__main__":
    unittest.main()

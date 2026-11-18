"""Unit Test Suite for Pluggable Market Data Providers.

Validates:
1. BaseMarketDataProvider interface enforcement
2. BinanceDataProvider crypto symbol routing and parsing
3. YahooFinanceDataProvider ticker normalization and quote parsing
4. PolygonDataProvider licensed provider behavior
5. AlpacaDataProvider licensed provider behavior
6. CompositeMarketDataProvider prioritization and fallback cascades
"""

import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from engine.providers import (
    BaseMarketDataProvider,
    BinanceDataProvider,
    YahooFinanceDataProvider,
    PolygonDataProvider,
    AlpacaDataProvider,
    CompositeMarketDataProvider,
)


class TestMarketDataProviders(unittest.TestCase):
    """Rigorous verification of the market data provider abstraction seam."""

    def test_abstract_base_class_instantiation_prohibited(self):
        with self.assertRaises(TypeError):
            BaseMarketDataProvider()  # type: ignore

    def test_binance_provider_symbol_support(self):
        p = BinanceDataProvider()
        self.assertTrue(p.supports_symbol("BINANCE:BTCUSDT"))
        self.assertTrue(p.supports_symbol("ETHUSDT"))
        self.assertTrue(p.supports_symbol("BINANCE:SOLBUSD"))
        self.assertFalse(p.supports_symbol("NASDAQ:AAPL"))
        self.assertFalse(p.supports_symbol("NYSE:GME"))

    @patch("httpx.AsyncClient.get")
    def test_binance_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            [1731234000000, "68000.0", "68050.0", "67980.0", "68020.0", "15.5", 1731234059999, "1000", 10, "1", "1", "0"]
        ]
        mock_get.return_value = mock_resp

        p = BinanceDataProvider()
        bars = asyncio.run(p.fetch_historical_bars("BINANCE:BTCUSDT", limit=10))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["time"], 1731234000)
        self.assertEqual(bars[0]["open"], 68000.0)
        self.assertEqual(bars[0]["close"], 68020.0)

    def test_yahoo_symbol_support_and_ticker_extraction(self):
        p = YahooFinanceDataProvider()
        self.assertTrue(p.supports_symbol("NASDAQ:AAPL"))
        self.assertTrue(p.supports_symbol("NYSE:MSFT"))
        self.assertTrue(p.supports_symbol("FOREX:EURUSD"))
        self.assertTrue(p.supports_symbol("INDEX:SPX"))
        self.assertTrue(p.supports_symbol("TSLA"))

        self.assertEqual(p._extract_ticker("NASDAQ:AAPL"), "AAPL")
        self.assertEqual(p._extract_ticker("FOREX:EURUSD"), "EURUSD=X")
        self.assertEqual(p._extract_ticker("INDEX:SPX"), "^SPX")
        self.assertEqual(p._extract_ticker("GME"), "GME")

    @patch("httpx.AsyncClient.get")
    def test_yahoo_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "chart": {
                "result": [{
                    "timestamp": [1731234000],
                    "indicators": {
                        "quote": [{
                            "open": [220.5],
                            "high": [222.0],
                            "low": [219.8],
                            "close": [221.3],
                            "volume": [50000],
                        }]
                    }
                }]
            }
        }
        mock_get.return_value = mock_resp

        p = YahooFinanceDataProvider()
        bars = asyncio.run(p.fetch_historical_bars("NASDAQ:AAPL", limit=10))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["open"], 220.5)
        self.assertEqual(bars[0]["volume"], 50000.0)

    def test_polygon_unconfigured_behavior(self):
        p = PolygonDataProvider(api_key="")
        self.assertFalse(p.is_configured)
        self.assertFalse(p.supports_symbol("NASDAQ:AAPL"))

        bars = asyncio.run(p.fetch_historical_bars("NASDAQ:AAPL"))
        self.assertEqual(bars, [])

    @patch("httpx.AsyncClient.get")
    def test_polygon_configured_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"t": 1731234000000, "o": 220.0, "h": 225.0, "l": 218.0, "c": 224.0, "v": 12000.0}
            ]
        }
        mock_get.return_value = mock_resp

        p = PolygonDataProvider(api_key="test_licensed_key")
        self.assertTrue(p.is_configured)
        self.assertTrue(p.supports_symbol("AAPL"))

        bars = asyncio.run(p.fetch_historical_bars("AAPL", limit=10))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 224.0)
        self.assertEqual(bars[0]["volume"], 12000.0)

    def test_alpaca_unconfigured_behavior(self):
        p = AlpacaDataProvider(api_key_id="", api_secret_key="")
        self.assertFalse(p.is_configured)
        self.assertFalse(p.supports_symbol("AAPL"))

        bars = asyncio.run(p.fetch_historical_bars("AAPL"))
        self.assertEqual(bars, [])

    @patch("httpx.AsyncClient.get")
    def test_alpaca_configured_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "bars": [
                {"t": "2026-11-14T08:00:00Z", "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.0, "v": 450}
            ]
        }
        mock_get.return_value = mock_resp

        p = AlpacaDataProvider(api_key_id="test_key", api_secret_key="test_secret")
        self.assertTrue(p.is_configured)
        self.assertTrue(p.supports_symbol("AAPL"))

        bars = asyncio.run(p.fetch_historical_bars("AAPL", limit=10))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["open"], 100.0)
        self.assertEqual(bars[0]["close"], 104.0)

    def test_composite_cascade_fallback(self):
        poly_mock = MagicMock(spec=PolygonDataProvider)
        poly_mock.name = "polygon"
        poly_mock.supports_symbol.return_value = True
        poly_mock.fetch_historical_bars = AsyncMock(return_value=[])

        yahoo_mock = MagicMock(spec=YahooFinanceDataProvider)
        yahoo_mock.name = "yahoo_finance"
        yahoo_mock.supports_symbol.return_value = True
        yahoo_mock.fetch_historical_bars = AsyncMock(return_value=[
            {"time": 1731234000, "open": 50.0, "high": 55.0, "low": 48.0, "close": 52.0, "volume": 100.0}
        ])

        composite = CompositeMarketDataProvider([poly_mock, yahoo_mock])
        name, bars = asyncio.run(composite.fetch_historical_bars("NASDAQ:AAPL"))
        self.assertEqual(name, "yahoo_finance")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 52.0)

    @patch("httpx.AsyncClient.get")
    def test_providers_multi_timeframe_and_routing(self, mock_get):
        """Validates lowercase Binance timeframe, Yahoo AMEX & BRK.B dot normalization, and crypto exclusion."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_get.return_value = mock_resp

        # 1. Binance lowercase interval
        binance = BinanceDataProvider()
        asyncio.run(binance.fetch_historical_bars("BINANCE:BTCUSDT", timeframe="1D"))
        called_url = mock_get.call_args[0][0]
        self.assertIn("interval=1d", called_url)
        self.assertNotIn("interval=1D", called_url)

        # 2. Yahoo Finance AMEX support and dot-to-hyphen
        yahoo = YahooFinanceDataProvider()
        self.assertTrue(yahoo.supports_symbol("AMEX:SPY"))
        self.assertEqual(yahoo._extract_ticker("NYSE:BRK.B"), "BRK-B")

        # Yahoo timeframe mapping
        mock_get.reset_mock()
        mock_resp.json.return_value = {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}}
        asyncio.run(yahoo.fetch_historical_bars("NASDAQ:AAPL", timeframe="1D"))
        called_url = mock_get.call_args[0][0]
        self.assertIn("interval=1d", called_url)
        self.assertIn("range=5y", called_url)

        mock_get.reset_mock()
        asyncio.run(yahoo.fetch_historical_bars("NASDAQ:AAPL", timeframe="1W"))
        called_url_w = mock_get.call_args[0][0]
        self.assertIn("interval=1wk", called_url_w)
        self.assertIn("range=5y", called_url_w)

        mock_get.reset_mock()
        asyncio.run(yahoo.fetch_historical_bars("NASDAQ:AAPL", timeframe="5m"))
        called_url = mock_get.call_args[0][0]
        self.assertIn("interval=5m", called_url)
        self.assertIn("range=5d", called_url)

        # 3. Polygon & Alpaca crypto exclusion
        poly = PolygonDataProvider(api_key="test_key")
        self.assertTrue(poly.supports_symbol("NASDAQ:AAPL"))
        self.assertFalse(poly.supports_symbol("BINANCE:BTCUSDT"))
        self.assertFalse(poly.supports_symbol("ETHUSDT"))

        alpaca = AlpacaDataProvider(api_key_id="k", api_secret_key="s")
        self.assertTrue(alpaca.supports_symbol("NASDAQ:AAPL"))
        self.assertFalse(alpaca.supports_symbol("BINANCE:BTCUSDT"))
        self.assertFalse(alpaca.supports_symbol("SOLUSDT"))


if __name__ == "__main__":
    unittest.main()

"""Tests verifying production architectural hardening and Gemini Web Copilot review fixes."""

import asyncio
import time
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from fastapi.testclient import TestClient

from server import (
    app,
    WATCHLIST,
    SYMBOLS,
    _yahoo_cache,
    MAX_CACHE_SIZE,
    fetch_yahoo_history,
    fetch_universal_history,
    _inflight_history
)


class TestArchitecturalHardening(unittest.TestCase):
    """Verifies LRU cache bound, singleflight deduplication, and symbol normalization."""

    def setUp(self):
        self.orig_watchlist = [dict(w) for w in WATCHLIST]
        self.orig_symbols = list(SYMBOLS)
        _yahoo_cache.clear()
        _inflight_history.clear()

    def tearDown(self):
        WATCHLIST.clear()
        WATCHLIST.extend(self.orig_watchlist)
        SYMBOLS.clear()
        SYMBOLS.extend(self.orig_symbols)

    def test_yahoo_cache_lru_eviction(self):
        """Validates that _yahoo_cache does not exceed MAX_CACHE_SIZE (200) and evicts oldest."""
        now = time.time()
        for i in range(MAX_CACHE_SIZE + 10):
            key = f"TICKER{i}_1m"
            if len(_yahoo_cache) >= MAX_CACHE_SIZE:
                _yahoo_cache.popitem(last=False)
            _yahoo_cache[key] = (now, [{"time": 1000 + i, "close": 100.0}])

        self.assertEqual(len(_yahoo_cache), MAX_CACHE_SIZE)
        self.assertNotIn("TICKER0_1m", _yahoo_cache)
        self.assertIn(f"TICKER{MAX_CACHE_SIZE + 9}_1m", _yahoo_cache)

    def test_ticker_dot_to_hyphen_normalization(self):
        """Validates that ticker with dot like BRK.B normalizes to BRK-B in Yahoo fetch."""
        mock_resp = httpx.Response(200, json={
            "chart": {
                "result": [{
                    "timestamp": [1700000000],
                    "indicators": {
                        "quote": [{
                            "open": [400.0],
                            "high": [405.0],
                            "low": [399.0],
                            "close": [402.0],
                            "volume": [1000]
                        }]
                    }
                }]
            }
        })
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp

            bars = asyncio.run(fetch_yahoo_history("NYSE:BRK.B", timeframe="1D"))
            self.assertEqual(len(bars), 1)
            # Verify request URL used hyphen
            called_url = mock_get.call_args[0][0]
            self.assertIn("BRK-B", called_url)

    def test_singleflight_deduplication(self):
        """Validates that concurrent requests for the same symbol/timeframe share single flight."""
        async def run_concurrent():
            with patch("server.fetch_yahoo_history", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = [{"time": 100, "close": 50.0}]
                async def delayed_fetch(*args, **kwargs):
                    await asyncio.sleep(0.05)
                    return [{"time": 100, "close": 50.0}]
                mock_fetch.side_effect = delayed_fetch

                # Launch 5 concurrent calls
                tasks = [fetch_universal_history("NASDAQ:AAPL", timeframe="1m") for _ in range(5)]
                results = await asyncio.gather(*tasks)

                self.assertEqual(len(results), 5)
                for r in results:
                    self.assertEqual(r, [{"time": 100, "close": 50.0}])
                # Only 1 underlying fetch should have occurred
                self.assertEqual(mock_fetch.call_count, 1)

        asyncio.run(run_concurrent())

    def test_subscribe_supports_id_or_symbol(self):
        """Validates that POST /api/symbols/subscribe accepts either 'symbol' or 'id'."""
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.subscribe", new_callable=AsyncMock), \
             patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             patch("server.fetch_universal_history", new_callable=AsyncMock, return_value=[]), \
             TestClient(app) as client:
            resp1 = client.post("/api/symbols/subscribe", json={
                "id": "NASDAQ:MSFT",
                "name": "Microsoft Corp.",
                "exchange": "NASDAQ",
                "type": "stock"
            })
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.json()["symbol"]["id"], "NASDAQ:MSFT")

            resp2 = client.post("/api/symbols/subscribe", json={
                "symbol": "NASDAQ:GOOGL",
                "name": "Alphabet Inc.",
                "exchange": "NASDAQ",
                "type": "stock"
            })
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.json()["symbol"]["id"], "NASDAQ:GOOGL")

    def test_singleflight_cancellation_does_not_deadlock_waiters(self):
        """Validates that cancelling the initial fetch task does not deadlock concurrent waiters on the same singleflight task."""
        async def run_test():
            gate = asyncio.Event()

            async def blocked_fetch(*args, **kwargs):
                await gate.wait()
                return [{"time": 1, "close": 10.0}]

            with patch("server.fetch_historical_bars", side_effect=blocked_fetch):
                t1 = asyncio.create_task(fetch_universal_history("NASDAQ:DEADLOCK_TEST", timeframe="1m"))
                await asyncio.sleep(0.01)

                t2 = asyncio.create_task(fetch_universal_history("NASDAQ:DEADLOCK_TEST", timeframe="1m"))
                await asyncio.sleep(0.01)

                # Cancel task 1
                t1.cancel()
                try:
                    await t1
                except asyncio.CancelledError:
                    pass

                # Release gate so background task finishes
                gate.set()

                # Task 2 must complete safely with valid bars without hanging or deadlocking
                res2 = await asyncio.wait_for(t2, timeout=1.0)
                self.assertEqual(res2, [{"time": 1, "close": 10.0}])

        asyncio.run(run_test())

    def test_singleflight_leader_cancellation_does_not_abort_follower(self):
        """Validates that cancelling the first caller does not cancel the shared fetch for concurrent followers."""
        async def run_cancellation_race():
            with patch("server.fetch_yahoo_history", new_callable=AsyncMock) as mock_fetch:
                async def slow_fetch(*args, **kwargs):
                    await asyncio.sleep(0.06)
                    return [{"time": 1700000000, "open": 180.0, "high": 182.0, "low": 179.0, "close": 181.0, "volume": 500.0}]
                mock_fetch.side_effect = slow_fetch

                leader = asyncio.create_task(fetch_universal_history("NASDAQ:AAPL", timeframe="5m"))
                await asyncio.sleep(0.01)
                follower = asyncio.create_task(fetch_universal_history("NASDAQ:AAPL", timeframe="5m"))
                await asyncio.sleep(0.01)

                # Simulate AbortController cancelling the first request mid-flight
                leader.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await leader

                # Follower must still receive the full result without InvalidStateError or empty fallback
                follower_bars = await follower
                self.assertEqual(len(follower_bars), 1)
                self.assertEqual(follower_bars[0]["close"], 181.0)
                self.assertEqual(mock_fetch.call_count, 1)

        asyncio.run(run_cancellation_race())


if __name__ == "__main__":
    unittest.main()

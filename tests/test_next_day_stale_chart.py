"""Tests for Phase 4: Fix Stale Chart Data on Next-Day Startup.

Covers:
1. R4-1: Intraday cache freshness uses _last_remote_fetch rather than last_bar_ts.
2. R4-2: collapse_daily_bars_by_ny_date deduplicates 1D non-crypto bars by NY date,
   preserves distinct NY dates sorted chronologically, handles empty/single/malformed
   items robustly, and leaves crypto and 1W symbols untouched.
3. R4-3: fetch_yahoo_history logs HTTP 429, 5xx (500/502/503), and host network
   errors at WARNING level.
"""

import asyncio
from datetime import datetime
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import zoneinfo

import server
from engine.market_time import NY_TZ


class TestCollapseDailyBarsByNyDate(unittest.TestCase):
    """Verifies R4-2: collapse_daily_bars_by_ny_date pure function behavior."""

    def test_collapses_same_ny_date_retains_earliest_time_and_latest_ohlcv(self):
        # 1795444200 = 2026-11-23 09:30:00 ET, 1795467600 = 2026-11-23 16:00:00 ET
        dt_open = datetime.fromtimestamp(1795444200, tz=zoneinfo.ZoneInfo("America/New_York"))
        dt_close = datetime.fromtimestamp(1795467600, tz=zoneinfo.ZoneInfo("America/New_York"))
        self.assertEqual(dt_open.date(), dt_close.date())

        b_open = {
            "time": 1795444200,
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 102.0,
            "volume": 1000.0,
        }
        b_close = {
            "time": 1795467600,
            "open": 100.5,
            "high": 110.0,
            "low": 98.0,
            "close": 108.0,
            "volume": 5000.0,
        }

        res = server.collapse_daily_bars_by_ny_date([b_open, b_close])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["time"], 1795444200)
        self.assertEqual(res[0]["open"], 100.5)
        self.assertEqual(res[0]["high"], 110.0)
        self.assertEqual(res[0]["low"], 98.0)
        self.assertEqual(res[0]["close"], 108.0)
        self.assertEqual(res[0]["volume"], 5000.0)

        # Input order independence (latest first, earliest second)
        res_rev = server.collapse_daily_bars_by_ny_date([b_close, b_open])
        self.assertEqual(res_rev, res)

    def test_preserves_distinct_ny_dates_in_chronological_order(self):
        t_nov20 = int(datetime(2026, 11, 20, 16, 0, tzinfo=NY_TZ).timestamp())
        t_nov23_open = 1795444200  # 2026-11-23 09:30 ET
        # 2026-11-23 19:30 ET is 2026-11-24 00:30 UTC, still 2026-11-23 in NY
        t_nov23_evening = int(datetime(2026, 11, 23, 19, 30, tzinfo=NY_TZ).timestamp())
        t_nov24 = int(datetime(2026, 11, 24, 9, 30, tzinfo=NY_TZ).timestamp())

        bars = [
            {"time": t_nov24, "open": 20.0, "high": 21.0, "low": 19.0, "close": 20.5, "volume": 300.0},
            {"time": t_nov23_evening, "open": 15.0, "high": 16.0, "low": 14.0, "close": 15.8, "volume": 250.0},
            {"time": t_nov20, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0},
            {"time": t_nov23_open, "open": 14.0, "high": 15.0, "low": 13.5, "close": 14.2, "volume": 120.0},
        ]

        res = server.collapse_daily_bars_by_ny_date(bars)
        self.assertEqual(len(res), 3)
        self.assertEqual([b["time"] for b in res], [t_nov20, t_nov23_open, t_nov24])
        self.assertEqual(res[1]["close"], 15.8)
        self.assertEqual(res[1]["volume"], 250.0)

    def test_empty_single_and_malformed_items_handled_robustly(self):
        self.assertEqual(server.collapse_daily_bars_by_ny_date([]), [])

        single = [{"time": 1795444200, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}]
        res_single = server.collapse_daily_bars_by_ny_date(single)
        self.assertEqual(res_single, single)
        self.assertIsNot(res_single[0], single[0])

        malformed_bad_ts = {"time": "not-a-timestamp", "close": 99.0}
        malformed_none_ts = {"time": None, "close": 88.0}
        valid_bar = {"time": 1795444200, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}

        res = server.collapse_daily_bars_by_ny_date([
            None,
            "garbage",
            malformed_bad_ts,
            valid_bar,
            malformed_none_ts,
        ])
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], valid_bar)
        self.assertIn(malformed_bad_ts, res)
        self.assertIn(malformed_none_ts, res)


class TestIntradayCacheFreshnessAndCryptoExclusion(unittest.TestCase):
    """Verifies R4-1 intraday freshness and R4-2 integration in fetch_historical_bars."""

    def setUp(self):
        server._last_remote_fetch.clear()

    def tearDown(self):
        server._last_remote_fetch.clear()

    def test_intraday_next_day_startup_empty_last_remote_fetch_forces_remote_fetch(self):
        """Even if SQLite has bars within tf_seconds of now_ts, empty _last_remote_fetch is stale."""
        test_sym = "NASDAQ:NVDA"
        now_ts = 1795530000
        # 40 bars where the newest bar is only 10s old (<= 60s for 1m)
        cached_bars = [
            {"time": now_ts - 10 - (39 - i) * 60, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}
            for i in range(40)
        ]
        fresh_bars = [
            {"time": now_ts - (39 - i) * 60, "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 2000.0}
            for i in range(40)
        ]

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars

        # Ensure _last_remote_fetch has no entry for (test_sym, "1m")
        self.assertNotIn((test_sym, "1m"), server._last_remote_fetch)

        with patch("server.get_storage", return_value=mock_storage), \
             patch("server.time.time", return_value=float(now_ts)), \
             patch("server.fetch_yahoo_history", new_callable=AsyncMock, return_value=fresh_bars) as mock_yahoo:
            res = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1m", limit=300))
            mock_yahoo.assert_awaited_once()
            self.assertEqual(res[-1]["close"], 101.5)
            self.assertIn((test_sym, "1m"), server._last_remote_fetch)

    def test_intraday_recent_last_remote_fetch_uses_cache(self):
        """When _last_remote_fetch is within tf_seconds, cached intraday bars are served without remote fetch."""
        test_sym = "NASDAQ:NVDA"
        now_ts = 1795530000
        cached_bars = [
            {"time": now_ts - 3600 - (39 - i) * 300, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}
            for i in range(40)
        ]

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars
        # Fetched 120s ago; for 5m (tf_seconds=300), 120 <= 300 -> fresh
        server._last_remote_fetch[(test_sym, "5m")] = float(now_ts - 120)

        with patch("server.get_storage", return_value=mock_storage), \
             patch("server.time.time", return_value=float(now_ts)), \
             patch("server.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo:
            res = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="5m", limit=300))
            mock_yahoo.assert_not_called()
            self.assertEqual(len(res), 40)

    def test_intraday_stale_last_remote_fetch_refetches(self):
        """When _last_remote_fetch exceeds tf_seconds, remote fetch is triggered."""
        test_sym = "NASDAQ:NVDA"
        now_ts = 1795530000
        cached_bars = [
            {"time": now_ts - (39 - i) * 300, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}
            for i in range(40)
        ]
        fresh_bars = [
            {"time": now_ts - (39 - i) * 300, "open": 105.0, "high": 106.0, "low": 104.0, "close": 105.5, "volume": 3000.0}
            for i in range(40)
        ]

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars
        server._last_remote_fetch[(test_sym, "5m")] = float(now_ts - 301)

        with patch("server.get_storage", return_value=mock_storage), \
             patch("server.time.time", return_value=float(now_ts)), \
             patch("server.fetch_yahoo_history", new_callable=AsyncMock, return_value=fresh_bars) as mock_yahoo:
            res = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="5m", limit=300))
            mock_yahoo.assert_awaited_once()
            self.assertEqual(res[-1]["close"], 105.5)

    def test_daily_equity_collapses_on_cache_remote_and_offline_fallback(self):
        """Equity 1D bars collapse duplicate NY dates in cache-first, remote, and offline fallback paths."""
        test_sym = "NASDAQ:NVDA"
        base_day = datetime(2026, 1, 1, 9, 30, tzinfo=NY_TZ)
        base_ts = int(base_day.timestamp())
        distinct_bars = [
            {"time": base_ts + i * 86400, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0}
            for i in range(255)
        ]
        # Append duplicate NY date bar (2026-11-23 09:30 & 16:00 ET)
        dup_open = {"time": 1795444200, "open": 180.0, "high": 182.0, "low": 179.0, "close": 181.0, "volume": 1000.0}
        dup_close = {"time": 1795467600, "open": 180.0, "high": 185.0, "low": 178.0, "close": 184.0, "volume": 5000.0}
        input_bars = distinct_bars + [dup_open, dup_close]

        # 1) Cache-first return
        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = list(input_bars)
        now_ts = 1795470000
        server._last_remote_fetch[(test_sym, "1D")] = float(now_ts - 10)
        with patch("server.get_storage", return_value=mock_storage), \
             patch("server.is_regular_session_open", return_value=True), \
             patch("server.time.time", return_value=float(now_ts)), \
             patch("server.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo:
            res_cache = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=1500))
            mock_yahoo.assert_not_called()
            self.assertEqual(len(res_cache), 256)
            self.assertEqual(res_cache[-1]["time"], 1795444200)
            self.assertEqual(res_cache[-1]["close"], 184.0)

        # 2) Remote fetch return
        server._last_remote_fetch.clear()
        mock_storage_remote = MagicMock()
        mock_storage_remote.get_bars.return_value = []
        with patch("server.get_storage", return_value=mock_storage_remote), \
             patch("server.time.time", return_value=float(now_ts)), \
             patch("server.fetch_yahoo_history", new_callable=AsyncMock, return_value=list(input_bars)):
            res_remote = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=1500))
            self.assertEqual(len(res_remote), 256)
            self.assertEqual(res_remote[-1]["time"], 1795444200)
            self.assertEqual(res_remote[-1]["close"], 184.0)
            saved_bars = mock_storage_remote.save_bars.call_args[0][2]
            self.assertEqual(len(saved_bars), 256)

        # 3) Offline fallback return
        server._last_remote_fetch.clear()
        mock_storage_offline = MagicMock()
        mock_storage_offline.get_bars.side_effect = [[], list(input_bars)]
        with patch("server.get_storage", return_value=mock_storage_offline), \
             patch("server.fetch_yahoo_history", new_callable=AsyncMock, return_value=[]):
            res_offline = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=1500))
            self.assertEqual(len(res_offline), 256)
            self.assertEqual(res_offline[-1]["time"], 1795444200)
            self.assertEqual(res_offline[-1]["close"], 184.0)

    def test_crypto_daily_bars_not_collapsed_by_ny_date(self):
        """Crypto symbols (is_crypto_symbol == True) must NOT be collapsed by NY date."""
        crypto_sym = "BINANCE:BTCUSDT"
        self.assertTrue(server.is_crypto_symbol(crypto_sym))

        # Two bars on the same NY calendar date (09:30 ET and 16:00 ET)
        b1 = {"time": 1795444200, "open": 90000.0, "high": 91000.0, "low": 89500.0, "close": 90500.0, "volume": 100.0}
        b2 = {"time": 1795467600, "open": 90500.0, "high": 92000.0, "low": 90000.0, "close": 91500.0, "volume": 200.0}
        base_ts = int(datetime(2026, 1, 1, 0, 0, tzinfo=NY_TZ).timestamp())
        history = [
            {"time": base_ts + i * 86400, "open": 80000.0, "high": 81000.0, "low": 79000.0, "close": 80500.0, "volume": 50.0}
            for i in range(255)
        ] + [b1, b2]

        # Remote fetch path
        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = []
        with patch("server.get_storage", return_value=mock_storage), \
             patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=list(history)):
            res_remote = asyncio.run(server.fetch_historical_bars(crypto_sym, timeframe="1D", limit=1500))
            self.assertEqual(len(res_remote), 257)

        # Cache-first path
        now_ts = 1795470000
        server._last_remote_fetch[(crypto_sym, "1D")] = float(now_ts - 10)
        mock_storage.get_bars.return_value = list(history)
        with patch("server.get_storage", return_value=mock_storage), \
             patch("server.time.time", return_value=float(now_ts)), \
             patch("server.fetch_binance_history", new_callable=AsyncMock) as mock_bin:
            res_cache = asyncio.run(server.fetch_historical_bars(crypto_sym, timeframe="1D", limit=1500))
            mock_bin.assert_not_called()
            self.assertEqual(len(res_cache), 257)

        # Offline fallback path
        server._last_remote_fetch.clear()
        mock_storage_off = MagicMock()
        mock_storage_off.get_bars.side_effect = [[], list(history)]
        with patch("server.get_storage", return_value=mock_storage_off), \
             patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=[]):
            res_off = asyncio.run(server.fetch_historical_bars(crypto_sym, timeframe="1D", limit=1500))
            self.assertEqual(len(res_off), 257)


class TestYahooHistoryWarningLogging(unittest.TestCase):
    """Verifies R4-3: HTTP 429, 5xx, and network exceptions log at WARNING level."""

    def setUp(self):
        server._yahoo_cache.clear()

    def tearDown(self):
        server._yahoo_cache.clear()

    def test_429_logs_warning(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("server.get_shared_client", return_value=mock_client), \
             self.assertLogs(server.logger, level=logging.WARNING) as cm:
            bars = asyncio.run(server.fetch_yahoo_history("NASDAQ:AAPL", timeframe="1D"))

        self.assertEqual(bars, [])
        self.assertTrue(any("429" in msg for msg in cm.output))

    def test_5xx_logs_warning(self):
        for code in (500, 502, 503):
            server._yahoo_cache.clear()
            mock_resp = MagicMock()
            mock_resp.status_code = code
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)

            with patch("server.get_shared_client", return_value=mock_client), \
                 self.assertLogs(server.logger, level=logging.WARNING) as cm:
                bars = asyncio.run(server.fetch_yahoo_history("NASDAQ:MSFT", timeframe="1D"))

            self.assertEqual(bars, [])
            self.assertTrue(any(str(code) in msg for msg in cm.output))

    def test_network_error_logs_warning(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("Connection reset by peer"))

        with patch("server.get_shared_client", return_value=mock_client), \
             self.assertLogs(server.logger, level=logging.WARNING) as cm:
            bars = asyncio.run(server.fetch_yahoo_history("NASDAQ:NVDA", timeframe="1D"))

        self.assertEqual(bars, [])
        self.assertTrue(any("network error" in msg.lower() for msg in cm.output))


if __name__ == "__main__":
    unittest.main()

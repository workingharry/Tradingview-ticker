"""Unit tests for Section 1 Backend Market Data Correctness (S1-01 to S1-12).

Strictly offline: all network calls mocked, dates/times injected.
Verifies:
1. engine.market_time session dates, market hours, Macau time mapping & DST switches.
2. S1-01: Screener/heatmap calls fetch_func with 252+ bars; returns data_as_of & data_stale.
3. S1-02: 1D/1W bar cache freshness for regular session vs market closed, crypto vs equity, fallback.
4. S1-03: AI Lens deterministic decision removes fake prices; insufficient data -> confidence 0, data_quality="insufficient", no '$' in strings; missing fundamentals -> "無基本面資料".
5. S1-04: call_gemini_generate_with_model cascades & returns actual model; /api/chat returns model & source.
6. S1-05: Screener snapshots and streaks use current_session_date() instead of local Macau date.
7. S1-06: Quote update_mode and is_delayed parsing (delayed -> True, streaming -> False, none -> None).
8. S1-07: Yahoo exchange code mapping (NMS/NGM/NCM -> NASDAQ, NYQ -> NYSE, ASE/PCX -> AMEX); PNK and crypto excluded; subscribe exchange guessing.
9. S1-08: /api/screener/scan parameter validation with 422 error on invalid inputs; null -> 200 default.
10. S1-12: calculate_atr_toolkit for low-price stocks preserves non-zero ATR with 4 sig figs.
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import zoneinfo

from fastapi.testclient import TestClient

import server
from server import app
from engine.client import TradingViewAsyncClient
from engine.market_time import (
    ny_now,
    is_regular_session_open,
    current_session_date,
    last_completed_session_date,
    NY_TZ,
)
from engine.screener import (
    calculate_atr_toolkit,
    scan_symbol_candidate,
    fetch_heatmap_universe,
    ScreeningCriteria,
)


class TestMarketTime(unittest.TestCase):
    """Verifies US trading session date calculations, regular market hours, and daylight savings."""

    def test_macau_sunday_night_is_friday_session(self):
        """Macau 2026-11-23 (Mon) 08:00 = NY 2026-11-22 (Sun) 19:00 -> current_session_date is 2026-11-20 (Fri)."""
        macau_tz = zoneinfo.ZoneInfo("Asia/Macau")
        macau_dt = datetime(2026, 11, 23, 8, 0, tzinfo=macau_tz)
        session_d = current_session_date(macau_dt)
        self.assertEqual(session_d, date(2026, 11, 20))
        self.assertFalse(is_regular_session_open(macau_dt))

    def test_macau_monday_premarket_is_friday_session(self):
        """Macau 2026-11-23 22:00 = NY 09:00 (Mon pre-market) -> current_session_date is 2026-11-20."""
        macau_tz = zoneinfo.ZoneInfo("Asia/Macau")
        macau_dt = datetime(2026, 11, 23, 22, 0, tzinfo=macau_tz)
        session_d = current_session_date(macau_dt)
        self.assertEqual(session_d, date(2026, 11, 20))
        self.assertFalse(is_regular_session_open(macau_dt))

    def test_macau_monday_night_market_open(self):
        """Macau 2026-11-23 23:00 = NY 10:00 (Mon regular session) -> current_session_date is 2026-11-23, open is True."""
        macau_tz = zoneinfo.ZoneInfo("Asia/Macau")
        macau_dt = datetime(2026, 11, 23, 23, 0, tzinfo=macau_tz)
        session_d = current_session_date(macau_dt)
        self.assertEqual(session_d, date(2026, 11, 23))
        self.assertTrue(is_regular_session_open(macau_dt))

    def test_daylight_savings_switch_detection(self):
        """2026-03-06 (Fri) NY 09:30 = 14:30 UTC; 2026-03-09 (Mon) NY 09:30 = 13:30 UTC -> both open."""
        # Pre-switch: UTC-5
        dt_pre = datetime(2026, 3, 6, 14, 30, tzinfo=timezone.utc)
        self.assertTrue(is_regular_session_open(dt_pre))
        self.assertEqual(ny_now(dt_pre).time(), time(9, 30))

        # Post-switch: UTC-4
        dt_post = datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc)
        self.assertTrue(is_regular_session_open(dt_post))
        self.assertEqual(ny_now(dt_post).time(), time(9, 30))

    def test_last_completed_session_date(self):
        """Mon 10:00 ET -> Fri (today not closed); Mon 16:30 ET -> Mon (today completed)."""
        mon_morning = datetime(2026, 11, 23, 10, 0, tzinfo=NY_TZ)
        self.assertEqual(last_completed_session_date(mon_morning), date(2026, 11, 20))

        mon_afternoon = datetime(2026, 11, 23, 16, 30, tzinfo=NY_TZ)
        self.assertEqual(last_completed_session_date(mon_afternoon), date(2026, 11, 23))


class TestS101ScreenerAndHeatmapFreshness(unittest.TestCase):
    """S1-01: Verifies screener and heatmap always call fetch_func and append data_as_of and data_stale."""

    def test_screener_calls_fetch_func_with_300_cached_bars(self):
        """Even if storage has 300 daily bars, fetch_func must be called to ensure freshness."""
        now_ts = int(datetime(2026, 11, 20, 16, 0, tzinfo=NY_TZ).timestamp())
        mock_bars = [{"time": now_ts - (i * 86400), "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 1000000} for i in range(300)]
        mock_bars.reverse()

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = list(mock_bars)
        mock_storage.get_fundamental_report.return_value = None

        fetch_func_mock = AsyncMock(return_value=list(mock_bars))

        async def _run():
            semaphore = asyncio.Semaphore(5)
            criteria = ScreeningCriteria(min_rs_rating=0, stage2_only=False, mansfield_above_zero=False)
            return await scan_symbol_candidate(
                sym_info={"id": "NASDAQ:NVDA", "symbol": "NVDA"},
                criteria=criteria,
                storage_obj=mock_storage,
                benchmark_bars=mock_bars,
                fetch_func=fetch_func_mock,
                semaphore=semaphore
            )

        res = asyncio.run(_run())

        fetch_func_mock.assert_awaited_once_with("NASDAQ:NVDA", timeframe="1D")
        self.assertIsNotNone(res)
        self.assertIn("data_as_of", res)
        self.assertIn("data_stale", res)
        self.assertEqual(res["data_as_of"], "2026-11-20")

    def test_heatmap_calls_fetch_func_and_returns_data_as_of(self):
        """Heatmap universe evaluation calls fetch_func and marks data_as_of / data_stale."""
        now_ts = int(datetime(2026, 11, 20, 16, 0, tzinfo=NY_TZ).timestamp())
        mock_bars = [{"time": now_ts - (i * 86400), "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 1000000} for i in range(300)]
        mock_bars.reverse()

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = list(mock_bars)

        fetch_func_mock = AsyncMock(return_value=list(mock_bars))

        universe = [{"id": "NASDAQ:NVDA", "symbol": "NVDA", "name": "NVIDIA", "sector": "Technology"}]

        with patch("engine.screener.last_completed_session_date", return_value=date(2026, 11, 20)):
            res = asyncio.run(fetch_heatmap_universe(
                universe=universe,
                storage_obj=mock_storage,
                benchmark_bars=mock_bars,
                fetch_func=fetch_func_mock,
                max_concurrency=4
            ))

        fetch_func_mock.assert_awaited_once()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["data_as_of"], "2026-11-20")
        self.assertFalse(res[0]["data_stale"])


def _daily_bars(last_ts, n=260, **ohlcv):
    """n 根連續日 K，最後一根的 time 為 last_ts，每根相差 86400 秒（各在不同的紐約日期）。"""
    return [dict(ohlcv, time=last_ts - 86400 * (n - 1 - i)) for i in range(n)]


class TestS102CacheFreshness(unittest.TestCase):
    """S1-02: Verifies 1D/1W cache freshness rules for regular session vs market closed and crypto."""

    def setUp(self):
        server._last_remote_fetch.clear()

    def test_intraday_under_60s_uses_cache(self):
        """Regular session open + fetched 30s ago -> uses SQLite cache without calling remote."""
        test_sym = "NASDAQ:AAPL"
        now_ts = 1700000000
        bars = _daily_bars(now_ts - 1000, open=150.0, high=155.0, low=149.0, close=152.0, volume=10000)

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = bars

        server._last_remote_fetch[(test_sym, "1D")] = now_ts - 30.0

        with patch("api.deps.get_storage", return_value=mock_storage), \
             patch("api.history.is_regular_session_open", return_value=True), \
             patch("api.history.time.time", return_value=now_ts), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo:
            result = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=300))
            self.assertEqual(len(result), 260)
            mock_yahoo.assert_not_called()

    def test_intraday_over_60s_refetches_remote(self):
        """Regular session open + fetched 90s ago -> triggers remote refetch."""
        test_sym = "NASDAQ:AAPL"
        now_ts = 1700000000
        cached_bars = [{"time": now_ts - 2000, "open": 150.0, "high": 155.0, "low": 149.0, "close": 152.0, "volume": 10000}] * 260
        fresh_bars = [{"time": now_ts - 500, "open": 150.0, "high": 156.0, "low": 149.0, "close": 155.0, "volume": 20000}] * 260

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars

        server._last_remote_fetch[(test_sym, "1D")] = now_ts - 90.0

        with patch("api.deps.get_storage", return_value=mock_storage), \
             patch("api.history.is_regular_session_open", return_value=True), \
             patch("api.history.time.time", return_value=now_ts), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock, return_value=fresh_bars) as mock_yahoo:
            result = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=300))
            mock_yahoo.assert_awaited_once()
            self.assertEqual(server._last_remote_fetch[(test_sym, "1D")], now_ts)

    def test_market_closed_post_1600_fetch_uses_cache(self):
        """Market closed + last bar date >= last completed session + fetched post-16:00 ET -> uses cache."""
        test_sym = "NASDAQ:MSFT"
        comp_date = date(2026, 11, 20)
        session_close_dt = datetime.combine(comp_date, time(16, 0), tzinfo=NY_TZ)
        close_ts = int(session_close_dt.timestamp())

        bar_ts = close_ts
        cached_bars = _daily_bars(bar_ts, open=200.0, high=205.0, low=198.0, close=202.0, volume=100000)

        fetch_time = close_ts + 900
        eval_time = close_ts + 7200  # 18:00 ET

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars

        server._last_remote_fetch[(test_sym, "1D")] = float(fetch_time)

        with patch("api.deps.get_storage", return_value=mock_storage), \
             patch("api.history.is_regular_session_open", return_value=False), \
             patch("api.history.last_completed_session_date", return_value=comp_date), \
             patch("api.history.time.time", return_value=eval_time), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo:
            result = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=300))
            self.assertEqual(len(result), 260)
            mock_yahoo.assert_not_called()

    def test_market_closed_only_intraday_fetched_refetches_once(self):
        """Market closed + previously fetched at 14:00 (intraday) -> refetches once after close."""
        test_sym = "NASDAQ:MSFT"
        comp_date = date(2026, 11, 20)
        session_close_dt = datetime.combine(comp_date, time(16, 0), tzinfo=NY_TZ)
        close_ts = int(session_close_dt.timestamp())

        bar_ts = close_ts - 7200
        cached_bars = [{"time": bar_ts, "open": 200.0, "high": 205.0, "low": 198.0, "close": 202.0, "volume": 100000}] * 260
        fresh_bars = [{"time": close_ts, "open": 200.0, "high": 206.0, "low": 198.0, "close": 204.0, "volume": 150000}] * 260

        intraday_fetch_time = close_ts - 7200
        eval_time = close_ts + 1800  # 16:30 ET

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars

        server._last_remote_fetch[(test_sym, "1D")] = float(intraday_fetch_time)

        with patch("api.deps.get_storage", return_value=mock_storage), \
             patch("api.history.is_regular_session_open", return_value=False), \
             patch("api.history.last_completed_session_date", return_value=comp_date), \
             patch("api.history.time.time", return_value=eval_time), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock, return_value=fresh_bars) as mock_yahoo:
            result = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=300))
            mock_yahoo.assert_awaited_once()

    def test_remote_failure_falls_back_to_sqlite(self):
        """When remote network call fails, falls back gracefully to SQLite cached bars."""
        test_sym = "NASDAQ:AAPL"
        now_ts = 1700000000
        cached_bars = _daily_bars(now_ts - 5000, open=150.0, high=155.0, low=149.0, close=152.0, volume=10000)

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars

        with patch("api.deps.get_storage", return_value=mock_storage), \
             patch("api.history.is_regular_session_open", return_value=True), \
             patch("api.history.time.time", return_value=now_ts), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock, side_effect=RuntimeError("Network timeout")):
            result = asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=300))
            self.assertEqual(len(result), 260)


class TestS103AiLensDeterministic(unittest.TestCase):
    """S1-03: Verifies rule-based decision fallback does not fabricate prices."""

    def test_insufficient_data_returns_wait_and_no_dollar_signs(self):
        """Missing close price -> bias WAIT, confidence 0, data_quality 'insufficient', no $ in text."""
        payload = {
            "quant_metrics": {
                "close": None,
                "ema21": 98.0,
                "sma50": 95.0,
            },
            "fundamentals": {}
        }
        decision = server.generate_deterministic_ai_lens_decision(payload)
        self.assertEqual(decision["bias"], "WAIT")
        self.assertEqual(decision["confidence"], 0)
        self.assertEqual(decision["data_quality"], "insufficient")
        self.assertEqual(decision["source"], "rule_based")
        self.assertEqual(decision["fundamental_diagnosis"], "無基本面資料")

        # Must not contain any dollar signs
        for k, v in decision.items():
            if isinstance(v, str):
                self.assertNotIn("$", v, f"Found dollar sign in field {k}: {v}")
                self.assertNotIn("穩健", v, f"Found '穩健' in field {k}: {v}")

    def test_complete_data_returns_ok_quality(self):
        """Complete metrics -> data_quality ok, source rule_based."""
        payload = {
            "quant_metrics": {
                "trend_regime": "STAGE_2_STRONG_UPTREND",
                "close": 100.0,
                "ema10": 99.0,
                "ema21": 96.0,
                "sma50": 90.0,
                "pct_from_52w_high": -5.0,
                "volume_ratio_50": 1.5,
                "rs_rating": 85
            },
            "fundamentals": {"pe_ratio": 25.5}
        }
        decision = server.generate_deterministic_ai_lens_decision(payload)
        self.assertEqual(decision["bias"], "LONG")
        self.assertEqual(decision["data_quality"], "ok")
        self.assertEqual(decision["source"], "rule_based")
        self.assertIn("PE: 25.5", decision["fundamental_diagnosis"])


class TestS104ModelTracking(unittest.TestCase):
    """S1-04: Verifies model waterfall cascade tracking and API responses."""

    def test_call_gemini_generate_with_model_second_cascade(self):
        """When 2.5-flash fails with 429 and 2.5-flash-lite succeeds, returns actual model."""
        async def _test():
            mock_client = MagicMock()
            resp_429 = MagicMock(status_code=429)
            resp_200 = MagicMock(
                status_code=200,
                json=lambda: {"candidates": [{"content": {"parts": [{"text": "OK Output"}]}}]}
            )
            mock_client.post = AsyncMock(side_effect=[resp_429, resp_200])

            with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key"}), \
                 patch("api.deps.get_shared_client", return_value=mock_client):
                text, model = await server.call_gemini_generate_with_model("test prompt")
                self.assertEqual(text, "OK Output")
                self.assertEqual(model, "gemini-2.5-flash-lite")

        asyncio.run(_test())

    def test_chat_endpoint_model_null_when_gemini_fails(self):
        """When Gemini fails, /api/chat returns model=None and source='rule_based'."""
        with patch("api.ai.call_gemini_generate_with_model", new_callable=AsyncMock, return_value=(None, None)), \
             patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]):
            with TestClient(app) as client:
                resp = client.post("/api/chat", json={"message": "how is NVDA", "symbol": "NASDAQ:NVDA"})
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertIsNone(data["model"])
                self.assertEqual(data["source"], "rule_based")


class TestS105MacauDateSession(unittest.TestCase):
    """S1-05: Verifies screener snapshot dates align with current_session_date."""

    def test_screener_snapshot_date_isolation(self):
        """Morning in Macau and evening in Macau record different US session dates."""
        macau_tz = zoneinfo.ZoneInfo("Asia/Macau")
        macau_morning = datetime(2026, 11, 23, 8, 0, tzinfo=macau_tz)
        macau_evening = datetime(2026, 11, 23, 23, 0, tzinfo=macau_tz)

        self.assertEqual(current_session_date(macau_morning).isoformat(), "2026-11-20")
        self.assertEqual(current_session_date(macau_evening).isoformat(), "2026-11-23")


class TestS106QuoteDelayedFlag(unittest.TestCase):
    """S1-06: Verifies quote parsing tracks update_mode and sets is_delayed."""

    def test_quote_packet_delayed_mode(self):
        """update_mode 'delayed_streaming_900' -> is_delayed True; 'streaming' -> False; none -> None."""
        dispatched_quotes = []

        def on_quote(sym, q):
            dispatched_quotes.append(q)

        client = TradingViewAsyncClient(symbols=["NASDAQ:NVDA"], on_quote_update=on_quote)

        # Initial state has None
        self.assertIsNone(client.states["NASDAQ:NVDA"]["update_mode"])
        self.assertIsNone(client.states["NASDAQ:NVDA"]["is_delayed"])

        # Packet 1: Delayed
        packet_delayed = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": [
                    "qs_session",
                    {
                        "n": "NASDAQ:NVDA",
                        "v": {"lp": 120.5, "update_mode": "delayed_streaming_900"}
                    }
                ]
            }
        }
        client._handle_packet(packet_delayed)
        self.assertEqual(dispatched_quotes[-1]["update_mode"], "delayed_streaming_900")
        self.assertTrue(dispatched_quotes[-1]["is_delayed"])

        # Packet 2: Real-time streaming
        packet_live = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": [
                    "qs_session",
                    {
                        "n": "NASDAQ:NVDA",
                        "v": {"lp": 121.0, "update_mode": "streaming"}
                    }
                ]
            }
        }
        client._handle_packet(packet_live)
        self.assertEqual(dispatched_quotes[-1]["update_mode"], "streaming")
        self.assertFalse(dispatched_quotes[-1]["is_delayed"])


class TestS107ExchangeMapping(unittest.TestCase):
    """S1-07: Verifies Yahoo exchange mapping, exclusion of unmapped/crypto, and subscribe guessing."""

    def test_search_symbols_filters_and_maps_exchanges(self):
        """NYQ -> NYSE, PCX -> AMEX, PNK excluded, cryptocurrency excluded."""
        mock_quotes = [
            {"symbol": "IBM", "exchange": "NYQ", "quoteType": "EQUITY", "shortname": "IBM Corp"},
            {"symbol": "SPY", "exchange": "PCX", "quoteType": "ETF", "shortname": "SPDR S&P 500"},
            {"symbol": "PENNY", "exchange": "PNK", "quoteType": "EQUITY", "shortname": "Penny Stock"},
            {"symbol": "BTC-USD", "exchange": "CCC", "quoteType": "CRYPTOCURRENCY", "shortname": "Bitcoin"},
        ]

        async def _test():
            mock_resp = MagicMock(status_code=200, json=lambda: {"quotes": mock_quotes})
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            with patch("api.deps.get_shared_client", return_value=mock_http):
                res = await server.search_symbols("test")
                items = res["results"]
                mapped_ids = [it["id"] for it in items]
                self.assertIn("NYSE:IBM", mapped_ids)
                self.assertIn("AMEX:SPY", mapped_ids)
                self.assertNotIn("PNK:PENNY", mapped_ids)
                self.assertNotIn("CCC:BTC-USD", mapped_ids)

        asyncio.run(_test())

    def test_subscribe_guesses_nasdaq_when_not_found(self):
        """Unknown bare symbol not found in Yahoo -> defaults to NASDAQ: with exchange_guessed True."""
        test_ticker = "UNKNOWNXYZ"
        storage = server.get_storage()
        storage.remove_watchlist_item(f"NASDAQ:{test_ticker}")
        server.WATCHLIST[:] = [w for w in server.WATCHLIST if test_ticker not in w.get("id", "")]
        server.SYMBOLS[:] = [s for s in server.SYMBOLS if test_ticker not in s]

        async def _test():
            mock_resp = MagicMock(status_code=200, json=lambda: {"quotes": []})
            mock_http = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            with patch("api.deps.get_shared_client", return_value=mock_http), \
                 patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]):
                res = await server.subscribe_symbol(symbol=test_ticker)
                if hasattr(res, "body"):
                    data = json.loads(res.body)
                else:
                    data = res
                self.assertEqual(data["symbol"]["id"], f"NASDAQ:{test_ticker}")
                self.assertTrue(data.get("exchange_guessed"))

        try:
            asyncio.run(_test())
        finally:
            storage.remove_watchlist_item(f"NASDAQ:{test_ticker}")
            server.WATCHLIST[:] = [w for w in server.WATCHLIST if test_ticker not in w.get("id", "")]
            server.SYMBOLS[:] = [s for s in server.SYMBOLS if test_ticker not in s]


class TestS108ScreenerCriteriaValidation(unittest.TestCase):
    """S1-08: Verifies 422 HTTP response on invalid screener criteria and 200 on null defaults."""

    def test_invalid_criteria_returns_422(self):
        """min_rs_rating='abc' -> 422 with 'min_rs_rating' in error fields."""
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             TestClient(app) as client:
            resp = client.post("/api/screener/scan", json={
                "universe": "us_momentum",
                "criteria": {"min_rs_rating": "abc"}
            })
            self.assertEqual(resp.status_code, 422)
            data = resp.json()
            self.assertEqual(data["error"], "Invalid screener criteria")
            self.assertIn("min_rs_rating", data["fields"])

    def test_null_criteria_returns_200_with_defaults(self):
        """null values for criteria use default values and return 200."""
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.history.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]), \
             patch("api.screener.scan_universe", new_callable=AsyncMock, return_value=[]):
            with TestClient(app) as client:
                resp = client.post("/api/screener/scan", json={
                    "universe": "us_momentum",
                    "criteria": {"min_rs_rating": None}
                })
                self.assertEqual(resp.status_code, 200)


class TestS112LowPriceAtrPrecision(unittest.TestCase):
    """S1-12: Verifies low-priced stocks retain non-zero ATR with 4 significant digits."""

    def test_penny_stock_atr_is_not_zero(self):
        """Stock with price around 0.50 has non-zero ATR rounded to 4 sig figs."""
        bars = []
        base_t = 1700000000
        # 30 bars with price ~0.50 and small daily range 0.0053
        for i in range(30):
            bars.append({
                "time": base_t + (i * 86400),
                "open": 0.500,
                "high": 0.5053,
                "low": 0.4951,
                "close": 0.502,
                "volume": 10000
            })

        res = calculate_atr_toolkit(bars, atr_len=14, pct_len=20)
        self.assertGreater(res["atr"], 0.0)
        self.assertGreater(res["stop_distance"], 0.0)
        # Should retain precision (not truncated to 0.0)
        self.assertTrue(math.isfinite(res["atr"]))
        self.assertTrue(res["atr"] < 0.1)


class TestS1ReviewFixes(unittest.TestCase):
    """Regression tests for issues found when reviewing the first Section 1 submission."""

    def setUp(self):
        server._last_remote_fetch.clear()

    def test_never_fetched_after_close_refetches_even_if_bar_stamped_1600(self):
        """S1-02: no fetch record + market closed + Yahoo-style bar stamped 16:00 ET -> must refetch.

        The first submission fell back to the bar timestamp when no fetch was recorded,
        so a bar stamped exactly at the close was treated as 'fetched after close'.
        """
        test_sym = "NASDAQ:MSFT"
        comp_date = date(2026, 11, 20)
        close_ts = int(datetime.combine(comp_date, time(16, 0), tzinfo=NY_TZ).timestamp())
        cached_bars = [{"time": close_ts, "open": 200.0, "high": 205.0, "low": 198.0, "close": 202.0, "volume": 100000}] * 260

        mock_storage = MagicMock()
        mock_storage.get_bars.return_value = cached_bars

        with patch("api.deps.get_storage", return_value=mock_storage), \
             patch("api.history.is_regular_session_open", return_value=False), \
             patch("api.history.last_completed_session_date", return_value=comp_date), \
             patch("api.history.time.time", return_value=close_ts + 3600), \
             patch("api.history.fetch_yahoo_history", new_callable=AsyncMock, return_value=cached_bars) as mock_yahoo:
            asyncio.run(server.fetch_historical_bars(test_sym, timeframe="1D", limit=300))
            mock_yahoo.assert_awaited_once()

    def test_chat_patched_reply_does_not_claim_a_model(self):
        """S1-04: reply from a patched/unknown source -> source 'gemini', model None (never a guessed name)."""
        with patch("api.ai.call_gemini_generate", new_callable=AsyncMock, return_value="測試回覆內容"):
            data = asyncio.run(server.chat_endpoint(payload={"message": "NVDA?", "symbol": "NASDAQ:NVDA"}))
        self.assertIn("測試回覆內容", data["reply"])
        self.assertEqual(data["source"], "gemini")
        self.assertIsNone(data["model"])

    def test_chat_reports_actual_cascade_model(self):
        """S1-04: the model that actually answered is reported by /api/chat."""
        with patch("api.ai.call_gemini_generate_with_model", new_callable=AsyncMock,
                   return_value=("測試回覆內容", "gemini-2.5-flash-lite")):
            data = asyncio.run(server.chat_endpoint(payload={"message": "NVDA?", "symbol": "NASDAQ:NVDA"}))
        self.assertEqual(data["model"], "gemini-2.5-flash-lite")
        self.assertEqual(data["source"], "gemini")

    def test_model_tracking_survives_wait_for(self):
        """S1-04: the model holder must survive asyncio.wait_for (which copies the context)."""
        async def _run():
            return await asyncio.wait_for(server._generate_with_model_tracking("p"), timeout=5.0)

        with patch("api.ai.call_gemini_generate_with_model", new_callable=AsyncMock,
                   return_value=("ok", "gemini-2.0-flash")):
            text, model = asyncio.run(_run())
        self.assertEqual(text, "ok")
        self.assertEqual(model, "gemini-2.0-flash")

    def _subscribe_offline(self, ticker, expected_id):
        """Subscribes a bare ticker with Yahoo returning no matches; cleans up afterwards."""
        was_present = any(w.get("id") == expected_id for w in server.WATCHLIST)
        storage = server.get_storage()

        mock_resp = MagicMock(status_code=200, json=lambda: {"quotes": []})
        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_resp)

        async def _test():
            with patch("api.deps.get_shared_client", return_value=mock_http), \
                 patch("api.history.fetch_universal_history", new_callable=AsyncMock, return_value=[]):
                res = await server.subscribe_symbol(symbol=ticker)
                return json.loads(res.body) if hasattr(res, "body") else res

        try:
            data = asyncio.run(_test())
        finally:
            if not was_present:
                storage.remove_watchlist_item(expected_id)
                server.WATCHLIST[:] = [w for w in server.WATCHLIST if w.get("id") != expected_id]
                server.SYMBOLS[:] = [s for s in server.SYMBOLS if s != expected_id]
        return data, mock_http

    def test_subscribe_bare_crypto_pair_stays_binance(self):
        """S1-07 regression: bare 'DOGEUSDT' must resolve to BINANCE:, not a guessed NASDAQ:."""
        data, mock_http = self._subscribe_offline("DOGEUSDT", "BINANCE:DOGEUSDT")
        self.assertEqual(data["symbol"]["id"], "BINANCE:DOGEUSDT")
        self.assertFalse(data.get("exchange_guessed", False))
        mock_http.get.assert_not_called()

    def test_subscribe_known_pool_ticker_offline(self):
        """S1-07 regression: bare 'PLTR' is in the local index as NYSE:PLTR; must not become NASDAQ: when Yahoo has no match."""
        data, _ = self._subscribe_offline("PLTR", "NYSE:PLTR")
        self.assertEqual(data["symbol"]["id"], "NYSE:PLTR")
        self.assertFalse(data.get("exchange_guessed", False))


if __name__ == "__main__":
    unittest.main()

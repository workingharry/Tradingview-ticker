"""Tests for All-US Double-Stage Funnel, Four Frameworks (W/D/M/C), V6 Earnings Gate, and SQLite Streak Tracking.
Strict Test-First: All tests verify behavior with mocks, no real internet calls.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestMarketScanner(unittest.TestCase):
    """Verifies all-US market scanner payload, response parser, multi-frameworks evaluation, and streak tracking."""

    def test_build_tv_scan_payload_structure_and_criteria(self):
        """Verifies build_tv_scan_payload compiles valid TradingView scan query."""
        from engine.market_scanner import build_tv_scan_payload

        criteria = {
            "min_price": 15.0,
            "min_avg_vol": 500000,
            "min_eps_yoy": 25.0,
            "min_rev_yoy": 20.0,
            "min_roe": 15.0,
            "min_volume_ratio": 1.2,
            "min_perf_26w": 10.0,
            "stage2_only": True,
            "allow_missing_fundamentals": False,
        }

        payload = build_tv_scan_payload(criteria, limit=100)

        # 1. Structure
        self.assertIn("filter", payload)
        self.assertIn("columns", payload)
        self.assertIn("sort", payload)
        self.assertEqual(payload.get("range"), [0, 100])

        filters = {f["left"]: f for f in payload["filter"]}

        # 2. Base US common stock filter
        self.assertIn("exchange", filters)
        self.assertEqual(filters["exchange"]["operation"], "in_range")
        self.assertEqual(set(filters["exchange"]["right"]), {"NASDAQ", "NYSE", "AMEX"})

        self.assertIn("type", filters)
        self.assertEqual(filters["type"]["right"], "stock")

        # 3. Numeric thresholds
        self.assertIn("close", filters)
        self.assertEqual(filters["close"]["right"], 15.0)

        self.assertIn("average_volume_30d_calc", filters)
        self.assertEqual(filters["average_volume_30d_calc"]["right"], 500000)

        # 4. Fundamental gates when allow_missing_fundamentals is False
        self.assertIn("earnings_per_share_diluted_yoy_growth_ttm", filters)
        self.assertEqual(filters["earnings_per_share_diluted_yoy_growth_ttm"]["right"], 25.0)

        self.assertIn("total_revenue_yoy_growth_fq", filters)
        self.assertEqual(filters["total_revenue_yoy_growth_fq"]["right"], 20.0)

        self.assertIn("return_on_equity", filters)
        self.assertEqual(filters["return_on_equity"]["right"], 15.0)

        # 5. When allow_missing_fundamentals is True, server-side fundamental filters are excluded
        loose_criteria = dict(criteria)
        loose_criteria["allow_missing_fundamentals"] = True
        loose_payload = build_tv_scan_payload(loose_criteria, limit=50)
        loose_filters = {f["left"]: f for f in loose_payload["filter"]}

        self.assertNotIn("earnings_per_share_diluted_yoy_growth_ttm", loose_filters)
        self.assertNotIn("total_revenue_yoy_growth_fq", loose_filters)
        self.assertNotIn("return_on_equity", loose_filters)

    def test_parse_tv_scan_response_safe_normalization(self):
        """Verifies parse_tv_scan_response handles valid data, null fields, and zero division."""
        from engine.market_scanner import parse_tv_scan_response

        mock_tv_response = {
            "totalCount": 6543,
            "data": [
                {
                    "s": "NASDAQ:NVDA",
                    "d": [
                        "NVDA", "NVIDIA Corp.", "NASDAQ", "Technology", "Semiconductors",
                        120.0, 3.5, 50000000, 45000000, 1.8,
                        110.0, 100.0, 90.0, 118.0, 115.0,
                        140.0, 40.0, 65.0, 180.0, 4.5,
                        3000000000000, 120.0, 110.0, 95.0, 45.0,
                        int((datetime.now(timezone.utc) + timedelta(days=5)).timestamp())  # 5 days away
                    ]
                },
                {
                    "s": "NYSE:NULLY",
                    "d": [
                        "NULLY", "Null Corp", "NYSE", None, None,
                        10.0, None, 0, 0, 0.0,
                        None, None, None, None, None,
                        None, None, None, None, None,
                        None, None, None, None, None,
                        None
                    ]
                }
            ]
        }

        columns = [
            "name", "description", "exchange", "sector", "industry",
            "close", "change", "volume", "average_volume_30d_calc", "relative_volume_10d_calc",
            "SMA50", "SMA150", "SMA200", "EMA10", "EMA21",
            "price_52_week_high", "price_52_week_low", "Perf.6M", "Perf.Y", "ATR",
            "market_cap_basic", "earnings_per_share_diluted_yoy_growth_ttm",
            "earnings_per_share_diluted_qoq_growth_fq", "total_revenue_yoy_growth_fq",
            "return_on_equity", "earnings_release_next_date"
        ]

        items, total_count = parse_tv_scan_response(mock_tv_response, columns, risk_dollar=500.0)

        self.assertEqual(total_count, 6543)
        self.assertEqual(len(items), 2)

        # Check NVDA
        nvda = items[0]
        self.assertEqual(nvda["symbol"], "NVDA")
        self.assertEqual(nvda["exchange"], "NASDAQ")
        self.assertEqual(nvda["close"], 120.0)
        self.assertAlmostEqual(nvda["pct_from_52w_high"], 14.29, places=2)
        self.assertAlmostEqual(nvda["pct_above_52w_low"], 200.0, places=2)
        self.assertAlmostEqual(nvda["atr_pct"], 3.75, places=2)
        self.assertGreaterEqual(nvda["rs_rating"], 80)
        self.assertEqual(nvda["suggested_shares"], int(500.0 / (2.0 * 4.5)))

        # Check NULLY zero-division & null safety
        nully = items[1]
        self.assertEqual(nully["symbol"], "NULLY")
        self.assertEqual(nully["close"], 10.0)
        self.assertEqual(nully["pct_from_52w_high"], 0.0)
        self.assertEqual(nully["pct_above_52w_low"], 0.0)
        self.assertEqual(nully["atr_pct"], 0.0)
        self.assertEqual(nully["suggested_shares"], 0)

    def test_evaluate_multi_frameworks_tier_and_v6_rules(self):
        """Verifies Weinstein (W), Deepvue (D), Minervini (M), CAN SLIM (C) tiers and V6 earnings risk."""
        from engine.market_scanner import evaluate_multi_frameworks

        today = date(2026, 11, 22)

        # 1. Perfect 4/4 Tier 1 candidate
        tier1_candidate = {
            "symbol": "NVDA",
            "close": 120.0,
            "avg_vol": 5_000_000,
            "volume_ratio_50": 1.8,
            "sma50": 105.0,
            "sma150": 95.0,
            "sma200": 85.0,
            "pct_from_52w_high": 8.0,   # <= 15% (C, M)
            "pct_above_52w_low": 120.0, # >= 30% (M)
            "perf_26w": 45.0,           # > 0 (W, M, C)
            "eps_yoy": 65.0,            # >= 25% (C), >= 20% (D)
            "eps_qoq": 40.0,
            "rev_yoy": 55.0,            # >= 25% (C), >= 20% (D)
            "roe": 35.0,                # >= 17% (C)
            "earnings_release_next_date": "2026-11-26", # 4 days away -> V6-Hard
        }

        res1 = evaluate_multi_frameworks(tier1_candidate, reference_date=today)
        self.assertEqual(res1["filter_count"], 4)
        self.assertEqual(res1["tier"], "Tier 1")
        self.assertEqual(set(res1["frameworks"]), {"W", "D", "M", "C"})
        self.assertEqual(res1["v6_risk"], "V6-Hard")

        # 2. Strict After-hours Deepvue volume check: rel_vol == 0 must fail D
        after_hours_candidate = dict(tier1_candidate)
        after_hours_candidate["volume_ratio_50"] = 0.0
        res_ah = evaluate_multi_frameworks(after_hours_candidate, reference_date=today)
        self.assertNotIn("D", res_ah["frameworks"])
        self.assertEqual(res_ah["filter_count"], 3)
        self.assertEqual(res_ah["tier"], "Tier 2")

        # 3. Minervini calibration: at 20% distance from 52W high, M passes (<= 25%) while C fails (<= 15%)
        calibrated_candidate = dict(tier1_candidate)
        calibrated_candidate["pct_from_52w_high"] = 20.0
        res_cal = evaluate_multi_frameworks(calibrated_candidate, reference_date=today)
        self.assertIn("M", res_cal["frameworks"])
        self.assertNotIn("C", res_cal["frameworks"])

        # 4. Both M and C fail when too far from 52W high (28%)
        lagging_candidate = dict(tier1_candidate)
        lagging_candidate["pct_from_52w_high"] = 28.0
        lagging_candidate["earnings_release_next_date"] = "2026-12-02" # 10 days away -> V6-Soft
        res_lag = evaluate_multi_frameworks(lagging_candidate, reference_date=today)
        self.assertNotIn("M", res_lag["frameworks"])
        self.assertNotIn("C", res_lag["frameworks"])
        self.assertEqual(res_lag["v6_risk"], "V6-Soft")

        # 5. Safe earnings far away -> Clear
        safe_candidate = dict(tier1_candidate)
        safe_candidate["earnings_release_next_date"] = "2027-01-15"
        res_safe = evaluate_multi_frameworks(safe_candidate, reference_date=today)
        self.assertEqual(res_safe["v6_risk"], "Clear")

    def test_all_us_local_filtering_and_cache_isolation(self):
        """Verifies min_rs_rating, max_pct_from_52w_high, and power_trend_only locally filter items and isolate cache keys."""
        import asyncio
        from engine.market_scanner import scan_all_us_stocks, filter_scanned_items, _SCAN_CACHE

        mock_items = [
            # A: Passes all (RS 85, 52W high offset 12%, Power Trend valid)
            {
                "symbol": "LEAD", "close": 100.0, "rs_rating": 85, "pct_from_52w_high": 12.0,
                "ema10": 95.0, "ema21": 90.0, "sma50": 80.0, "sma150": 70.0, "sma200": 60.0,
                "volume_ratio_50": 1.5, "eps_yoy": 30.0, "rev_yoy": 25.0, "roe": 20.0, "perf_26w": 20.0,
                "pct_above_52w_low": 50.0,
            },
            # B: Fails RS (RS 65 < 80)
            {
                "symbol": "LOWRS", "close": 50.0, "rs_rating": 65, "pct_from_52w_high": 8.0,
                "ema10": 48.0, "ema21": 45.0, "sma50": 40.0, "sma150": 35.0, "sma200": 30.0,
                "volume_ratio_50": 1.5, "eps_yoy": 30.0, "rev_yoy": 25.0, "roe": 20.0, "perf_26w": 20.0,
                "pct_above_52w_low": 50.0,
            },
            # C: Fails 52W High (22% > 15%) and Fails Power Trend (EMA10 < EMA21)
            {
                "symbol": "FAROFF", "close": 80.0, "rs_rating": 90, "pct_from_52w_high": 22.0,
                "ema10": 70.0, "ema21": 75.0, "sma50": 60.0, "sma150": 55.0, "sma200": 50.0,
                "volume_ratio_50": 1.5, "eps_yoy": 30.0, "rev_yoy": 25.0, "roe": 20.0, "perf_26w": 20.0,
                "pct_above_52w_low": 50.0,
            },
        ]

        # 1. Pure function filter_scanned_items verification
        # RS threshold
        filtered_rs = filter_scanned_items(mock_items, {"min_rs_rating": 80})
        self.assertEqual([it["symbol"] for it in filtered_rs], ["LEAD", "FAROFF"])

        # 52W High threshold
        filtered_high = filter_scanned_items(mock_items, {"max_pct_from_52w_high": 15.0})
        self.assertEqual([it["symbol"] for it in filtered_high], ["LEAD", "LOWRS"])

        # Power Trend only
        filtered_pt = filter_scanned_items(mock_items, {"power_trend_only": True})
        self.assertEqual([it["symbol"] for it in filtered_pt], ["LEAD", "LOWRS"])

        # Combined: only LEAD passes
        filtered_all = filter_scanned_items(mock_items, {
            "min_rs_rating": 80,
            "max_pct_from_52w_high": 15.0,
            "power_trend_only": True,
        })
        self.assertEqual([it["symbol"] for it in filtered_all], ["LEAD"])

        # 2. Cache isolation in scan_all_us_stocks
        _SCAN_CACHE.clear()
        mock_raw_json = {
            "totalCount": 3,
            "data": [
                {
                    "s": "NASDAQ:LEAD",
                    "d": ["LEAD", "Leader", "NASDAQ", "Tech", "Semis", 100.0, 2.0, 1000000, 800000, 1.5,
                           80.0, 70.0, 60.0, 95.0, 90.0, 113.6, 50.0, 80.0, 150.0, 2.0,
                           1000000000, 30.0, 25.0, 25.0, 20.0, None]
                },
                {
                    "s": "NASDAQ:LOWRS",
                    "d": ["LOWRS", "Low RS", "NASDAQ", "Tech", "Semis", 50.0, 1.0, 1000000, 800000, 1.5,
                           40.0, 35.0, 30.0, 48.0, 45.0, 54.3, 25.0, -10.0, -10.0, 1.5,
                           500000000, 30.0, 25.0, 25.0, 20.0, None]
                }
            ]
        }

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = lambda: None
            mock_resp.json = lambda: mock_raw_json
            mock_post.return_value = mock_resp

            # Call 1: Loose filter (RS 0)
            res1, _ = asyncio.run(scan_all_us_stocks(criteria={"min_rs_rating": 0}, limit=10))
            self.assertEqual(len(res1), 2)

            # Call 2: Strict filter (RS 80) -> MUST NOT return cached res1
            res2, _ = asyncio.run(scan_all_us_stocks(criteria={"min_rs_rating": 80}, limit=10))
            self.assertEqual(len(res2), 1)
            self.assertEqual(res2[0]["symbol"], "LEAD")

    def test_all_us_remote_error_warning_and_http_200(self):
        """Verifies remote httpx timeout/error produces warning in /api/screener/scan response with HTTP 200."""
        import httpx
        from server import app

        client = TestClient(app)

        with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectTimeout("Connection timed out")):
            response = client.post("/api/screener/scan", json={"universe": "all_us"})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["universe"], "all_us")
            self.assertEqual(data["items"], [])
            self.assertIn("warning", data)
            self.assertTrue(len(data["warning"]) > 0)
            self.assertIn("全美股遠端掃描連線逾時或異常", data["warning"])

    def test_sqlite_screener_snapshots_and_streak_tracking(self):
        """Verifies SQLite snapshot table creates, records, and calculates consecutive streak_days and filter_delta."""
        from engine.storage import Storage

        with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
            storage = Storage(db_path=tmp.name)

            # Day 1: NVDA (filter_count=3, Tier 2), TSLA (filter_count=1, Watch)
            day1_items = [
                {"symbol": "NVDA", "filter_count": 3, "frameworks": ["W", "M", "C"], "tier": "Tier 2", "close": 110.0},
                {"symbol": "TSLA", "filter_count": 1, "frameworks": ["W"], "tier": "Watch", "close": 200.0},
            ]
            storage.record_screener_snapshot("2026-11-20", day1_items)

            # Check Day 1 streaks
            streaks_d1 = storage.get_screener_streaks(["NVDA", "TSLA"], current_date="2026-11-20")
            self.assertEqual(streaks_d1["NVDA"]["streak_days"], 1)
            self.assertEqual(streaks_d1["NVDA"]["filter_delta"], "NEW")
            self.assertEqual(streaks_d1["TSLA"]["streak_days"], 0) # filter_count < 2
            self.assertEqual(streaks_d1["TSLA"]["filter_delta"], "NEW")

            # Day 2: NVDA advances (filter_count=4, Tier 1), AMD first appearance (filter_count=2, Tier 3)
            day2_items = [
                {"symbol": "NVDA", "filter_count": 4, "frameworks": ["W", "D", "M", "C"], "tier": "Tier 1", "close": 115.0},
                {"symbol": "AMD", "filter_count": 2, "frameworks": ["W", "M"], "tier": "Tier 3", "close": 140.0},
            ]
            storage.record_screener_snapshot("2026-11-21", day2_items)

            streaks_d2 = storage.get_screener_streaks(["NVDA", "AMD", "TSLA"], current_date="2026-11-21")
            self.assertEqual(streaks_d2["NVDA"]["streak_days"], 2)
            self.assertEqual(streaks_d2["NVDA"]["filter_delta"], "+1")  # 4 - 3 = +1
            self.assertEqual(streaks_d2["AMD"]["streak_days"], 1)
            self.assertEqual(streaks_d2["AMD"]["filter_delta"], "NEW")

            # Day 3: NVDA drops to filter_count=2
            day3_items = [
                {"symbol": "NVDA", "filter_count": 2, "frameworks": ["W", "M"], "tier": "Tier 3", "close": 112.0},
            ]
            storage.record_screener_snapshot("2026-11-22", day3_items)

            streaks_d3 = storage.get_screener_streaks(["NVDA"], current_date="2026-11-22")
            self.assertEqual(streaks_d3["NVDA"]["streak_days"], 3)     # Still >= 2 for 3 consecutive days
            self.assertEqual(streaks_d3["NVDA"]["filter_delta"], "-2") # 2 - 4 = -2


if __name__ == "__main__":
    unittest.main()

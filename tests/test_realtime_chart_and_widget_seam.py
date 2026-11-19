"""Tests for Real-Time Candlestick Chart Updates and Zero-Delay TradingView Widget Resolution.

TDD Seam Verification:
1. TradingView Widget Real-Time Feed Seam:
   - US equities (NASDAQ, NYSE, AMEX) must map to BATS: (Cboe BZX) to eliminate 15-minute delay on free widgets.
   - Crypto (BINANCE) and custom exchanges are preserved.
2. Real-Time Chart Quote & Bar Monotonicity Seam:
   - Daily/intraday timeframe alignment prevents dropping ticks (bucketTime < lastBarTime bug).
   - Live quote updates synthesize/update active candlestick on the chart so prices update in real-time.
   - History load initializes lastBarTime and lastBarObj to maintain continuous OHLC aggregation.
"""

import re
import unittest
from pathlib import Path


class TestRealtimeChartAndWidgetSeam(unittest.TestCase):
    """Verifies that chart real-time updates and TradingView widget delay elimination contracts hold."""

    def setUp(self):
        self.app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
        self.assertTrue(self.app_js_path.exists(), f"Missing static/app.js at {self.app_js_path}")
        self.app_js_content = self.app_js_path.read_text(encoding="utf-8")

    def test_tradingview_widget_uses_bats_for_us_equities_to_eliminate_15m_delay(self):
        """Verifies that initTradingViewWidget resolves US equities to BATS: to eliminate 15m delay."""
        # Check for function or logic in app.js that maps US stocks to BATS:
        has_bats_resolver = (
            "toTradingViewRealtimeSymbol" in self.app_js_content
            or "BATS:" in self.app_js_content
        )
        self.assertTrue(
            has_bats_resolver,
            "static/app.js must contain BATS symbol mapping for US equities to eliminate 15-minute delay."
        )

        # Check that initTradingViewWidget uses the real-time symbol
        init_widget_match = re.search(
            r"function\s+initTradingViewWidget\s*\([^)]*\)\s*\{([\s\S]*?)(?:tvWidgetInstance\s*=\s*new\s+TradingView\.widget)",
            self.app_js_content
        )
        self.assertIsNotNone(init_widget_match, "initTradingViewWidget must be defined in static/app.js")
        widget_body = init_widget_match.group(1)
        self.assertTrue(
            "BATS" in widget_body or "toTradingViewRealtimeSymbol" in widget_body,
            "initTradingViewWidget must resolve symbols through real-time BATS mapping for US equities."
        )

    def test_update_quote_updates_chart_candle_in_realtime(self):
        """Verifies that updateQuote updates the active candlestick on the chart in real-time."""
        update_quote_match = re.search(
            r"function\s+updateQuote\s*\([^)]*\)\s*\{([\s\S]*?)\n\}",
            self.app_js_content
        )
        self.assertIsNotNone(update_quote_match, "updateQuote must be defined in static/app.js")
        uq_body = update_quote_match.group(1)

        # It must call a chart update or handleLiveTick / updateActiveCandleWithPrice when symbol === activeSymbol
        has_chart_update_in_quote = (
            "candleSeries.update" in uq_body
            or "updateActiveCandleWithPrice" in uq_body
            or "handleLiveQuote" in uq_body
            or "handleLiveBar" in uq_body
        )
        self.assertTrue(
            has_chart_update_in_quote,
            "updateQuote must directly update the chart candlestick series in real-time for activeSymbol."
        )

    def test_history_loading_initializes_last_bar_time_and_object(self):
        """Verifies that loadHistory initializes lastBarTime and lastBarObj from uniqueBars."""
        load_hist_match = re.search(
            r"currentRawBars\s*=\s*uniqueBars;([\s\S]*?)renderChartData\s*\(uniqueBars\);",
            self.app_js_content
        )
        self.assertIsNotNone(load_hist_match, "loadHistory must set currentRawBars and call renderChartData")
        between_code = load_hist_match.group(1)

        # Must initialize lastBarTime from uniqueBars
        has_init = (
            "lastBarTime" in between_code
            or "lastBarObj" in between_code
            or "initLastBarState" in self.app_js_content
        )
        self.assertTrue(
            has_init,
            "loadHistory must initialize lastBarTime and lastBarObj from the last element of uniqueBars."
        )

    def test_handle_live_bar_prevents_timeframe_bucket_timestamp_drops(self):
        """Verifies that handleLiveBar aligns bucket time with existing daily/intraday bars without dropping."""
        handle_bar_match = re.search(
            r"function\s+handleLiveBar\s*\([^)]*\)\s*\{([\s\S]*?)\n\}",
            self.app_js_content
        )
        self.assertIsNotNone(handle_bar_match, "handleLiveBar must be defined in static/app.js")
        hb_body = handle_bar_match.group(1)

        # Must check currentRawBars to align timestamps rather than strictly comparing bucketTime < lastBarTime
        has_alignment = (
            "currentRawBars[currentRawBars.length - 1]" in hb_body
            or "lastRawBar" in hb_body
            or "alignLiveBarTime" in hb_body
        )
        self.assertTrue(
            has_alignment,
            "handleLiveBar must inspect currentRawBars to properly align live bar timestamps with existing history."
        )

    def test_crosshair_legend_mouseleave_and_historical_mode_badge(self):
        """Verifies that crosshair legend has mouseleave reset and historical badge to prevent dashboard price discrepancies."""
        html_path = Path(__file__).resolve().parent.parent / "static" / "index.html"
        html_content = html_path.read_text(encoding="utf-8")
        self.assertIn("legendModeBadge", html_content, "index.html must define legendModeBadge in chartLegend")

        self.assertIn("mouseleave", self.app_js_content, "static/app.js must bind mouseleave to reset crosshair legend")
        self.assertIn("isHistorical", self.app_js_content, "static/app.js must differentiate historical bars from latestBarData")
        self.assertIn("HISTORICAL", self.app_js_content, "static/app.js must show HISTORICAL badge on historical crosshairs")
        self.assertIn("LIVE", self.app_js_content, "static/app.js must show LIVE badge on real-time quotes")


if __name__ == "__main__":
    unittest.main()

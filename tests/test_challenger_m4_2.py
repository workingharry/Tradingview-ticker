"""Empirical Adversarial Verification Suite for Milestone M4: Full Pipeline Invariants.

Executed by Challenger M4-2 to rigorously verify:
1. Invariant 1: OHLC bounds (low <= open <= high, low <= close <= high, low <= high, positive prices).
2. Invariant 2: Positive volume deltas (volume >= 0.0 always, resilient to 24h rolling reset).
3. Invariant 3: Timestamp quantization (time % 60 == 0, strictly monotonic, zero duplicates post-seed).
4. Invariant 4: Protocol framing survival (Unicode, emojis, embedded ~m~, fragmentation, corruption resync).
5. Invariant 5: Float sanitization (zero NaN, zero Infinity, zero negative/zero prices).
"""

import unittest
import math
import random
import json
import asyncio
from typing import List, Dict, Any
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

import server
from server import app, manager, on_quote_update_handler, on_bar_update_handler
from engine.protocol import (
    parse_tradingview_packets,
    parse_tradingview_packets_with_remainder,
    encode_tradingview_message,
    encode_heartbeat_response,
    StreamingTradingViewParser,
)
from engine.aggregator import TickToBarAggregator, CandlestickBar
from engine.client import TradingViewAsyncClient


class TestOHLCBoundsInvariant(unittest.TestCase):
    """Adversarial testing of Invariant 1: OHLC mathematical bounds."""

    def setUp(self):
        self.aggregator = TickToBarAggregator(timeframe_seconds=60)

    def _assert_ohlc_invariants(self, bar: CandlestickBar, msg: str = ""):
        self.assertIsNotNone(bar, f"Bar should not be None: {msg}")
        self.assertLessEqual(bar.low, bar.open, f"Invariant violated: low <= open failed ({bar.low} > {bar.open}) {msg}")
        self.assertLessEqual(bar.low, bar.close, f"Invariant violated: low <= close failed ({bar.low} > {bar.close}) {msg}")
        self.assertLessEqual(bar.low, bar.high, f"Invariant violated: low <= high failed ({bar.low} > {bar.high}) {msg}")
        self.assertGreaterEqual(bar.high, bar.open, f"Invariant violated: high >= open failed ({bar.high} < {bar.open}) {msg}")
        self.assertGreaterEqual(bar.high, bar.close, f"Invariant violated: high >= close failed ({bar.high} < {bar.close}) {msg}")
        self.assertGreater(bar.open, 0.0, f"Invariant violated: open must be positive ({bar.open} <= 0) {msg}")
        self.assertGreater(bar.high, 0.0, f"Invariant violated: high must be positive ({bar.high} <= 0) {msg}")
        self.assertGreater(bar.low, 0.0, f"Invariant violated: low must be positive ({bar.low} <= 0) {msg}")
        self.assertGreater(bar.close, 0.0, f"Invariant violated: close must be positive ({bar.close} <= 0) {msg}")

    def test_single_tick_window_invariants(self):
        """Verifies that a single initial tick satisfies open == high == low == close."""
        bar = self.aggregator.process_tick("BTC", price=65432.10, cumulative_volume=100.0, tick_time=1731234010)
        self.assertIsNotNone(bar)
        self.assertEqual(bar.open, 65432.10)
        self.assertEqual(bar.high, 65432.10)
        self.assertEqual(bar.low, 65432.10)
        self.assertEqual(bar.close, 65432.10)
        self._assert_ohlc_invariants(bar, "single tick")

    def test_monotonic_increasing_and_decreasing_series(self):
        """Verifies OHLC bounds under strictly monotonic price series."""
        # Strictly rising prices
        prices_up = [100.0, 105.0, 110.0, 115.0, 120.0]
        for p in prices_up:
            bar = self.aggregator.process_tick("UP", price=p, tick_time=1731234020)
            self._assert_ohlc_invariants(bar, f"rising price {p}")
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.high, 120.0)
        self.assertEqual(bar.low, 100.0)
        self.assertEqual(bar.close, 120.0)

        # Strictly falling prices
        prices_down = [200.0, 190.0, 180.0, 170.0, 160.0]
        for p in prices_down:
            bar = self.aggregator.process_tick("DOWN", price=p, tick_time=1731234020)
            self._assert_ohlc_invariants(bar, f"falling price {p}")
        self.assertEqual(bar.open, 200.0)
        self.assertEqual(bar.high, 200.0)
        self.assertEqual(bar.low, 160.0)
        self.assertEqual(bar.close, 160.0)

    def test_extreme_volatility_spikes_and_dips(self):
        """Verifies that flash crashes and massive price spikes preserve OHLC bounds."""
        volatile_prices = [50000.0, 1.0, 100000.0, 0.01, 75000.0, 50000.0]
        for p in volatile_prices:
            bar = self.aggregator.process_tick("VOLATILE", price=p, tick_time=1731234030)
            self._assert_ohlc_invariants(bar, f"volatile price {p}")
        self.assertEqual(bar.open, 50000.0)
        self.assertEqual(bar.high, 100000.0)
        self.assertEqual(bar.low, 0.01)
        self.assertEqual(bar.close, 50000.0)

    def test_fuzz_random_walk_ohlc_invariants(self):
        """Property-based stress: 1,000 random walk ticks across 10 1-minute windows."""
        random.seed(42)
        base_time = 1731234000
        current_price = 50000.0

        for i in range(1000):
            # Price delta between -5% and +5%
            pct_change = random.uniform(-0.05, 0.05)
            current_price = max(1.0, current_price * (1.0 + pct_change))
            # Distribute across 10 windows (each 60s)
            tick_time = base_time + (i % 600)
            vol = 1000.0 + i * 2.5

            bar = self.aggregator.process_tick("FUZZ", price=current_price, cumulative_volume=vol, tick_time=tick_time)
            if bar is not None:
                self._assert_ohlc_invariants(bar, f"fuzz step {i}")

        # Check all historical bars
        for bar in self.aggregator.bar_history.get("FUZZ", []):
            self._assert_ohlc_invariants(bar, "fuzz historical bar")

    def test_rest_history_ohlc_invariants(self):
        """Verifies that all bars returned by GET /api/history strictly satisfy OHLC invariants."""
        mock_seed = [
            {"time": 1731234000 + i * 60, "open": 68000.0 + i, "high": 68050.0 + i, "low": 67950.0 + i, "close": 68020.0 + i, "volume": 10.0 + i}
            for i in range(15)
        ]
        with patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=mock_seed):
            with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock):
                with patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock):
                    with TestClient(app) as client:
                        resp = client.get("/api/history", params={"symbol": "BINANCE:BTCUSDT"})
                        self.assertEqual(resp.status_code, 200)
                        bars = resp.json().get("bars", [])
                        self.assertGreaterEqual(len(bars), 10)
                        for b in bars:
                            bar = CandlestickBar(
                                time=b["time"], open=b["open"], high=b["high"], low=b["low"], close=b["close"], volume=b["volume"]
                            )
                            self._assert_ohlc_invariants(bar, f"REST bar at {b['time']}")


class TestPositiveVolumeDeltaInvariant(unittest.TestCase):
    """Adversarial testing of Invariant 2: Positive volume deltas and rolling 24h reset resilience."""

    def setUp(self):
        self.aggregator = TickToBarAggregator(timeframe_seconds=60)

    def test_monotonically_increasing_cumulative_volume(self):
        """Verifies delta calculation during standard cumulative volume increase."""
        vols = [100.0, 150.0, 180.5, 200.0]
        deltas = []
        for i, v in enumerate(vols):
            bar = self.aggregator.process_tick("BTC", price=50000.0, cumulative_volume=v, tick_time=1731234000 + i * 5)
            self.assertGreaterEqual(bar.volume, 0.0)

        # First tick had no prev_vol so delta=0, then +50, +30.5, +19.5 => total 100.0
        self.assertEqual(self.aggregator.get_current_bar("BTC").volume, 100.0)

    def test_rolling_24h_volume_reset_handling(self):
        """Verifies that 24h rolling volume wrap-around/reset NEVER produces negative volume."""
        # Symbol trades up to 500,000 volume, then TradingView rolls over to 25.0
        self.aggregator.process_tick("BTC", price=50000.0, cumulative_volume=500000.0, tick_time=1731234010)
        self.aggregator.process_tick("BTC", price=50010.0, cumulative_volume=500100.0, tick_time=1731234020)
        bar_before_reset = self.aggregator.get_current_bar("BTC")
        self.assertEqual(bar_before_reset.volume, 100.0)

        # 24h volume reset: cumulative volume drops to 25.0
        bar_at_reset = self.aggregator.process_tick("BTC", price=50020.0, cumulative_volume=25.0, tick_time=1731234030)
        self.assertGreaterEqual(bar_at_reset.volume, 0.0)
        # Volume delta must be 0.0 (ignored reset step), so volume remains 100.0
        self.assertEqual(bar_at_reset.volume, 100.0)

        # Next normal tick after reset: cumulative volume increases to 40.0 (+15.0)
        bar_after_reset = self.aggregator.process_tick("BTC", price=50030.0, cumulative_volume=40.0, tick_time=1731234040)
        self.assertEqual(bar_after_reset.volume, 115.0)

    def test_multiple_rapid_volume_resets_and_drops(self):
        """Stress test with chaotic cumulative volume changes (resets, drops, surges)."""
        chaotic_volumes = [1000.0, 50.0, 100.0, 10.0, 20.0, 5.0, 500.0, 0.0, 100.0]
        for i, cv in enumerate(chaotic_volumes):
            bar = self.aggregator.process_tick("CHAOS", price=100.0, cumulative_volume=cv, tick_time=1731234000 + i * 2)
            self.assertGreaterEqual(bar.volume, 0.0, f"Volume became negative ({bar.volume}) at step {i} with cv={cv}")

    def test_none_zero_and_negative_volume_ticks(self):
        """Verifies that None, 0.0, and negative cumulative volumes are safely handled with 0 delta."""
        self.aggregator.process_tick("SAFE", price=100.0, cumulative_volume=50.0, tick_time=1731234000)
        # Pass negative volume
        bar1 = self.aggregator.process_tick("SAFE", price=101.0, cumulative_volume=-20.0, tick_time=1731234010)
        self.assertEqual(bar1.volume, 0.0)
        # Pass None volume
        bar2 = self.aggregator.process_tick("SAFE", price=102.0, cumulative_volume=None, tick_time=1731234020)
        self.assertEqual(bar2.volume, 0.0)
        # Pass 0.0 volume
        bar3 = self.aggregator.process_tick("SAFE", price=103.0, cumulative_volume=0.0, tick_time=1731234030)
        self.assertEqual(bar3.volume, 0.0)

    def test_historical_bars_negative_volume_sanitization(self):
        """Verifies that seed_history clamps hostile negative volumes to 0.0."""
        hostile_seed = [
            {"time": 1731234000, "open": 100, "high": 110, "low": 90, "close": 105, "volume": -50.0},
            {"time": 1731234060, "open": 105, "high": 115, "low": 95, "close": 110, "volume": -100.0},
        ]
        self.aggregator.seed_history("SEED_TEST", hostile_seed)
        history = self.aggregator.get_history("SEED_TEST")
        self.assertEqual(len(history), 2)
        for b in history:
            self.assertGreaterEqual(b["volume"], 0.0, f"Historical bar at {b['time']} has negative volume: {b['volume']}")


class TestTimestampQuantizationAndMonotonicity(unittest.TestCase):
    """Adversarial testing of Invariant 3: Timestamp quantization and monotonicity."""

    def setUp(self):
        self.aggregator = TickToBarAggregator(timeframe_seconds=60)

    def test_timestamp_quantization_modulo_60(self):
        """Verifies that all bucket times satisfy time % 60 == 0."""
        seconds_offsets = [0, 1, 15, 30, 45, 58, 59]
        base = 1731234000
        for off in seconds_offsets:
            bar = self.aggregator.process_tick(f"SYM_{off}", price=100.0, tick_time=base + off)
            self.assertEqual(bar.time % 60, 0, f"Timestamp {bar.time} for offset {off} not quantized to 60s")
            self.assertEqual(bar.time, base)

    def test_millisecond_timestamp_normalization(self):
        """Verifies that millisecond timestamps (> 10,000,000,000) are cleanly converted to seconds."""
        ms_time = 1731234059123  # Millisecond timestamp
        bar = self.aggregator.process_tick("MS_SYM", price=100.0, tick_time=ms_time)
        self.assertEqual(bar.time, 1731234000)
        self.assertEqual(bar.time % 60, 0)

    def test_history_seed_to_live_transition_zero_duplicates(self):
        """Verifies zero duplicate timestamps when transitioning from seeded history to live ticks."""
        seed_bars = [
            {"time": 1731234000, "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 10.0},
            {"time": 1731234060, "open": 102.0, "high": 108.0, "low": 100.0, "close": 107.0, "volume": 12.0},
            {"time": 1731234120, "open": 107.0, "high": 110.0, "low": 104.0, "close": 108.0, "volume": 15.0},
        ]
        self.aggregator.seed_history("BTC", seed_bars)

        # 1. Immediate get_history has no duplicates
        history1 = self.aggregator.get_history("BTC")
        timestamps1 = [b["time"] for b in history1]
        self.assertEqual(len(timestamps1), len(set(timestamps1)), "Duplicates found immediately after seed!")
        self.assertEqual(timestamps1, [1731234000, 1731234060, 1731234120])

        # 2. Live tick arrives within the active seeded window (1731234120..1731234179)
        self.aggregator.process_tick("BTC", price=109.0, cumulative_volume=100.0, tick_time=1731234145)
        history2 = self.aggregator.get_history("BTC")
        timestamps2 = [b["time"] for b in history2]
        self.assertEqual(len(timestamps2), len(set(timestamps2)), "Duplicate timestamps after tick in active window!")
        self.assertEqual(timestamps2, [1731234000, 1731234060, 1731234120])
        self.assertEqual(history2[-1]["close"], 109.0)

        # 3. Next live tick arrives in subsequent window (1731234180..1731234239)
        self.aggregator.process_tick("BTC", price=111.0, cumulative_volume=120.0, tick_time=1731234190)
        history3 = self.aggregator.get_history("BTC")
        timestamps3 = [b["time"] for b in history3]
        self.assertEqual(len(timestamps3), len(set(timestamps3)), "Duplicate timestamps after window rollover!")
        self.assertEqual(timestamps3, [1731234000, 1731234060, 1731234120, 1731234180])

        # 4. Strict monotonic increase verification
        for i in range(len(timestamps3) - 1):
            self.assertLess(timestamps3[i], timestamps3[i + 1])
            self.assertEqual(timestamps3[i + 1] - timestamps3[i], 60)

    def test_stale_and_out_of_order_tick_rejection(self):
        """Verifies that stale historical ticks from earlier windows are dropped without corrupting history."""
        # Current active window is 1731234180
        self.aggregator.process_tick("BTC", price=100.0, tick_time=1731234185)

        # Stale tick arrives with timestamp belonging to previous window (1731234060)
        stale_bar = self.aggregator.process_tick("BTC", price=999.0, tick_time=1731234065)
        self.assertEqual(stale_bar.time, 1731234180)
        self.assertEqual(stale_bar.close, 100.0)  # Unaffected by 999.0

        history = self.aggregator.get_history("BTC")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["time"], 1731234180)
        self.assertEqual(history[0]["close"], 100.0)


class TestProtocolFramingSurvival(unittest.TestCase):
    """Adversarial testing of Invariant 4: Protocol framing survival across Unicode, emojis, embedded tokens."""

    def test_unicode_and_japanese_payload_roundtrip(self):
        """Verifies that Japanese and Unicode strings encode and parse with exact character preservation."""
        payload_dict = {
            "m": "qsd",
            "p": ["cs_test", {"n": "BINANCE:BTCUSDT", "v": {"name": "ビットコイン", "city": "東京", "cyrillic": "Биткойн"}}]
        }
        encoded = encode_tradingview_message(payload_dict)
        packets = parse_tradingview_packets(encoded)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "data")
        parsed = packets[0]["data"]
        self.assertEqual(parsed["p"][1]["v"]["name"], "ビットコイン")
        self.assertEqual(parsed["p"][1]["v"]["city"], "東京")
        self.assertEqual(parsed["p"][1]["v"]["cyrillic"], "Биткойн")

    def test_emoji_and_multibyte_characters(self):
        """Verifies framing survival with emojis and surrogate pairs (🚀, 📈, 🔥, 💎)."""
        payload_dict = {
            "m": "alert",
            "p": ["🚀 To the moon! 📈 Bullish 🔥 Diamond hands 💎"]
        }
        encoded = encode_tradingview_message(payload_dict)
        packets = parse_tradingview_packets(encoded)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["data"]["p"][0], "🚀 To the moon! 📈 Bullish 🔥 Diamond hands 💎")

    def test_embedded_delimiters_in_json_strings(self):
        """Verifies that literal '~m~123~m~' inside JSON string values does not prematurely terminate framing."""
        payload_dict = {
            "m": "qsd",
            "p": ["session", {"desc": "~m~999~m~nested_fake_header", "code": "~h~1"}]
        }
        encoded = encode_tradingview_message(payload_dict)
        packets = parse_tradingview_packets(encoded)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["data"]["p"][1]["desc"], "~m~999~m~nested_fake_header")
        self.assertEqual(packets[0]["data"]["p"][1]["code"], "~h~1")

    def test_streaming_parser_arbitrary_chunk_fragmentation(self):
        """Stress-tests StreamingTradingViewParser by feeding bytes 1 char at a time across delimiters and JSON."""
        msg1 = encode_tradingview_message({"m": "quote_1", "p": [1, 2, 3]})
        msg2 = encode_heartbeat_response("~h~42")
        msg3 = encode_tradingview_message({"m": "quote_2", "p": ["東京", "🚀"]})
        full_stream = msg1 + msg2 + msg3

        # Test single-char chunk streaming
        parser = StreamingTradingViewParser()
        assembled_packets = []
        for char in full_stream:
            pkts = parser.feed(char)
            assembled_packets.extend(pkts)

        self.assertEqual(len(assembled_packets), 3)
        self.assertEqual(assembled_packets[0]["type"], "data")
        self.assertEqual(assembled_packets[0]["data"]["m"], "quote_1")
        self.assertEqual(assembled_packets[1]["type"], "heartbeat")
        self.assertEqual(assembled_packets[1]["raw"], "~h~42")
        self.assertEqual(assembled_packets[2]["type"], "data")
        self.assertEqual(assembled_packets[2]["data"]["m"], "quote_2")
        self.assertEqual(parser.buffered_length, 0)

    def test_corrupted_framing_and_byte_resynchronization(self):
        """Verifies forward-scanning resynchronization when corrupted noise precedes or sandwiches packets."""
        valid_msg1 = encode_tradingview_message({"m": "valid_1"})
        valid_msg2 = encode_tradingview_message({"m": "valid_2"})

        # Corrupted noise at front, between packets, and at tail
        noisy_stream = "MALFORMED_NOISE_123" + valid_msg1 + "!@#$%^&*CORRUPT_BYTES~m~not_a_num~m~" + valid_msg2 + "TRAILING_JUNK"
        packets = parse_tradingview_packets(noisy_stream)

        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0]["data"]["m"], "valid_1")
        self.assertEqual(packets[1]["data"]["m"], "valid_2")

    def test_high_volume_concatenated_packets(self):
        """Verifies batched parsing of 100 concatenated packets in a single network frame."""
        batch_stream = "".join(encode_tradingview_message({"m": f"msg_{i}", "p": [i]}) for i in range(100))
        packets = parse_tradingview_packets(batch_stream)
        self.assertEqual(len(packets), 100)
        for i, pkt in enumerate(packets):
            self.assertEqual(pkt["data"]["m"], f"msg_{i}")
            self.assertEqual(pkt["data"]["p"], [i])


class TestFloatSanitization(unittest.TestCase):
    """Adversarial testing of Invariant 5: Float sanitization (zero NaN, zero Infinity, zero negative prices)."""

    def setUp(self):
        self.aggregator = TickToBarAggregator(timeframe_seconds=60)

    def test_nan_price_rejection(self):
        """Verifies that float('nan') and string 'NaN' ticks are rejected and never enter bars."""
        # Initial valid bar
        self.aggregator.process_tick("BTC", price=50000.0, tick_time=1731234000)

        # Feed NaN
        bar_nan1 = self.aggregator.process_tick("BTC", price=float("nan"), tick_time=1731234010)
        self.assertFalse(math.isnan(bar_nan1.close))
        self.assertEqual(bar_nan1.close, 50000.0)

        bar_nan2 = self.aggregator.process_tick("BTC", price="NaN", tick_time=1731234020)
        self.assertFalse(math.isnan(bar_nan2.close))
        self.assertEqual(bar_nan2.close, 50000.0)

    def test_infinity_price_rejection(self):
        """Verifies that Infinity and -Infinity ticks are rejected."""
        self.aggregator.process_tick("BTC", price=50000.0, tick_time=1731234000)

        bar_inf = self.aggregator.process_tick("BTC", price=float("inf"), tick_time=1731234010)
        self.assertFalse(math.isinf(bar_inf.high))
        self.assertEqual(bar_inf.high, 50000.0)

        bar_ninf = self.aggregator.process_tick("BTC", price=float("-inf"), tick_time=1731234020)
        self.assertFalse(math.isinf(bar_ninf.low))
        self.assertEqual(bar_ninf.low, 50000.0)

    def test_negative_and_zero_price_rejection(self):
        """Verifies that prices <= 0.0 are strictly rejected."""
        self.aggregator.process_tick("BTC", price=50000.0, tick_time=1731234000)

        # Negative price
        bar_neg = self.aggregator.process_tick("BTC", price=-1000.0, tick_time=1731234010)
        self.assertEqual(bar_neg.low, 50000.0)
        self.assertEqual(bar_neg.close, 50000.0)

        # Zero price
        bar_zero = self.aggregator.process_tick("BTC", price=0.0, tick_time=1731234020)
        self.assertEqual(bar_zero.close, 50000.0)

        # Negative zero price
        bar_nzero = self.aggregator.process_tick("BTC", price=-0.0, tick_time=1731234030)
        self.assertEqual(bar_nzero.close, 50000.0)

    def test_non_numeric_price_types_handled_gracefully(self):
        """Verifies that garbage types (strings, lists, objects) do not raise unhandled exceptions."""
        self.aggregator.process_tick("BTC", price=50000.0, tick_time=1731234000)

        garbage_inputs = ["invalid_price", [], {}, object(), None]
        for g in garbage_inputs:
            bar = self.aggregator.process_tick("BTC", price=g, tick_time=1731234010)
            self.assertEqual(bar.close, 50000.0)

    def test_client_handle_packet_float_sanitization(self):
        """Verifies that TradingViewAsyncClient._handle_packet sanitizes NaN and Infinity from quotes."""
        client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT"])
        packet = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": [
                    "qs_session",
                    {
                        "n": "BINANCE:BTCUSDT",
                        "v": {
                            "lp": "NaN",
                            "volume": "Infinity",
                            "ch": "-Infinity",
                            "chp": "invalid",
                        }
                    }
                ]
            }
        }
        client._handle_packet(packet)
        st = client.states["BINANCE:BTCUSDT"]
        self.assertEqual(st["price"], 0.0)
        self.assertEqual(st["volume"], 0.0)
        self.assertEqual(st["change"], 0.0)
        self.assertEqual(st["changePercentage"], 0.0)

    def test_extreme_float_magnitudes(self):
        """Verifies numerical stability for extreme market prices (1e12 and 1e-6)."""
        bar1 = self.aggregator.process_tick("MEGA", price=1e12, tick_time=1731234000)
        self.assertEqual(bar1.open, 1e12)
        self.assertEqual(bar1.high, 1e12)

        bar2 = self.aggregator.process_tick("MICRO", price=1e-6, tick_time=1731234000)
        self.assertEqual(bar2.open, 1e-6)
        self.assertEqual(bar2.low, 1e-6)


class TestFullPipelineEndToEndInvariants(unittest.TestCase):
    """End-to-End invariant challenge: WebSocket tick injection through REST and live frames."""

    def setUp(self):
        self.mock_fetch = patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=[]).start()
        self.mock_start = patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock).start()
        self.mock_stop = patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock).start()
        self.mock_storage = patch("engine.storage.Storage.get_bars", return_value=[]).start()
        self.mock_save = patch("engine.storage.Storage.save_bars", return_value=0).start()

    def tearDown(self):
        patch.stopall()

    def test_e2e_tick_injection_to_websocket_bar_stream_invariants(self):
        """Verifies that ticks fed to server.on_quote_update_handler preserve all 5 invariants in WebSocket broadcasts."""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/live") as ws:
                status_msg = ws.receive_json()
                self.assertEqual(status_msg["type"], "status")

                # Inject 5 consecutive ticks with volume increase
                test_ticks = [
                    {"price": 68000.0, "volume": 100.0, "time": 1731234010},
                    {"price": 68050.0, "volume": 150.0, "time": 1731234020},
                    {"price": 67980.0, "volume": 180.0, "time": 1731234030},
                    {"price": 68020.0, "volume": 200.0, "time": 1731234040},
                ]

                for t in test_ticks:
                    on_quote_update_handler("BINANCE:BTCUSDT", t)

                # Receive both quotes and bars from websocket
                received_bars = []
                # Each tick triggers quote and bar updates
                for _ in range(8):
                    msg = ws.receive_json()
                    if msg.get("type") == "bar":
                        received_bars.append(msg["bar"])

                self.assertGreater(len(received_bars), 0)
                for b in received_bars:
                    # Invariant 1: OHLC bounds
                    self.assertLessEqual(b["low"], b["open"])
                    self.assertLessEqual(b["low"], b["close"])
                    self.assertLessEqual(b["low"], b["high"])
                    self.assertGreaterEqual(b["high"], b["open"])
                    self.assertGreaterEqual(b["high"], b["close"])
                    # Invariant 2: Positive volume
                    self.assertGreaterEqual(b["volume"], 0.0)
                    # Invariant 3: Quantized timestamp
                    self.assertEqual(b["time"] % 60, 0)
                    # Invariant 5: Float finite & positive
                    self.assertTrue(math.isfinite(b["open"]) and b["open"] > 0)
                    self.assertTrue(math.isfinite(b["high"]) and b["high"] > 0)
                    self.assertTrue(math.isfinite(b["low"]) and b["low"] > 0)
                    self.assertTrue(math.isfinite(b["close"]) and b["close"] > 0)


if __name__ == "__main__":
    unittest.main()

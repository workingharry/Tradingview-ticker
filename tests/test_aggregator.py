"""Unit tests for real-time TickToBarAggregator."""

import unittest
from engine.aggregator import TickToBarAggregator, CandlestickBar


class TestTickToBarAggregator(unittest.TestCase):
    def test_single_bar_ohlc_invariants(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:BTCUSDT"

        # Tick 1: open = 50000 at t = 100
        # 100 - (100 % 60) = 60
        bar = agg.process_tick(symbol, price=50000.0, cumulative_volume=10.0, tick_time=100)
        self.assertIsNotNone(bar)
        self.assertEqual(bar.time, 60)
        self.assertEqual(bar.open, 50000.0)
        self.assertEqual(bar.high, 50000.0)
        self.assertEqual(bar.low, 50000.0)
        self.assertEqual(bar.close, 50000.0)
        self.assertEqual(bar.volume, 0.0)

        # Tick 2: price = 50200 at t = 110
        bar = agg.process_tick(symbol, price=50200.0, cumulative_volume=12.5, tick_time=110)
        self.assertEqual(bar.high, 50200.0)
        self.assertEqual(bar.low, 50000.0)
        self.assertEqual(bar.close, 50200.0)
        self.assertEqual(bar.volume, 2.5)

        # Tick 3: price = 49800 at t = 115
        bar = agg.process_tick(symbol, price=49800.0, cumulative_volume=15.0, tick_time=115)
        self.assertEqual(bar.high, 50200.0)
        self.assertEqual(bar.low, 49800.0)
        self.assertEqual(bar.close, 49800.0)
        self.assertEqual(bar.volume, 5.0)

        # Mathematical Invariants
        self.assertTrue(bar.high >= bar.open)
        self.assertTrue(bar.high >= bar.low)
        self.assertTrue(bar.high >= bar.close)
        self.assertTrue(bar.low <= bar.open)
        self.assertTrue(bar.low <= bar.close)

    def test_window_transition(self):
        completed_bars = []

        def on_complete(sym, b):
            completed_bars.append((sym, b.to_dict()))

        agg = TickToBarAggregator(timeframe_seconds=60, on_bar_completed=on_complete)
        symbol = "BINANCE:ETHUSDT"

        # Bar 1 in window [60, 120)
        agg.process_tick(symbol, price=3000.0, tick_time=70)
        agg.process_tick(symbol, price=3050.0, tick_time=90)

        # Bar 2 in window [120, 180) -> triggers completion of bar 1
        bar2 = agg.process_tick(symbol, price=3100.0, tick_time=125)

        self.assertEqual(len(completed_bars), 1)
        self.assertEqual(completed_bars[0][0], symbol)
        self.assertEqual(completed_bars[0][1]["time"], 60)
        self.assertEqual(completed_bars[0][1]["close"], 3050.0)

        self.assertEqual(bar2.time, 120)
        self.assertEqual(bar2.open, 3100.0)
        self.assertEqual(bar2.close, 3100.0)

    def test_partial_quote_without_price_does_not_corrupt_bar(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:SOLUSDT"

        # Initial partial tick when no bar exists yet should return None and not insert a zero-price bar
        initial_partial = agg.process_tick(symbol, price=None, cumulative_volume=100.0, tick_time=60)
        self.assertIsNone(initial_partial)
        self.assertEqual(len(agg.get_history(symbol)), 0)

        # Now create valid bar
        agg.process_tick(symbol, price=150.0, cumulative_volume=100.0, tick_time=65)

        # Partial update with price=None and cumulative_volume increase
        bar = agg.process_tick(symbol, price=None, cumulative_volume=105.0, tick_time=70)
        self.assertIsNotNone(bar)
        self.assertEqual(bar.open, 150.0)
        self.assertEqual(bar.high, 150.0)
        self.assertEqual(bar.low, 150.0)
        self.assertEqual(bar.close, 150.0)
        self.assertEqual(bar.volume, 5.0)

        # Non-positive price (e.g. price <= 0.0) should also be rejected
        zero_bar = agg.process_tick(symbol, price=0.0, cumulative_volume=106.0, tick_time=75)
        self.assertEqual(zero_bar.close, 150.0)
        self.assertEqual(zero_bar.volume, 6.0)

    def test_seed_history_followed_by_window_transition(self):
        # High-severity regression test: seed_history must not double-append the last bar
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:BTCUSDT"

        seed_data = [
            {"time": 60, "open": 60000, "high": 60100, "low": 59900, "close": 60050, "volume": 10},
            {"time": 120, "open": 60050, "high": 60200, "low": 60000, "close": 60150, "volume": 12},
        ]
        agg.seed_history(symbol, seed_data)

        # Tick in window 180 (window transition after seed)
        agg.process_tick(symbol, price=60300.0, tick_time=185)

        history = agg.get_history(symbol)
        timestamps = [b["time"] for b in history]
        # Invariant: Timestamps MUST be strictly increasing with zero duplicates
        self.assertEqual(timestamps, [60, 120, 180])
        self.assertEqual(len(timestamps), len(set(timestamps)))

    def test_out_of_order_tick_does_not_corrupt_current_bar(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:BTCUSDT"

        agg.process_tick(symbol, price=60000.0, tick_time=130)
        # Delayed tick from previous minute (tick_time=70) arrives late
        agg.process_tick(symbol, price=55000.0, tick_time=70)

        current = agg.get_current_bar(symbol)
        self.assertIsNotNone(current)
        self.assertEqual(current.time, 120)
        self.assertEqual(current.close, 60000.0)  # Not overwritten by stale 55000.0

    def test_millisecond_timestamp_normalization(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "NASDAQ:AAPL"

        # 1700000000000 is 1700000000 in seconds
        # 1700000000 - (1700000000 % 60) = 1699999980
        ms_time = 1700000015000
        bar = agg.process_tick(symbol, price=180.0, tick_time=ms_time)
        self.assertIsNotNone(bar)
        self.assertEqual(bar.time, 1700000015 - (1700000015 % 60))

    def test_volume_delta_calculation_and_rounding(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:SOLUSDT"

        # Baseline tick: initial cumulative volume suppresses large initial delta
        b1 = agg.process_tick(symbol, price=100.0, cumulative_volume=1000.0, tick_time=65)
        self.assertEqual(b1.volume, 0.0)

        # Increment
        b2 = agg.process_tick(symbol, price=101.0, cumulative_volume=1005.12345678, tick_time=70)
        self.assertEqual(b2.volume, 5.12345678)

        # 24h rollover drop: negative delta clamped to 0
        b3 = agg.process_tick(symbol, price=102.0, cumulative_volume=500.0, tick_time=75)
        self.assertEqual(b3.volume, 5.12345678)

        # Next increment from new baseline
        b4 = agg.process_tick(symbol, price=103.0, cumulative_volume=502.5, tick_time=80)
        self.assertEqual(b4.volume, round(5.12345678 + 2.5, 8))

    def test_seed_history_enforces_ohlc_normalization(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:BTCUSDT"

        # Dirty seed data with high < open and low > close
        dirty_seed = [
            {"time": 60, "open": 100.0, "high": 90.0, "low": 110.0, "close": 95.0, "volume": -5.0}
        ]
        agg.seed_history(symbol, dirty_seed)

        history = agg.get_history(symbol)
        self.assertEqual(len(history), 1)
        bar = history[0]
        self.assertTrue(bar["high"] >= bar["open"])
        self.assertTrue(bar["high"] >= bar["close"])
        self.assertTrue(bar["low"] <= bar["open"])
        self.assertTrue(bar["low"] <= bar["close"])
        self.assertEqual(bar["volume"], 0.0)

    def test_nan_and_infinite_price_rejection(self):
        agg = TickToBarAggregator(timeframe_seconds=60)
        symbol = "BINANCE:BTCUSDT"

        # 1. NaN price on unseeded aggregator returns None and creates no bar
        res1 = agg.process_tick(symbol, price=float("nan"), cumulative_volume=10.0, tick_time=65)
        self.assertIsNone(res1)
        self.assertEqual(len(agg.get_history(symbol)), 0)

        # 2. Establish valid candle
        bar = agg.process_tick(symbol, price=50000.0, cumulative_volume=10.0, tick_time=65)
        self.assertIsNotNone(bar)
        self.assertEqual(bar.close, 50000.0)

        # 3. Subsequent NaN price must not corrupt active candle close
        bar2 = agg.process_tick(symbol, price=float("nan"), cumulative_volume=12.0, tick_time=70)
        self.assertIsNotNone(bar2)
        self.assertEqual(bar2.close, 50000.0)
        self.assertEqual(bar2.volume, 2.0)

        # 4. Infinite price (+inf / -inf) must be rejected
        bar3 = agg.process_tick(symbol, price=float("inf"), cumulative_volume=13.0, tick_time=75)
        self.assertEqual(bar3.close, 50000.0)
        bar4 = agg.process_tick(symbol, price=float("-inf"), cumulative_volume=14.0, tick_time=80)
        self.assertEqual(bar4.close, 50000.0)

        # 5. Non-numeric price string rejected
        bar5 = agg.process_tick(symbol, price="invalid_str", cumulative_volume=15.0, tick_time=85)
        self.assertEqual(bar5.close, 50000.0)

    def test_callback_exception_isolation_and_window_advancement(self):
        def crashing_complete_cb(sym, bar):
            raise ValueError("Broadcaster network failure")

        def crashing_update_cb(sym, bar):
            raise RuntimeError("Database logging failure")

        agg = TickToBarAggregator(
            timeframe_seconds=60,
            on_bar_completed=crashing_complete_cb,
            on_bar_update=crashing_update_cb,
        )
        symbol = "BINANCE:ETHUSDT"

        # Bar 1 at t=65 (window 60)
        b1 = agg.process_tick(symbol, price=3000.0, tick_time=65)
        self.assertEqual(b1.time, 60)

        # Bar 2 at t=125 (window 120): triggers crashing_complete_cb
        # Must catch exception and successfully advance current_bar to window 120
        b2 = agg.process_tick(symbol, price=3100.0, tick_time=125)
        self.assertIsNotNone(b2)
        self.assertEqual(b2.time, 120)
        self.assertEqual(b2.open, 3100.0)

        # Bar 3 at t=130: updates active bar at 120 normally without stuck state
        b3 = agg.process_tick(symbol, price=3150.0, tick_time=130)
        self.assertEqual(b3.time, 120)
        self.assertEqual(b3.close, 3150.0)

    def test_seed_history_merges_in_flight_live_bars(self):
        """Validates that seeding history merges with newer live ticks that arrived while fetch was in-flight."""
        agg = TickToBarAggregator(timeframe_seconds=60)
        sym = "BINANCE:BTCUSDT"

        # 1. Live tick arrives while history is being fetched
        agg.process_tick(sym, price=65500.0, cumulative_volume=10.0, tick_time=125)
        live_bar = agg.get_current_bar(sym)
        self.assertIsNotNone(live_bar)
        self.assertEqual(live_bar.time, 120)
        self.assertEqual(live_bar.close, 65500.0)

        # 2. Historical REST seed arrives with older bars and an earlier price for bucket 120
        seed_bars = [
            {"time": 0, "open": 64000.0, "high": 64200.0, "low": 63900.0, "close": 64100.0, "volume": 5.0},
            {"time": 60, "open": 64100.0, "high": 64500.0, "low": 64050.0, "close": 64400.0, "volume": 8.0},
            {"time": 120, "open": 64400.0, "high": 65000.0, "low": 64300.0, "close": 64900.0, "volume": 3.0},
        ]
        agg.seed_history(sym, seed_bars)

        # 3. Active bar at 120 must preserve live tick close (65500) and max high (65500 >= 65000)
        final_bar = agg.get_current_bar(sym)
        self.assertEqual(final_bar.time, 120)
        self.assertEqual(final_bar.close, 65500.0)
        self.assertEqual(final_bar.high, 65500.0)
        self.assertEqual(final_bar.open, 64400.0)
        # History queue must contain the 2 prior closed bars
        hist = agg.get_history(sym)
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["time"], 0)
        self.assertEqual(hist[1]["time"], 60)
        self.assertEqual(hist[2]["time"], 120)

    def test_remove_symbol_purges_memory(self):
        """Validates that remove_symbol cleanly unloads all cached state for an evicted symbol."""
        agg = TickToBarAggregator(timeframe_seconds=60)
        sym = "NASDAQ:EVIC_TEST"
        agg.process_tick(sym, price=100.0, cumulative_volume=5.0, tick_time=10)
        self.assertIsNotNone(agg.get_current_bar(sym))

        agg.remove_symbol(sym)
        self.assertIsNone(agg.get_current_bar(sym))
        self.assertEqual(agg.get_history(sym), [])
        self.assertNotIn(sym, agg.last_cumulative_volume)


if __name__ == "__main__":
    unittest.main()

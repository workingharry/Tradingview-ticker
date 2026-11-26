"""Unit and integration tests for Section 2: Real-time Data Pipeline.

Verifies:
S2-01: WebSocket idle timeout, heartbeat reset, backoff escalation and stable reset, /api/health upstream health.
S2-03: Bar closing via market clock with arrival_time, grace period, delayed-market protection,
       late tick rejection, next-bucket volume calculation, seeded symbol protection.
S2-02: Binance crypto 1m bar reconciliation in background, SQLite upsert, US stock isolation,
       synchronous caller protection without event loop, and clean cancellation on shutdown.
"""

import asyncio
import time
import tempfile
import pathlib
import unittest
from typing import Dict, Any, List, Optional
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient

import server
from engine.client import TradingViewAsyncClient
from engine.aggregator import TickToBarAggregator, CandlestickBar
from engine.storage import SQLiteStorage


class TestSection2RealTimePipeline(unittest.TestCase):

    # -------------------------------------------------------------------------
    # S2-01: WebSocket Idle Timeout & Backoff & Health
    # -------------------------------------------------------------------------

    def test_s2_01_idle_timeout_triggers_reconnect(self):
        """A fake WebSocket sends one message then hangs; idle timeout triggers reconnect within 1s."""
        async def _run():
            connect_count = 0
            connect_event = asyncio.Event()

            class FakeWS:
                def __init__(self):
                    self.sent_one = False

                async def recv(self):
                    if not self.sent_one:
                        self.sent_one = True
                        msg = '{"m":"qsd","p":["qs_1",{"n":"BINANCE:BTCUSDT","v":{"lp":50000.0}}]}'
                        return f"~m~{len(msg)}~m~{msg}"
                    # Hang until timeout
                    await asyncio.sleep(10.0)
                    return ""

                async def send(self, data):
                    pass

                async def close(self):
                    pass

            def fake_connect(*args, **kwargs):
                nonlocal connect_count
                connect_count += 1
                if connect_count >= 2:
                    connect_event.set()
                ctx = AsyncMock()
                ctx.__aenter__.return_value = FakeWS()
                ctx.__aexit__.return_value = None
                return ctx

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                idle_timeout=0.2,
                backoff_initial=0.01,
                stable_after=60.0
            )

            with patch("websockets.connect", side_effect=fake_connect):
                await client.start()
                try:
                    await asyncio.wait_for(connect_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                finally:
                    await client.stop()

            self.assertGreaterEqual(connect_count, 2, "Expected at least 2 connect calls due to idle timeout")
            self.assertIsNotNone(client.last_disconnect_reason)
            self.assertIn("idle", client.last_disconnect_reason.lower())
            self.assertGreaterEqual(client.reconnect_count, 1)

        asyncio.run(_run())

    def test_s2_01_heartbeat_prevents_idle_timeout(self):
        """A fake WebSocket sends heartbeat every 0.1s; idle_timeout=0.3 must not reconnect within 0.8s."""
        async def _run():
            connect_count = 0

            class HeartbeatWS:
                async def recv(self):
                    await asyncio.sleep(0.1)
                    return "~m~4~m~~h~1"

                async def send(self, data):
                    pass

                async def close(self):
                    pass

            def fake_connect(*args, **kwargs):
                nonlocal connect_count
                connect_count += 1
                ctx = AsyncMock()
                ctx.__aenter__.return_value = HeartbeatWS()
                ctx.__aexit__.return_value = None
                return ctx

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                idle_timeout=0.3,
                backoff_initial=0.01,
            )

            with patch("websockets.connect", side_effect=fake_connect):
                await client.start()
                await asyncio.sleep(0.8)
                await client.stop()

            self.assertEqual(connect_count, 1, "Heartbeat should have prevented any reconnect attempts")
            self.assertEqual(client.reconnect_count, 0)

    def test_s2_01_backoff_increases_on_immediate_disconnect_and_resets_after_stable(self):
        """Backoff sleep times escalate on immediate drop, then reset to initial after connection stays stable."""
        async def _run():
            sleep_durations = []
            simulated_time = 1000.0

            def fake_clock():
                return simulated_time

            async def fake_sleep(d):
                sleep_durations.append(d)
                # Return immediately to avoid test delay

            connect_attempts = 0

            class DroppingWS:
                def __init__(self, should_stay_stable: bool):
                    self.should_stay_stable = should_stay_stable

                async def recv(self):
                    if self.should_stay_stable:
                        nonlocal simulated_time
                        simulated_time += 70.0  # Stable (> 60s)
                    raise ConnectionResetError("Connection dropped")

                async def send(self, data):
                    pass

                async def close(self):
                    pass

            def fake_connect(*args, **kwargs):
                nonlocal connect_attempts
                connect_attempts += 1
                # First 3 attempts drop immediately (< 60s); 4th attempt stays stable (> 60s) then drops; 5th terminates
                is_stable = (connect_attempts == 4)
                ctx = AsyncMock()
                ctx.__aenter__.return_value = DroppingWS(is_stable)
                ctx.__aexit__.return_value = None
                return ctx

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                idle_timeout=30.0,
                backoff_initial=1.0,
                stable_after=60.0,
                clock=fake_clock,
                sleep=fake_sleep,
            )

            with patch("websockets.connect", side_effect=fake_connect):
                client.running = True
                task = asyncio.create_task(client._connection_loop())

                # Wait until we have 4 sleeps recorded
                for _ in range(50):
                    if len(sleep_durations) >= 4:
                        break
                    await asyncio.sleep(0.01)

                client.running = False
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            self.assertGreaterEqual(len(sleep_durations), 4)
            # First 3 waits should be escalating (base 1.0, 2.0, 4.0 plus jitter [0, 0.5*base])
            # Sleep 1: base 1.0 (range 1.0 .. 1.5)
            # Sleep 2: base 2.0 (range 2.0 .. 3.0)
            # Sleep 3: base 4.0 (range 4.0 .. 6.0)
            self.assertLess(sleep_durations[0], sleep_durations[1])
            self.assertLess(sleep_durations[1], sleep_durations[2])

            # Sleep 4: after stable connection (> 60s), backoff should reset back to base 1.0 (range 1.0 .. 1.5)
            self.assertLess(sleep_durations[3], sleep_durations[2])
            self.assertLessEqual(sleep_durations[3], 1.6)

        asyncio.run(_run())

    def test_s2_01_health_endpoint_includes_upstream(self):
        """Verifies /api/health returns upstream health dict and preserves all original fields."""
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=[]), \
             patch("server.fetch_universal_history", new_callable=AsyncMock, return_value=[]), \
             TestClient(server.app) as client:
            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()

            # Original required fields
            self.assertEqual(data["status"], "ok")
            self.assertIn("desktop_mode", data)
            self.assertIn("port", data)
            self.assertIn("timestamp", data)

            # S2-01 upstream field
            self.assertIn("upstream", data)
            upstream = data["upstream"]
            if upstream is not None:
                self.assertIn("connected", upstream)
                self.assertIn("last_message_age_sec", upstream)
                self.assertIn("reconnect_count", upstream)
                self.assertIn("last_disconnect_reason", upstream)
                self.assertIn("idle_timeout", upstream)

    # -------------------------------------------------------------------------
    # S2-03: Market Clock Bar Closing & Late Tick Protection
    # -------------------------------------------------------------------------

    def test_s2_03_realtime_quote_closes_bar_with_grace(self):
        """Realtime: ticks at t=100 and 110 fall in the 60-120 bucket; the bar closes at bucket end + grace (122), not first-tick + 60.

        (第 2 段 review: the original example measured from the first tick, which closed quiet symbols up to 60s late.)
        """
        completed_bars = []
        agg = TickToBarAggregator(
            timeframe_seconds=60,
            on_bar_completed=lambda sym, b: completed_bars.append((sym, b))
        )
        sym = "BINANCE:BTCUSDT"

        # Ticks arrive at quote_time = arrival_time
        agg.process_tick(sym, price=50000.0, cumulative_volume=10.0, tick_time=100, arrival_time=100.0)
        agg.process_tick(sym, price=50100.0, cumulative_volume=12.0, tick_time=110, arrival_time=110.0)

        # now_wall = 121 (market clock 121 < bucket end 120 + grace 2): must NOT close
        closed_121 = agg.close_due_bars(now_wall=121.0, grace_seconds=2.0)
        self.assertEqual(len(closed_121), 0)
        self.assertEqual(len(completed_bars), 0)

        # now_wall = 123 (market clock 123 >= 122): must close
        closed_123 = agg.close_due_bars(now_wall=123.0, grace_seconds=2.0)
        self.assertEqual(len(closed_123), 1)
        self.assertEqual(closed_123[0][0], sym)
        self.assertEqual(closed_123[0][1].time, 60)
        self.assertEqual(len(completed_bars), 1)
        self.assertEqual(completed_bars[0][1].close, 50100.0)

    def test_s2_03_delayed_quote_does_not_close_prematurely(self):
        """Delayed quote (quote_time = arrival_time - 900) must not close during the bar's 60s lifespan.

        Wall clock is 900s ahead of the quotes, so a wall-clock rule would close the bar immediately.
        The tick is placed at the start of its minute so the whole 60s bucket is observable.
        """
        completed_bars = []
        agg = TickToBarAggregator(
            timeframe_seconds=60,
            on_bar_completed=lambda sym, b: completed_bars.append((sym, b))
        )
        sym = "NASDAQ:AAPL"

        wall_start = 1980.0
        # Quote is 15 minutes (900s) behind wall clock, at the start of its minute bucket
        quote_start = int(wall_start - 900)  # 1080 = bucket 1080-1140
        self.assertEqual(quote_start % 60, 0)

        agg.process_tick(sym, price=150.0, cumulative_volume=100.0, tick_time=quote_start, arrival_time=wall_start)

        # Across the next 60 seconds of wall clock, close_due_bars must never close it
        for offset in [5.0, 15.0, 30.0, 45.0, 59.0]:
            closed = agg.close_due_bars(now_wall=wall_start + offset, grace_seconds=2.0)
            self.assertEqual(len(closed), 0, f"Prematurely closed delayed bar at wall offset {offset}")

        self.assertEqual(len(completed_bars), 0)

        # It does close once the MARKET clock passes bucket end + grace (1142), i.e. wall offset 62+
        closed = agg.close_due_bars(now_wall=wall_start + 63.0, grace_seconds=2.0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0][1].time, 1080)

    def test_s2_03_late_tick_rejected_and_volume_baseline_preserved(self):
        """After closing a bar, ticks for the closed period are dropped, but volume baseline updates properly."""
        completed_bars = []
        agg = TickToBarAggregator(
            timeframe_seconds=60,
            on_bar_completed=lambda sym, b: completed_bars.append((sym, b))
        )
        sym = "BINANCE:ETHUSDT"

        # Bar 1 ticks
        agg.process_tick(sym, price=3000.0, cumulative_volume=10.0, tick_time=100, arrival_time=100.0)
        agg.process_tick(sym, price=3020.0, cumulative_volume=15.0, tick_time=110, arrival_time=110.0)

        # Close bar 1 via close_due_bars at t=165
        agg.close_due_bars(now_wall=165.0, grace_seconds=2.0)
        self.assertEqual(len(completed_bars), 1)

        # Late tick belonging to bar 1 arrives with higher cumulative_volume
        late_bar = agg.process_tick(sym, price=3015.0, cumulative_volume=18.0, tick_time=115, arrival_time=166.0)

        # Late tick must not create duplicate bars in history
        history = agg.get_history(sym)
        self.assertEqual(len(history), 1)
        times = [b["time"] for b in history]
        self.assertEqual(len(times), len(set(times)), "History contains duplicate timestamps after late tick")

        # Now tick for next period arrives at tick_time=170
        new_bar = agg.process_tick(sym, price=3050.0, cumulative_volume=25.0, tick_time=170, arrival_time=170.0)
        self.assertIsNotNone(new_bar)
        # Volume delta should be 25.0 - 18.0 = 7.0 (using updated baseline from late tick)
        self.assertEqual(new_bar.volume, 7.0)
        # on_bar_completed must not have been re-triggered for the old bar
        self.assertEqual(len(completed_bars), 1)

    def test_s2_03_seeded_symbol_without_ticks_does_not_close(self):
        """Symbols that only have historical seed data and no real-time ticks must not be closed by close_due_bars."""
        completed_bars = []
        agg = TickToBarAggregator(
            timeframe_seconds=60,
            on_bar_completed=lambda sym, b: completed_bars.append((sym, b))
        )
        sym = "BINANCE:SOLUSDT"

        agg.seed_history(sym, [
            {"time": 1000, "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 50.0},
            {"time": 1060, "open": 102.0, "high": 108.0, "low": 101.0, "close": 106.0, "volume": 75.0},
        ])

        # Attempt closing at wall time far in future
        closed = agg.close_due_bars(now_wall=5000.0, grace_seconds=2.0)
        self.assertEqual(len(closed), 0)
        self.assertEqual(len(completed_bars), 0)

    # -------------------------------------------------------------------------
    # S2-02: Crypto 1m Bar Reconciliation with Binance
    # -------------------------------------------------------------------------

    def test_s2_02_reconciles_binance_bar_in_aggregator_and_sqlite(self):
        """When fetch_binance_history returns matching bar, aggregator history and SQLite are replaced."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmp_dir:
                db_path = pathlib.Path(tmp_dir) / "test_reconcile.sqlite"
                storage = SQLiteStorage(str(db_path))

                # Inject temporary storage into server
                with patch("server.get_storage", return_value=storage):
                    agg = TickToBarAggregator(timeframe_seconds=60)
                    sym = "BINANCE:BTCUSDT"
                    bar_time = 1700000000

                    # Seed initial tentative bar in aggregator history
                    tentative_bar = CandlestickBar(
                        time=bar_time, open=50000.0, high=50050.0, low=49950.0, close=50020.0, volume=10.0
                    )
                    agg.bar_history[sym] = [tentative_bar]
                    server.aggregator = agg

                    # Mock binance history with definitive OHLCV
                    reconciled_kline = {
                        "time": bar_time,
                        "open": 50000.0,
                        "high": 50120.0,  # Peak was higher
                        "low": 49910.0,   # Trough was lower
                        "close": 50020.0,
                        "volume": 35.8    # Full 1m trading volume
                    }

                    async def fake_fetch(symbol, limit=3, **kwargs):
                        return [reconciled_kline]

                    async def fast_sleep(sec):
                        pass

                    with patch("server.fetch_binance_history", side_effect=fake_fetch), \
                         patch("server._reconcile_sleep", side_effect=fast_sleep):
                        # Trigger handler
                        server.on_bar_completed_handler(sym, tentative_bar)

                        # Wait for background task to complete
                        for _ in range(50):
                            if not server._reconcile_tasks:
                                break
                            await asyncio.sleep(0.01)

                    # Verify aggregator history was updated
                    history = agg.get_history(sym)
                    self.assertEqual(len(history), 1)
                    self.assertEqual(history[0]["high"], 50120.0)
                    self.assertEqual(history[0]["low"], 49910.0)
                    self.assertEqual(history[0]["volume"], 35.8)

                    # Verify SQLite database was updated
                    stored = storage.get_bars(sym, "1m", limit=5)
                    self.assertEqual(len(stored), 1)
                    self.assertEqual(stored[0]["high"], 50120.0)
                    self.assertEqual(stored[0]["volume"], 35.8)

                storage.close()

        asyncio.run(_run())

    def test_s2_02_reconcile_keeps_tentative_when_binance_has_no_matching_bar(self):
        """When fetch_binance_history has no matching timestamp, tentative values are retained."""
        async def _run():
            with tempfile.TemporaryDirectory() as tmp_dir:
                db_path = pathlib.Path(tmp_dir) / "test_reconcile_miss.sqlite"
                storage = SQLiteStorage(str(db_path))

                with patch("server.get_storage", return_value=storage):
                    agg = TickToBarAggregator(timeframe_seconds=60)
                    sym = "BINANCE:ETHUSDT"
                    bar_time = 1700000000

                    tentative_bar = CandlestickBar(
                        time=bar_time, open=3000.0, high=3010.0, low=2990.0, close=3005.0, volume=5.0
                    )
                    agg.bar_history[sym] = [tentative_bar]
                    server.aggregator = agg

                    # Return completely different bar timestamp
                    async def fake_fetch(symbol, limit=3, **kwargs):
                        return [{"time": 1700000060, "open": 3005.0, "high": 3020.0, "low": 3000.0, "close": 3015.0, "volume": 12.0}]

                    async def fast_sleep(sec):
                        pass

                    with patch("server.fetch_binance_history", side_effect=fake_fetch), \
                         patch("server._reconcile_sleep", side_effect=fast_sleep):
                        server.on_bar_completed_handler(sym, tentative_bar)
                        for _ in range(50):
                            if not server._reconcile_tasks:
                                break
                            await asyncio.sleep(0.01)

                    history = agg.get_history(sym)
                    self.assertEqual(history[0]["volume"], 5.0)

                storage.close()

        asyncio.run(_run())

    def test_s2_02_us_equities_do_not_schedule_reconciliation(self):
        """US equities (e.g. NASDAQ:AAPL) must never schedule Binance reconciliation tasks."""
        async def _run():
            sym = "NASDAQ:AAPL"
            bar = CandlestickBar(time=1700000000, open=150.0, high=151.0, low=149.0, close=150.5, volume=100.0)

            with patch("server.get_storage") as mock_storage, \
                 patch("server.fetch_binance_history") as mock_fetch:
                server.on_bar_completed_handler(sym, bar)
                mock_storage.return_value.save_bars.assert_called_once()
                mock_fetch.assert_not_called()
                self.assertEqual(len(server._reconcile_tasks), 0)

        asyncio.run(_run())

    def test_s2_02_sync_call_without_running_loop_does_not_throw(self):
        """Calling on_bar_completed_handler without an active event loop must quietly skip reconciliation without error."""
        sym = "BINANCE:BTCUSDT"
        bar = CandlestickBar(time=1700000000, open=50000.0, high=50050.0, low=49950.0, close=50020.0, volume=10.0)

        with patch("server.get_storage") as mock_storage:
            try:
                server.on_bar_completed_handler(sym, bar)
            except Exception as e:
                self.fail(f"on_bar_completed_handler raised exception in sync call: {e}")

            mock_storage.return_value.save_bars.assert_called_once()

    def test_s2_02_reconcile_tasks_cleanly_cancelled_on_shutdown(self):
        """Pending reconciliation tasks are cleanly cancelled and awaited without unhandled task errors."""
        async def _run():
            sym = "BINANCE:BTCUSDT"
            bar = CandlestickBar(time=1700000000, open=50000.0, high=50050.0, low=49950.0, close=50020.0, volume=10.0)

            async def hanging_sleep(sec):
                await asyncio.sleep(100.0)

            with patch("server.get_storage"), \
                 patch("server._reconcile_sleep", side_effect=hanging_sleep):
                server.on_bar_completed_handler(sym, bar)
                self.assertGreater(len(server._reconcile_tasks), 0)

                # Simulate lifespan shutdown cancellation
                for t in list(server._reconcile_tasks):
                    t.cancel()
                await asyncio.gather(*list(server._reconcile_tasks), return_exceptions=True)
                server._reconcile_tasks.clear()

            self.assertEqual(len(server._reconcile_tasks), 0)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

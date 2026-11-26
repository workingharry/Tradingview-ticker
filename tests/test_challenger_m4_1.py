"""Adversarial E2E Concurrency & Stress Verification Suite.

Executed by Challenger M4-1 to empirically verify:
1. Concurrently connecting 30 WebSocket clients.
2. Firing 50 concurrent requests to /api/history across diverse symbols.
3. Abrupt termination of 15 clients mid-stream during active tick broadcasting.
4. Clean queue decrement to 15, clean receipt by remaining 15 clients, and drop to 0 on exit.
5. High-churn rapid connect/disconnect waves under continuous tick streaming.
6. Differential backpressure: slow consumer bounded drop-oldest isolation alongside fast consumer.
7. Concurrency integrity: simultaneous /api/history reads and tick aggregation without race conditions.
8. Zero queue leaks, zero task leaks, zero unhandled 500 exceptions.
"""

import time
import os
import threading
import concurrent.futures
import unittest
from unittest.mock import patch, AsyncMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import server
from server import app, manager
from engine.aggregator import CandlestickBar


MOCK_HISTORICAL_KLINES = [
    {
        "time": 1731234000 + i * 60,
        "open": 68000.0 + i * 10.0,
        "high": 68050.0 + i * 10.0,
        "low": 67980.0 + i * 10.0,
        "close": 68030.0 + i * 10.0,
        "volume": 15.0 + i * 1.5,
    }
    for i in range(25)
]


class TestAdversarialE2EConcurrency(unittest.TestCase):
    """Adversarial stress and concurrency verification for full E2E application."""

    def setUp(self):
        """Prepare hermetic offline mocks for external network calls."""
        import tempfile
        from engine.storage import close_storage
        self.tmp_dir = tempfile.mkdtemp()
        self.orig_db_env = os.environ.get("TERMINAL_DB_PATH")
        os.environ["TERMINAL_DB_PATH"] = str(Path(self.tmp_dir) / "test_m4_1.sqlite")
        close_storage()

        self.fetch_patcher = patch(
            "api.history.fetch_binance_history",
            new_callable=AsyncMock,
            return_value=list(MOCK_HISTORICAL_KLINES),
        )
        self.client_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.client_stop_patcher = patch(
            "engine.client.TradingViewAsyncClient.stop",
            new_callable=AsyncMock,
        )
        self.mock_fetch = self.fetch_patcher.start()
        self.mock_start = self.client_start_patcher.start()
        self.mock_stop = self.client_stop_patcher.start()

    def tearDown(self):
        """Clean up patchers and ensure zero leaked queues."""
        self.fetch_patcher.stop()
        self.client_start_patcher.stop()
        self.client_stop_patcher.stop()
        manager.client_queues.clear()
        from engine.storage import close_storage
        close_storage()
        if hasattr(self, "orig_db_env") and self.orig_db_env is not None:
            os.environ["TERMINAL_DB_PATH"] = self.orig_db_env
        else:
            os.environ.pop("TERMINAL_DB_PATH", None)
        if hasattr(self, "tmp_dir"):
            import shutil
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_adversarial_concurrent_30_clients_and_50_history_requests(self):
        """Adversarial Core Test:
        - Spin up FastAPI TestClient.
        - Concurrently connect 30 WebSocket clients.
        - Fire 50 concurrent requests to /api/history across multiple symbols.
        - Abruptly terminate 15 clients mid-stream while broadcasting live ticks.
        - Assert all remaining clients receive messages without error.
        - Assert server active queues count cleanly decrements to 15, and after closing remaining, drops to 0.
        """
        with TestClient(app) as client:
            # 1. Concurrently connect 30 WebSocket clients
            connect_barrier = threading.Barrier(31)
            disconnect_15_event = threading.Event()
            disconnect_rest_event = threading.Event()
            received_by_client = {i: [] for i in range(30)}
            client_errors = []

            def ws_client_worker(cid: int):
                try:
                    with client.websocket_connect("/ws/live") as ws:
                        # Initial status handshake
                        status_msg = ws.receive_json()
                        received_by_client[cid].append(status_msg)
                        connect_barrier.wait(timeout=10.0)

                        if cid < 15:
                            # Abruptly terminate: wait for trigger, then exit context
                            disconnect_15_event.wait(timeout=5.0)
                            return
                        else:
                            # Remaining 15 clients: expect live tick broadcast
                            # 2 ticks = 2 bar updates + 2 quote updates = 4 messages
                            for _ in range(4):
                                msg = ws.receive_json()
                                received_by_client[cid].append(msg)
                            disconnect_rest_event.wait(timeout=5.0)
                            return
                except Exception as exc:
                    client_errors.append((cid, exc))
                    try:
                        connect_barrier.abort()
                    except Exception:
                        pass

            threads = []
            for i in range(30):
                t = threading.Thread(target=ws_client_worker, args=(i,))
                threads.append(t)
                t.start()
                time.sleep(0.02)

            try:
                # Wait for all 30 clients to connect and receive initial status
                connect_barrier.wait(timeout=10.0)
                self.assertEqual(
                    len(manager.client_queues), 30,
                    f"Expected 30 active queues in manager, got {len(manager.client_queues)}"
                )

                # 2. Fire 50 concurrent requests to /api/history across diverse symbols
                symbols = [
                    "BINANCE:BTCUSDT",
                    "BINANCE:ETHUSDT",
                    "NASDAQ:AAPL",
                    "UNKNOWN:XYZ",
                    "FOREX:EURUSD",
                ]

                def fetch_history_task(task_id: int):
                    target_sym = symbols[task_id % len(symbols)]
                    response = client.get(f"/api/history?symbol={target_sym}")
                    return (response.status_code, response.json(), target_sym)

                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    history_responses = list(executor.map(fetch_history_task, range(50)))

                self.assertEqual(len(history_responses), 50)
                for status_code, body, sym in history_responses:
                    self.assertEqual(status_code, 200)
                    self.assertIn("symbol", body)
                    self.assertIn("bars", body)
                    self.assertEqual(body["symbol"], sym)
                    self.assertIsInstance(body["bars"], list)
                    # Verify OHLC invariants on any returned bars
                    for bar in body["bars"]:
                        self.assertGreaterEqual(bar["high"], bar["open"])
                        self.assertGreaterEqual(bar["high"], bar["close"])
                        self.assertLessEqual(bar["low"], bar["open"])
                        self.assertLessEqual(bar["low"], bar["close"])
                        self.assertGreaterEqual(bar["volume"], 0.0)

                # 3. Abruptly terminate 15 clients mid-stream while broadcasting live ticks
                disconnect_15_event.set()

                # Broadcast 2 live ticks concurrently with disconnections
                server.on_quote_update_handler(
                    "BINANCE:BTCUSDT",
                    {"price": 68900.0, "volume": 100.5, "time": 1731235600}
                )
                server.on_quote_update_handler(
                    "BINANCE:BTCUSDT",
                    {"price": 68950.0, "volume": 105.0, "time": 1731235610}
                )

                # Wait for first 15 threads to terminate
                for i in range(15):
                    threads[i].join(timeout=3.0)
                    self.assertFalse(threads[i].is_alive(), f"Thread {i} failed to terminate promptly")

                # Small pause for ASGI background loop to process disconnect callbacks
                time.sleep(0.06)

                # 4. Assert active queues count cleanly decrements to 15
                self.assertEqual(
                    len(manager.client_queues), 15,
                    f"Active queues did not decrement to 15 (observed: {len(manager.client_queues)})"
                )

                # Wait for all remaining 15 clients to receive their 4 broadcast messages
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    if all(len(received_by_client[cid]) == 5 for cid in range(15, 30)):
                        break
                    time.sleep(0.02)

                # 5. Assert all remaining 15 clients received messages without error
                self.assertEqual(
                    len(client_errors), 0,
                    f"Unexpected client errors during concurrency test: {client_errors}"
                )
                for cid in range(15, 30):
                    received = received_by_client[cid]
                    # Expected: 1 status + 2 bars + 2 quotes = 5 messages
                    self.assertEqual(
                        len(received), 5,
                        f"Client {cid} received {len(received)} messages, expected 5: {[m.get('type') for m in received]}"
                    )
                    self.assertEqual(received[0]["type"], "status")
                    msg_types = [m["type"] for m in received[1:]]
                    self.assertEqual(msg_types, ["bar", "quote", "bar", "quote"])

                # 6. Close remaining 15 clients and assert drops to 0
                disconnect_rest_event.set()
                for cid in range(15, 30):
                    threads[cid].join(timeout=3.0)
                    self.assertFalse(threads[cid].is_alive(), f"Client thread {cid} failed to terminate")

                time.sleep(0.06)
                self.assertEqual(
                    len(manager.client_queues), 0,
                    f"Residual queues remained after all clients disconnected: {len(manager.client_queues)}"
                )
            finally:
                disconnect_15_event.set()
                disconnect_rest_event.set()
                for t in threads:
                    t.join(timeout=1.0)

    def test_adversarial_rapid_client_churn_waves_under_tick_stream(self):
        """Stress-tests rapid waves of connecting and abruptly disconnecting clients
        while continuous tick traffic is broadcasted."""
        with TestClient(app) as client:
            churn_cycles = 4
            batch_size = 10
            total_churn_clients = churn_cycles * batch_size

            for wave in range(churn_cycles):
                barrier = threading.Barrier(batch_size + 1)

                def churn_client():
                    try:
                        with client.websocket_connect("/ws/live") as ws:
                            _ = ws.receive_json()
                            barrier.wait()
                            # Immediate abrupt exit
                    except Exception as e:
                        logger.debug("Churn client disconnected: %s", e)

                threads = [threading.Thread(target=churn_client) for _ in range(batch_size)]
                for t in threads:
                    t.start()

                barrier.wait()
                # Broadcast ticks during disconnect churn
                server.on_quote_update_handler(
                    "BINANCE:ETHUSDT",
                    {"price": 3200.0 + wave, "volume": 50.0, "time": 1731236000 + wave * 10}
                )

                for t in threads:
                    t.join(timeout=2.0)
                    self.assertFalse(t.is_alive())

            time.sleep(0.05)
            self.assertEqual(
                len(manager.client_queues), 0,
                f"Queues leaked after {total_churn_clients} churn clients: {len(manager.client_queues)}"
            )

    def test_adversarial_differential_backpressure_slow_vs_fast_consumer(self):
        """Verifies that a slow/stalled consumer with bounded queue (drop-oldest)
        never impedes or delays a concurrent fast consumer."""
        with TestClient(app) as client:
            slow_received = []
            fast_received = []
            sync_barrier = threading.Barrier(3)
            stop_flag = threading.Event()

            def slow_client_worker():
                with client.websocket_connect("/ws/live") as ws:
                    init = ws.receive_json()
                    slow_received.append(init)
                    sync_barrier.wait()
                    # Simulates extremely sluggish reader (deliberate delay)
                    while not stop_flag.is_set():
                        try:
                            time.sleep(0.05)
                            m = ws.receive_json()
                            slow_received.append(m)
                        except Exception:
                            break

            def fast_client_worker():
                with client.websocket_connect("/ws/live") as ws:
                    init = ws.receive_json()
                    fast_received.append(init)
                    sync_barrier.wait()
                    # Fast reader consumes as fast as packets arrive
                    while not stop_flag.is_set():
                        try:
                            m = ws.receive_json()
                            fast_received.append(m)
                            if len(fast_received) >= 101:  # status + 100 quotes
                                break
                        except Exception:
                            break

            t_slow = threading.Thread(target=slow_client_worker)
            t_fast = threading.Thread(target=fast_client_worker)
            t_slow.start()
            t_fast.start()

            sync_barrier.wait()
            self.assertEqual(len(manager.client_queues), 2)

            # Rapidly push 100 quotes without yielding
            for i in range(100):
                manager.broadcast_nowait({"type": "quote", "seq": i, "price": 100.0 + i})

            # Fast client finishes within 1 second
            t_fast.join(timeout=2.0)
            self.assertFalse(t_fast.is_alive(), "Fast client was blocked by slow client!")
            self.assertEqual(len(fast_received), 101)  # status + 100 quotes

            # Stop slow client
            stop_flag.set()
            t_slow.join(timeout=2.0)

            time.sleep(0.05)
            self.assertEqual(len(manager.client_queues), 0)

    def test_adversarial_simultaneous_history_reads_and_tick_aggregation(self):
        """Verifies that concurrent /api/history reads while ticks are aggressively
        modifying the aggregator produce zero data races or invariant violations."""
        with TestClient(app) as client:
            test_sym = "BINANCE:BTCUSDT"
            stop_ticks = threading.Event()
            read_errors = []

            def tick_producer():
                base_time = 1731238000
                tick_count = 0
                while not stop_ticks.is_set() and tick_count < 100:
                    server.on_quote_update_handler(
                        test_sym,
                        {
                            "price": 68000.0 + (tick_count % 50),
                            "volume": 1000.0 + tick_count * 5.0,
                            "time": base_time + tick_count,
                        }
                    )
                    tick_count += 1
                    time.sleep(0.001)

            def history_reader(reader_id: int):
                for _ in range(15):
                    try:
                        resp = client.get(f"/api/history?symbol={test_sym}")
                        if resp.status_code != 200:
                            read_errors.append(f"Status code {resp.status_code}")
                            continue
                        bars = resp.json().get("bars", [])
                        for b in bars:
                            if b["high"] < max(b["open"], b["close"]):
                                read_errors.append(f"High invariant violated: {b}")
                            if b["low"] > min(b["open"], b["close"]):
                                read_errors.append(f"Low invariant violated: {b}")
                            if b["volume"] < 0:
                                read_errors.append(f"Volume negative: {b}")
                    except Exception as exc:
                        read_errors.append(str(exc))
                    time.sleep(0.002)

            prod_thread = threading.Thread(target=tick_producer)
            prod_thread.start()

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                reader_futures = [executor.submit(history_reader, i) for i in range(5)]
                concurrent.futures.wait(reader_futures)

            stop_ticks.set()
            prod_thread.join(timeout=2.0)

            self.assertEqual(
                len(read_errors), 0,
                f"Data race or invariant violations observed during concurrent history reads: {read_errors}"
            )


if __name__ == "__main__":
    unittest.main()

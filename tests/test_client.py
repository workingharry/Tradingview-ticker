"""Unit tests for TradingViewAsyncClient."""

import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from typing import Dict, Any
from engine.client import TradingViewAsyncClient
from engine.protocol import encode_heartbeat_response


class TestTradingViewAsyncClient(unittest.TestCase):
    def test_packet_handling_and_quote_dispatch(self):
        received_quotes = []

        def on_quote(sym: str, q: Dict[str, Any]):
            received_quotes.append((sym, q))

        client = TradingViewAsyncClient(
            symbols=["BINANCE:BTCUSDT"],
            on_quote_update=on_quote
        )

        packet = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": [
                    "qs_1",
                    {
                        "n": "BINANCE:BTCUSDT",
                        "v": {
                            "lp": 67500.5,
                            "volume": 12345.67,
                            "ch": 150.2,
                            "chp": 0.22,
                            "lp_time": 1700000000
                        }
                    }
                ]
            }
        }

        client._handle_packet(packet)

        # State updated
        st = client.states["BINANCE:BTCUSDT"]
        self.assertEqual(st["price"], 67500.5)
        self.assertEqual(st["volume"], 12345.67)
        self.assertEqual(st["change"], 150.2)
        self.assertEqual(st["changePercentage"], 0.22)
        self.assertEqual(st["time"], 1700000000)

        # Callback invoked with clean dict copy
        self.assertEqual(len(received_quotes), 1)
        self.assertEqual(received_quotes[0][0], "BINANCE:BTCUSDT")
        self.assertEqual(received_quotes[0][1]["price"], 67500.5)

    def test_consumer_exception_isolation(self):
        def crashing_quote_callback(sym: str, q: Dict[str, Any]):
            raise asyncio.CancelledError("Consumer cancelled unexpectedly!")

        def crashing_status_callback(status: str):
            raise asyncio.CancelledError("Status consumer cancelled!")

        client = TradingViewAsyncClient(
            symbols=["BINANCE:BTCUSDT"],
            on_quote_update=crashing_quote_callback,
            on_status_change=crashing_status_callback
        )

        packet = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": [
                    "qs_1",
                    {
                        "n": "BINANCE:BTCUSDT",
                        "v": {"lp": 60000.0}
                    }
                ]
            }
        }

        # Neither _handle_packet nor _set_status should raise any exception
        try:
            client._handle_packet(packet)
            client._set_status("connecting")
        except Exception as e:
            self.fail(f"Callback error leaked out of client: {e}")

    def test_async_callback_support(self):
        async def run_async_test():
            executed = asyncio.Event()

            async def async_quote_callback(sym: str, q: Dict[str, Any]):
                executed.set()

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                on_quote_update=async_quote_callback
            )

            packet = {
                "type": "data",
                "data": {
                    "m": "qsd",
                    "p": ["qs_1", {"n": "BINANCE:BTCUSDT", "v": {"lp": 50000.0}}]
                }
            }

            client._handle_packet(packet)
            # Give event loop a cycle to run scheduled task
            await asyncio.sleep(0.05)
            self.assertTrue(executed.is_set())

        asyncio.run(run_async_test())

    def test_dynamic_subscription(self):
        async def run_sub_test():
            client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT"])
            self.assertIn("BINANCE:BTCUSDT", client.symbols)
            self.assertNotIn("BINANCE:ETHUSDT", client.symbols)

            await client.subscribe("BINANCE:ETHUSDT")
            self.assertIn("BINANCE:ETHUSDT", client.symbols)
            self.assertIn("BINANCE:ETHUSDT", client.states)
            self.assertEqual(client.states["BINANCE:ETHUSDT"]["price"], 0.0)

        asyncio.run(run_sub_test())

    def test_heartbeat_response(self):
        raw_hb = "~h~99"
        pong = encode_heartbeat_response(raw_hb)
        self.assertEqual(pong, "~m~5~m~~h~99")

    def test_clean_start_stop_lifecycle(self):
        async def run_lifecycle_test():
            statuses = []

            def record_status(st: str):
                statuses.append(st)

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                on_status_change=record_status
            )

            # Start client
            await client.start()
            self.assertTrue(client.running)
            self.assertIsNotNone(client._task)

            # Allow loop to begin connecting
            await asyncio.sleep(0.05)

            # Stop client
            await client.stop()
            self.assertFalse(client.running)
            self.assertTrue(client._task.done())
            self.assertIn("stopped", statuses)

        asyncio.run(run_lifecycle_test())

    def test_consumer_cancelled_error_isolation(self):
        def cancelling_quote_cb(sym: str, q: Dict[str, Any]):
            raise asyncio.CancelledError("Consumer cancelled")

        def cancelling_status_cb(st: str):
            raise asyncio.CancelledError("Status cancelled")

        client = TradingViewAsyncClient(
            symbols=["BINANCE:BTCUSDT"],
            on_quote_update=cancelling_quote_cb,
            on_status_change=cancelling_status_cb,
        )
        packet = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": ["qs_1", {"n": "BINANCE:BTCUSDT", "v": {"lp": 50000.0}}]
            }
        }
        # Neither _handle_packet nor _set_status should allow CancelledError to leak
        client._handle_packet(packet)
        client._set_status("connecting")
        self.assertEqual(client.states["BINANCE:BTCUSDT"]["price"], 50000.0)

    def test_connection_loop_survives_consumer_cancelled_error(self):
        async def run_test():
            from unittest.mock import patch

            def on_quote_cancel(sym, q):
                raise asyncio.CancelledError("Consumer cancelled")

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                on_quote_update=on_quote_cancel
            )

            class DummyWS:
                def __init__(self):
                    self.first = True
                async def recv(self):
                    if self.first:
                        self.first = False
                        msg = '{"m":"qsd","p":["qs_1",{"n":"BINANCE:BTCUSDT","v":{"lp":50000.0}}]}'
                        return f"~m~{len(msg)}~m~{msg}"
                    await asyncio.sleep(10)
                    return ""
                async def send(self, msg): pass
                async def close(self): pass

            dummy = DummyWS()
            with patch("websockets.connect") as mock_conn:
                mock_conn.return_value.__aenter__.return_value = dummy
                await client.start()
                await asyncio.sleep(0.05)
                loop_crashed = client.running and client._task.done()
                self.assertFalse(loop_crashed, "Supervisor connection loop crashed on consumer CancelledError!")
                await client.stop()

        asyncio.run(run_test())

    def test_async_callback_task_tracking_and_cleanup(self):
        async def run_test():
            completed = asyncio.Event()

            async def async_cb(sym: str, q: Dict[str, Any]):
                await asyncio.sleep(0.01)
                completed.set()

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                on_quote_update=async_cb
            )
            packet = {
                "type": "data",
                "data": {
                    "m": "qsd",
                    "p": ["qs_1", {"n": "BINANCE:BTCUSDT", "v": {"lp": 50000.0}}]
                }
            }
            client._handle_packet(packet)
            self.assertEqual(len(client._background_tasks), 1)
            await asyncio.sleep(0.05)
            self.assertTrue(completed.is_set())
            # Task removed upon completion
            self.assertEqual(len(client._background_tasks), 0)

        asyncio.run(run_test())

    def test_async_callback_cancellation_on_client_stop(self):
        async def run_test():
            async def long_cb(sym: str, q: Dict[str, Any]):
                await asyncio.sleep(10)

            client = TradingViewAsyncClient(
                symbols=["BINANCE:BTCUSDT"],
                on_quote_update=long_cb
            )
            packet = {
                "type": "data",
                "data": {
                    "m": "qsd",
                    "p": ["qs_1", {"n": "BINANCE:BTCUSDT", "v": {"lp": 50000.0}}]
                }
            }
            client._handle_packet(packet)
            self.assertEqual(len(client._background_tasks), 1)

            # Stopping client must cancel and gather background tasks cleanly
            await client.stop()
            self.assertEqual(len(client._background_tasks), 0)

        asyncio.run(run_test())

    def test_null_and_malformed_quote_fields_isolation(self):
        client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT"])
        packet = {
            "type": "data",
            "data": {
                "m": "qsd",
                "p": [
                    "qs_1",
                    {
                        "n": "BINANCE:BTCUSDT",
                        "v": {
                            "lp": None,
                            "volume": None,
                            "ch": "invalid_number",
                            "chp": None,
                            "lp_time": None
                        }
                    }
                ]
            }
        }
        # Must not raise TypeError or ValueError
        client._handle_packet(packet)
        st = client.states["BINANCE:BTCUSDT"]
        self.assertEqual(st["price"], 0.0)
        self.assertEqual(st["volume"], 0.0)

    def test_dynamic_unsubscription(self):
        """Validates that unsubscribe removes symbol, purges state, and sends quote_remove_symbols if open."""
        async def run_test():
            client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT", "BINANCE:ETHUSDT"])
            self.assertIn("BINANCE:BTCUSDT", client.symbols)
            self.assertIn("BINANCE:BTCUSDT", client.states)

            # Mock websocket
            client.ws = MagicMock()
            client.ws.state.name = "OPEN"
            client.ws.closed = False
            client.qsl = "qsl_test"
            client.qs = "qs_test"
            client.cs = "cs_test"
            client.chart_series["BINANCE:BTCUSDT_1D"] = [{"time": 1}]
            client.key_to_series_id["BINANCE:BTCUSDT_1D"] = "sds_old"
            client.series_map["sds_old"] = "BINANCE:BTCUSDT_1D"
            client._send = AsyncMock()

            await client.unsubscribe("BINANCE:BTCUSDT")

            self.assertNotIn("BINANCE:BTCUSDT", client.symbols)
            self.assertNotIn("BINANCE:BTCUSDT", client.states)
            self.assertNotIn("BINANCE:BTCUSDT_1D", client.chart_series)
            self.assertNotIn("sds_old", client.series_map)
            self.assertNotIn("BINANCE:BTCUSDT_1D", client.key_to_series_id)
            self.assertEqual(client._send.await_count, 3)
            calls = [call[0] for call in client._send.await_args_list]
            self.assertIn(("quote_remove_symbols", ["qsl_test", "BINANCE:BTCUSDT"]), calls)
            self.assertIn(("quote_remove_symbols", ["qs_test", "BINANCE:BTCUSDT"]), calls)
            self.assertIn(("remove_series", ["cs_test", "sds_old"]), calls)

        asyncio.run(run_test())

    def test_scheme_b_live_du_update_after_request_complete(self):
        """Validates that after request_chart_series completes, incoming du packet updates chart_series via series_keys."""
        client = TradingViewAsyncClient(symbols=["BINANCE:BTCUSDT"])
        series_id = "sds_test123"
        series_key = "BINANCE:BTCUSDT_1D"

        # Register series_keys mapping
        client.series_keys[series_id] = series_key
        client.chart_series[series_key] = [
            {"time": 1700000000, "open": 50000.0, "high": 50500.0, "low": 49900.0, "close": 50200.0, "volume": 100.0}
        ]

        # series_futures does NOT contain series_id (simulating request_chart_series already finished)
        self.assertNotIn(series_id, client.series_futures)

        # Incoming 'du' packet for ongoing bar update
        du_packet = {
            "type": "data",
            "data": {
                "m": "du",
                "p": [
                    "cs_test",
                    {
                        series_id: {
                            "s": [
                                {"i": 0, "v": [1700000000, 50000.0, 50800.0, 49900.0, 50600.0, 150.0]}
                            ]
                        }
                    }
                ]
            }
        }
        client._handle_packet(du_packet)

        # Verify bar at 1700000000 was updated with new high and close
        latest_bars = client.chart_series[series_key]
        self.assertEqual(len(latest_bars), 1)
        self.assertEqual(latest_bars[0]["high"], 50800.0)
        self.assertEqual(latest_bars[0]["close"], 50600.0)
        self.assertEqual(latest_bars[0]["volume"], 150.0)


if __name__ == "__main__":
    unittest.main()

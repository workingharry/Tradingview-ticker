"""TradingView Asynchronous WebSocket Ingestion Client.

Runs entirely within the host application's asyncio event loop without threads,
signal hijacking, or event loop manipulation. Handles auto-reconnection with
jittered exponential backoff, isolated callback dispatch, dynamic symbol subscription,
and instantaneous heartbeat echoes.
"""

import asyncio
import websockets
import random
import logging
import inspect
import math
from typing import List, Dict, Any, Optional, Callable, Set
from .protocol import (
    parse_tradingview_packets,
    encode_tradingview_message,
    encode_heartbeat_response,
    StreamingTradingViewParser,
)

logger = logging.getLogger("tradingview.client")


def _random_token(length: int = 12) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(chars) for _ in range(length))


class TradingViewAsyncClient:
    WS_URL = "wss://data.tradingview.com/socket.io/websocket"
    WS_ORIGIN = "https://www.tradingview.com"

    QUOTE_FIELDS = [
        "base-currency-logoid", "ch", "chp", "currency-logoid", "currency_code",
        "currency_id", "base_currency_id", "current_session", "description",
        "exchange", "format", "fractional", "is_tradable", "language",
        "local_description", "listed_exchange", "logoid", "lp", "lp_time",
        "minmov", "minmove2", "original_name", "pricescale", "pro_name",
        "short_name", "type", "typespecs", "update_mode", "volume",
        "variable_tick_size", "value_unit_id", "high_price", "low_price"
    ]

    def __init__(
        self,
        symbols: List[str],
        on_quote_update: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_status_change: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.symbols: List[str] = list(symbols)
        self.on_quote_update: Optional[Callable[[str, Dict[str, Any]], Any]] = on_quote_update
        self.on_status_change: Optional[Callable[[str], Any]] = on_status_change

        self.states: Dict[str, Dict[str, Any]] = {
            s: {"volume": 0.0, "price": 0.0, "change": 0.0, "changePercentage": 0.0, "time": 0}
            for s in self.symbols
        }

        self.running: bool = False
        self.connected: bool = False
        self.ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._background_tasks: Set[asyncio.Task] = set()

        # Session token tracking for subscription management
        self.cs: Optional[str] = None
        self.qs: Optional[str] = None
        self.qsl: Optional[str] = None

        # Chart Series storage and in-flight request tracking (Scheme B)
        self.chart_series: Dict[str, List[Dict[str, Any]]] = {}
        self.series_futures: Dict[str, tuple[str, asyncio.Future]] = {}
        self.series_keys: Dict[str, str] = {}
        self.series_map: Dict[str, str] = {}
        self.key_to_series_id: Dict[str, str] = {}

    def _is_open(self) -> bool:
        """Determines if the WebSocket connection is actively open across websockets v13-v15."""
        if not self.ws:
            return False
        state = getattr(self.ws, "state", None)
        if state is not None:
            return getattr(state, "name", "") == "OPEN"
        return not getattr(self.ws, "closed", True)

    def _schedule_callback_task(self, coro: Any, description: str) -> asyncio.Task:
        """Tracks background awaitable callbacks, prevents task garbage collection, and logs exceptions."""
        task = asyncio.create_task(coro) # tracked
        self._background_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.error("Exception in async callback (%s): %s", description, exc, exc_info=exc)

        task.add_done_callback(_on_done)
        return task

    def _set_status(self, status: str) -> None:
        """Notifies status subscribers safely with isolated exception handling."""
        logger.info("TradingView connection status: %s", status)
        if self.on_status_change:
            try:
                res = self.on_status_change(status)
                if inspect.isawaitable(res):
                    self._schedule_callback_task(res, f"on_status_change({status})")
            except (Exception, asyncio.CancelledError) as e:
                logger.exception("Error in on_status_change callback: %s", e)

    def _dispatch_quote(self, symbol: str, quote: Dict[str, Any]) -> None:
        """Dispatches quote snapshots to consumers with isolated exception protection."""
        if self.on_quote_update:
            try:
                res = self.on_quote_update(symbol, quote)
                if inspect.isawaitable(res):
                    self._schedule_callback_task(res, f"on_quote_update for {symbol}")
            except (Exception, asyncio.CancelledError) as e:
                logger.exception("Error in on_quote_update callback for %s: %s", symbol, e)

    async def _send(self, name_or_msg: Any, params: Optional[list] = None) -> None:
        if self._is_open():
            msg = encode_tradingview_message(name_or_msg, params)
            try:
                await self.ws.send(msg)
            except Exception as e:
                logger.debug("WebSocket send failed during active session: %s", e)

    async def _authenticate_and_subscribe(self) -> None:
        self.cs = "cs_" + _random_token()
        self.qs = "qs_" + _random_token()
        self.qsl = "qs_snapshoter_basic-symbol-quotes_" + _random_token()

        await self._send("set_auth_token", ["unauthorized_user_token"])
        await self._send("chart_create_session", [self.cs, ""])
        await self._send("quote_create_session", [self.qs])
        await self._send("quote_create_session", [self.qsl])
        await self._send("quote_set_fields", [self.qsl] + self.QUOTE_FIELDS)
        await self._send("quote_add_symbols", [self.qsl] + self.symbols)
        await self._send("quote_fast_symbols", [self.qs] + self.symbols)

    async def subscribe(self, symbol: str) -> None:
        """Dynamically subscribes to a new symbol at runtime without reconnecting."""
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.states[symbol] = {
                "volume": 0.0,
                "price": 0.0,
                "change": 0.0,
                "changePercentage": 0.0,
                "time": 0,
            }
        if self._is_open() and self.qsl and self.qs:
            await self._send("quote_add_symbols", [self.qsl, symbol])
            await self._send("quote_fast_symbols", [self.qs, symbol])

    async def unsubscribe(self, symbol: str) -> None:
        """Dynamically unsubscribes from a symbol at runtime and cleans up its state."""
        if symbol in self.symbols:
            self.symbols.remove(symbol)
        self.states.pop(symbol, None)
        for key in [k for k in self.chart_series if k.startswith(f"{symbol}_")]:
            self.chart_series.pop(key, None)
            old_sid = self.key_to_series_id.pop(key, None)
            if old_sid:
                self.series_map.pop(old_sid, None)
                if self._is_open() and self.cs:
                    await self._send("remove_series", [self.cs, old_sid])
        if self._is_open() and self.qsl and self.qs:
            await self._send("quote_remove_symbols", [self.qsl, symbol])
            await self._send("quote_remove_symbols", [self.qs, symbol])
            logger.info("Sent quote_remove_symbols for %s", symbol)

    def _handle_packet(self, packet: Dict[str, Any]) -> None:
        if packet.get("type") != "data":
            return

        data = packet.get("data", {})
        if not isinstance(data, dict):
            return

        if data.get("m") == "qsd":
            params = data.get("p", [])
            if len(params) > 1 and isinstance(params[1], dict):
                info = params[1]
                symbol = info.get("n")
                values = info.get("v", {})

                if symbol in self.states and isinstance(values, dict):
                    st = self.states[symbol]
                    if "lp" in values and values["lp"] is not None:
                        try:
                            val = float(values["lp"])
                            if math.isfinite(val):
                                st["price"] = val
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing lp in quote: %s", values)
                    if "volume" in values and values["volume"] is not None:
                        try:
                            val = float(values["volume"])
                            if math.isfinite(val):
                                st["volume"] = val
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing volume in quote: %s", values)
                    if "ch" in values and values["ch"] is not None:
                        try:
                            val = float(values["ch"])
                            if math.isfinite(val):
                                st["change"] = val
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing ch in quote: %s", values)
                    if "chp" in values and values["chp"] is not None:
                        try:
                            val = float(values["chp"])
                            if math.isfinite(val):
                                st["changePercentage"] = val
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing chp in quote: %s", values)
                    if "lp_time" in values and values["lp_time"] is not None:
                        try:
                            st["time"] = int(values["lp_time"])
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing lp_time in quote: %s", values)
                    high_val = values.get("high_price") if "high_price" in values else values.get("high")
                    if high_val is not None:
                        try:
                            val = float(high_val)
                            if math.isfinite(val):
                                st["high"] = val
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing high in quote: %s", values)
                    low_val = values.get("low_price") if "low_price" in values else values.get("low")
                    if low_val is not None:
                        try:
                            val = float(low_val)
                            if math.isfinite(val):
                                st["low"] = val
                        except (ValueError, TypeError):
                            logger.debug("Failed parsing low in quote: %s", values)

                    self._dispatch_quote(symbol, dict(st))

        elif data.get("m") == "timescale_update":
            params = data.get("p", [])
            if len(params) > 1 and isinstance(params[1], dict):
                payload = params[1]
                for series_id, s_data in payload.items():
                    if isinstance(s_data, dict) and "s" in s_data:
                        raw_bars = s_data["s"]
                        parsed_bars: List[Dict[str, Any]] = []
                        for b_item in raw_bars:
                            v = b_item.get("v") if isinstance(b_item, dict) else None
                            if v and len(v) >= 5:
                                try:
                                    parsed_bars.append({
                                        "time": int(v[0]),
                                        "open": float(v[1]),
                                        "high": float(v[2]),
                                        "low": float(v[3]),
                                        "close": float(v[4]),
                                        "volume": float(v[5]) if len(v) > 5 else 0.0,
                                    })
                                except (ValueError, TypeError, IndexError):
                                    continue
                        if parsed_bars:
                            series_key = None
                            if series_id in self.series_futures:
                                series_key, fut = self.series_futures[series_id]
                                if not fut.done():
                                    fut.set_result(parsed_bars)
                            elif series_id in self.series_keys:
                                series_key = self.series_keys[series_id]

                            if series_key:
                                self.series_keys[series_id] = series_key
                                if len(self.series_keys) > 200:
                                    self.series_keys.pop(next(iter(self.series_keys)), None)

                                self.chart_series[series_key] = parsed_bars
                                if len(self.chart_series) > 200:
                                    self.chart_series.pop(next(iter(self.chart_series)), None)

        elif data.get("m") == "du":
            # Live data update for active series
            params = data.get("p", [])
            if len(params) > 1 and isinstance(params[1], dict):
                payload = params[1]
                for series_id, s_data in payload.items():
                    if isinstance(s_data, dict) and "s" in s_data:
                        raw_bars = s_data["s"]
                        series_key = self.series_map.get(series_id) or (
                            self.series_futures[series_id][0] if series_id in self.series_futures else None
                        )
                        if not series_key and series_id in self.series_keys:
                            series_key = self.series_keys[series_id]

                        if series_key and series_key in self.chart_series and self.chart_series[series_key]:
                            for b_item in raw_bars:
                                v = b_item.get("v") if isinstance(b_item, dict) else None
                                if v and len(v) >= 5:
                                    try:
                                        bar_dict = {
                                            "time": int(v[0]),
                                            "open": float(v[1]),
                                            "high": float(v[2]),
                                            "low": float(v[3]),
                                            "close": float(v[4]),
                                            "volume": float(v[5]) if len(v) > 5 else 0.0,
                                        }
                                        last_bar = self.chart_series[series_key][-1]
                                        if last_bar["time"] == bar_dict["time"]:
                                            self.chart_series[series_key][-1] = bar_dict
                                        elif bar_dict["time"] > last_bar["time"]:
                                            self.chart_series[series_key].append(bar_dict)
                                            if len(self.chart_series[series_key]) > 1000:
                                                self.chart_series[series_key] = self.chart_series[series_key][-1000:]
                                    except (ValueError, TypeError, IndexError) as err:
                                        logger.debug("Failed updating live series bar: %s", err)

    async def request_chart_series(
        self,
        symbol: str,
        timeframe: str = "1D",
        n_bars: int = 300,
        timeout: float = 4.0
    ) -> List[Dict[str, Any]]:
        """Requests historical candlestick bars via TradingView WebSocket chart session (Scheme B)."""
        series_key = f"{symbol}_{timeframe}"
        if not self._is_open() or not self.cs:
            return self.chart_series.get(series_key, [])

        res_map = {
            "1m": "1", "1M": "1",
            "5m": "5", "5M": "5",
            "15m": "15", "15M": "15",
            "1h": "60", "1H": "60", "60m": "60", "60M": "60",
            "1d": "1D", "1D": "1D",
            "1w": "1W", "1W": "1W", "1wk": "1W", "1WK": "1W",
        }
        resolution = res_map.get(str(timeframe or "1D"), "1D")
        sym_id = f"sym_{_random_token(6)}"
        series_id = f"sds_{_random_token(6)}"

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.series_futures[series_id] = (series_key, fut)
        old_sid = self.key_to_series_id.get(series_key)
        if old_sid and old_sid != series_id:
            self.series_map.pop(old_sid, None)
            if self._is_open() and self.cs:
                await self._send("remove_series", [self.cs, old_sid])
        self.series_map[series_id] = series_key
        self.key_to_series_id[series_key] = series_id
        self.series_keys[series_id] = series_key
        if len(self.series_keys) > 200:
            self.series_keys.pop(next(iter(self.series_keys)), None)

        try:
            import json
            sym_spec = {"symbol": symbol, "adjustment": "splits"}
            await self._send("resolve_symbol", [self.cs, sym_id, f"={json.dumps(sym_spec)}"])
            await self._send("create_series", [self.cs, series_id, "s1", sym_id, resolution, n_bars, ""])

            bars = await asyncio.wait_for(fut, timeout=timeout)
            return bars
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug("TradingView chart series request for %s timed out or failed: %s", series_key, e)
            return self.chart_series.get(series_key, [])
        finally:
            self.series_futures.pop(series_id, None)

    async def _connection_loop(self) -> None:
        backoff = 1.0
        browser_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        while self.running:
            try:
                self._set_status("connecting")
                connect_kwargs: Dict[str, Any] = {
                    "origin": self.WS_ORIGIN,
                    "ping_interval": None,
                    "ping_timeout": None,
                    "close_timeout": 5,
                }
                try:
                    sig = inspect.signature(websockets.connect)
                    if "user_agent_header" in sig.parameters:
                        connect_kwargs["user_agent_header"] = browser_ua
                    elif "additional_headers" in sig.parameters:
                        connect_kwargs["additional_headers"] = [("User-Agent", browser_ua)]
                    elif "extra_headers" in sig.parameters:
                        connect_kwargs["extra_headers"] = [("User-Agent", browser_ua)]
                except Exception:
                    connect_kwargs["user_agent_header"] = browser_ua

                async with websockets.connect(self.WS_URL, **connect_kwargs) as ws:
                    self.ws = ws
                    self.connected = True
                    backoff = 1.0
                    self._set_status("connected")

                    await self._authenticate_and_subscribe()

                    parser = StreamingTradingViewParser()
                    while self.running:
                        raw_msg = await ws.recv()
                        chunk = raw_msg.decode("utf-8", errors="replace") if isinstance(raw_msg, (bytes, bytearray)) else str(raw_msg)
                        packets = parser.feed(chunk)
                        for pkt in packets:
                            if pkt["type"] == "heartbeat":
                                pong = encode_heartbeat_response(pkt["raw"])
                                await ws.send(pong)
                            else:
                                try:
                                    self._handle_packet(pkt)
                                except (Exception, asyncio.CancelledError) as err:
                                    logger.error("Error processing packet: %s", err, exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                self._set_status(f"disconnected: {e}")
                if not self.running:
                    break
                jitter = random.uniform(0.0, 0.5 * backoff)
                await asyncio.sleep(backoff + jitter)
                backoff = min(backoff * 2.0, 30.0)
            finally:
                self.connected = False
                self.ws = None
                self.series_map.clear()
                self.key_to_series_id.clear()
                for sid, (skey, fut) in list(self.series_futures.items()):
                    if not fut.done():
                        fut.cancel()
                self.series_futures.clear()

    async def start(self) -> None:
        """Starts the background connection supervisor in the active asyncio event loop."""
        if not self.running:
            self.running = True
            loop_coro = self._connection_loop()
            self._task = asyncio.create_task(loop_coro) # tracked

    async def stop(self) -> None:
        """Gracefully terminates connection, background callback tasks, and supervisor task with timeout."""
        self.running = False
        if self._is_open():
            try:
                await asyncio.wait_for(self.ws.close(), timeout=3.0)
            except Exception as e:
                logger.debug("Error while closing websocket: %s", e)
        if self._background_tasks:
            for t in list(self._background_tasks):
                t.cancel()
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
            self._background_tasks.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug("Supervisor task cancelled cleanly.")
        for sid, (skey, fut) in list(self.series_futures.items()):
            if not fut.done():
                fut.cancel()
        self.series_futures.clear()
        self.series_map.clear()
        self.key_to_series_id.clear()
        self._set_status("stopped")

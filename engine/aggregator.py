"""Real-Time Tick-to-Bar OHLCV Aggregator.

Quantizes incoming price and volume ticks into fixed-resolution candlestick bars (default 1-minute),
calculates rolling 24-hour volume deltas, enforces strict OHLC invariants, and provides
resilient seeding and out-of-order tick protection.
"""

from dataclasses import dataclass, asdict
from collections import deque
from typing import Dict, List, Optional, Callable, Any
import time
import math
import logging
import asyncio

logger = logging.getLogger("tradingview.aggregator")


@dataclass
class CandlestickBar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TickToBarAggregator:
    def __init__(
        self,
        timeframe_seconds: int = 60,
        max_history: int = 500,
        on_bar_update: Optional[Callable[[str, CandlestickBar], None]] = None,
        on_bar_completed: Optional[Callable[[str, CandlestickBar], None]] = None,
    ) -> None:
        self.timeframe_seconds: int = timeframe_seconds
        self.max_history: int = max_history
        self.on_bar_update: Optional[Callable[[str, CandlestickBar], None]] = on_bar_update
        self.on_bar_completed: Optional[Callable[[str, CandlestickBar], None]] = on_bar_completed

        self.current_bars: Dict[str, CandlestickBar] = {}
        self.bar_history: Dict[str, deque] = {}
        self.last_cumulative_volume: Dict[str, float] = {}

    def seed_history(self, symbol: str, bars: List[Dict[str, Any]]) -> None:
        """
        Seeds historical closed bars and sets the most recent bar as the current active bar.
        Merges with any in-flight live bars so concurrent ticks are never lost or regressed.
        """
        if symbol not in self.bar_history:
            self.bar_history[symbol] = deque(maxlen=self.max_history)
        if not bars:
            return

        existing_bars: Dict[int, CandlestickBar] = {}
        for b in self.bar_history.get(symbol, []):
            existing_bars[b.time] = b
        curr = self.current_bars.get(symbol)
        if curr is not None:
            existing_bars[curr.time] = curr

        merged_by_time: Dict[int, CandlestickBar] = {}
        for b in bars:
            t = int(b["time"])
            o = float(b["open"])
            c = float(b["close"])
            h = max(float(b["high"]), o, c)
            l = min(float(b["low"]), o, c)
            v = max(0.0, float(b.get("volume", 0.0)))
            merged_by_time[t] = CandlestickBar(time=t, open=o, high=h, low=l, close=c, volume=v)

        for t, live_bar in existing_bars.items():
            if t in merged_by_time:
                seed_bar = merged_by_time[t]
                merged_by_time[t] = CandlestickBar(
                    time=t,
                    open=seed_bar.open,
                    high=max(seed_bar.high, live_bar.high),
                    low=min(seed_bar.low, live_bar.low),
                    close=live_bar.close,
                    volume=max(seed_bar.volume, live_bar.volume),
                )
            else:
                merged_by_time[t] = live_bar

        sorted_bars = [merged_by_time[t] for t in sorted(merged_by_time.keys())]
        self.bar_history[symbol] = deque(sorted_bars[:-1], maxlen=self.max_history)
        self.current_bars[symbol] = sorted_bars[-1]

    def process_tick(
        self,
        symbol: str,
        price: Optional[float],
        cumulative_volume: Optional[float] = None,
        tick_time: Optional[int] = None,
    ) -> Optional[CandlestickBar]:
        """
        Processes an incoming tick.
        - Quantizes timestamps into timeframe buckets.
        - Calculates positive volume delta from 24h rolling volume.
        - Enforces OHLC invariants (high >= max(open, close), low <= min(open, close)).
        - Safely ignores null, NaN, infinite, or non-positive price ticks without inserting zero-price bars.
        - Drops stale/out-of-order ticks that belong to previous windows.
        - Safely isolates callbacks (on_bar_completed, on_bar_update) so subscriber exceptions never corrupt or stall active bar aggregation.
        """
        if tick_time is None or tick_time <= 0:
            tick_time = int(time.time())

        # Normalize millisecond timestamps to seconds
        if tick_time > 10_000_000_000:
            tick_time = tick_time // 1000

        if symbol not in self.bar_history:
            self.bar_history[symbol] = deque(maxlen=self.max_history)

        volume_delta = 0.0
        if cumulative_volume is not None and cumulative_volume > 0:
            prev_vol = self.last_cumulative_volume.get(symbol)
            if prev_vol is not None and cumulative_volume >= prev_vol:
                volume_delta = round(cumulative_volume - prev_vol, 8)
            self.last_cumulative_volume[symbol] = cumulative_volume

        # Validate price: must be finite, non-null, and positive (> 0.0)
        is_valid_price = False
        if price is not None:
            try:
                p = float(price)
                if math.isfinite(p) and p > 0.0:
                    price = p
                    is_valid_price = True
            except (ValueError, TypeError):
                is_valid_price = False

        # If tick has no price or invalid price, only accumulate volume on existing bar
        if not is_valid_price:
            current_bar = self.current_bars.get(symbol)
            if current_bar is not None and volume_delta > 0:
                current_bar.volume = round(current_bar.volume + volume_delta, 8)
                if self.on_bar_update:
                    try:
                        self.on_bar_update(symbol, current_bar)
                    except (Exception, asyncio.CancelledError) as e:
                        logger.error("Error in on_bar_update callback for %s: %s", symbol, e, exc_info=True)
            return current_bar

        bucket_time = tick_time - (tick_time % self.timeframe_seconds)
        current_bar = self.current_bars.get(symbol)

        if current_bar is None or bucket_time > current_bar.time:
            if current_bar is not None:
                # Defense in depth: prevent appending duplicate timestamp to bar_history
                if not self.bar_history[symbol] or self.bar_history[symbol][-1].time != current_bar.time:
                    self.bar_history[symbol].append(current_bar)
                if self.on_bar_completed:
                    try:
                        self.on_bar_completed(symbol, current_bar)
                    except (Exception, asyncio.CancelledError) as e:
                        logger.error("Error in on_bar_completed callback for %s: %s", symbol, e, exc_info=True)

            new_bar = CandlestickBar(
                time=bucket_time,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=round(volume_delta, 8),
            )
            self.current_bars[symbol] = new_bar
            if self.on_bar_update:
                try:
                    self.on_bar_update(symbol, new_bar)
                except (Exception, asyncio.CancelledError) as e:
                    logger.error("Error in on_bar_update callback for %s: %s", symbol, e, exc_info=True)
            return new_bar
        elif bucket_time == current_bar.time:
            current_bar.high = max(current_bar.high, price)
            current_bar.low = min(current_bar.low, price)
            current_bar.close = price
            current_bar.volume = round(current_bar.volume + volume_delta, 8)

            if self.on_bar_update:
                try:
                    self.on_bar_update(symbol, current_bar)
                except (Exception, asyncio.CancelledError) as e:
                    logger.error("Error in on_bar_update callback for %s: %s", symbol, e, exc_info=True)
            return current_bar
        else:
            # Out of order or historical tick: ignore to protect current active bar integrity
            return current_bar

    def get_current_bar(self, symbol: str) -> Optional[CandlestickBar]:
        return self.current_bars.get(symbol)

    def remove_symbol(self, symbol: str) -> None:
        """Removes in-memory bar state for an evicted symbol."""
        self.current_bars.pop(symbol, None)
        self.bar_history.pop(symbol, None)
        self.last_cumulative_volume.pop(symbol, None)

    def get_history(self, symbol: str) -> List[Dict[str, Any]]:
        history = list(self.bar_history.get(symbol, []))
        result = [b.to_dict() for b in history]
        current = self.current_bars.get(symbol)
        if current is not None:
            if not result or result[-1]["time"] != current.time:
                result.append(current.to_dict())
            else:
                result[-1] = current.to_dict()
        return result

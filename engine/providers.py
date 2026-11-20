"""Pluggable Market Data Provider Architecture for TradingView Desktop Terminal.

Provides an enterprise-grade abstraction layer allowing seamless switching between:
1. Licensed commercial providers (Polygon.io, Alpaca Markets)
2. Free/Reverse-engineered public fallback feeds (Binance REST, Yahoo Finance REST)
3. Offline/Cached SQLite fallback feeds
"""

from __future__ import annotations

import abc
import datetime
import os
import re
import logging
from typing import Dict, Any, List, Optional
import httpx

logger = logging.getLogger("tradingview.providers")
_VALID_TICKER_RE = re.compile(r"^(?!.*\.\.)[A-Z0-9\^][A-Z0-9._\-\^=]{0,23}$")


class BaseMarketDataProvider(abc.ABC):
    """Abstract base class for all market data feed providers."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider identifier name."""
        ...

    @abc.abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        """Determines if this provider handles the requested ticker symbol."""
        ...

    @abc.abstractmethod
    async def fetch_historical_bars(
        self, symbol: str, timeframe: str = "1m", limit: int = 300
    ) -> List[Dict[str, Any]]:
        """Fetches historical OHLCV bars formatted for Lightweight Charts.

        Returns list of dicts: [{'time': int, 'open': float, 'high': float,
                                 'low': float, 'close': float, 'volume': float}]
        """
        ...


class BinanceDataProvider(BaseMarketDataProvider):
    """High-frequency public crypto provider using Binance Public API."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None, timeout: float = 6.0):
        self._client = http_client
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "binance"

    def supports_symbol(self, symbol: str) -> bool:
        norm = symbol.upper().replace("BINANCE:", "")
        return any(norm.endswith(quote) for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH"))

    async def fetch_historical_bars(
        self, symbol: str, timeframe: str = "1m", limit: int = 300
    ) -> List[Dict[str, Any]]:
        pair = symbol.upper().replace("BINANCE:", "")
        if not _VALID_TICKER_RE.match(pair):
            return []
        raw_tf = str(timeframe or "1m").lower()
        binance_tf = "1w" if raw_tf in ("1w", "1wk") else raw_tf
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval={binance_tf}&limit={limit}"
        
        should_close = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
            should_close = True

        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("Binance API returned HTTP %s for %s", resp.status_code, pair)
                return []
            
            raw_klines = resp.json()
            bars: List[Dict[str, Any]] = []
            for k in raw_klines:
                bars.append({
                    "time": int(k[0]) // 1000,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return bars
        except Exception as e:
            logger.warning("Binance fetch error for %s: %s", pair, e)
            return []
        finally:
            if should_close:
                await client.aclose()


class YahooFinanceDataProvider(BaseMarketDataProvider):
    """Public equities and indices provider using Yahoo Finance v8 chart API."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None, timeout: float = 6.0):
        self._client = http_client
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "yahoo_finance"

    def supports_symbol(self, symbol: str) -> bool:
        norm = symbol.upper()
        return norm.startswith(("NASDAQ:", "NYSE:", "AMEX:", "FOREX:", "INDEX:")) or ":" not in norm

    def _extract_ticker(self, symbol: str) -> str:
        if ":" in symbol:
            prefix, ticker = symbol.split(":", 1)
            prefix = prefix.upper()
            if prefix == "FOREX":
                return f"{ticker.replace('.', '-')}=X"
            elif prefix == "INDEX":
                return f"^{ticker.replace('.', '-')}"
            return ticker.replace(".", "-")
        return symbol.replace(".", "-")

    async def fetch_historical_bars(
        self, symbol: str, timeframe: str = "1m", limit: int = 300
    ) -> List[Dict[str, Any]]:
        ticker = self._extract_ticker(symbol)
        if not _VALID_TICKER_RE.match(ticker.upper()):
            return []
        tf_map = {
            "1m": ("1m", "1d"),
            "5m": ("5m", "5d"),
            "15m": ("15m", "5d"),
            "60m": ("60m", "1mo"),
            "1h": ("60m", "1mo"),
            "1d": ("1d", "5y"),
            "1w": ("1wk", "5y"),
        }
        interval, y_range = tf_map.get(str(timeframe or "1m").lower(), ("1m", "1d"))
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={y_range}"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

        should_close = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
            should_close = True

        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning("Yahoo Finance API returned HTTP %s for %s", resp.status_code, ticker)
                return []

            data = resp.json()
            chart = data.get("chart", {}).get("result")
            if not chart:
                return []

            result = chart[0]
            timestamps = result.get("timestamp", [])
            indicators = result.get("indicators", {}).get("quote", [{}])[0]

            opens = indicators.get("open", [])
            highs = indicators.get("high", [])
            lows = indicators.get("low", [])
            closes = indicators.get("close", [])
            volumes = indicators.get("volume", [])

            bars: List[Dict[str, Any]] = []
            for i, ts in enumerate(timestamps):
                if i >= len(opens) or i >= len(highs) or i >= len(lows) or i >= len(closes):
                    break
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
                if o is None or h is None or l is None or c is None:
                    continue
                v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
                bars.append({
                    "time": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                })
            return bars[-limit:]
        except Exception as e:
            logger.warning("Yahoo Finance fetch error for %s: %s", ticker, e)
            return []
        finally:
            if should_close:
                await client.aclose()


class PolygonDataProvider(BaseMarketDataProvider):
    """Licensed commercial equities, forex, and crypto provider via Polygon.io REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout: float = 6.0,
    ):
        self._api_key = api_key or os.environ.get("POLYGON_API_KEY", "")
        self._client = http_client
        self._timeout = timeout

    @property
    def api_key(self) -> str:
        return self._api_key or os.environ.get("POLYGON_API_KEY", "")

    @property
    def name(self) -> str:
        return "polygon"

    @property
    def is_configured(self) -> bool:
        key = self.api_key
        return bool(key and key.strip())

    def supports_symbol(self, symbol: str) -> bool:
        if not self.is_configured:
            return False
        norm = symbol.upper()
        if norm.startswith("BINANCE:") or norm.endswith(("USDT", "BUSD", "USDC")):
            return False
        return True

    async def fetch_historical_bars(
        self, symbol: str, timeframe: str = "1m", limit: int = 300
    ) -> List[Dict[str, Any]]:
        if not self.is_configured:
            return []

        ticker = symbol.split(":")[-1] if ":" in symbol else symbol
        ticker = ticker.replace(".", "-")
        if not _VALID_TICKER_RE.match(ticker.upper()):
            return []
        poly_map = {
            "1m": (1, "minute", 7),
            "5m": (5, "minute", 30),
            "15m": (15, "minute", 60),
            "60m": (1, "hour", 90),
            "1h": (1, "hour", 90),
            "1d": (1, "day", 1825),
            "1w": (1, "week", 1825),
        }
        multiplier, timespan, days_back = poly_map.get(str(timeframe or "1m").lower(), (1, "minute", 7))
        today = datetime.date.today()
        from_date = today - datetime.timedelta(days=days_back)
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date.isoformat()}/{today.isoformat()}"
        params = {
            "adjusted": "true",
            "sort": "desc",
            "limit": str(limit),
        }

        should_close = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
            should_close = True

        try:
            resp = await client.get(url, params=params, headers={"Authorization": f"Bearer {self.api_key}"})
            if resp.status_code != 200:
                logger.warning("Polygon API returned HTTP %s for %s", resp.status_code, ticker)
                return []

            data = resp.json()
            raw_results = data.get("results", [])
            bars: List[Dict[str, Any]] = []
            for r in reversed(raw_results):
                bars.append({
                    "time": int(r["t"]) // 1000,
                    "open": float(r["o"]),
                    "high": float(r["h"]),
                    "low": float(r["l"]),
                    "close": float(r["c"]),
                    "volume": float(r.get("v", 0.0)),
                })
            return bars
        except Exception as e:
            logger.warning("Polygon API fetch error for %s: %s", ticker, e)
            return []
        finally:
            if should_close:
                await client.aclose()


class AlpacaDataProvider(BaseMarketDataProvider):
    """Licensed commercial equities and crypto provider via Alpaca Market Data API v2."""

    def __init__(
        self,
        api_key_id: Optional[str] = None,
        api_secret_key: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout: float = 6.0,
    ):
        self._key_id = api_key_id or os.environ.get("APCA_API_KEY_ID", "")
        self._secret_key = api_secret_key or os.environ.get("APCA_API_SECRET_KEY", "")
        self._client = http_client
        self._timeout = timeout

    @property
    def key_id(self) -> str:
        return self._key_id or os.environ.get("APCA_API_KEY_ID", "")

    @property
    def secret_key(self) -> str:
        return self._secret_key or os.environ.get("APCA_API_SECRET_KEY", "")

    @property
    def name(self) -> str:
        return "alpaca"

    @property
    def is_configured(self) -> bool:
        k = self.key_id
        s = self.secret_key
        return bool(k and k.strip() and s and s.strip())

    def supports_symbol(self, symbol: str) -> bool:
        if not self.is_configured:
            return False
        norm = symbol.upper()
        if norm.startswith("BINANCE:") or norm.endswith(("USDT", "BUSD", "USDC")):
            return False
        return True

    async def fetch_historical_bars(
        self, symbol: str, timeframe: str = "1m", limit: int = 300
    ) -> List[Dict[str, Any]]:
        if not self.is_configured:
            return []

        raw_ticker = symbol.split(":")[-1] if ":" in symbol else symbol
        if not _VALID_TICKER_RE.match(raw_ticker.upper()):
            return []
        ticker = raw_ticker.upper()  # Alpaca stock bars expect dotted symbols (BRK.B)
        alpaca_map = {
            "1m": "1Min",
            "5m": "5Min",
            "15m": "15Min",
            "60m": "1Hour",
            "1h": "1Hour",
            "1d": "1Day",
            "1w": "1Week",
        }
        tf_val = alpaca_map.get(str(timeframe or "1m").lower(), "1Min")
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
        headers = {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }
        params = {"timeframe": tf_val, "limit": str(limit)}

        should_close = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
            should_close = True

        try:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                logger.warning("Alpaca API returned HTTP %s for %s", resp.status_code, ticker)
                return []

            data = resp.json()
            raw_bars = data.get("bars", [])
            bars: List[Dict[str, Any]] = []
            for b in raw_bars:
                dt = datetime.datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                bars.append({
                    "time": int(dt.timestamp()),
                    "open": float(b["o"]),
                    "high": float(b["h"]),
                    "low": float(b["l"]),
                    "close": float(b["c"]),
                    "volume": float(b.get("v", 0.0)),
                })
            return bars
        except Exception as e:
            logger.warning("Alpaca API fetch error for %s: %s", ticker, e)
            return []
        finally:
            if should_close:
                await client.aclose()


class CompositeMarketDataProvider:
    """Orchestrator prioritizing licensed feeds over public fallbacks and local storage."""

    def __init__(self, providers: Optional[List[BaseMarketDataProvider]] = None):
        if providers is not None:
            self._providers = providers
        else:
            self._providers = [
                PolygonDataProvider(),
                AlpacaDataProvider(),
                BinanceDataProvider(),
                YahooFinanceDataProvider(),
            ]

    @property
    def providers(self) -> List[BaseMarketDataProvider]:
        return list(self._providers)

    async def fetch_historical_bars(
        self, symbol: str, timeframe: str = "1m", limit: int = 300
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Queries providers in configured precedence until non-empty bars are returned."""
        for provider in self._providers:
            if provider.supports_symbol(symbol):
                bars = await provider.fetch_historical_bars(symbol, timeframe=timeframe, limit=limit)
                if bars:
                    logger.info("Successfully fetched %d bars from provider '%s' for %s", len(bars), provider.name, symbol)
                    return provider.name, bars

        return "none", []

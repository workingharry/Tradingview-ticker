"""Application configuration constants, symbol/timeframe normalization, and origin checks."""

import math
import re
from typing import Any, Dict, List, Optional

from engine.screener import US_MOMENTUM_UNIVERSE

WATCHLIST: List[Dict[str, Any]] = [
    {"id": "NASDAQ:NVDA", "name": "NVIDIA Corp.", "exchange": "NASDAQ", "pair": "NVDA", "type": "stock", "sector": "Technology", "industry": "Semiconductors"},
    {"id": "NASDAQ:FAST", "name": "Fastenal Company", "exchange": "NASDAQ", "pair": "FAST", "type": "stock", "sector": "Industrials", "industry": "Industrial Distribution"},
    {"id": "NASDAQ:PRCH", "name": "Porch Group Inc.", "exchange": "NASDAQ", "pair": "PRCH", "type": "stock", "sector": "Technology", "industry": "Software - Application"},
    {"id": "AMEX:SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "AMEX", "pair": "SPY", "type": "etf", "sector": "Index ETF", "industry": "Large Cap Blend"},
    {"id": "NASDAQ:QQQ", "name": "Invesco QQQ Trust", "exchange": "NASDAQ", "pair": "QQQ", "type": "etf", "sector": "Index ETF", "industry": "Large Cap Growth"},
    {"id": "NASDAQ:AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "pair": "AAPL", "type": "stock", "sector": "Technology", "industry": "Consumer Electronics"},
    {"id": "BINANCE:BTCUSDT", "name": "Bitcoin", "exchange": "Binance", "pair": "BTC/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Layer 1"},
    {"id": "BINANCE:ETHUSDT", "name": "Ethereum", "exchange": "Binance", "pair": "ETH/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Smart Contract"},
    {"id": "BINANCE:SOLUSDT", "name": "Solana", "exchange": "Binance", "pair": "SOL/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Layer 1"},
]

POPULAR_SEARCH_INDEX: List[Dict[str, str]] = [
    {"id": "NASDAQ:NVDA", "name": "NVIDIA Corp.", "exchange": "NASDAQ", "pair": "NVDA", "type": "stock", "sector": "Technology", "industry": "Semiconductors"},
    {"id": "NASDAQ:FAST", "name": "Fastenal Company", "exchange": "NASDAQ", "pair": "FAST", "type": "stock", "sector": "Industrials", "industry": "Industrial Distribution"},
    {"id": "NASDAQ:PRCH", "name": "Porch Group Inc.", "exchange": "NASDAQ", "pair": "PRCH", "type": "stock", "sector": "Technology", "industry": "Software - Application"},
    {"id": "AMEX:SPY", "name": "SPDR S&P 500 ETF", "exchange": "AMEX", "pair": "SPY", "type": "etf", "sector": "Index ETF", "industry": "Large Cap Blend"},
    {"id": "NASDAQ:QQQ", "name": "Invesco QQQ Trust", "exchange": "NASDAQ", "pair": "QQQ", "type": "etf", "sector": "Index ETF", "industry": "Large Cap Growth"},
    {"id": "AMEX:IWM", "name": "iShares Russell 2000 ETF", "exchange": "AMEX", "pair": "IWM", "type": "etf", "sector": "Index ETF", "industry": "Small Cap Blend"},
    {"id": "NYSE:DIA", "name": "SPDR Dow Jones Industrial ETF", "exchange": "NYSE", "pair": "DIA", "type": "etf", "sector": "Index ETF", "industry": "Large Cap Value"},
    {"id": "AMEX:SMH", "name": "VanEck Semiconductor ETF", "exchange": "AMEX", "pair": "SMH", "type": "etf", "sector": "Technology", "industry": "Semiconductors"},
    {"id": "NASDAQ:AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "pair": "AAPL", "type": "stock", "sector": "Technology", "industry": "Consumer Electronics"},
    {"id": "NASDAQ:TSLA", "name": "Tesla, Inc.", "exchange": "NASDAQ", "pair": "TSLA", "type": "stock", "sector": "Consumer Cyclical", "industry": "Auto Manufacturers"},
    {"id": "NASDAQ:MSFT", "name": "Microsoft Corp.", "exchange": "NASDAQ", "pair": "MSFT", "type": "stock", "sector": "Technology", "industry": "Software - Infrastructure"},
    {"id": "NASDAQ:AMZN", "name": "Amazon.com, Inc.", "exchange": "NASDAQ", "pair": "AMZN", "type": "stock", "sector": "Consumer Cyclical", "industry": "Internet Retail"},
    {"id": "NASDAQ:GOOGL", "name": "Alphabet Inc.", "exchange": "NASDAQ", "pair": "GOOGL", "type": "stock", "sector": "Communication Services", "industry": "Internet Content"},
    {"id": "NASDAQ:META", "name": "Meta Platforms, Inc.", "exchange": "NASDAQ", "pair": "META", "type": "stock", "sector": "Communication Services", "industry": "Internet Content"},
    {"id": "NASDAQ:AMD", "name": "Advanced Micro Devices", "exchange": "NASDAQ", "pair": "AMD", "type": "stock", "sector": "Technology", "industry": "Semiconductors"},
    {"id": "NYSE:PLTR", "name": "Palantir Technologies", "exchange": "NYSE", "pair": "PLTR", "type": "stock", "sector": "Technology", "industry": "Software - Infrastructure"},
    {"id": "NASDAQ:COIN", "name": "Coinbase Global", "exchange": "NASDAQ", "pair": "COIN", "type": "stock", "sector": "Financial", "industry": "Capital Markets"},
    {"id": "BINANCE:BTCUSDT", "name": "Bitcoin", "exchange": "Binance", "pair": "BTC/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Layer 1"},
    {"id": "BINANCE:ETHUSDT", "name": "Ethereum", "exchange": "Binance", "pair": "ETH/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Smart Contract"},
    {"id": "BINANCE:SOLUSDT", "name": "Solana", "exchange": "Binance", "pair": "SOL/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Layer 1"},
    {"id": "BINANCE:BNBUSDT", "name": "Binance Coin", "exchange": "Binance", "pair": "BNB/USDT", "type": "crypto", "sector": "Cryptocurrency", "industry": "Exchange Token"},
]

SYMBOLS: List[str] = [item["id"] for item in WATCHLIST]

WS_HEARTBEAT_INTERVAL: float = 15.0

MAX_CACHE_SIZE = 200

YAHOO_EXCHANGE_MAP: Dict[str, str] = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "ASE": "AMEX",
    "PCX": "AMEX",
}


def _parse_criteria_number(
    raw: Dict[str, Any],
    key: str,
    default: Any,
    kind: type,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    errors: Optional[List[str]] = None,
    exclusive_min: bool = False,
) -> Any:
    """Safely extracts and validates numeric screening criteria.

    - Returns `default` if key is missing, None, or empty string.
    - Appends `key` to `errors` if value is non-numeric, NaN, Infinity, or out-of-bounds.
    """
    val = raw.get(key)
    if val is None or val == "":
        return default
    try:
        num = kind(val)
        if isinstance(num, float) and (math.isnan(num) or math.isinf(num)):
            if errors is not None:
                errors.append(key)
            return default
        if min_value is not None:
            if exclusive_min and num <= min_value:
                if errors is not None:
                    errors.append(key)
                return default
            elif not exclusive_min and num < min_value:
                if errors is not None:
                    errors.append(key)
                return default
        if max_value is not None and num > max_value:
            if errors is not None:
                errors.append(key)
            return default
        return num
    except (ValueError, TypeError):
        if errors is not None:
            errors.append(key)
        return default

VALID_TICKER_RE = re.compile(r"^[A-Z0-9._\-\^=]{1,24}$")
VALID_SYMBOL_ID_RE = re.compile(r"^[A-Z0-9_]{1,15}:[A-Z0-9._\-\^=]{1,24}$")
ALLOWED_ASSET_TYPES = {"crypto", "stock", "etf", "index", "forex"}

CANONICAL_TIMEFRAMES: Dict[str, str] = {
    "1m": "1m",
    "1M": "1m",
    "5m": "5m",
    "5M": "5m",
    "15m": "15m",
    "15M": "15m",
    "1h": "1h",
    "1H": "1h",
    "60m": "1h",
    "60M": "1h",
    "1d": "1D",
    "1D": "1D",
    "1w": "1W",
    "1W": "1W",
    "1wk": "1W",
    "1WK": "1W",
}


def normalize_timeframe(timeframe: Optional[str], default: str = "1D") -> str:
    """Normalizes raw timeframe input into canonical case: minute/hour lowercase, daily/weekly uppercase."""
    if not timeframe or not isinstance(timeframe, str):
        return default
    cleaned = timeframe.strip()
    return CANONICAL_TIMEFRAMES.get(cleaned, CANONICAL_TIMEFRAMES.get(cleaned.upper(), default))


def is_valid_ticker(ticker: str) -> bool:
    """Validates that a raw ticker contains only safe financial symbol characters."""
    return bool(ticker and VALID_TICKER_RE.match(ticker.strip().upper()))


def is_valid_symbol_id(symbol: str) -> bool:
    """Validates EXCHANGE:TICKER or standalone TICKER format against injection/traversal."""
    if not symbol or len(symbol) > 40:
        return False
    clean = symbol.strip().upper()
    if ":" in clean:
        return bool(VALID_SYMBOL_ID_RE.match(clean))
    return bool(VALID_TICKER_RE.match(clean))


def normalize_symbol_id(raw_symbol: Optional[str]) -> str:
    """Normalizes bare or prefixed symbols into canonical EXCHANGE:TICKER format."""
    if not raw_symbol or not isinstance(raw_symbol, str):
        return ""
    clean = raw_symbol.strip().upper()
    if not clean:
        return ""
    if ":" in clean:
        return clean
    for pool in (WATCHLIST, POPULAR_SEARCH_INDEX, US_MOMENTUM_UNIVERSE):
        for entry in pool:
            eid = str(entry.get("id") or "").strip().upper()
            if ":" in eid and eid.split(":", 1)[1] == clean:
                return eid
    if "USDT" in clean or "BUSD" in clean:
        return f"BINANCE:{clean}"
    return f"NASDAQ:{clean}"

def is_crypto_symbol(symbol: str) -> bool:
    """Detects whether a symbol corresponds to crypto or an equity/ETF."""
    sym_upper = symbol.upper()
    return sym_upper.startswith("BINANCE:") or "USDT" in sym_upper or "BUSD" in sym_upper

TAURI_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]
LOOPBACK_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$"


def is_allowed_origin(origin: Optional[str]) -> bool:
    """Validates origin against permitted Tauri schemes and loopback origins."""
    if not origin:
        return True
    origin = origin.strip().rstrip("/")
    if origin.startswith("tauri://"):
        return True
    for o in TAURI_ORIGINS:
        if origin == o.rstrip("/"):
            return True
    if re.match(LOOPBACK_ORIGIN_REGEX, origin, re.IGNORECASE):
        return True
    return False

MIN_1M_HISTORY_BARS: int = 30
ONE_MIN_SEED_COOLDOWN_SEC: float = 60.0
ONE_MIN_INFLIGHT_WAIT_SEC: float = 3.0

GEMINI_MODELS_CASCADE: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

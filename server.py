"""TradingView Real-Time Terminal Backend Server.

FastAPI application providing unified asyncio lifespan management, REST endpoints
for symbols, search, dynamic subscription, and historical candles (dual-source: Binance
for crypto, Yahoo Finance v8 for equities/ETFs/benchmarks), a WebSocket live broadcaster
with bounded drop-oldest queues, and static asset delivery.
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
import secrets
import signal
import socket
import sys
import time
import threading
import urllib.parse
from contextlib import asynccontextmanager, contextmanager
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Sequence, Union, Tuple

def init_certifi_ca_bundle() -> Optional[str]:
    """Initializes and verifies TLS root CA certificates from certifi.

    Guarantees that SSL_CERT_FILE and REQUESTS_CA_BUNDLE point to a valid, existing
    CA bundle on disk, repairing any stale or nonexistent inherited environment variables
    to prevent SSL/TLS connection failures during HTTPS requests to market data providers.
    """
    ca_bundle: Optional[str] = None
    try:
        import certifi
        bundle_candidate = certifi.where()
        if bundle_candidate and os.path.exists(bundle_candidate):
            ca_bundle = str(bundle_candidate)
    except Exception as _cert_err:
        logging.getLogger("tradingview.server").debug("Certifi import notice: %s", _cert_err)

    # Fallback search locations for PyInstaller frozen bundles
    if not ca_bundle:
        search_roots = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                search_roots.append(Path(meipass))
            exec_dir = Path(sys.executable).resolve().parent
            search_roots.append(exec_dir)
            search_roots.append(exec_dir / "_internal")

        for root in search_roots:
            for sub in [Path("certifi") / "cacert.pem", Path("cacert.pem")]:
                candidate = root / sub
                if candidate.exists():
                    ca_bundle = str(candidate)
                    break
            if ca_bundle:
                break

    if ca_bundle and os.path.exists(ca_bundle):
        # Repair SSL_CERT_FILE if unset or pointing to a non-existent file
        current_ssl = os.environ.get("SSL_CERT_FILE")
        if not current_ssl or not os.path.exists(current_ssl):
            os.environ["SSL_CERT_FILE"] = ca_bundle

        # Repair REQUESTS_CA_BUNDLE if unset or pointing to a non-existent file
        current_req = os.environ.get("REQUESTS_CA_BUNDLE")
        if not current_req or not os.path.exists(current_req):
            os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle

        return ca_bundle
    return None


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tradingview.server")
CA_BUNDLE_PATH: Optional[str] = init_certifi_ca_bundle()

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from engine.aggregator import TickToBarAggregator, CandlestickBar
from engine.client import TradingViewAsyncClient
from engine.storage import get_storage, close_storage
from engine.providers import PolygonDataProvider, AlpacaDataProvider
from engine.screener import (
    ScreeningCriteria,
    US_MOMENTUM_UNIVERSE,
    scan_universe,
    evaluate_canslim_stage,
)


def load_dotenv_custom(paths: Optional[List[Path]] = None) -> List[str]:
    """Loads environment variables from .env files without external dependencies."""
    candidate_paths = paths if paths is not None else [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
        Path.home() / ".env",
        Path.home() / ".gemini" / ".env",
    ]
    loaded = []
    seen = set()
    for p in candidate_paths:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k:
                    # Prevent ambient home .env files from polluting ephemeral desktop runtime flags
                    if resolved in (Path.home() / ".env", Path.home() / ".gemini" / ".env") and k in ("DESKTOP_MODE", "DESKTOP_PORT", "DESKTOP_TOKEN"):
                        continue
                    # Never set nonexistent SSL CA file paths from .env
                    if k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE") and not os.path.exists(v):
                        logger.warning("Ignoring nonexistent CA bundle path in %s for %s: %s", resolved, k, v)
                        continue
                    os.environ.setdefault(k, v)
        loaded.append(str(resolved))
    return loaded


load_dotenv_custom()

# Ensure CA bundle remains valid if .env declared invalid or missing paths
if CA_BUNDLE_PATH and os.path.exists(CA_BUNDLE_PATH):
    current_ssl = os.environ.get("SSL_CERT_FILE")
    if not current_ssl or not os.path.exists(current_ssl):
        os.environ["SSL_CERT_FILE"] = CA_BUNDLE_PATH
    current_req = os.environ.get("REQUESTS_CA_BUNDLE")
    if not current_req or not os.path.exists(current_req):
        os.environ["REQUESTS_CA_BUNDLE"] = CA_BUNDLE_PATH

_poly_provider = PolygonDataProvider()
_alpaca_provider = AlpacaDataProvider()


class YahooSessionManager:
    """Thread-safe and async-safe Yahoo Finance session manager acquiring cookies and crumb."""
    _instance: Optional["YahooSessionManager"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "YahooSessionManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        self._thread_lock = threading.Lock()
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None
        self._cookies: Dict[str, str] = {}
        self._crumb: Optional[str] = None
        self._expires_at: float = 0.0
        self._initialized = True

    def _get_lock(self) -> asyncio.Lock:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        with self._thread_lock:
            if self._lock is None or self._lock_loop is not current_loop:
                self._lock = asyncio.Lock()
                self._lock_loop = current_loop
        return self._lock

    def is_valid(self) -> bool:
        return bool(self._cookies) and (time.monotonic() < self._expires_at)

    async def get_session(self, force_refresh: bool = False) -> Tuple[Dict[str, str], Optional[str]]:
        if not force_refresh and self.is_valid():
            return self._cookies, self._crumb

        async with self._get_lock():
            if not force_refresh and self.is_valid():
                return self._cookies, self._crumb
            try:
                headers = {"User-Agent": self._user_agent}
                async with httpx.AsyncClient(timeout=10.0, headers=headers, follow_redirects=True) as cli:
                    resp_cookie = await cli.get("https://fc.yahoo.com")
                    raw_cookies = dict(cli.cookies)
                    crumb_resp = await cli.get("https://query1.finance.yahoo.com/v1/test/getcrumb")
                    crumb = None
                    if crumb_resp.status_code == 200:
                        crumb_text = crumb_resp.text.strip()
                        if crumb_text and "<" not in crumb_text:
                            crumb = crumb_text
                    self._cookies = raw_cookies
                    self._crumb = crumb
                    self._expires_at = time.monotonic() + 3600.0
                    logging.getLogger("tradingview.server").debug(
                        "Yahoo session refreshed: %d cookies, crumb=%s", len(self._cookies), bool(self._crumb)
                    )
            except Exception as e:
                logging.getLogger("tradingview.server").warning("Error refreshing Yahoo session: %s", e)
                self._expires_at = time.monotonic() + 15.0
            return self._cookies, self._crumb

    def invalidate(self) -> None:
        self._expires_at = 0.0


# logger initialized before load_dotenv_custom()

# Desktop mode runtime configuration
_desktop_mode: bool = os.environ.get("DESKTOP_MODE", "").lower() in ("1", "true", "yes")
_desktop_token: Optional[str] = os.environ.get("DESKTOP_TOKEN") or None
_desktop_port: Optional[int] = None
if os.environ.get("DESKTOP_PORT"):
    try:
        _desktop_port = int(os.environ["DESKTOP_PORT"])
    except ValueError:
        logger.warning("Invalid DESKTOP_PORT environment variable: %s", os.environ.get("DESKTOP_PORT"))
        _desktop_port = None


def is_desktop_mode() -> bool:
    """Returns whether the server is currently operating in macOS desktop mode."""
    return _desktop_mode


def get_desktop_token() -> Optional[str]:
    """Returns the active desktop session authentication token."""
    global _desktop_token
    if _desktop_mode and not _desktop_token:
        _desktop_token = secrets.token_hex(16)
    return _desktop_token


def get_desktop_port() -> Optional[int]:
    """Returns the bound loopback port for desktop mode."""
    return _desktop_port


def set_desktop_mode(
    enabled: bool = True,
    token: Optional[str] = None,
    port: Optional[int] = None
) -> None:
    """Configures desktop mode flag, session token, and bound port."""
    global _desktop_mode, _desktop_token, _desktop_port
    _desktop_mode = enabled
    if enabled:
        _desktop_token = token or _desktop_token or secrets.token_hex(16)
        if port is not None:
            _desktop_port = port
    else:
        _desktop_token = None
        _desktop_port = None


@contextmanager
def desktop_mode_context(token: Optional[str] = None, port: int = 0):
    """Context manager for safely testing desktop mode with auto-restoration."""
    prev_mode = _desktop_mode
    prev_token = _desktop_token
    prev_port = _desktop_port
    try:
        active_token = token or secrets.token_hex(16)
        set_desktop_mode(enabled=True, token=active_token, port=port)
        yield active_token
    finally:
        set_desktop_mode(enabled=prev_mode, token=prev_token, port=prev_port)


def bind_loopback_socket(host: str = "127.0.0.1", port: int = 0) -> tuple[socket.socket, int]:
    """Binds a TCP loopback socket, returning the socket object and OS-assigned port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    assigned_port = sock.getsockname()[1]
    return sock, assigned_port


def is_parent_alive(initial_ppid: int) -> bool:
    """Checks whether the original parent process is still alive to prevent orphaned sidecars."""
    if initial_ppid <= 1:
        return True
    current_ppid = os.getppid()
    if current_ppid == 1 or current_ppid != initial_ppid:
        return False
    try:
        os.kill(initial_ppid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


async def _parent_watchdog_loop(initial_ppid: int, interval: float = 2.0) -> None:
    """Background task in desktop mode that gracefully stops the server if the parent Tauri process dies."""
    try:
        while True:
            await asyncio.sleep(interval)
            if not is_parent_alive(initial_ppid):
                logger.warning("Parent process (PID=%d) no longer alive; shutting down sidecar.", initial_ppid)
                os.kill(os.getpid(), signal.SIGTERM)
                break
    except asyncio.CancelledError:
        logger.debug("Parent watchdog task cancelled during shutdown.")


def verify_token(token: Optional[str]) -> bool:
    """Verifies that the provided token matches the active desktop session token using constant-time comparison."""
    if not is_desktop_mode():
        return True
    active_token = get_desktop_token()
    if not active_token or not token:
        return False
    return secrets.compare_digest(str(token).strip(), str(active_token).strip())


def extract_token_from_request(request: Request) -> Optional[str]:
    """Extracts session token from query parameters, X-Session-Token header, or Authorization header."""
    query_token = request.query_params.get("token")
    if query_token and query_token.strip():
        return query_token.strip()

    header_token = request.headers.get("x-session-token") or request.headers.get("x-terminal-token")
    if header_token and header_token.strip():
        return header_token.strip()

    auth_header = request.headers.get("authorization")
    if auth_header:
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        elif len(parts) == 1 and parts[0].strip():
            return parts[0].strip()

    return None


class DesktopAuthMiddleware(BaseHTTPMiddleware):
    """Enforces session token verification for REST endpoints when desktop mode is active."""

    async def dispatch(self, request: Request, call_next):
        csp = "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com https://s3.tradingview.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* https://*.tradingview.com wss://*.tradingview.com; frame-src 'self' https://s.tradingview.com https://*.tradingview.com;"
        if not is_desktop_mode():
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Content-Security-Policy", csp)
            return response

        # Allow preflight CORS OPTIONS requests without auth
        if request.method == "OPTIONS":
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Content-Security-Policy", csp)
            return response

        # Allow unauthenticated health check probe and non-API routes (static files, index)
        path = request.url.path
        normalized_path = path.rstrip("/")
        if normalized_path == "/api/health" or not normalized_path.startswith("/api"):
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Content-Security-Policy", csp)
            return response

        token = extract_token_from_request(request)
        if not verify_token(token):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized: invalid or missing session token",
                    "detail": "Invalid or missing session token"
                }
            )

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

def resolve_static_dir() -> Path:
    """Resolves the static assets directory for both development and frozen PyInstaller bundles."""
    if getattr(sys, "frozen", False):
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_path = Path(meipass)
            candidates.append(meipass_path / "static")
            candidates.append(meipass_path / "_internal" / "static")

        exec_dir = Path(sys.executable).resolve().parent
        candidates.append(exec_dir / "_internal" / "static")
        candidates.append(exec_dir / "static")

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return candidates[0] if candidates else Path(__file__).resolve().parent / "static"
    return Path(__file__).resolve().parent / "static"


STATIC_DIR = resolve_static_dir()

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


class ConnectionManager:
    """Manages active WebSocket client connections with per-client bounded queues and drop-oldest strategy."""

    def __init__(self, max_queue_size: int = 100):
        self.max_queue_size = max_queue_size
        self.client_queues: Dict[WebSocket, asyncio.Queue] = {}
        self.client_loops: Dict[WebSocket, asyncio.AbstractEventLoop] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.client_queues[websocket] = queue
        try:
            self.client_loops[websocket] = asyncio.get_running_loop()
        except RuntimeError as loop_err:
            logger.debug("Failed to acquire running loop for websocket: %s", loop_err)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.client_queues:
            del self.client_queues[websocket]
        if websocket in self.client_loops:
            del self.client_loops[websocket]

    def broadcast_nowait(self, message: Dict[str, Any]) -> None:
        """Non-blocking drop-oldest broadcast to all connected client queues."""
        for ws, queue in list(self.client_queues.items()):
            loop = self.client_loops.get(ws) or getattr(queue, "_loop", None)

            def _push(q=queue, m=message):
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        logger.debug("Broadcaster queue already drained.")
                try:
                    q.put_nowait(m)
                except asyncio.QueueFull:
                    logger.debug("Broadcaster queue full; message dropped.")

            if loop and loop.is_running():
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None
                if current_loop is loop:
                    _push()
                else:
                    loop.call_soon_threadsafe(_push)
            else:
                _push()

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Awaitable broadcast wrapper for async callers and testing."""
        self.broadcast_nowait(message)

    async def client_writer(self, websocket: WebSocket) -> None:
        """Dedicated writer task for a single WebSocket connection."""
        queue = self.client_queues.get(websocket)
        if not queue:
            return
        try:
            while True:
                await asyncio.sleep(0)
                msg = await queue.get()
                await websocket.send_json(msg)
                queue.task_done()
        except (WebSocketDisconnect, asyncio.CancelledError):
            logger.debug("Client writer disconnected")
        except Exception as e:
            logger.debug("Client writer error on %s: %s", websocket, e)
        finally:
            self.disconnect(websocket)

    async def close_all(self) -> None:
        """Gracefully closes all active client connections during server shutdown."""
        for ws in list(self.client_queues.keys()):
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception as e:
                logger.debug("Client websocket close error: %s", e)
        self.client_queues.clear()
        self.client_loops.clear()


manager = ConnectionManager(max_queue_size=100)

aggregator: Optional[TickToBarAggregator] = None
tv_client: Optional[TradingViewAsyncClient] = None
current_status = "initializing"

MAX_CACHE_SIZE = 200
_yahoo_cache: OrderedDict[str, tuple[float, List[Dict[str, Any]]]] = OrderedDict()
_inflight_history: Dict[str, asyncio.Task] = {}
_shared_client: Optional[httpx.AsyncClient] = None

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


def get_shared_client() -> Optional[httpx.AsyncClient]:
    """Returns the active shared HTTP client if server lifespan is running, else None."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        return _shared_client
    return None


def is_crypto_symbol(symbol: str) -> bool:
    """Detects whether a symbol corresponds to crypto or an equity/ETF."""
    sym_upper = symbol.upper()
    return sym_upper.startswith("BINANCE:") or "USDT" in sym_upper or "BUSD" in sym_upper


async def fetch_binance_history(symbol: str, limit: int = 300, **kwargs) -> List[Dict[str, Any]]:
    """Fetches historical kline candles from Binance public REST API."""
    if not is_valid_symbol_id(symbol):
        return []
    raw_pair = symbol.split(":")[-1].upper()
    if not is_valid_ticker(raw_pair):
        return []
    raw_tf = str(kwargs.get("interval", "1m")).lower()
    binance_tf = "1w" if raw_tf in ("1w", "1wk") else raw_tf
    eff_limit = 1500 if binance_tf in ("1d", "1w") and limit == 300 else limit
    url = f"https://api.binance.com/api/v3/klines?symbol={raw_pair}&interval={binance_tf}&limit={eff_limit}"
    bars: List[Dict[str, Any]] = []

    async def _fetch(http_cli: httpx.AsyncClient) -> None:
        resp = await http_cli.get(url)
        if resp.status_code == 200:
            raw_bars = resp.json()
            if isinstance(raw_bars, list):
                for b in raw_bars:
                    try:
                        bars.append({
                            "time": int(b[0]) // 1000,
                            "open": float(b[1]),
                            "high": float(b[2]),
                            "low": float(b[3]),
                            "close": float(b[4]),
                            "volume": float(b[5]),
                        })
                    except (IndexError, ValueError, TypeError):
                        continue
        else:
            logger.warning("Binance API returned status %s for %s", resp.status_code, symbol)

    try:
        shared = get_shared_client()
        if shared is not None:
            await _fetch(shared)
        else:
            async with httpx.AsyncClient(timeout=8.0) as http_cli:
                await _fetch(http_cli)
    except Exception as e:
        logger.warning("Could not fetch Binance history for %s: %s", symbol, e)
    return bars


async def fetch_yahoo_history(symbol: str, timeframe: str = "1m") -> List[Dict[str, Any]]:
    """Fetches historical kline candles for US equities, ETFs, and indices from Yahoo Finance v8 API with TTL caching and LRU eviction."""
    if not is_valid_symbol_id(symbol):
        return []
    ticker = symbol.split(":")[-1].upper().replace(".", "-")
    if not is_valid_ticker(ticker):
        return []
    cache_key = f"{ticker}_{timeframe}"
    now = time.time()
    if cache_key in _yahoo_cache:
        cached_time, cached_bars = _yahoo_cache[cache_key]
        ttl = 10.0 if not cached_bars else 30.0
        if now - cached_time < ttl:
            _yahoo_cache.move_to_end(cache_key)
            return cached_bars
        del _yahoo_cache[cache_key]

    tf_map = {
        "1m": ("1m", "1d"),
        "5m": ("5m", "5d"),
        "15m": ("15m", "5d"),
        "1h": ("60m", "1mo"),
        "1D": ("1d", "5y"),
        "1d": ("1d", "5y"),
        "1W": ("1wk", "5y"),
        "1w": ("1wk", "5y"),
    }
    clean_tf = normalize_timeframe(timeframe, default="1m")
    interval, range_str = tf_map.get(clean_tf, tf_map.get(timeframe, ("1m", "1d")))
    hosts = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    bars: List[Dict[str, Any]] = []

    async def _fetch(http_cli: httpx.AsyncClient) -> None:
        for host in hosts:
            url = f"https://{host}/v8/finance/chart/{ticker}?interval={interval}&range={range_str}"
            try:
                resp = await http_cli.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("chart", {}).get("result")
                    if results and len(results) > 0:
                        res0 = results[0]
                        timestamps = res0.get("timestamp") or []
                        quote = res0.get("indicators", {}).get("quote", [{}])[0]
                        opens = quote.get("open", [])
                        highs = quote.get("high", [])
                        lows = quote.get("low", [])
                        closes = quote.get("close", [])
                        volumes = quote.get("volume", [])

                        n = len(timestamps)
                        for i in range(n):
                            t = timestamps[i]
                            o = opens[i] if i < len(opens) else None
                            h = highs[i] if i < len(highs) else None
                            l = lows[i] if i < len(lows) else None
                            c = closes[i] if i < len(closes) else None
                            v = volumes[i] if i < len(volumes) else 0

                            if t is not None and o is not None and h is not None and l is not None and c is not None:
                                bars.append({
                                    "time": int(t),
                                    "open": float(o),
                                    "high": float(h),
                                    "low": float(l),
                                    "close": float(c),
                                    "volume": float(v) if v is not None else 0.0,
                                })
                        if bars:
                            if len(_yahoo_cache) >= MAX_CACHE_SIZE:
                                _yahoo_cache.popitem(last=False)
                            _yahoo_cache[cache_key] = (now, bars)
                            return
                elif resp.status_code == 429:
                    logger.debug("Yahoo Finance API returned status 429 for %s, entering negative cooldown", ticker)
                    break
                elif resp.status_code in (500, 502, 503):
                    logger.debug("Yahoo Finance API on %s returned status %s for %s, trying next host...", host, resp.status_code, ticker)
                    continue
            except Exception as host_err:
                logger.debug("Yahoo Finance host %s network error for %s: %s", host, ticker, host_err)
                continue
        if not bars:
            if len(_yahoo_cache) >= MAX_CACHE_SIZE:
                _yahoo_cache.popitem(last=False)
            _yahoo_cache[cache_key] = (now, [])

    try:
        shared = get_shared_client()
        if shared is not None:
            await _fetch(shared)
        else:
            async with httpx.AsyncClient(timeout=8.0, headers=headers) as http_cli:
                await _fetch(http_cli)
    except Exception as e:
        logger.warning("Could not fetch Yahoo history for %s: %s", ticker, e)
    return bars


async def fetch_historical_bars(symbol: str, timeframe: str = "1m", limit: int = 300) -> List[Dict[str, Any]]:
    """Fetches historical OHLCV bars with licensed provider precedence, dual-source fallback, SQLite caching, and offline support."""
    if not is_valid_symbol_id(symbol):
        return []
    clean_tf = normalize_timeframe(timeframe, default="1m")
    if limit == 300 and clean_tf in ("1D", "1W", "1WK"):
        limit = 1500
    bars: List[Dict[str, Any]] = []

    # 0. Cache-first fast path: if SQLite already holds sufficiently fresh bars, serve immediately (0ms latency)
    storage = get_storage()
    cached = storage.get_bars(symbol, clean_tf, limit=limit)
    min_expected = min(limit, 250) if clean_tf in ("1D", "1W", "1WK") else min(limit, 30)
    if cached and len(cached) >= min_expected:
        now_ts = int(time.time())
        last_bar_ts = cached[-1].get("time", 0)
        tf_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1D": 86400, "1W": 604800}.get(clean_tf, 86400)
        if (now_ts - last_bar_ts) <= tf_seconds:
            logger.info("Serving %d fresh cached bars from SQLite for %s (%s) [cache-first]", len(cached), symbol, clean_tf)
            return cached

    # 1. Check licensed commercial providers if credentials are configured in environment
    if _poly_provider.is_configured:
        try:
            bars = await _poly_provider.fetch_historical_bars(symbol, timeframe=clean_tf, limit=limit)
        except Exception as poly_err:
            logger.warning("Licensed Polygon provider error for %s: %s", symbol, poly_err)

    if not bars and _alpaca_provider.is_configured:
        try:
            bars = await _alpaca_provider.fetch_historical_bars(symbol, timeframe=clean_tf, limit=limit)
        except Exception as alpaca_err:
            logger.warning("Licensed Alpaca provider error for %s: %s", symbol, alpaca_err)

    # 2. Public fallback feeds (Binance for crypto, Yahoo Finance for equities)
    if not bars:
        try:
            if is_crypto_symbol(symbol):
                if clean_tf == "1m":
                    bars = await fetch_binance_history(symbol, limit=limit)
                else:
                    bars = await fetch_binance_history(symbol, limit=limit, interval=clean_tf)
            else:
                bars = await fetch_yahoo_history(symbol, timeframe=clean_tf)
        except Exception as e:
            logger.warning("Remote fetch historical bars error for %s (%s): %s", symbol, clean_tf, e)

    storage = get_storage()
    if bars:
        try:
            storage.save_bars(symbol, clean_tf, bars)
        except Exception as save_err:
            logger.warning("Failed to persist bars to SQLite for %s: %s", symbol, save_err)
        return bars

    cached = storage.get_bars(symbol, clean_tf, limit=limit)
    if cached:
        logger.info("Serving %d cached bars from SQLite for %s (%s) [offline fallback]", len(cached), symbol, clean_tf)
        return cached

    return []


async def fetch_universal_history(symbol: str, timeframe: str = "1D") -> List[Dict[str, Any]]:
    """Universal router dispatching with singleflight deduplication to prevent cache stampedes."""
    flight_key = f"{symbol}_{timeframe}"
    existing_task = _inflight_history.get(flight_key)
    if existing_task is not None and not existing_task.done():
        return await asyncio.shield(existing_task)

    async def _runner() -> List[Dict[str, Any]]:
        try:
            req_limit = 1500 if str(timeframe).upper() in ("1D", "1W", "1WK") else 300
            return await fetch_historical_bars(symbol, timeframe=timeframe, limit=req_limit)
        except Exception as e:
            logger.warning("Fetch universal history error for %s (%s): %s", symbol, timeframe, e)
            return []
        finally:
            _inflight_history.pop(flight_key, None)

    runner_coro = _runner()
    task = asyncio.create_task(runner_coro) # tracked
    _inflight_history[flight_key] = task
    return await asyncio.shield(task)


def on_bar_update_handler(symbol: str, bar: CandlestickBar) -> None:
    """Dispatches real-time bar updates to connected WebSocket clients."""
    manager.broadcast_nowait({
        "type": "bar",
        "symbol": symbol,
        "bar": bar.to_dict()
    })


def on_quote_update_handler(symbol: str, quote_data: Dict[str, Any]) -> None:
    """Feeds incoming quote ticks to the aggregator and broadcasts quote updates."""
    price = quote_data.get("price")
    vol = quote_data.get("volume")
    t = quote_data.get("time")

    if aggregator:
        aggregator.process_tick(symbol, price=price, cumulative_volume=vol, tick_time=t)

    manager.broadcast_nowait({
        "type": "quote",
        "symbol": symbol,
        "data": quote_data
    })


def on_status_change_handler(status: str) -> None:
    """Updates global connection status and notifies clients."""
    global current_status
    current_status = status
    manager.broadcast_nowait({
        "type": "status",
        "status": status
    })


def on_bar_completed_handler(symbol: str, bar: CandlestickBar) -> None:
    """Persists completed 1m candlestick bar to SQLite storage."""
    try:
        get_storage().save_bars(symbol, "1m", [bar])
    except Exception as e:
        logger.warning("Failed to persist completed bar for %s: %s", symbol, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI unified lifespan manager executing within the primary asyncio loop."""
    global aggregator, tv_client, current_status, WATCHLIST, SYMBOLS
    logger.info("Initializing TradingView real-time engine...")
    current_status = "initializing"

    storage = get_storage()
    try:
        persisted_wl = storage.get_watchlist()
        if persisted_wl:
            WATCHLIST.clear()
            WATCHLIST.extend(persisted_wl)
            SYMBOLS.clear()
            SYMBOLS.extend([w["id"] for w in persisted_wl])
    except Exception as wl_err:
        logger.warning("Failed to load watchlist from SQLite: %s", wl_err)

    aggregator = TickToBarAggregator(
        timeframe_seconds=60,
        max_history=500,
        on_bar_update=on_bar_update_handler,
        on_bar_completed=on_bar_completed_handler
    )

    try:
        pruned_count = storage.prune_bars(keep_per_series=2000)
        if pruned_count > 0:
            logger.info("Pruned %d excess historical bars on startup", pruned_count)
    except Exception as e:
        logger.warning("Failed to prune old bars on startup: %s", e)

    # Seed historical bars for crypto symbols concurrently using shared HTTP client
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=8.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )

    crypto_symbols = [s["id"] for s in WATCHLIST if s["id"].startswith("BINANCE:")]
    # Immediate non-blocking seed from local SQLite cache (0ms startup latency)
    for sym in crypto_symbols:
        cached = storage.get_bars(sym, "1m", limit=300)
        if cached:
            aggregator.seed_history(sym, cached)
            logger.info("Cold-start offline seeded %d cached bars for %s", len(cached), sym)

    # Schedule background network refresh without blocking SERVER_READY handshake
    async def _background_crypto_seed():
        try:
            seed_tasks = [fetch_binance_history(sym, limit=300) for sym in crypto_symbols]
            seed_results = await asyncio.gather(*seed_tasks, return_exceptions=True)
            for sym, res in zip(crypto_symbols, seed_results):
                if isinstance(res, list) and res:
                    aggregator.seed_history(sym, res)
                    try:
                        storage.save_bars(sym, "1m", res)
                    except Exception as e:
                        logger.warning("Failed to persist startup bars for %s: %s", sym, e)
                    logger.info("Background seeded %d live historical bars for %s", len(res), sym)
        except Exception as seed_err:
            logger.warning("Background crypto seeding failed: %s", seed_err)

    crypto_seed_coro = _background_crypto_seed()
    crypto_seed_task: Optional[asyncio.Task] = asyncio.create_task(crypto_seed_coro) # tracked
    try:
        await asyncio.wait([crypto_seed_task], timeout=0.05)
    except Exception as seed_wait_err:
        logger.debug("Non-blocking crypto seed micro-wait exited: %s", seed_wait_err)

    tv_client = TradingViewAsyncClient(
        symbols=SYMBOLS,
        on_quote_update=on_quote_update_handler,
        on_status_change=on_status_change_handler
    )

    await tv_client.start()
    logger.info("TradingView WebSocket client running in unified lifespan loop.")

    watchdog_task: Optional[asyncio.Task] = None
    if is_desktop_mode():
        port = get_desktop_port() or 8000
        token = get_desktop_token() or ""
        handshake = f"SERVER_READY:PORT={port}:TOKEN={token}"
        print(handshake, flush=True)
        logger.info("Desktop mode readiness handshake emitted: PORT=%s", port)
        watchdog_coro = _parent_watchdog_loop(os.getppid())
        watchdog_task = asyncio.create_task(watchdog_coro) # tracked

    yield

    logger.info("Shutting down TradingView engine...")
    if crypto_seed_task and not crypto_seed_task.done():
        crypto_seed_task.cancel()
        try:
            await crypto_seed_task
        except asyncio.CancelledError:
            logger.debug("Crypto seed task cancellation completed.")
    if watchdog_task and not watchdog_task.done():
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            logger.debug("Parent watchdog task shutdown completed.")
    if tv_client:
        await tv_client.stop()
    await manager.close_all()
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
    _inflight_history.clear()
    close_storage()
    logger.info("Engine shutdown complete.")


app = FastAPI(title="TradingView Real-Time Terminal", lifespan=lifespan)

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

# Mount DesktopAuthMiddleware first, then CORSMiddleware so that CORSMiddleware
# wraps as the outermost middleware, ensuring CORS headers are added to all responses
# (including 401 Unauthorized responses).
app.add_middleware(DesktopAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=TAURI_ORIGINS,
    allow_origin_regex=LOOPBACK_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
@app.get("/api/health/")
async def health_check():
    """Health check endpoint for Tauri sidecar process manager and monitoring."""
    return {
        "status": "ok",
        "desktop_mode": is_desktop_mode(),
        "port": get_desktop_port(),
        "timestamp": time.time()
    }


@app.get("/api/symbols")
async def get_symbols():
    """Returns the curated watchlist symbols, active quote states, and connection status."""
    data = []
    for item in WATCHLIST:
        sym = item["id"]
        st = tv_client.states.get(sym, {}) if tv_client else {}
        data.append({
            **item,
            "state": st
        })
    return {"symbols": data, "status": current_status}


@app.get("/api/symbols/search")
async def search_symbols(q: str = Query(default="")):
    """Searches across popular stocks, ETFs, crypto, and queries live Yahoo search with synthesis fallback."""
    query = q.strip().upper()

    # Build comprehensive local universe index (Momentum Universe + Watchlist + Popular Index)
    combined_local: List[Dict[str, Any]] = list(POPULAR_SEARCH_INDEX)
    seen_ids = {item["id"] for item in combined_local}

    for u in US_MOMENTUM_UNIVERSE:
        uid = u["id"]
        if uid not in seen_ids:
            seen_ids.add(uid)
            exch = uid.split(":")[0] if ":" in uid else "NASDAQ"
            combined_local.append({
                "id": uid,
                "name": u.get("name") or u["symbol"],
                "exchange": exch,
                "pair": u["symbol"],
                "type": "stock",
                "sector": u.get("sector", "Technology"),
                "industry": ""
            })

    for w in WATCHLIST:
        wid = w["id"]
        if wid not in seen_ids:
            seen_ids.add(wid)
            combined_local.append(w)

    if not query:
        return {"results": combined_local[:24]}

    results = []
    # 1. Local index match
    for item in combined_local:
        if (query in item["id"].upper() or
            query in item["name"].upper() or
            query in item.get("pair", "").upper()):
            results.append(item)

    # 2. Live remote search via Yahoo Finance API
    try:
        y_url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}&quotesCount=15&newsCount=0"
        shared = get_shared_client()
        cli = shared or httpx.AsyncClient(timeout=4.0)
        should_close = shared is None
        try:
            resp = await cli.get(y_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                y_data = resp.json()
                quotes = y_data.get("quotes", [])
                for q_item in quotes:
                    raw_ticker = str(q_item.get("symbol") or "").strip().upper()
                    if not raw_ticker or not is_valid_ticker(raw_ticker.replace(".", "-").replace("^", "")):
                        continue
                    q_type = str(q_item.get("quoteType", "")).lower()
                    sym_type = "crypto" if q_type in ("cryptocurrency", "crypto") else ("etf" if q_type == "etf" else ("index" if q_type == "index" else "stock"))
                    exch = str(q_item.get("exchDisp") or q_item.get("exchange") or "US").upper()
                    short_name = q_item.get("shortname") or q_item.get("longname") or raw_ticker
                    sid = f"{exch}:{raw_ticker}" if ":" not in raw_ticker else raw_ticker
                    sec = str(q_item.get("sector") or ("Cryptocurrency" if sym_type == "crypto" else ("Index ETF" if sym_type == "etf" else "")))[:64]
                    ind = str(q_item.get("industry") or ("Layer 1" if sym_type == "crypto" else ("Large Cap Blend" if sym_type == "etf" else "")))[:64]
                    if not any(r["id"] == sid or r["pair"].upper() == raw_ticker for r in results):
                        results.append({
                            "id": sid,
                            "name": str(short_name)[:64],
                            "exchange": exch[:20],
                            "pair": raw_ticker[:24],
                            "type": sym_type,
                            "sector": sec,
                            "industry": ind
                        })
        finally:
            if should_close:
                await cli.aclose()
    except Exception as e:
        logger.debug("Remote Yahoo search query failed for %s: %s", query, e)

    found_exact = any(item["pair"].upper() == query or item["id"].upper() == query for item in results)
    if not found_exact and is_valid_symbol_id(query):
        if "USDT" in query:
            synth_id = f"BINANCE:{query}" if not query.startswith("BINANCE:") else query
            pair_name = synth_id.split(":")[-1]
            results.insert(0, {
                "id": synth_id,
                "name": pair_name,
                "exchange": "Binance",
                "pair": pair_name,
                "type": "crypto"
            })
        else:
            synth_id = query if ":" in query else f"NASDAQ:{query}"
            exch = synth_id.split(":")[0]
            pair = synth_id.split(":")[-1]
            results.insert(0, {
                "id": synth_id,
                "name": f"{pair} (Custom Ticker)",
                "exchange": exch,
                "pair": pair,
                "type": "stock"
            })

    return {"results": results[:15]}


@app.post("/api/symbols/subscribe")
async def subscribe_symbol(
    payload: Optional[Dict[str, Any]] = Body(default=None),
    symbol: Optional[str] = Query(default=None)
):
    """Dynamically subscribes to a new symbol at runtime and warms its historical bar aggregator."""
    clean_query_sym = symbol if isinstance(symbol, str) else None
    clean_payload = payload if isinstance(payload, dict) else {}
    raw_val = clean_query_sym or clean_payload.get("symbol") or clean_payload.get("id") or clean_payload.get("pair") or ""
    raw_symbol = str(raw_val).strip().upper() if isinstance(raw_val, (str, int, float)) else ""
    if not raw_symbol:
        return JSONResponse({"error": "Symbol is required"}, status_code=400)

    if ":" not in raw_symbol:
        if "USDT" in raw_symbol:
            symbol = f"BINANCE:{raw_symbol}"
        else:
            symbol = f"NASDAQ:{raw_symbol}"
    else:
        symbol = raw_symbol

    if not is_valid_symbol_id(symbol):
        return JSONResponse({"error": "Invalid symbol format"}, status_code=400)

    pair = symbol.split(":")[-1]
    raw_name = clean_payload.get("name")
    name = str(raw_name).strip()[:64] if isinstance(raw_name, str) and raw_name.strip() else pair
    raw_exch = clean_payload.get("exchange")
    exchange = str(raw_exch).strip()[:20] if isinstance(raw_exch, str) and raw_exch.strip() else symbol.split(":")[0]
    raw_type = str(clean_payload.get("type") or "").strip().lower()
    sym_type = raw_type if raw_type in ALLOWED_ASSET_TYPES else ("crypto" if is_crypto_symbol(symbol) else "stock")
    raw_sec = clean_payload.get("sector")
    raw_ind = clean_payload.get("industry")
    sector = str(raw_sec).strip()[:64] if isinstance(raw_sec, str) and raw_sec.strip() else ("Cryptocurrency" if sym_type == "crypto" else ("Index ETF" if sym_type == "etf" else ""))
    industry = str(raw_ind).strip()[:64] if isinstance(raw_ind, str) and raw_ind.strip() else ""

    item = {
        "id": symbol,
        "name": name,
        "exchange": exchange,
        "pair": pair,
        "type": sym_type,
        "sector": sector,
        "industry": industry
    }

    if not any(w["id"] == symbol for w in WATCHLIST):
        MAX_WATCHLIST_SIZE = 50
        if len(WATCHLIST) >= MAX_WATCHLIST_SIZE:
            # Preserve initial core symbols (first 5), evict oldest dynamic symbol
            evict_idx = 5 if len(WATCHLIST) > 5 else 0
            evicted = WATCHLIST.pop(evict_idx)
            evicted_id = evicted["id"]
            if evicted_id in SYMBOLS and evicted_id not in [w["id"] for w in WATCHLIST]:
                SYMBOLS.remove(evicted_id)
                try:
                    get_storage().remove_watchlist_item(evicted_id)
                except Exception as wl_evict_err:
                    logger.debug("Failed to remove evicted watchlist item from SQLite: %s", wl_evict_err)
                if tv_client:
                    try:
                        await tv_client.unsubscribe(evicted_id)
                        logger.info("Unsubscribed evicted symbol from TradingView client: %s", evicted_id)
                    except Exception as unsub_err:
                        logger.debug("Error unsubscribing evicted symbol %s: %s", evicted_id, unsub_err)
                if aggregator:
                    if hasattr(aggregator, "remove_symbol"):
                        aggregator.remove_symbol(evicted_id)
                    else:
                        aggregator.bar_history.pop(evicted_id, None)
                        aggregator.current_bars.pop(evicted_id, None)
                        aggregator.last_cumulative_volume.pop(evicted_id, None)
                manager.broadcast_nowait({
                    "type": "symbol_removed",
                    "symbol": evicted_id,
                })
        WATCHLIST.append(item)
        if symbol not in SYMBOLS:
            SYMBOLS.append(symbol)
        try:
            get_storage().add_watchlist_item(item)
        except Exception as wl_save_err:
            logger.warning("Failed to persist new watchlist item to SQLite: %s", wl_save_err)

    if tv_client:
        await tv_client.subscribe(symbol)

    if aggregator and not aggregator.get_history(symbol):
        bars = await fetch_universal_history(symbol, timeframe="1m")
        if bars:
            aggregator.seed_history(symbol, bars)
            logger.info("Dynamically seeded %d bars for new subscription: %s", len(bars), symbol)

    manager.broadcast_nowait({
        "type": "symbol_added",
        "symbol": item
    })

    return {"status": "subscribed", "symbol": item}


@app.delete("/api/symbols/unsubscribe")
@app.post("/api/symbols/unsubscribe")
async def unsubscribe_symbol(payload: Optional[Dict[str, Any]] = Body(default=None), symbol: Optional[str] = Query(default=None)):
    """Unsubscribes from a symbol, evicts from watchlist, and updates persistent storage."""
    clean_query_sym = symbol if isinstance(symbol, str) else None
    clean_payload = payload if isinstance(payload, dict) else {}
    raw_sym = clean_query_sym or clean_payload.get("symbol") or clean_payload.get("id") or ""
    if not raw_sym:
        return JSONResponse({"error": "Symbol is required"}, status_code=400)
    clean_sym = str(raw_sym).strip().upper()
    if ":" not in clean_sym:
        clean_sym = f"BINANCE:{clean_sym}" if "USDT" in clean_sym else f"NASDAQ:{clean_sym}"

    storage = get_storage()
    try:
        storage.remove_watchlist_item(clean_sym)
    except Exception as e:
        logger.warning("Failed to remove watchlist item from storage: %s", e)

    removed = False
    for i, w in enumerate(list(WATCHLIST)):
        if w["id"] == clean_sym:
            WATCHLIST.pop(i)
            removed = True
            break
    if clean_sym in SYMBOLS:
        SYMBOLS.remove(clean_sym)

    if tv_client:
        try:
            await tv_client.unsubscribe(clean_sym)
        except Exception as e:
            logger.debug("Error unsubscribing TradingView client for %s: %s", clean_sym, e)

    if aggregator and hasattr(aggregator, "remove_symbol"):
        aggregator.remove_symbol(clean_sym)

    manager.broadcast_nowait({
        "type": "symbol_removed",
        "symbol": clean_sym
    })
    return {"status": "unsubscribed", "symbol": clean_sym, "removed": removed}


@app.post("/api/symbols/reorder")
async def reorder_symbols_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Persists a new sort order for the user's watchlist."""
    clean_payload = payload if isinstance(payload, dict) else {}
    order = clean_payload.get("order")
    if not order or not isinstance(order, list):
        return JSONResponse({"error": "Invalid order payload, list of symbol IDs required"}, status_code=400)

    clean_ids = [str(s).strip().upper() for s in order if str(s).strip()]
    storage = get_storage()
    reordered = False
    try:
        reordered = storage.reorder_watchlist(clean_ids)
    except Exception as e:
        logger.warning("Failed to persist reordered watchlist to storage: %s", e)

    order_map = {sid: i for i, sid in enumerate(clean_ids)}
    global WATCHLIST, SYMBOLS
    WATCHLIST.sort(key=lambda w: order_map.get(w.get("id"), 9999))
    SYMBOLS = [w["id"] for w in WATCHLIST]

    return {"status": "ok", "reordered": reordered, "count": len(clean_ids)}


@app.get("/api/history")
async def get_history(
    symbol: str = Query(default="BINANCE:BTCUSDT"),
    timeframe: str = Query(default="1m"),
    seed: bool = Query(default=False),
    backfill: bool = Query(default=False),
    engine: str = Query(default="A")
):
    """Returns historical OHLCV bars supporting Scheme A (dual-source) and Scheme B (TradingView WebSocket series)."""
    clean_tf = normalize_timeframe(timeframe, default="1m")
    if not aggregator:
        return {"symbol": symbol, "timeframe": clean_tf, "engine": engine, "bars": []}

    # Scheme B: TradingView WebSocket Native Protocol Series
    if engine.upper() in ("B", "TV_WS") and tv_client and tv_client.connected:
        try:
            tv_bars = await tv_client.request_chart_series(symbol, timeframe=clean_tf, n_bars=300, timeout=2.0)
            if tv_bars and len(tv_bars) > 0:
                return {"symbol": symbol, "timeframe": clean_tf, "engine": "B", "bars": tv_bars}
        except Exception as e:
            logger.debug("Scheme B TV WS request error: %s", e)

    # Scheme A: Dual-Source High Availability (Binance for Crypto, Yahoo Finance for Equities)
    if clean_tf == "1m":
        bars = aggregator.get_history(symbol)
        if (not bars or backfill) and (
            seed
            or backfill
            or symbol.startswith("BINANCE:")
            or symbol in ("AMEX:SPY", "NASDAQ:QQQ", "AMEX:IWM")
            or symbol.endswith(":SPY")
            or symbol.endswith(":QQQ")
            or symbol.endswith(":IWM")
        ):
            seed_bars = await fetch_universal_history(symbol, timeframe="1m")
            if seed_bars:
                aggregator.seed_history(symbol, seed_bars)
                bars = aggregator.get_history(symbol)
        if not bars:
            cached_bars = get_storage().get_bars(symbol, timeframe="1m", limit=300)
            if cached_bars:
                aggregator.seed_history(symbol, cached_bars)
                bars = aggregator.get_history(symbol)
        return {"symbol": symbol, "timeframe": clean_tf, "engine": "A", "bars": bars}

    bars = await fetch_universal_history(symbol, timeframe=clean_tf)
    return {"symbol": symbol, "timeframe": clean_tf, "engine": "A", "bars": bars}


GEMINI_MODELS_CASCADE: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]


async def call_gemini_generate(prompt: str, temperature: float = 0.4, max_tokens: int = 800) -> Optional[str]:
    """Calls Gemini API with automatic model waterfall (2.5-flash -> 2.5-flash-lite -> 2.0-flash)."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return None
    shared = get_shared_client()
    cli = shared or httpx.AsyncClient(timeout=12.0)
    should_close = shared is None
    headers = {"x-goog-api-key": gemini_key}
    try:
        for model in GEMINI_MODELS_CASCADE:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}
            }
            try:
                res = await cli.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    candidates = res.json().get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts if "text" in p)
                        if text.strip():
                            return text.strip()
                elif res.status_code in (404, 429, 503):
                    logger.debug("Gemini model %s returned HTTP %d, cascading...", model, res.status_code)
                    continue
            except Exception as e:
                logger.debug("Gemini model %s network exception: %s", model, e)
                continue
    finally:
        if should_close:
            await cli.aclose()
    return None


@app.get("/api/fundamentals")
async def get_fundamentals(symbol: str = Query(...), refresh: bool = Query(default=False)):
    """Fetches key fundamental financial metrics and Gemini AI CANSLIM analysis for a symbol."""
    if not is_valid_symbol_id(symbol):
        return JSONResponse({"error": "Invalid symbol format"}, status_code=400)

    clean_sym = symbol.strip().upper()
    storage = get_storage()

    if not refresh:
        cached = storage.get_fundamental_report(clean_sym)
        if cached:
            return {"status": "ok", "cached": True, **cached}

    # Fetch fundamental stats from Yahoo Finance with Session Crumb
    ticker = clean_sym.split(":")[-1].replace(".", "-")
    session_mgr = YahooSessionManager()
    cookies, crumb = await session_mgr.get_session()
    crumb_param = f"&crumb={crumb}" if crumb else ""
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=financialData,defaultKeyStatistics,summaryDetail,price{crumb_param}"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    metrics: Dict[str, Any] = {
        "symbol": clean_sym,
        "ticker": ticker,
        "current_price": None,
        "market_cap": None,
        "pe_ratio": None,
        "forward_pe": None,
        "eps": None,
        "revenue_yoy": None,
        "gross_margins": None,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
        "pct_from_high": None,
        "company_name": ticker,
    }

    try:
        shared = get_shared_client()
        cli = shared or httpx.AsyncClient(timeout=8.0, headers=headers)
        should_close = shared is None
        try:
            req_headers = dict(headers)
            if cookies:
                req_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            resp = await cli.get(url, headers=req_headers)

            if resp.status_code in (401, 429):
                cookies, crumb = await session_mgr.get_session(force_refresh=True)
                crumb_param = f"&crumb={crumb}" if crumb else ""
                url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=financialData,defaultKeyStatistics,summaryDetail,price{crumb_param}"
                req_headers = dict(headers)
                if cookies:
                    req_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
                resp = await cli.get(url, headers=req_headers)

            if resp.status_code == 200:
                q_data = resp.json().get("quoteSummary", {}).get("result", [{}])[0]
                summary = q_data.get("summaryDetail", {})
                fin = q_data.get("financialData", {})
                stats = q_data.get("defaultKeyStatistics", {})
                price = q_data.get("price", {})

                cur_p = (price.get("regularMarketPrice") or {}).get("raw")
                h52 = (summary.get("fiftyTwoWeekHigh") or {}).get("raw")
                l52 = (summary.get("fiftyTwoWeekLow") or {}).get("raw")

                pct_from_h = None
                if cur_p is not None and h52 and h52 > 0:
                    pct_from_h = round(((cur_p - h52) / h52) * 100.0, 2)

                metrics.update({
                    "current_price": cur_p,
                    "market_cap": (price.get("marketCap") or summary.get("marketCap") or {}).get("fmt"),
                    "pe_ratio": (summary.get("trailingPE") or {}).get("raw"),
                    "forward_pe": (stats.get("forwardPE") or {}).get("raw"),
                    "eps": (stats.get("trailingEps") or {}).get("raw"),
                    "revenue_yoy": (fin.get("revenueGrowth") or {}).get("fmt"),
                    "gross_margins": (fin.get("grossMargins") or {}).get("fmt"),
                    "fifty_two_week_high": h52,
                    "fifty_two_week_low": l52,
                    "pct_from_high": pct_from_h,
                    "company_name": price.get("shortName") or price.get("longName") or ticker,
                })
        finally:
            if should_close:
                await cli.aclose()
    except Exception as e:
        logger.warning("Failed to fetch Yahoo fundamentals for %s: %s", ticker, e)

    if metrics["current_price"] is None or metrics["fifty_two_week_high"] is None:
        try:
            chart_url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
            chart_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json"
            }
            shared_chart = get_shared_client()
            chart_cli = shared_chart or httpx.AsyncClient(timeout=6.0, headers=chart_headers)
            should_close_chart = shared_chart is None
            try:
                c_resp = await chart_cli.get(chart_url, headers=chart_headers)
                if c_resp.status_code == 200:
                    c_data = c_resp.json()
                    c_results = c_data.get("chart", {}).get("result", [{}])
                    if c_results:
                        meta = c_results[0].get("meta", {})
                        h52 = meta.get("fiftyTwoWeekHigh")
                        l52 = meta.get("fiftyTwoWeekLow")
                        cur_p = meta.get("regularMarketPrice")
                        comp_name = meta.get("longName") or meta.get("shortName") or ticker
                        pct_h = None
                        if cur_p is not None and h52 and h52 > 0:
                            pct_h = round(((cur_p - h52) / h52) * 100.0, 2)

                        if cur_p is not None and metrics["current_price"] is None:
                            metrics["current_price"] = cur_p
                        if h52 is not None and metrics["fifty_two_week_high"] is None:
                            metrics["fifty_two_week_high"] = h52
                        if l52 is not None and metrics["fifty_two_week_low"] is None:
                            metrics["fifty_two_week_low"] = l52
                        if pct_h is not None and metrics["pct_from_high"] is None:
                            metrics["pct_from_high"] = pct_h
                        if comp_name:
                            metrics["company_name"] = comp_name

                        timestamps = c_results[0].get("timestamp") or []
                        quote = c_results[0].get("indicators", {}).get("quote", [{}])[0]
                        o_list = quote.get("open", [])
                        h_list = quote.get("high", [])
                        l_list = quote.get("low", [])
                        c_list = quote.get("close", [])
                        v_list = quote.get("volume", [])
                        parsed_bars = []
                        for bi in range(len(timestamps)):
                            bt = timestamps[bi]
                            bo = o_list[bi] if bi < len(o_list) else None
                            bh = h_list[bi] if bi < len(h_list) else None
                            bl = l_list[bi] if bi < len(l_list) else None
                            bc = c_list[bi] if bi < len(c_list) else None
                            bv = v_list[bi] if bi < len(v_list) else 0
                            if bt and bo is not None and bh is not None and bl is not None and bc is not None:
                                parsed_bars.append({
                                    "time": int(bt), "open": float(bo), "high": float(bh),
                                    "low": float(bl), "close": float(bc), "volume": float(bv or 0)
                                })
                        if parsed_bars:
                            storage.save_bars(clean_sym, "1D", parsed_bars)
            finally:
                if should_close_chart:
                    await chart_cli.aclose()
        except Exception as chart_fb_err:
            logger.debug("Chart endpoint fundamental fallback notice for %s: %s", ticker, chart_fb_err)

    if metrics["current_price"] is None or metrics["fifty_two_week_high"] is None:
        try:
            fb_bars = storage.get_bars(clean_sym, "1D", limit=260)
            if not fb_bars and aggregator:
                fb_bars = aggregator.get_history(clean_sym)
            if fb_bars:
                latest = fb_bars[-1]
                if metrics["current_price"] is None:
                    metrics["current_price"] = latest.get("close")
                highs = [b["high"] for b in fb_bars if isinstance(b.get("high"), (int, float))]
                lows = [b["low"] for b in fb_bars if isinstance(b.get("low"), (int, float))]
                if highs and metrics["fifty_two_week_high"] is None:
                    metrics["fifty_two_week_high"] = round(max(highs), 2)
                if lows and metrics["fifty_two_week_low"] is None:
                    metrics["fifty_two_week_low"] = round(min(lows), 2)
                if metrics["current_price"] and metrics["fifty_two_week_high"] and metrics["fifty_two_week_high"] > 0:
                    metrics["pct_from_high"] = round(((metrics["current_price"] - metrics["fifty_two_week_high"]) / metrics["fifty_two_week_high"]) * 100.0, 2)
        except Exception as fb_err:
            logger.debug("Metrics fallback from cached bars notice for %s: %s", ticker, fb_err)

    ai_summary = ""
    model_name = "gemini-2.5-flash"
    gemini_key = os.environ.get("GEMINI_API_KEY")

    if gemini_key:
        try:
            prompt = f"""請以資深美股動能/CANSLIM投資分析師角色，分析以下標的之基本面與技術位階動能：
標的：{clean_sym} ({metrics.get('company_name', ticker)})
現價：{metrics.get('current_price', 'N/A')}
52週高點：{metrics.get('fifty_two_week_high', 'N/A')}
距52週高點：{metrics.get('pct_from_high', 'N/A')}%
本益比 (PE)：{metrics.get('pe_ratio', 'N/A')}
前瞻本益比：{metrics.get('forward_pe', 'N/A')}
每股盈餘 (EPS)：{metrics.get('eps', 'N/A')}
營收YoY成長率：{metrics.get('revenue_yoy', 'N/A')}
毛利率：{metrics.get('gross_margins', 'N/A')}

請以結構化繁體中文產出報告，包含以下4大模組：
1. 【核心財務與估值指標評估】
2. 【近期營收與獲利動能 (CANSLIM 標準)】
3. 【機構籌碼與技術位階綜合評析】
4. 【關鍵潛在風險與下檔支撐提示】"""
            gen_text = await call_gemini_generate(prompt, temperature=0.4, max_tokens=800)
            if gen_text:
                ai_summary = gen_text
        except Exception as gemini_err:
            logger.debug("Gemini API call failed, generating fallback summary: %s", gemini_err)

    if not ai_summary:
        h_diff = f"{metrics.get('pct_from_high', 'N/A')}%"
        pe_val = metrics.get('pe_ratio', 'N/A')
        rev_val = metrics.get('revenue_yoy', 'N/A')
        ai_summary = f"""### 📊 {clean_sym} 基本面量化診斷摘要 (離線/本機模式)
1. **估值與規模**：目前市值約 {metrics.get('market_cap', 'N/A')}，本益比 (PE) 為 {pe_val}，前瞻本益比為 {metrics.get('forward_pe', 'N/A')}。
2. **營收與成長**：最新季度營收成長率 YoY 約為 {rev_val}，毛利率約為 {metrics.get('gross_margins', 'N/A')}。
3. **技術位階與高點距離**：當前股價距離 52 週最高點約 {h_diff}。依據 CANSLIM 準則，距高點 15% 以內之強勢突破股具備較佳動能。
4. **風險提示**：高本益比或成長趨緩時期需注意大盤拉回回測季線 (50 SMA) 之支撐力道。"""

    storage.save_fundamental_report(
        symbol=clean_sym,
        metrics=metrics,
        ai_summary=ai_summary,
        model_used=model_name
    )

    return {
        "status": "ok",
        "cached": False,
        "symbol": clean_sym,
        "metrics": metrics,
        "ai_summary": ai_summary,
        "model_used": model_name,
        "updated_at": int(time.time())
    }


def _calc_ema(prices: Sequence[float], period: int) -> Optional[float]:
    """Calculates Exponential Moving Average over a price sequence."""
    if not prices or len(prices) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(prices[:period]) / float(period)
    for p in prices[period:]:
        ema = p * k + ema * (1.0 - k)
    return ema


def _calc_sma(prices: Sequence[float], period: int) -> Optional[float]:
    """Calculates Simple Moving Average over a price sequence."""
    if not prices or len(prices) < period or period <= 0:
        return None
    return sum(prices[-period:]) / float(period)


def build_ai_agent_lens_payload(
    symbol: str,
    timeframe: str,
    bars: List[Dict[str, Any]],
    benchmark_bars: Optional[List[Dict[str, Any]]] = None,
    fundamentals: Optional[Dict[str, Any]] = None,
    is_live: Optional[bool] = None,
) -> Dict[str, Any]:
    """Generates a non-repainting structured quantitative JSON snapshot (schema: ai-agent-lens/v1).

    Enforces the Non-Repainting Contract:
    All indicators, moving averages, and trend regimes are calculated strictly on finalized,
    confirmed candles. When the latest candle is actively forming (live intraday session),
    it is isolated under 'unconfirmed_latest_bar'. When the market period has already closed
    (e.g., weekend/after-hours daily candles or explicitly confirmed bars), all bars are confirmed.
    """
    clean_sym = str(symbol).strip().upper()
    clean_tf = normalize_timeframe(timeframe, default="1D")

    if not bars:
        return {
            "schema": "ai-agent-lens/v1",
            "symbol": clean_sym,
            "timeframe": clean_tf,
            "timestamp": int(time.time()),
            "is_confirmed_bar": False,
            "confirmed_bar": None,
            "unconfirmed_latest_bar": None,
            "quant_metrics": {
                "trend_regime": "NEUTRAL",
                "close": None,
                "ema10": None,
                "ema21": None,
                "sma50": None,
                "sma200": None,
                "adr_pct_20": None,
                "volume_ratio_50": None,
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "pct_from_52w_high": None,
                "rs_ratio": None,
                "rs_rating": None,
            },
            "fundamentals": fundamentals or {},
        }

    now_ts = int(time.time())
    tf_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1D": 86400, "1W": 604800}.get(clean_tf, 86400)
    last_bar = bars[-1]

    if is_live is None:
        if "is_closed" in last_bar:
            latest_is_live = not bool(last_bar["is_closed"])
        elif "is_confirmed" in last_bar:
            latest_is_live = not bool(last_bar["is_confirmed"])
        elif "unconfirmed" in last_bar:
            latest_is_live = bool(last_bar["unconfirmed"])
        else:
            last_ts = int(last_bar.get("time", 0))
            if abs(now_ts - last_ts) <= (7 * tf_seconds):
                latest_is_live = (now_ts < (last_ts + tf_seconds))
            else:
                latest_is_live = False
    else:
        latest_is_live = bool(is_live)

    if latest_is_live and len(bars) >= 2:
        confirmed_bars = bars[:-1]
        confirmed_bar = dict(confirmed_bars[-1])
        unconfirmed_bar = {
            "time": bars[-1]["time"],
            "open": bars[-1].get("open"),
            "high": bars[-1].get("high"),
            "low": bars[-1].get("low"),
            "close": bars[-1].get("close"),
            "volume": bars[-1].get("volume", 0.0),
        }
    else:
        confirmed_bars = bars
        confirmed_bar = dict(bars[-1])
        unconfirmed_bar = None

    prices = [float(b["close"]) for b in confirmed_bars if b.get("close") is not None and not math.isnan(float(b["close"]))]
    volumes = [float(b.get("volume", 0.0)) for b in confirmed_bars if not math.isnan(float(b.get("volume", 0.0)))]

    ema10 = _calc_ema(prices, 10)
    ema21 = _calc_ema(prices, 21)
    sma50 = _calc_sma(prices, 50)
    sma200 = _calc_sma(prices, 200)

    # 20-period ADR%
    adr_bars = confirmed_bars[-20:]
    adr_pcts = [
        ((float(b["high"]) - float(b["low"])) / float(b["low"])) * 100.0
        for b in adr_bars
        if b.get("high") is not None and b.get("low") is not None and float(b["low"]) > 0
    ]
    adr_pct_20 = (sum(adr_pcts) / len(adr_pcts)) if adr_pcts else None

    # 50-period volume ratio
    vol_window = volumes[-50:]
    mean_vol_50 = (sum(vol_window) / len(vol_window)) if vol_window else 0.0
    confirmed_vol = float(confirmed_bar.get("volume", 0.0))
    vol_ratio_50 = (confirmed_vol / mean_vol_50) if mean_vol_50 > 0 else 1.0

    # 52W High / Low
    win_len = 52 if clean_tf in ("1W", "1WK") else 252
    h52_window = confirmed_bars[-win_len:] if len(confirmed_bars) > win_len else confirmed_bars
    valid_highs = [float(b["high"]) for b in h52_window if b.get("high") is not None]
    valid_lows = [float(b["low"]) for b in h52_window if b.get("low") is not None]
    confirmed_close = float(confirmed_bar["close"])

    high_52w = max(valid_highs) if valid_highs else confirmed_close
    low_52w = min(valid_lows) if valid_lows else confirmed_close
    pct_from_52w_high = (((confirmed_close - high_52w) / high_52w) * 100.0) if high_52w > 0 else 0.0

    # RS Ratio & RS Rating vs benchmark
    rs_ratio = None
    rs_rating = None
    if benchmark_bars:
        bench_confirmed = None
        for bb in reversed(benchmark_bars):
            if bb.get("time") is not None and bb["time"] <= confirmed_bar["time"]:
                bench_confirmed = bb
                break
        if bench_confirmed is None and benchmark_bars:
            bench_confirmed = benchmark_bars[-1]

        if bench_confirmed and bench_confirmed.get("close") and float(bench_confirmed["close"]) > 0:
            rs_ratio = (confirmed_close / float(bench_confirmed["close"])) * 100.0

        if len(confirmed_bars) >= 5 and len(benchmark_bars) >= 5:
            start_ts = max(confirmed_bars[0].get("time", 0), benchmark_bars[0].get("time", 0))
            t_aligned = [b for b in confirmed_bars if b.get("time", 0) >= start_ts]
            b_aligned = [b for b in benchmark_bars if b.get("time", 0) >= start_ts]
            if len(t_aligned) >= 2 and len(b_aligned) >= 2:
                t_last = float(t_aligned[-1]["close"])
                t_first = float(t_aligned[0]["close"])
                b_last = float(b_aligned[-1]["close"])
                b_first = float(b_aligned[0]["close"])
                if t_first > 0 and b_first > 0 and t_last > 0 and b_last > 0:
                    t_perf = (t_last - t_first) / t_first
                    b_perf = (b_last - b_first) / b_first
                    diff = (t_perf - b_perf) * 100.0
                    try:
                        score = int(round(100.0 / (1.0 + math.exp(-diff / 30.0))))
                        rs_rating = max(1, min(99, score))
                    except Exception:
                        rs_rating = 50

    # Trend Regime Classification (Stan Weinstein / Minervini Stage Analysis)
    c = confirmed_close
    if ema10 is not None and ema21 is not None and sma50 is not None and sma200 is not None:
        if c > ema10 > ema21 > sma50 > sma200 and sma50 > sma200:
            trend_regime = "STAGE_2_STRONG_UPTREND"
        elif sma50 > sma200 and c > sma50:
            trend_regime = "STAGE_2_UPTREND_PULLBACK"
        elif (c < sma50 and sma50 < sma200) or (c < sma200 and sma50 < sma200):
            trend_regime = "STAGE_4_DOWNTREND"
        else:
            trend_regime = "NEUTRAL"
    elif ema10 is not None and ema21 is not None and sma50 is not None:
        if c > ema10 > ema21 > sma50:
            trend_regime = "STAGE_2_STRONG_UPTREND"
        elif c > sma50:
            trend_regime = "STAGE_2_UPTREND_PULLBACK"
        elif c < sma50:
            trend_regime = "STAGE_4_DOWNTREND"
        else:
            trend_regime = "NEUTRAL"
    else:
        trend_regime = "NEUTRAL"

    quant_metrics = {
        "trend_regime": trend_regime,
        "close": round(confirmed_close, 4),
        "ema10": round(ema10, 4) if ema10 is not None else None,
        "ema21": round(ema21, 4) if ema21 is not None else None,
        "sma50": round(sma50, 4) if sma50 is not None else None,
        "sma200": round(sma200, 4) if sma200 is not None else None,
        "adr_pct_20": round(adr_pct_20, 2) if adr_pct_20 is not None else None,
        "volume_ratio_50": round(vol_ratio_50, 2) if vol_ratio_50 is not None else None,
        "fifty_two_week_high": round(high_52w, 4),
        "fifty_two_week_low": round(low_52w, 4),
        "pct_from_52w_high": round(pct_from_52w_high, 2),
        "rs_ratio": round(rs_ratio, 4) if rs_ratio is not None else None,
        "rs_rating": rs_rating,
    }

    return {
        "schema": "ai-agent-lens/v1",
        "symbol": clean_sym,
        "timeframe": clean_tf,
        "timestamp": int(time.time()),
        "is_confirmed_bar": True,
        "confirmed_bar": confirmed_bar,
        "unconfirmed_latest_bar": unconfirmed_bar,
        "quant_metrics": quant_metrics,
        "fundamentals": fundamentals or {},
    }


def generate_deterministic_ai_lens_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generates an offline, deterministic, expert CANSLIM / Stage 2 quantitative decision."""
    quant = payload.get("quant_metrics", {})
    regime = quant.get("trend_regime", "NEUTRAL")
    close_p = quant.get("close") or 100.0
    ema10 = quant.get("ema10") or close_p
    ema21 = quant.get("ema21") or (close_p * 0.98)
    sma50 = quant.get("sma50") or (close_p * 0.95)
    pct_from_high = quant.get("pct_from_52w_high", 0.0)
    vol_ratio = quant.get("volume_ratio_50") or 1.0
    rs_rating = quant.get("rs_rating")

    funds = payload.get("fundamentals") or {}
    fund_notes = []
    if funds.get("pe_ratio") is not None:
        fund_notes.append(f"PE: {funds['pe_ratio']}")
    if funds.get("revenue_yoy"):
        fund_notes.append(f"營收YoY: {funds['revenue_yoy']}")
    if funds.get("eps") is not None:
        fund_notes.append(f"EPS: {funds['eps']}")
    fund_diag = ("、".join(fund_notes) + "。") if fund_notes else "基本面財務指標穩健，持續追蹤季報與機構籌碼動向。"

    if regime == "STAGE_2_STRONG_UPTREND":
        if pct_from_high >= -15.0 and (rs_rating is None or rs_rating >= 65):
            bias = "LONG"
            confidence = min(95, 80 + (5 if vol_ratio > 1.2 else 0) + (5 if (rs_rating or 0) >= 80 else 0))
            setup = f"強勢第二階段多頭格局 (Stage 2 Uptrend)，均線呈多頭排列，距52週高點僅 {pct_from_high:.1f}%。"
            invalidation = f"日K收盤跌破 21 EMA (${ema21:.2f}) 或跌破前波整理波段低點。"
            failure = "大盤指數 (SPY) 出現帶量分配日群聚，或高檔獲利了結賣壓引發假突破。"
            human_note = "建議逢拉回 10/21 EMA 附近分批介入，嚴格守停損，第一目標前波高點。"
        else:
            bias = "WAIT"
            confidence = 68
            setup = f"處於多頭架構但短線正乖離偏大或距離高點較遠 ({pct_from_high:.1f}%)，待量縮整理。"
            invalidation = f"實體跌破 50 SMA (${sma50:.2f}) 即轉為弱勢整理。"
            failure = "均線過度發散後引發之均值回歸強烈回洗。"
            human_note = "耐心等待量縮回測 21 EMA 確認獲得支撐後再尋求低風險買點。"
    elif regime == "STAGE_2_UPTREND_PULLBACK":
        bias = "WAIT"
        confidence = 72
        setup = f"第二階段多頭健康回檔，力守 50 SMA (${sma50:.2f}) 之上，留意止跌K線與量縮沉澱。"
        invalidation = f"日K實體收盤跌破 50 SMA (${sma50:.2f}) 則第二階段結構遭到破壞。"
        failure = "回檔演變為第四階段盤頭下殺，跌破關鍵法人生命線 (50 SMA)。"
        human_note = "觀察能否在 10/21 EMA 重新出量站上，切勿在下跌中途猜底。"
    elif regime == "STAGE_4_DOWNTREND":
        bias = "SHORT"
        confidence = 80
        setup = f"第四階段空頭趨勢 (Stage 4 Downtrend)，價格受壓制於均線下方，弱勢整理架構。"
        invalidation = f"強勢跳空站回 50 SMA (${sma50:.2f}) 或突破前波反彈高點。"
        failure = "超跌買盤介入或市場消息面引發之強烈軋空反彈。"
        human_note = "順勢偏空或空手觀望，切忌在空頭排列架構下盲目抄底。"
    else:  # NEUTRAL
        bias = "WAIT"
        confidence = 50
        setup = "均線糾結無明確單向趨勢 (Consolidation/Neutral)，多空力量拉鋸中。"
        invalidation = "放量向上突破盤整區間上緣或向下摜破區間下緣。"
        failure = "箱型整理中的假突破與假跌破雙向洗盤。"
        human_note = "持幣觀望，等待突破盤整箱體並確立新趨勢方向後再行介入。"

    return {
        "bias": bias,
        "confidence": confidence,
        "setup_summary": setup,
        "invalidation_condition": invalidation,
        "failure_mode": failure,
        "fundamental_diagnosis": fund_diag,
        "human_decision_note": human_note,
    }


@app.get("/api/ai-lens")
async def ai_lens_endpoint(
    symbol: str = Query(default="NASDAQ:AAPL"),
    timeframe: str = Query(default="1D"),
    refresh: bool = Query(default=False),
):
    """Retrieves or generates non-repainting AI Agent Lens quantitative report and decision."""
    clean_sym = str(symbol).strip().upper() if isinstance(symbol, str) else "NASDAQ:AAPL"
    if ":" not in clean_sym:
        clean_sym = f"BINANCE:{clean_sym}" if "USDT" in clean_sym else f"NASDAQ:{clean_sym}"
    if not is_valid_symbol_id(clean_sym):
        return JSONResponse({"error": "Invalid symbol format"}, status_code=400)
    clean_tf = normalize_timeframe(timeframe, default="1D")
    storage = get_storage()

    now_ts = int(time.time())
    if not refresh:
        cached_rep = storage.get_ai_agent_lens_report(clean_sym, clean_tf)
        if cached_rep and (now_ts - cached_rep.get("updated_at", 0)) < 1800:
            return {
                "status": "ok",
                "cached": True,
                "symbol": clean_sym,
                "timeframe": clean_tf,
                "report": cached_rep,
            }

    target_bars = await fetch_universal_history(clean_sym, timeframe=clean_tf)
    if not target_bars:
        target_bars = storage.get_bars(clean_sym, clean_tf, limit=1500)

    bench_sym = "BINANCE:BTCUSDT" if is_crypto_symbol(clean_sym) else "AMEX:SPY"
    bench_bars = await fetch_universal_history(bench_sym, timeframe=clean_tf)
    if not bench_bars:
        bench_bars = storage.get_bars(bench_sym, clean_tf, limit=1500)

    fund_rep = storage.get_fundamental_report(clean_sym)
    fundamentals = fund_rep.get("metrics") if fund_rep else None

    lens_payload = build_ai_agent_lens_payload(
        symbol=clean_sym,
        timeframe=clean_tf,
        bars=target_bars,
        benchmark_bars=bench_bars,
        fundamentals=fundamentals,
    )

    decision = None
    model_name = "gemini-2.5-flash"
    gemini_key = os.environ.get("GEMINI_API_KEY")

    if gemini_key:
        try:
            quant = lens_payload["quant_metrics"]
            prompt = f"""你是一名專精於美股成長股、CANSLIM 與第二階段動能策略的資深量化交易系統架構師 (AI Agent Lens)。
請依據以下給定的【非重繪結構化量化快照數據 (AI Agent Lens v1)】，產出嚴格 JSON 格式之決策報告。

【標的量化快照 (基於已收盤確認K線)】：
標的：{lens_payload['symbol']}
時間週期：{lens_payload['timeframe']}
確認K線時間：{lens_payload['confirmed_bar']['time'] if lens_payload.get('confirmed_bar') else 'N/A'}
收盤價：{quant.get('close')}
趨勢架構 (Regime)：{quant.get('trend_regime')}
EMA 10：{quant.get('ema10')} | EMA 21：{quant.get('ema21')}
SMA 50：{quant.get('sma50')} | SMA 200：{quant.get('sma200')}
20週期 ADR%：{quant.get('adr_pct_20')}%
50週期成交量比：{quant.get('volume_ratio_50')}x
52週高點：{quant.get('fifty_two_week_high')} | 距高點距離：{quant.get('pct_from_52w_high')}%
大盤相對強弱 (RS Ratio)：{quant.get('rs_ratio')} | RS Rating (1-99)：{quant.get('rs_rating')}
基本面數據：{json.dumps(lens_payload.get('fundamentals', {}), ensure_ascii=False)}

【輸出合約要求】：
請直接輸出合法的 JSON 字串，不要包含任何 markdown 標記（如 ```json），格式如下：
{{
  "bias": "LONG" 或 "SHORT" 或 "WAIT",
  "confidence": 0至100之整數,
  "setup_summary": "型態與架構診斷摘要（繁體中文，約30-60字）",
  "invalidation_condition": "關鍵失效條件（精確到具體價位或均線支撐壓力，此為風控生命線，繁體中文）",
  "failure_mode": "主要潛在失敗模式或歸因風險（繁體中文）",
  "fundamental_diagnosis": "基本面與動能綜合診斷（繁體中文）",
  "human_decision_note": "交易員執行指南（進出場與風控指引，繁體中文）"
}}"""
            gen_res = await call_gemini_generate(prompt, temperature=0.2, max_tokens=800)
            if gen_res:
                clean_text = gen_res.strip().replace("```json", "").replace("```", "").strip()
                parsed = json.loads(clean_text)
                if isinstance(parsed, dict) and "bias" in parsed and "confidence" in parsed:
                    decision = parsed
        except Exception as gemini_err:
            logger.debug("Gemini AI Lens call failed, falling back to deterministic engine: %s", gemini_err)

    if not decision:
        decision = generate_deterministic_ai_lens_decision(lens_payload)

    confirmed_time = (lens_payload.get("confirmed_bar") or {}).get("time") or now_ts

    storage.save_ai_agent_lens_report(
        symbol=clean_sym,
        timeframe=clean_tf,
        bar_time=confirmed_time,
        lens_data=lens_payload,
        decision_data=decision,
        model_used=model_name,
        updated_at=now_ts,
    )

    return {
        "status": "ok",
        "cached": False,
        "symbol": clean_sym,
        "timeframe": clean_tf,
        "report": {
            "symbol": clean_sym,
            "timeframe": clean_tf,
            "bar_time": confirmed_time,
            "lens": lens_payload,
            "decision": decision,
            "model_used": model_name,
            "updated_at": now_ts,
        }
    }


SCREENER_PRESETS: Dict[str, Dict[str, Any]] = {
    "stage2_vcp": {
        "id": "stage2_vcp",
        "name": "Minervini Stage 2 VCP 突破",
        "desc": "第二階段均線多頭排列 + 距 52W 高點 15% 內 + 波動收縮 (VCP) 放量突破",
        "criteria": {
            "min_rs_rating": 80,
            "mansfield_above_zero": True,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": True,
            "max_atr_percentile": 20.0,
            "expansion_breakout_only": False,
            "max_pct_from_52w_high": 15.0,
            "min_volume_ratio": 1.1,
            "min_rev_growth_yoy": None,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
    "rs_leaders": {
        "id": "rs_leaders",
        "name": "Mansfield RS 零軸翻揚領頭羊",
        "desc": "Mansfield RS 突破 0 軸強勢超越大盤 + CANSLIM RS Rating >= 85 + 放量走強",
        "criteria": {
            "min_rs_rating": 85,
            "mansfield_above_zero": True,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": True,
            "atr_squeeze_only": False,
            "max_atr_percentile": 30.0,
            "expansion_breakout_only": False,
            "max_pct_from_52w_high": 20.0,
            "min_volume_ratio": 1.2,
            "min_rev_growth_yoy": None,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
    "canslim_growth": {
        "id": "canslim_growth",
        "name": "CANSLIM 高成長突破潛力股",
        "desc": "營收 YoY 成長 >= 20% + 股價強於 50 SMA 季線 + 距高點 20% 內",
        "criteria": {
            "min_rs_rating": 75,
            "mansfield_above_zero": True,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": False,
            "max_atr_percentile": 30.0,
            "expansion_breakout_only": False,
            "max_pct_from_52w_high": 20.0,
            "min_volume_ratio": 1.0,
            "min_rev_growth_yoy": 20.0,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
    "low_vol_base": {
        "id": "low_vol_base",
        "name": "低波動底層籌碼收斂 (Base Consolidation)",
        "desc": "ATR% 百分位 <= 15% 極度量縮沉澱 + 均線糾結收斂蓄勢待發",
        "criteria": {
            "min_rs_rating": 65,
            "mansfield_above_zero": False,
            "stage2_only": False,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": True,
            "max_atr_percentile": 15.0,
            "expansion_breakout_only": False,
            "max_pct_from_52w_high": 30.0,
            "min_volume_ratio": 0.5,
            "min_rev_growth_yoy": None,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    }
}


@app.get("/api/screener/presets")
async def get_screener_presets():
    """Returns pre-configured institutional momentum screening strategies."""
    return {
        "status": "ok",
        "presets": list(SCREENER_PRESETS.values())
    }


@app.post("/api/screener/scan")
async def scan_screener_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Executes multi-factor stock screening across specified universe."""
    clean_payload = payload if isinstance(payload, dict) else {}
    universe_type = str(clean_payload.get("universe") or "us_momentum").strip().lower()
    raw_criteria = clean_payload.get("criteria") if isinstance(clean_payload.get("criteria"), dict) else {}

    min_rev_growth: Optional[float] = None
    if raw_criteria.get("min_rev_growth_yoy") is not None:
        try:
            min_rev_growth = float(raw_criteria["min_rev_growth_yoy"])
        except (ValueError, TypeError):
            min_rev_growth = None

    try:
        raw_risk = float(clean_payload.get("risk_dollar") or raw_criteria.get("risk_dollar") or 500.0)
        risk_dollar = raw_risk if math.isfinite(raw_risk) and raw_risk > 0 else 500.0
    except (ValueError, TypeError):
        risk_dollar = 500.0

    criteria = ScreeningCriteria(
        min_rs_rating=int(raw_criteria.get("min_rs_rating", 70)),
        mansfield_above_zero=bool(raw_criteria.get("mansfield_above_zero", True)),
        stage2_only=bool(raw_criteria.get("stage2_only", True)),
        weekly_stage2_only=bool(raw_criteria.get("weekly_stage2_only", False)),
        power_trend_only=bool(raw_criteria.get("power_trend_only", False)),
        atr_squeeze_only=bool(raw_criteria.get("atr_squeeze_only", False)),
        max_atr_percentile=float(raw_criteria.get("max_atr_percentile", 20.0)),
        expansion_breakout_only=bool(raw_criteria.get("expansion_breakout_only", False)),
        max_pct_from_52w_high=float(raw_criteria.get("max_pct_from_52w_high", 25.0)),
        min_volume_ratio=float(raw_criteria.get("min_volume_ratio", 1.0)),
        min_rev_growth_yoy=min_rev_growth,
        risk_dollar=risk_dollar,
        stop_atr_mult=float(raw_criteria.get("stop_atr_mult", 2.0)),
        mansfield_length=int(raw_criteria.get("mansfield_length", 252))
    )

    storage = get_storage()

    # Determine scanning candidate pool
    universe_items: List[Dict[str, Any]] = []
    if universe_type == "watchlist":
        db_wl = storage.get_watchlist()
        universe_items = db_wl if db_wl else list(WATCHLIST)
    elif universe_type == "custom":
        custom_input = clean_payload.get("custom_symbols") or clean_payload.get("symbols") or ""
        if isinstance(custom_input, list):
            raw_syms = [str(s).strip().upper() for s in custom_input if str(s).strip()]
        else:
            raw_syms = [s.strip().upper() for s in str(custom_input).replace(",", " ").replace(";", " ").split() if s.strip()]
        for rs in raw_syms:
            has_pref = ":" in rs
            full_id = rs if has_pref else f"NASDAQ:{rs}"
            universe_items.append({
                "id": full_id,
                "symbol": full_id.split(":")[-1],
                "name": full_id.split(":")[-1],
                "has_prefix": has_pref
            })
    else:  # "us_momentum" default
        universe_items = list(US_MOMENTUM_UNIVERSE)

    # Ingest benchmark SPY bars for date alignment & market regime
    bench_bars = await fetch_universal_history("AMEX:SPY", timeframe="1D")
    if not bench_bars:
        bench_bars = storage.get_bars("AMEX:SPY", "1D", limit=300)

    bench_stage = evaluate_canslim_stage(bench_bars) if bench_bars else {}
    bench_close = bench_stage.get("close") or 0.0
    bench_regime = bench_stage.get("trend_regime") or "NEUTRAL"

    if universe_type == "custom" and not universe_items:
        return {
            "status": "ok",
            "universe": universe_type,
            "elapsed_ms": 0,
            "total_scanned": 0,
            "matched_count": 0,
            "items": [],
            "benchmark_status": {
                "symbol": "SPY",
                "close": bench_close,
                "regime": bench_regime
            },
            "warning": "請在自訂代碼框中輸入至少一檔股票代碼（如 NVDA, TSLA, MRNA, CART）"
        }

    start_t = time.perf_counter()
    matched_items = await scan_universe(
        universe=universe_items,
        criteria=criteria,
        storage_obj=storage,
        benchmark_bars=bench_bars,
        fetch_func=fetch_universal_history,
        max_concurrency=8
    )
    elapsed_ms = int((time.perf_counter() - start_t) * 1000)

    return {
        "status": "ok",
        "universe": universe_type,
        "elapsed_ms": elapsed_ms,
        "total_scanned": len(universe_items),
        "matched_count": len(matched_items),
        "benchmark_status": {
            "symbol": "SPY",
            "close": bench_close,
            "regime": bench_regime,
            "mansfield_status": "BULLISH_ABOVE_ZERO" if bench_regime.startswith("STAGE_2") else "NEUTRAL"
        },
        "criteria": asdict(criteria),
        "items": matched_items
    }


@app.post("/api/screener/ai-summary")
async def screener_ai_summary_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Generates an institutional macro & momentum leadership synthesis for screened leaders."""
    clean_payload = payload if isinstance(payload, dict) else {}
    items = clean_payload.get("items") or []
    bench_status = clean_payload.get("benchmark_status") or {}
    try:
        raw_risk = float(clean_payload.get("risk_dollar", 500.0) or 500.0)
        risk_budget = raw_risk if math.isfinite(raw_risk) and raw_risk > 0 else 500.0
    except (ValueError, TypeError):
        risk_budget = 500.0

    if not items:
        return {
            "status": "ok",
            "summary": "當前篩選條件下無符合之標的。建議放寬「距 52 週新高距離（如調至 25%）」或關閉「僅限波動收縮 (Squeeze)」以擴大候選池。"
        }

    top_items = items[:5]
    top_summary = []
    for it in top_items:
        top_summary.append(
            f"- {it.get('symbol')} ({it.get('name')}): 收盤價 ${it.get('close')}, "
            f"Mansfield RS: {it.get('mansfield_rs')}, RS Rating: {it.get('rs_rating')}, "
            f"距52W高點: {it.get('pct_from_52w_high')}%, ATR%: {it.get('atr_pct')}%, "
            f"Squeeze: {it.get('is_squeeze')}, 建議股數: {it.get('suggested_shares')} 股"
        )

    prompt = f"""你是一名資深美股成長股對沖基金交易主管 (Portfolio Manager)，深諳 William O'Neil CANSLIM、Stan Weinstein 階段分析與 Mark Minervini SEPA 策略。
請依據本次量化篩選出的最新領頭羊清單與大盤宏觀狀態，為交易員提供精闢、客觀且具備風控生命線的盤勢點評（約 150-250 字，繁體中文）：

【大盤環境快照】：
- 基準標的：{bench_status.get('symbol', 'SPY')}
- 最新收盤：${bench_status.get('close', 'N/A')}
- 趨勢架構：{bench_status.get('regime', 'NEUTRAL')}

【本次篩選入榜之領頭羊 (Top {len(top_items)})】：
{chr(10).join(top_summary)}

請針對：
1. 【大盤動能與部位曝險建議】：依據大盤階段評估整體持股比重上限（如多頭滿載 vs 震盪拉回縮減至 30-50%）。
2. 【核心動能個股焦點】：剖析入榜龍頭的突破特徵與 VCP 收縮沉澱狀態。
3. 【關鍵停損與執行紀律】：點出防守生命線與試單風控原則（單筆預設風險額度 ${int(risk_budget)}）。"""

    try:
        gen_text = await asyncio.wait_for(
            call_gemini_generate(prompt, temperature=0.3, max_tokens=700),
            timeout=15.0
        )
        if gen_text:
            return {"status": "ok", "summary": gen_text.strip()}
    except Exception as gemini_err:
        logger.debug("Screener AI summary notice: %s", gemini_err)

    # Deterministic fallback summary if offline or timeout
    return {
        "status": "ok",
        "summary": f"### 📊 動能選股量化診斷摘要\n"
                   f"1. **大盤環境**：標普 SPY 目前處於 {bench_status.get('regime', 'NEUTRAL')} 架構，建議總曝險維持適度。\n"
                   f"2. **焦點標的**：本次篩選出 {len(items)} 檔動能股，首選為 {top_items[0].get('symbol')} "
                   f"(RS Rating: {top_items[0].get('rs_rating')}, 距高點 {top_items[0].get('pct_from_52w_high')}%)，具備高相對強度優勢。\n"
                   f"3. **風控生命線**：各標的進場應以 2x ATR 或跌破 50 SMA / 21 EMA 為強制停損點，嚴守單筆 ${int(risk_budget)} 風險紀律。"
    }


@app.post("/api/chat")
async def chat_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Interactive Gemini Market Copilot Chat with rich live context injection."""
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Invalid payload format"}, status_code=400)

    user_msg = str(payload.get("message") or "").strip()
    if not user_msg:
        return JSONResponse({"error": "Message is required"}, status_code=400)

    raw_symbol = str(payload.get("symbol") or "NASDAQ:NVDA").strip().upper()
    if ":" not in raw_symbol:
        if "USDT" in raw_symbol:
            clean_sym = f"BINANCE:{raw_symbol}"
        else:
            clean_sym = f"NASDAQ:{raw_symbol}"
    else:
        clean_sym = raw_symbol

    if not is_valid_symbol_id(clean_sym):
        clean_sym = "NASDAQ:NVDA"

    clean_tf = normalize_timeframe(payload.get("timeframe"), default="1D")

    # Context gathering: latest bars & indicators
    storage = get_storage()
    bars = storage.get_bars(clean_sym, clean_tf, limit=30)
    if not bars and aggregator:
        bars = aggregator.get_history(clean_sym)

    latest_bar = bars[-1] if bars else None
    close_price = latest_bar.get("close") if latest_bar else "N/A"
    high_price = latest_bar.get("high") if latest_bar else "N/A"
    low_price = latest_bar.get("low") if latest_bar else "N/A"
    vol_val = latest_bar.get("volume") if latest_bar else "N/A"

    fund_report = storage.get_fundamental_report(clean_sym) or {}
    lens_report = storage.get_ai_agent_lens_report(clean_sym, clean_tf) or {}
    quant_snapshot = (lens_report.get("lens") or {}).get("quant_metrics") or lens_report.get("quant_metrics") or {}
    decision_snapshot = lens_report.get("decision") or {}

    system_prompt = f"""你是一名世界級的美股成長股與動能交易策略助理 (Antigravity Copilot)。
你具備 Mark Minervini (SEPA)、William O'Neil (CANSLIM) 與 Stan Weinstein 第二階段型態學之深厚底蘊。
請依據使用者當前監控的即時行情快照與提問，提供精闢、客觀、具備明確風控支撐/壓力位的分析與建議。

【當前監控標的快照】：
標的代碼：{clean_sym}
時間週期：{clean_tf}
最新收盤：{close_price} (最高: {high_price}, 最低: {low_price}, 成交量: {vol_val})
量化位階快照：{json.dumps(quant_snapshot, ensure_ascii=False) if quant_snapshot else '暫無歷史AI Lens'}
歷史決策參考：{json.dumps(decision_snapshot, ensure_ascii=False) if decision_snapshot else '無'}
基本面診斷摘要：{fund_report.get('ai_summary', '暫無基本面快照')}

【回答原則】：
1. 一律使用專業繁體中文 (Traditional Chinese)，語氣自信、客觀且具備專業交易員風骨。
2. 針對使用者的提問給予精確解答，直接指出：當前趨勢型態、關鍵買點/防守位 (Stop loss)、潛在風險與獲利目標。
3. 若市場處於盤整或下行弱勢，切實警告「WAIT/觀望防守」原則。"""

    full_prompt = f"{system_prompt}\n\n【交易員提問】：\n{user_msg}"

    reply = await call_gemini_generate(full_prompt, temperature=0.5, max_tokens=1000)

    if not reply:
        # Robust dynamic technical & fundamental fallback
        swing_high = high_price
        swing_low = low_price
        if bars and len(bars) >= 2:
            valid_highs = [b["high"] for b in bars if isinstance(b.get("high"), (int, float))]
            valid_lows = [b["low"] for b in bars if isinstance(b.get("low"), (int, float))]
            if valid_highs:
                swing_high = round(max(valid_highs), 2)
            if valid_lows:
                swing_low = round(min(valid_lows), 2)
        elif isinstance(close_price, (int, float)):
            swing_high = round(close_price * 1.05, 2)
            swing_low = round(close_price * 0.95, 2)

        fund_info = ""
        m_cap = fund_report.get("metrics", {}).get("market_cap")
        if m_cap:
            fund_info += f"，市值規模約 **{m_cap}**"
        pe_val = fund_report.get("metrics", {}).get("pe_ratio")
        if pe_val:
            fund_info += f"，本益比 (PE) 為 **{pe_val}**"

        reply = f"""### 🤖 Antigravity Copilot (動態量化分析模式)
針對 **{clean_sym}** ({clean_tf}) 的即時診斷：
1. **當前位階與基本面**：最新收盤價為 **{close_price}**{fund_info}。
2. **關鍵支撐壓力**：
   - 上檔短壓：近期波段高點 **{swing_high}**
   - 下檔防守：關鍵支撐價位 **{swing_low}**（跌破此位需執行嚴格停損）
3. **策略執行指南**：針對您詢問的「{user_msg}」，建議嚴格依循市場結構。在突破前高 {swing_high} 伴隨放量前，勿貿然追高；若回測支撐 {swing_low} 獲支撐確認，方可考量分批佈局，嚴控單筆交易風險於 1%~2% 總資金以內。"""

    return {
        "status": "ok",
        "reply": reply,
        "symbol": clean_sym,
        "timeframe": clean_tf,
        "timestamp": int(time.time()),
        "model": "gemini-2.5-flash"
    }


async def recover_state_and_history(
    storage: Any,
    symbol: str,
    timeframe: str = "1D",
    since_time: Optional[int] = None,
    limit: int = 300,
) -> Dict[str, Any]:
    """Recovers terminal state and heals candle history gaps.

    Fetches cached bars from SQLite, determines if gap or backfill is needed,
    backfills from upstream providers if necessary, and returns unified state snapshot.
    """
    clean_sym = str(symbol).strip().upper()
    clean_tf = normalize_timeframe(timeframe, default="1D")
    now_ts = int(time.time())
    eff_limit = 1500 if clean_tf in ("1D", "1W", "1WK") and limit == 300 else limit

    cached_bars = storage.get_bars(clean_sym, clean_tf, limit=eff_limit)

    needs_backfill = False
    if not cached_bars:
        needs_backfill = True
    elif len(cached_bars) < min(eff_limit, 30):
        needs_backfill = True
    elif since_time and len(cached_bars) > 0 and cached_bars[-1]["time"] <= since_time:
        needs_backfill = True

    bars = cached_bars
    if needs_backfill:
        try:
            fetched = await fetch_universal_history(clean_sym, timeframe=clean_tf)
            if fetched:
                bars = fetched[-eff_limit:]
        except Exception as e:
            logger.warning("Recovery backfill error for %s (%s): %s", clean_sym, clean_tf, e)
            bars = cached_bars

    delta_bars: List[Dict[str, Any]] = []
    if since_time and bars:
        delta_bars = [b for b in bars if b.get("time", 0) > since_time]

    try:
        watchlist = storage.get_watchlist()
    except Exception:
        watchlist = list(WATCHLIST)

    try:
        preferences = storage.get_preferences()
    except Exception:
        preferences = {}

    ai_lens_report = None
    try:
        ai_lens_report = storage.get_ai_agent_lens_report(clean_sym, clean_tf)
    except Exception:
        ai_lens_report = None

    return {
        "status": "ok",
        "symbol": clean_sym,
        "timeframe": clean_tf,
        "server_time": now_ts,
        "bars": bars,
        "delta_bars": delta_bars,
        "watchlist": watchlist,
        "preferences": preferences,
        "ai_lens_report": ai_lens_report,
    }


@app.get("/api/recovery")
async def recovery_endpoint(
    symbol: str = Query(default="BINANCE:BTCUSDT"),
    timeframe: str = Query(default="1D"),
    since_time: Optional[int] = Query(default=None),
    limit: int = Query(default=300),
):
    """Unified terminal state and historical bar recovery endpoint."""
    storage = get_storage()
    raw_sym = str(symbol).strip().upper() if isinstance(symbol, str) else "BINANCE:BTCUSDT"
    if ":" not in raw_sym:
        clean_sym = f"BINANCE:{raw_sym}" if "USDT" in raw_sym else f"NASDAQ:{raw_sym}"
    else:
        clean_sym = raw_sym
    if not is_valid_symbol_id(clean_sym):
        return JSONResponse({"error": "Invalid symbol format"}, status_code=400)
    clean_tf = normalize_timeframe(timeframe, default="1D")
    result = await recover_state_and_history(
        storage=storage,
        symbol=clean_sym,
        timeframe=clean_tf,
        since_time=since_time,
        limit=limit,
    )
    return result


@app.get("/api/preferences")
async def get_preferences_endpoint():
    """Returns stored terminal preferences."""
    storage = get_storage()
    return {"status": "ok", "preferences": storage.get_preferences()}


@app.post("/api/preferences")
async def save_preferences_endpoint(payload: Dict[str, Any] = Body(default={})):
    """Saves terminal preferences."""
    storage = get_storage()
    clean_payload = payload if isinstance(payload, dict) else {}
    storage.save_preferences(clean_payload)
    return {"status": "ok", "preferences": storage.get_preferences()}


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """Real-time multiplexed quote, bar, and status stream with bounded queues."""
    if is_desktop_mode():
        origin = websocket.headers.get("origin")
        if not is_allowed_origin(origin):
            logger.warning("Rejected WebSocket connection from unauthorized origin: %s", origin)
            await websocket.close(code=1008, reason="Unauthorized origin")
            return

        token = websocket.query_params.get("token") or websocket.headers.get("x-session-token") or websocket.headers.get("x-terminal-token")
        if not token:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
        if not verify_token(token):
            logger.warning("Rejected unauthorized WebSocket connection: missing or invalid session token")
            await websocket.close(code=1008, reason="Unauthorized: invalid or missing session token")
            return

    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "status", "status": current_status})
        if tv_client and tv_client.states:
            for sym, q in list(tv_client.states.items()):
                if q.get("price", 0) > 0 or q.get("volume", 0) > 0:
                    await websocket.send_json({
                        "type": "quote",
                        "symbol": sym,
                        "data": q
                    })

        async def client_reader():
            try:
                while True:
                    await websocket.receive_text()
            except (WebSocketDisconnect, asyncio.CancelledError):
                logger.debug("Client reader disconnected cleanly")
            except Exception as e:
                logger.debug("Client reader error: %s", e)

        writer_coro = manager.client_writer(websocket)
        writer_task = asyncio.create_task(writer_coro) # tracked
        reader_coro = client_reader()
        reader_task = asyncio.create_task(reader_coro) # tracked

        done, pending = await asyncio.wait(
            [writer_task, reader_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            try:
                t.result()
            except (asyncio.CancelledError, WebSocketDisconnect):
                logger.debug("Task terminated on disconnect")
            except Exception as e:
                logger.debug("WebSocket task ended with: %s", e)
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                logger.debug("Pending task cancelled")
    except (WebSocketDisconnect, asyncio.CancelledError):
        logger.debug("Live endpoint disconnected")
    except Exception as e:
        logger.debug("WebSocket live endpoint error: %s", e)
    finally:
        manager.disconnect(websocket)


# Mount static assets directory
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse({"error": "index.html not found"}, status_code=404)

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="root_static")


def start_parent_watchdog(server: Any) -> None:
    """Monitors standard input and signals uvicorn server to exit on EOF (parent process killed)."""
    def _watch() -> None:
        try:
            sys.stdin.read()
        except Exception as err:
            logger.debug("Parent watchdog stdin channel closed: %s", err)
        logger.info("Parent process EOF detected on stdin. Triggering server shutdown.")
        server.should_exit = True
    threading.Thread(target=_watch, name="parent-eof-watchdog", daemon=True).start()


def run_server(
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    desktop_mode: bool = False,
    token: Optional[str] = None,
    dev_no_auth: bool = False,
    watch_stdin: bool = False
) -> None:
    """Launches the server with dynamic ephemeral socket binding if port=0 or desktop mode."""
    import uvicorn

    effective_port = port
    if effective_port is None:
        effective_port = 0 if desktop_mode else 8000

    if desktop_mode or effective_port == 0:
        active_token = "" if dev_no_auth else (token or secrets.token_hex(16))
        sock, assigned_port = bind_loopback_socket(host=host, port=effective_port)
        set_desktop_mode(enabled=True, token=active_token, port=assigned_port)
        logger.info("Starting server in desktop mode on %s:%d (dev_no_auth=%s)", host, assigned_port, dev_no_auth)
        config = uvicorn.Config(app, host=host, port=assigned_port, log_level="info")
        server = uvicorn.Server(config)
        if watch_stdin:
            start_parent_watchdog(server)
        try:
            server.run(sockets=[sock])
        finally:
            set_desktop_mode(enabled=False)
    else:
        set_desktop_mode(enabled=False)
        logger.info("Starting server in standard web mode on %s:%d", host, effective_port)
        if host == "127.0.0.1":
            uvicorn.run(app, host="127.0.0.1", port=effective_port, reload=False)
        else:
            uvicorn.run(app, host=host, port=effective_port, reload=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingView Real-Time Terminal Backend Server")
    parser.add_argument(
        "--desktop-mode",
        action="store_true",
        help="Enable macOS desktop mode with token authentication and ephemeral port"
    )
    parser.add_argument(
        "--dev-no-auth",
        action="store_true",
        help="Disable session token authentication for local development"
    )
    parser.add_argument(
        "--watch-stdin",
        action="store_true",
        help="Monitor stdin and terminate server upon EOF (prevents orphan processes)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind (default: 0 for desktop mode, 8000 for standard web mode)"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Pre-shared session token (auto-generated using secrets.token_hex(16) if omitted)"
    )
    args = parser.parse_args()

    is_desktop = args.desktop_mode or (args.port == 0) or os.environ.get("DESKTOP_MODE", "").lower() in ("1", "true", "yes")
    run_server(
        host=args.host,
        port=args.port,
        desktop_mode=is_desktop,
        token=args.token,
        dev_no_auth=args.dev_no_auth,
        watch_stdin=args.watch_stdin
    )

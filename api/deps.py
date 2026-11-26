"""Momentum Swing Terminal Backend Server.

FastAPI application providing unified asyncio lifespan management, REST endpoints
for symbols, search, dynamic subscription, and historical candles (dual-source: Binance
for crypto, Yahoo Finance v8 for equities/ETFs/benchmarks), a WebSocket live broadcaster
with bounded drop-oldest queues, and static asset delivery.
"""

import argparse
import asyncio
import contextvars
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
from datetime import datetime, timezone, date, timedelta, time as dt_time
from engine.market_time import (
    ny_now,
    is_regular_session_open,
    current_session_date,
    last_completed_session_date,
    NY_TZ,
)
from contextlib import asynccontextmanager, contextmanager
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Sequence, Union, Tuple, Callable, Set, Awaitable

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Body, Request, HTTPException
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
    build_theme_heatmap,
    fetch_heatmap_universe,
)
from engine.market_scanner import scan_all_us_stocks, evaluate_multi_frameworks

from api import get_server_module

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


class RedactTokenFilter(logging.Filter):
    """Redacts session token parameter values from log records to prevent secret leakage in access logs."""

    _TOKEN_RE = re.compile(r"(token=)[^&\s]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._TOKEN_RE.sub(r"\1[REDACTED]", record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._TOKEN_RE.sub(r"\1[REDACTED]", a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (self._TOKEN_RE.sub(r"\1[REDACTED]", v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return True


def setup_log_redaction_filters() -> None:
    """Attaches RedactTokenFilter to uvicorn.access and uvicorn.error loggers."""
    flt = RedactTokenFilter()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        log_obj = logging.getLogger(name)
        if not any(isinstance(f, RedactTokenFilter) for f in log_obj.filters):
            log_obj.addFilter(flt)


setup_log_redaction_filters()

def load_dotenv_custom(paths: Optional[List[Path]] = None) -> List[str]:
    """Loads environment variables from .env files without external dependencies."""
    if paths is not None:
        candidate_paths = paths
    else:
        candidate_paths = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parent / ".env",
        ]
        if os.getenv("TERMINAL_LOAD_HOME_DOTENV") == "1":
            candidate_paths.extend([
                Path.home() / ".env",
                Path.home() / ".gemini" / ".env",
            ])
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
_dev_no_auth: bool = False
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


def _sync_desktop_attrs():
    srv = get_server_module()
    if srv is not None:
        srv._desktop_mode = _desktop_mode
        srv._desktop_token = _desktop_token
        srv._desktop_port = _desktop_port
        srv._dev_no_auth = _dev_no_auth


def set_desktop_mode(
    enabled: bool = True,
    token: Optional[str] = None,
    port: Optional[int] = None,
    dev_no_auth: bool = False
) -> None:
    """Configures desktop mode flag, session token, bound port, and dev_no_auth override."""
    global _desktop_mode, _desktop_token, _desktop_port, _dev_no_auth
    _desktop_mode = enabled
    _dev_no_auth = dev_no_auth
    if enabled:
        if dev_no_auth:
            _desktop_token = ""
        else:
            _desktop_token = token or _desktop_token or secrets.token_hex(16)
        if port is not None:
            _desktop_port = port
    else:
        _desktop_token = None
        _desktop_port = None
        _dev_no_auth = False
    _sync_desktop_attrs()


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
    if not is_desktop_mode() or _dev_no_auth:
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
        csp = "default-src 'self'; script-src 'self' 'unsafe-inline' https://s3.tradingview.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* https://*.tradingview.com wss://*.tradingview.com; frame-src 'self' https://s.tradingview.com https://*.tradingview.com;"
        if not is_desktop_mode():
            if request.method in ("POST", "PUT", "DELETE"):
                origin = request.headers.get("origin")
                if origin and not is_allowed_origin(origin):
                    logger.warning("Rejected %s request from unauthorized origin: %s", request.method, origin)
                    return JSONResponse(status_code=403, content={"error": "Forbidden: unauthorized origin"})
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

WS_HEARTBEAT_INTERVAL: float = 15.0


class ConnectionManager:
    """Manages active WebSocket client connections with per-client bounded queues and drop-oldest strategy."""

    def __init__(self, max_queue_size: int = 100):
        self.max_queue_size = max_queue_size
        self.client_queues: Dict[WebSocket, asyncio.Queue] = {}
        self.client_loops: Dict[WebSocket, asyncio.AbstractEventLoop] = {}
        self._seq_lock = threading.Lock()
        self._bar_seq: Dict[str, int] = {}

    def next_bar_seq(self, symbol: str) -> int:
        """Returns a thread-safe monotonically increasing bar sequence number per symbol."""
        key = str(symbol).strip().upper()
        with self._seq_lock:
            seq = self._bar_seq.get(key, 0) + 1
            self._bar_seq[key] = seq
            return seq

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
_last_remote_fetch: Dict[Tuple[str, str], float] = {}
_shared_client: Optional[httpx.AsyncClient] = None

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


def get_shared_client() -> Optional[httpx.AsyncClient]:
    """Returns the active shared HTTP client if server lifespan is running, else None."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        srv = get_server_module()
    return getattr(srv, "_shared_client", _shared_client) if srv else _shared_client
    return None


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

class NoCacheStaticFiles(StaticFiles):
    """回傳的靜態檔加上 Cache-Control: no-cache（仍可用 ETag 重新驗證），避免 WebView 使用舊版前端。"""

    async def get_response(self, path: str, scope: Any):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

def start_parent_watchdog(server: Any) -> None:
    """Monitors standard input and signals uvicorn server to exit on EOF (parent process killed)."""
    if not sys.stdin or not hasattr(sys.stdin, "read"):
        logger.debug("sys.stdin is not available; parent watchdog disabled.")
        return
    def _watch() -> None:
        try:
            content = sys.stdin.read()
            if content == "" and not getattr(server, "started", True):
                logger.debug("Parent watchdog observed EOF before server completed startup.")
        except Exception as err:
            logger.debug("Parent watchdog stdin channel closed: %s", err)
        logger.info("Parent process EOF detected on stdin. Triggering server shutdown.")
        server.should_exit = True
    threading.Thread(target=_watch, name="parent-eof-watchdog", daemon=True).start()

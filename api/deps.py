"""Shared runtime dependencies: storage, HTTP clients, Yahoo session, desktop auth, and live state."""

import asyncio
import os
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict, Optional, Set, Tuple

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.bootstrap import logger
from api.config import is_allowed_origin
from api.ws_manager import manager
from engine.aggregator import TickToBarAggregator
from engine.client import TradingViewAsyncClient
from engine.providers import AlpacaDataProvider, PolygonDataProvider
from engine.storage import get_storage

_poly_provider = PolygonDataProvider(os.getenv("POLYGON_API_KEY"))
_alpaca_provider = AlpacaDataProvider(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"))

aggregator: Optional[TickToBarAggregator] = None
tv_client: Optional[TradingViewAsyncClient] = None
current_status: str = "disconnected"
_shared_client: Optional[httpx.AsyncClient] = None
_yahoo_cache: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
_inflight_history: Dict[str, asyncio.Future] = {}
_last_remote_fetch: Dict[Tuple[str, str], float] = {}
_last_1m_seed_attempt: Dict[str, float] = {}
_reconcile_tasks: Set[asyncio.Task] = set()


def get_shared_client() -> Optional[httpx.AsyncClient]:
    """Returns shared async httpx client initialized in lifespan, or None."""
    return _shared_client


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
                    logger.debug("Yahoo session refreshed: %d cookies, crumb=%s", len(self._cookies), bool(self._crumb))
            except Exception as e:
                logger.warning("Error refreshing Yahoo session: %s", e)
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


@contextmanager
def desktop_mode_context(token: Optional[str] = None, port: int = 0):
    """Context manager for safely testing desktop mode with auto-restoration."""
    prev_mode, prev_token, prev_port = _desktop_mode, _desktop_token, _desktop_port
    try:
        active_token = token or secrets.token_hex(16)
        set_desktop_mode(enabled=True, token=active_token, port=port)
        yield active_token
    finally:
        set_desktop_mode(enabled=prev_mode, token=prev_token, port=prev_port)

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
            return JSONResponse(status_code=401, content={"error": "Unauthorized: invalid or missing session token", "detail": "Invalid or missing session token"})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

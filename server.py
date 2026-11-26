"""Momentum Swing Terminal Backend Server — slim application assembly & entrypoint."""

import argparse
import asyncio
import os
import secrets
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from engine.aggregator import CandlestickBar, TickToBarAggregator
from engine.client import TradingViewAsyncClient
from engine.market_time import current_session_date, is_regular_session_open, last_completed_session_date
from engine.storage import close_storage, get_storage

import api.deps as deps
import api.history as history_mod
from api.bootstrap import (
    CA_BUNDLE_PATH,
    STATIC_DIR,
    NoCacheStaticFiles,
    RedactTokenFilter,
    _parent_watchdog_loop,
    bind_loopback_socket,
    init_certifi_ca_bundle,
    is_parent_alive,
    load_dotenv_custom,
    logger,
    resolve_static_dir,
    setup_log_redaction_filters,
    start_parent_watchdog,
)
from api.config import (
    ALLOWED_ASSET_TYPES,
    CANONICAL_TIMEFRAMES,
    GEMINI_MODELS_CASCADE,
    LOOPBACK_ORIGIN_REGEX,
    MAX_CACHE_SIZE,
    MIN_1M_HISTORY_BARS,
    ONE_MIN_INFLIGHT_WAIT_SEC,
    ONE_MIN_SEED_COOLDOWN_SEC,
    POPULAR_SEARCH_INDEX,
    SYMBOLS,
    TAURI_ORIGINS,
    VALID_SYMBOL_ID_RE,
    VALID_TICKER_RE,
    WATCHLIST,
    WS_HEARTBEAT_INTERVAL,
    YAHOO_EXCHANGE_MAP,
    _parse_criteria_number,
    is_allowed_origin,
    is_crypto_symbol,
    is_valid_symbol_id,
    is_valid_ticker,
    normalize_symbol_id,
    normalize_timeframe,
)
from api.deps import (
    DesktopAuthMiddleware,
    YahooSessionManager,
    _inflight_history,
    _last_1m_seed_attempt,
    _last_remote_fetch,
    _yahoo_cache,
    desktop_mode_context,
    extract_token_from_request,
    get_desktop_port,
    get_desktop_token,
    get_shared_client,
    is_desktop_mode,
    manager,
    set_desktop_mode,
    verify_token,
)
from api.ws_manager import ConnectionManager
from api.history import (
    collapse_daily_bars_by_ny_date,
    fetch_binance_history,
    fetch_historical_bars,
    fetch_universal_history,
    fetch_yahoo_history,
    get_history,
    on_bar_completed_handler,
    on_bar_update_handler,
    on_quote_update_handler,
    on_status_change_handler,
    router as history_router,
)
from api.symbols import (
    _resolve_known_bare_symbol,
    get_symbols,
    health_check,
    reorder_symbols_endpoint,
    router as symbols_router,
    search_symbols,
    subscribe_symbol,
    unsubscribe_symbol,
)
from api.ai import (
    _calc_ema,
    _calc_sma,
    _generate_with_model_tracking,
    ai_lens_endpoint,
    build_ai_agent_lens_payload,
    call_gemini_generate,
    call_gemini_generate_with_model,
    chat_endpoint,
    generate_deterministic_ai_lens_decision,
    get_fundamentals,
    router as ai_router,
)
from api.screener import (
    SCREENER_PRESETS,
    get_screener_presets,
    get_theme_heatmap_endpoint,
    router as screener_router,
    scan_screener_endpoint,
    screener_ai_summary_endpoint,
)
from api.state import (
    clear_drawings_endpoint,
    delete_drawing_endpoint,
    get_drawings_endpoint,
    get_preferences_endpoint,
    recover_state_and_history,
    recovery_endpoint,
    router as state_router,
    save_drawing_endpoint,
    save_preferences_endpoint,
)
from api.ws import router as ws_router, websocket_live_endpoint


async def lifespan(app: FastAPI):
    """FastAPI unified lifespan manager executing within the primary asyncio loop."""
    # Shared runtime state updated on api.deps
    logger.info("Initializing TradingView real-time engine...")
    deps.current_status = "initializing"

    storage = deps.get_storage()
    try:
        persisted_wl = storage.get_watchlist()
        if persisted_wl:
            WATCHLIST.clear()
            WATCHLIST.extend(persisted_wl)
            SYMBOLS.clear()
            SYMBOLS.extend([w["id"] for w in persisted_wl])
    except Exception as wl_err:
        logger.warning("Failed to load watchlist from SQLite: %s", wl_err)

    deps.aggregator = TickToBarAggregator(
        timeframe_seconds=60,
        max_history=500,
        on_bar_update=history_mod.on_bar_update_handler,
        on_bar_completed=history_mod.on_bar_completed_handler
    )

    try:
        pruned_count = storage.prune_bars(keep_per_series=2000)
        if pruned_count > 0:
            logger.info("Pruned %d excess historical bars on startup", pruned_count)
    except Exception as e:
        logger.warning("Failed to prune old bars on startup: %s", e)

    # Seed historical bars for crypto symbols concurrently using shared HTTP client
    # Shared httpx client updated on api.deps
    if deps._shared_client is None or deps._shared_client.is_closed:
        deps._shared_client = httpx.AsyncClient(
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
            deps.aggregator.seed_history(sym, cached)
            logger.info("Cold-start offline seeded %d cached bars for %s", len(cached), sym)

    # Schedule background network refresh without blocking SERVER_READY handshake
    async def _background_crypto_seed():
        try:
            seed_tasks = [history_mod.fetch_binance_history(sym, limit=300) for sym in crypto_symbols]
            seed_results = await asyncio.gather(*seed_tasks, return_exceptions=True)
            for sym, res in zip(crypto_symbols, seed_results):
                if isinstance(res, list) and res:
                    deps.aggregator.seed_history(sym, res)
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

    # Background periodic closing of due bars
    async def _close_due_bars_loop():
        while True:
            try:
                await asyncio.sleep(1.0)
                if deps.aggregator:
                    deps.aggregator.close_due_bars()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in close_due_bars_loop: %s", e)

    close_bars_task: Optional[asyncio.Task] = asyncio.create_task(_close_due_bars_loop())

    deps.tv_client = TradingViewAsyncClient(
        symbols=SYMBOLS,
        on_quote_update=history_mod.on_quote_update_handler,
        on_status_change=history_mod.on_status_change_handler
    )

    await deps.tv_client.start()
    logger.info("TradingView WebSocket client running in unified lifespan loop.")

    watchdog_task: Optional[asyncio.Task] = None
    if deps.is_desktop_mode():
        port = deps.get_desktop_port() or 8000
        token = deps.get_desktop_token() or ""
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
    if close_bars_task and not close_bars_task.done():
        close_bars_task.cancel()
        try:
            await close_bars_task
        except asyncio.CancelledError:
            logger.debug("Close due bars task cancellation completed.")
    for t in list(deps._reconcile_tasks):
        t.cancel()
    if deps._reconcile_tasks:
        await asyncio.gather(*list(deps._reconcile_tasks), return_exceptions=True)
    deps._reconcile_tasks.clear()
    # Python 3.9 binds asyncio.Semaphore to the loop it was created on; drop it so a later
    # lifespan (new event loop) creates a fresh one.
    globals()["_reconcile_semaphore"] = None
    if watchdog_task and not watchdog_task.done():
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            logger.debug("Parent watchdog task shutdown completed.")
    if deps.tv_client:
        await deps.tv_client.stop()
    await manager.close_all()
    if deps._shared_client is not None and not deps._shared_client.is_closed:
        await deps._shared_client.aclose()
        deps._shared_client = None
    _inflight_history.clear()
    close_storage()
    logger.info("Engine shutdown complete.")


app = FastAPI(title="Momentum Swing Terminal", lifespan=lifespan)


# Mount security & CORS middlewares in exact order (DesktopAuthMiddleware first, CORSMiddleware outer)
app.add_middleware(DesktopAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=TAURI_ORIGINS,
    allow_origin_regex=LOOPBACK_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(symbols_router)
app.include_router(history_router)
app.include_router(ai_router)
app.include_router(screener_router)
app.include_router(state_router)
app.include_router(ws_router)

if STATIC_DIR.exists():
    app.mount("/static", NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"error": "index.html not found"}, status_code=404)

    app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="root_static")


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
        set_desktop_mode(enabled=True, token=active_token, port=assigned_port, dev_no_auth=dev_no_auth)
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
    parser = argparse.ArgumentParser(description="Momentum Swing Terminal Backend Server")
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

"""Historical OHLCV fetching, NY daily bar collapse, reconciliation, and /api/history route."""

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

import api.deps as deps
from api.bootstrap import logger
from api.config import (
    MAX_CACHE_SIZE,
    MIN_1M_HISTORY_BARS,
    ONE_MIN_INFLIGHT_WAIT_SEC,
    ONE_MIN_SEED_COOLDOWN_SEC,
    is_crypto_symbol,
    is_valid_symbol_id,
    is_valid_ticker,
    normalize_symbol_id,
    normalize_timeframe,
)
from api.deps import YahooSessionManager
from engine.aggregator import CandlestickBar
from engine.market_time import NY_TZ, is_regular_session_open, last_completed_session_date

router = APIRouter()

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
        shared = deps.get_shared_client()
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
    if cache_key in deps._yahoo_cache:
        cached_time, cached_bars = deps._yahoo_cache[cache_key]
        ttl = 10.0 if not cached_bars else 30.0
        if now - cached_time < ttl:
            deps._yahoo_cache.move_to_end(cache_key)
            return cached_bars
        del deps._yahoo_cache[cache_key]

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
                            if len(deps._yahoo_cache) >= MAX_CACHE_SIZE:
                                deps._yahoo_cache.popitem(last=False)
                            deps._yahoo_cache[cache_key] = (now, bars)
                            return
                elif resp.status_code == 429:
                    logger.warning("Yahoo Finance API returned status 429 for %s, entering negative cooldown", ticker)
                    break
                elif resp.status_code in (500, 502, 503):
                    logger.warning("Yahoo Finance API on %s returned status %s for %s, trying next host...", host, resp.status_code, ticker)
                    continue
            except Exception as host_err:
                logger.warning("Yahoo Finance host %s network error for %s: %s", host, ticker, host_err)
                continue
        if not bars:
            if len(deps._yahoo_cache) >= MAX_CACHE_SIZE:
                deps._yahoo_cache.popitem(last=False)
            deps._yahoo_cache[cache_key] = (now, [])

    try:
        shared = deps.get_shared_client()
        if shared is not None:
            await _fetch(shared)
        else:
            async with httpx.AsyncClient(timeout=8.0, headers=headers) as http_cli:
                await _fetch(http_cli)
    except Exception as e:
        logger.warning("Could not fetch Yahoo history for %s: %s", ticker, e)
    return bars


def collapse_daily_bars_by_ny_date(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """When multiple daily bars exist for the same NY trading date, retain a single bar
    with the earliest time and latest OHLCV values, sorted by time."""
    if not bars:
        return []

    by_date: Dict[Any, List[Dict[str, Any]]] = OrderedDict()
    passthrough: List[Dict[str, Any]] = []

    for b in bars:
        if not isinstance(b, dict):
            continue
        t = b.get("time")
        try:
            if t is None or isinstance(t, bool):
                raise ValueError("invalid time")
            dt = datetime.fromtimestamp(int(t), tz=NY_TZ)
            ny_d = dt.date()
            if ny_d not in by_date:
                by_date[ny_d] = []
            by_date[ny_d].append(b)
        except (TypeError, ValueError, OverflowError, OSError) as err:
            logger.debug("Skipping bar timestamp grouping fallback for %r: %s", t, err)
            passthrough.append(dict(b))

    collapsed: List[Dict[str, Any]] = []
    for ny_d, group in by_date.items():
        if len(group) == 1:
            collapsed.append(dict(group[0]))
        else:
            earliest_time = min(int(x["time"]) for x in group)
            latest_bar = max(group, key=lambda x: int(x["time"]))
            merged = dict(latest_bar)
            merged["time"] = earliest_time
            collapsed.append(merged)

    collapsed.extend(passthrough)

    def _safe_sort_key(item: Dict[str, Any]) -> Tuple[int, int]:
        try:
            val = item.get("time")
            if val is None or isinstance(val, bool):
                return (1, 0)
            return (0, int(val))
        except (TypeError, ValueError) as err:
            logger.debug("Fallback sort key for non-numeric bar time %r: %s", item.get("time"), err)
            return (1, 0)

    collapsed.sort(key=_safe_sort_key)
    return collapsed


async def fetch_historical_bars(symbol: str, timeframe: str = "1m", limit: int = 300) -> List[Dict[str, Any]]:
    """Fetches historical OHLCV bars with licensed provider precedence, dual-source fallback, SQLite caching, and offline support."""
    if not is_valid_symbol_id(symbol):
        return []
    clean_tf = normalize_timeframe(timeframe, default="1m")
    if limit == 300 and clean_tf in ("1D", "1W", "1WK"):
        limit = 1500
    bars: List[Dict[str, Any]] = []

    # 0. Cache-first fast path: if SQLite already holds sufficiently fresh bars, serve immediately (0ms latency)
    storage = deps.get_storage()
    cached = storage.get_bars(symbol, clean_tf, limit=limit)
    min_expected = min(limit, 250) if clean_tf in ("1D", "1W", "1WK") else min(limit, 30)
    if cached and len(cached) >= min_expected:
        now_ts = int(time.time())
        last_bar_ts = cached[-1].get("time", 0)
        is_fresh = False

        if clean_tf in ("1D", "1W", "1WK"):
            # Never fetched remotely in this process -> 0.0 (treated as stale, refetch once).
            # Do NOT fall back to the bar timestamp: Yahoo's latest daily bar can carry a 16:00 ET timestamp.
            last_fetch_t = deps._last_remote_fetch.get((symbol, clean_tf), 0.0)
            if is_crypto_symbol(symbol):
                # Crypto: unclosed bar, fresh only if remote fetched within 60s
                if (now_ts - last_fetch_t) <= 60:
                    is_fresh = True
            else:
                if is_regular_session_open():
                    # Intraday: fresh only if remote fetched within 60s
                    if (now_ts - last_fetch_t) <= 60:
                        is_fresh = True
                else:
                    # Market closed: last bar NY date >= last_completed_session_date() AND fetched after 16:00 ET
                    comp_date = last_completed_session_date()
                    try:
                        last_bar_dt = datetime.fromtimestamp(last_bar_ts, tz=NY_TZ)
                        last_bar_date = last_bar_dt.date()
                    except (TypeError, ValueError, OverflowError, OSError) as err:
                        logger.debug("Invalid cached last_bar_ts %r for %s: %s", last_bar_ts, symbol, err)
                        last_bar_date = None

                    date_ok = False
                    if last_bar_date:
                        if clean_tf in ("1W", "1WK"):
                            week_start = comp_date - timedelta(days=comp_date.weekday())
                            date_ok = (last_bar_date >= comp_date or last_bar_date >= week_start)
                        else:
                            date_ok = (last_bar_date >= comp_date)

                    if date_ok:
                        session_close_dt = datetime.combine(comp_date, dt_time(16, 0), tzinfo=NY_TZ)
                        if last_fetch_t >= session_close_dt.timestamp():
                            is_fresh = True
        else:
            tf_seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}.get(clean_tf, 60)
            last_fetch_t = deps._last_remote_fetch.get((symbol, clean_tf), 0.0)
            if (now_ts - last_fetch_t) <= tf_seconds:
                is_fresh = True

        if is_fresh:
            if clean_tf == "1D" and not is_crypto_symbol(symbol):
                cached = collapse_daily_bars_by_ny_date(cached)
            logger.info("Serving %d fresh cached bars from SQLite for %s (%s) [cache-first]", len(cached), symbol, clean_tf)
            return cached

    # 1. Check licensed commercial providers if credentials are configured in environment
    if deps._poly_provider.is_configured:
        try:
            bars = await deps._poly_provider.fetch_historical_bars(symbol, timeframe=clean_tf, limit=limit)
        except Exception as poly_err:
            logger.warning("Licensed Polygon provider error for %s: %s", symbol, poly_err)

    if not bars and deps._alpaca_provider.is_configured:
        try:
            bars = await deps._alpaca_provider.fetch_historical_bars(symbol, timeframe=clean_tf, limit=limit)
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

    storage = deps.get_storage()
    if bars:
        if clean_tf == "1D" and not is_crypto_symbol(symbol):
            bars = collapse_daily_bars_by_ny_date(bars)
        deps._last_remote_fetch[(symbol, clean_tf)] = time.time()
        try:
            storage.save_bars(symbol, clean_tf, bars)
        except Exception as save_err:
            logger.warning("Failed to persist bars to SQLite for %s: %s", symbol, save_err)
        return bars

    cached = storage.get_bars(symbol, clean_tf, limit=limit)
    if cached:
        if clean_tf == "1D" and not is_crypto_symbol(symbol):
            cached = collapse_daily_bars_by_ny_date(cached)
        logger.info("Serving %d cached bars from SQLite for %s (%s) [offline fallback]", len(cached), symbol, clean_tf)
        return cached

    return []


async def fetch_universal_history(symbol: str, timeframe: str = "1D") -> List[Dict[str, Any]]:
    """Universal router dispatching with singleflight deduplication to prevent cache stampedes."""
    flight_key = f"{symbol}_{timeframe}"
    existing_task = deps._inflight_history.get(flight_key)
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
            deps._inflight_history.pop(flight_key, None)

    runner_coro = _runner()
    task = asyncio.create_task(runner_coro) # tracked
    deps._inflight_history[flight_key] = task
    return await asyncio.shield(task)


def on_bar_update_handler(symbol: str, bar: CandlestickBar) -> None:
    """Dispatches real-time bar updates to connected WebSocket clients with monotonic per-symbol seq."""
    deps.manager.broadcast_nowait({
        "type": "bar",
        "symbol": symbol,
        "seq": deps.manager.next_bar_seq(symbol),
        "bar": bar.to_dict()
    })


def on_quote_update_handler(symbol: str, quote_data: Dict[str, Any]) -> None:
    """Feeds incoming quote ticks to the aggregator and broadcasts quote updates."""
    price = quote_data.get("price")
    vol = quote_data.get("volume")
    t = quote_data.get("time")

    if deps.aggregator:
        deps.aggregator.process_tick(symbol, price=price, cumulative_volume=vol, tick_time=t)

    deps.manager.broadcast_nowait({
        "type": "quote",
        "symbol": symbol,
        "data": quote_data
    })


def on_status_change_handler(status: str) -> None:
    """Updates global connection status and notifies clients."""
    # status synced via deps.current_status
    deps.current_status = status
    deps.manager.broadcast_nowait({
        "type": "status",
        "status": status
    })


_reconcile_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
_reconcile_semaphore: Optional[asyncio.Semaphore] = None


def _get_reconcile_semaphore() -> asyncio.Semaphore:
    global _reconcile_semaphore
    if _reconcile_semaphore is None:
        _reconcile_semaphore = asyncio.Semaphore(4)
    return _reconcile_semaphore


async def _reconcile_binance_bar(symbol: str, bar_time: int) -> None:
    """Reconciles 1m Binance crypto bar against definitive public REST API candles."""
    # Wait OUTSIDE the semaphore: the limit of 4 applies to concurrent Binance requests,
    # not to tasks that are merely waiting for their bar to settle.
    target_time = bar_time + 62  # bar_time + 60 + 2
    wait_sec = target_time - time.time()
    if wait_sec > 0:
        await _reconcile_sleep(wait_sec)

    sem = _get_reconcile_semaphore()
    async with sem:
        try:
            bars = await fetch_binance_history(symbol, limit=3)
            matching = next((b for b in bars if int(b.get("time", 0)) == bar_time), None)
            if matching and deps.aggregator:
                replaced = deps.aggregator.replace_history_bar(symbol, matching)
                if replaced:
                    deps.get_storage().save_bars(symbol, "1m", [matching])
                    logger.debug("Reconciled Binance bar for %s at %d", symbol, bar_time)
                else:
                    logger.debug("Bar %d for %s not found in aggregator history to reconcile", bar_time, symbol)
            else:
                logger.debug("Binance history missing matching bar for %s at %d", symbol, bar_time)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("Failed reconciling Binance bar for %s at %d: %s", symbol, bar_time, e)


def on_bar_completed_handler(symbol: str, bar: CandlestickBar) -> None:
    """Persists completed 1m candlestick bar to SQLite storage and schedules crypto reconciliation."""
    try:
        deps.get_storage().save_bars(symbol, "1m", [bar])
    except Exception as e:
        logger.warning("Failed to persist completed bar for %s: %s", symbol, e)

    if not symbol.startswith("BINANCE:"):
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None or not loop.is_running():
        return

    coro = _reconcile_binance_bar(symbol, bar.time)
    task = asyncio.create_task(coro)
    deps._reconcile_tasks.add(task)

    def _cleanup(t: asyncio.Task) -> None:
        deps._reconcile_tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.debug("Reconcile task error for %s: %s", symbol, exc)

    task.add_done_callback(_cleanup)


@router.get("/api/history")
async def get_history(
    symbol: str = Query(default="BINANCE:BTCUSDT"),
    timeframe: str = Query(default="1m"),
    seed: bool = Query(default=False),
    backfill: bool = Query(default=False),
    engine: str = Query(default="A"),
    limit: Optional[int] = Query(default=None, ge=1, le=5000)
):
    """Returns historical OHLCV bars supporting Scheme A (dual-source) and Scheme B (TradingView WebSocket series)."""
    clean_sym = normalize_symbol_id(symbol) if is_valid_symbol_id(symbol) else symbol
    clean_tf = normalize_timeframe(timeframe, default="1m")
    if not deps.aggregator:
        return {"symbol": clean_sym, "timeframe": clean_tf, "engine": engine, "bars": []}

    def _apply_limit(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return items[-limit:] if (limit is not None and len(items) > limit) else items

    # Scheme B: TradingView WebSocket Native Protocol Series
    if engine.upper() in ("B", "TV_WS") and deps.tv_client and deps.tv_client.connected:
        try:
            tv_bars = await deps.tv_client.request_chart_series(clean_sym, timeframe=clean_tf, n_bars=limit or 300, timeout=2.0)
            if tv_bars and len(tv_bars) > 0:
                return {"symbol": clean_sym, "timeframe": clean_tf, "engine": "B", "bars": _apply_limit(tv_bars)}
        except Exception as e:
            logger.debug("Scheme B TV WS request error: %s", e)

    # Scheme A: Dual-Source High Availability (Binance for Crypto, Yahoo Finance for Equities)
    if clean_tf == "1m":
        bars = deps.aggregator.get_history(clean_sym)
        if seed and len(bars) < MIN_1M_HISTORY_BARS:
            pending = deps._inflight_history.get(f"{clean_sym}_1m")
            if pending is not None and not pending.done():
                try:
                    seeded = await asyncio.wait_for(asyncio.shield(pending), timeout=ONE_MIN_INFLIGHT_WAIT_SEC)
                except (asyncio.TimeoutError, Exception):
                    seeded = []
                    deps._last_1m_seed_attempt[clean_sym] = time.monotonic()
                if seeded:
                    deps.aggregator.seed_history(clean_sym, seeded)
                bars = deps.aggregator.get_history(clean_sym)
        # 伺服器啟動後才開始累積的 1m（只有幾根即時 K 棒）也要補歷史，否則畫面只會有最近幾分鐘。
        # 每個標的最多 60 秒補一次，避免資料源本來就沒有歷史時一直重抓。
        should_seed_short_history = (
            bool(bars)
            and len(bars) < MIN_1M_HISTORY_BARS
            and (clean_sym not in deps._last_1m_seed_attempt or (time.monotonic() - deps._last_1m_seed_attempt[clean_sym]) >= ONE_MIN_SEED_COOLDOWN_SEC)
        )
        pending_now = deps._inflight_history.get(f"{clean_sym}_1m")
        inflight_active = pending_now is not None and not pending_now.done()
        if should_seed_short_history and seed and not inflight_active:
            deps._last_1m_seed_attempt[clean_sym] = time.monotonic()
        if (not bars or backfill or (should_seed_short_history and seed)) and not inflight_active and (
            seed
            or backfill
            or clean_sym.startswith("BINANCE:")
            or clean_sym in ("AMEX:SPY", "NASDAQ:QQQ", "AMEX:IWM")
            or clean_sym.endswith(":SPY")
            or clean_sym.endswith(":QQQ")
            or clean_sym.endswith(":IWM")
        ):
            seed_bars = await fetch_universal_history(clean_sym, timeframe="1m")
            if seed_bars:
                deps.aggregator.seed_history(clean_sym, seed_bars)
                bars = deps.aggregator.get_history(clean_sym)
        if len(bars) < MIN_1M_HISTORY_BARS:
            cached_bars = deps.get_storage().get_bars(clean_sym, timeframe="1m", limit=limit or 300)
            if cached_bars:
                deps.aggregator.seed_history(clean_sym, cached_bars)
                bars = deps.aggregator.get_history(clean_sym)
        return {"symbol": clean_sym, "timeframe": clean_tf, "engine": "A", "bars": _apply_limit(bars)}

    bars = await fetch_universal_history(clean_sym, timeframe=clean_tf)
    return {"symbol": clean_sym, "timeframe": clean_tf, "engine": "A", "bars": _apply_limit(bars)}

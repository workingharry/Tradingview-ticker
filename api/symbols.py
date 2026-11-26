"""Health, symbol search, watchlist subscription/unsubscribe, and ordering endpoints."""

import asyncio
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from api import get_server_module
from api.deps import (
    ALLOWED_ASSET_TYPES,
    POPULAR_SEARCH_INDEX,
    SYMBOLS,
    WATCHLIST,
    YAHOO_EXCHANGE_MAP,
    aggregator,
    current_status,
    get_desktop_port,
    get_shared_client,
    is_crypto_symbol,
    is_desktop_mode,
    is_valid_symbol_id,
    logger,
    manager,
    normalize_symbol_id,
    tv_client,
)
from api.history import fetch_universal_history
from engine.screener import US_MOMENTUM_UNIVERSE
from engine.storage import get_storage

router = APIRouter()

async def health_check():
    """Health check endpoint for Tauri sidecar process manager and monitoring."""
    return {
        "status": "ok",
        "desktop_mode": (getattr(get_server_module(), 'is_desktop_mode', is_desktop_mode) if get_server_module() else is_desktop_mode)(),
        "port": get_desktop_port(),
        "timestamp": time.time(),
        "upstream": (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client).get_health() if (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client) else None,
    }


@router.get("/api/symbols")
async def get_symbols():
    """Returns the curated watchlist symbols, active quote states, and connection status."""
    data = []
    for item in WATCHLIST:
        sym = item["id"]
        st = (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client).states.get(sym, {}) if (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client) else {}
        data.append({
            **item,
            "state": st
        })
    return {"symbols": data, "status": (getattr(get_server_module(), 'current_status', current_status) if get_server_module() else current_status)}


@router.get("/api/symbols/search")
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
        shared = (getattr(get_server_module(), 'get_shared_client', get_shared_client) if get_server_module() else get_shared_client)()
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
                    if q_type in ("cryptocurrency", "crypto"):
                        continue
                    raw_exch = str(q_item.get("exchange") or "").upper()
                    mapped_exch = YAHOO_EXCHANGE_MAP.get(raw_exch)
                    if not mapped_exch:
                        continue

                    sym_type = "etf" if q_type == "etf" else ("index" if q_type == "index" else "stock")
                    short_name = q_item.get("shortname") or q_item.get("longname") or raw_ticker
                    sid = f"{mapped_exch}:{raw_ticker}"
                    sec = str(q_item.get("sector") or ("Index ETF" if sym_type == "etf" else ""))[:64]
                    ind = str(q_item.get("industry") or ("Large Cap Blend" if sym_type == "etf" else ""))[:64]
                    if not any(r["id"] == sid or r["pair"].upper() == raw_ticker for r in results):
                        results.append({
                            "id": sid,
                            "name": str(short_name)[:64],
                            "exchange": mapped_exch[:20],
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
                "type": "stock",
                "exchange_guessed": True
            })

    return {"results": results[:15]}


def _resolve_known_bare_symbol(clean: str) -> Optional[str]:
    """Resolves a bare ticker via local pools (watchlist, popular index, momentum universe) or crypto pair rules.

    Returns EXCHANGE:TICKER, or None when the ticker is unknown locally (caller then asks Yahoo).
    Mirrors normalize_symbol_id() but without its NASDAQ: default, so callers can tell a guess from a match.
    """
    for pool in (WATCHLIST, POPULAR_SEARCH_INDEX, US_MOMENTUM_UNIVERSE):
        for entry in pool:
            eid = str(entry.get("id") or "").strip().upper()
            if ":" in eid and eid.split(":", 1)[1] == clean:
                return eid
    if "USDT" in clean or "BUSD" in clean:
        return f"BINANCE:{clean}"
    return None

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

    exchange_guessed = False
    if ":" not in raw_symbol:
        existing = next((w["id"] for w in WATCHLIST if w.get("pair", "").upper() == raw_symbol or w["id"].split(":")[-1].upper() == raw_symbol), None)
        if not existing:
            existing = next((s for s in SYMBOLS if s.split(":")[-1].upper() == raw_symbol), None)
        if not existing:
            existing = _resolve_known_bare_symbol(raw_symbol)

        if existing:
            symbol = existing
        else:
            found_exchange = None
            try:
                shared = (getattr(get_server_module(), 'get_shared_client', get_shared_client) if get_server_module() else get_shared_client)()
                cli = shared or httpx.AsyncClient(timeout=4.0)
                should_close = shared is None
                try:
                    y_url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(raw_symbol)}&quotesCount=10&newsCount=0"
                    resp = await cli.get(y_url, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code == 200:
                        y_quotes = resp.json().get("quotes", [])
                        for q in y_quotes:
                            if str(q.get("symbol", "")).upper() == raw_symbol:
                                q_exch = str(q.get("exchange", "")).upper()
                                if q_exch in YAHOO_EXCHANGE_MAP:
                                    found_exchange = YAHOO_EXCHANGE_MAP[q_exch]
                                    break
                finally:
                    if should_close:
                        await cli.aclose()
            except Exception as e:
                logger.debug("Yahoo lookup in subscribe failed: %s", e)

            if found_exchange:
                symbol = f"{found_exchange}:{raw_symbol}"
            else:
                symbol = f"NASDAQ:{raw_symbol}"
                exchange_guessed = True
    else:
        symbol = normalize_symbol_id(raw_symbol)

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
                    (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)().remove_watchlist_item(evicted_id)
                except Exception as wl_evict_err:
                    logger.debug("Failed to remove evicted watchlist item from SQLite: %s", wl_evict_err)
                if tv_client:
                    try:
                        await (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client).unsubscribe(evicted_id)
                        logger.info("Unsubscribed evicted symbol from TradingView client: %s", evicted_id)
                    except Exception as unsub_err:
                        logger.debug("Error unsubscribing evicted symbol %s: %s", evicted_id, unsub_err)
                if aggregator:
                    if hasattr((getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator), "remove_symbol"):
                        (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).remove_symbol(evicted_id)
                    else:
                        (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).bar_history.pop(evicted_id, None)
                        (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).current_bars.pop(evicted_id, None)
                        (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).last_cumulative_volume.pop(evicted_id, None)
                (getattr(get_server_module(), 'manager', manager) if get_server_module() else manager).broadcast_nowait({
                    "type": "symbol_removed",
                    "symbol": evicted_id,
                })
        WATCHLIST.append(item)
        if symbol not in SYMBOLS:
            SYMBOLS.append(symbol)
        try:
            (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)().add_watchlist_item(item)
        except Exception as wl_save_err:
            logger.warning("Failed to persist new watchlist item to SQLite: %s", wl_save_err)

    if tv_client:
        await (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client).subscribe(symbol)

    if (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator) and not (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).get_history(symbol):
        bars = await (getattr(get_server_module(), 'fetch_universal_history', fetch_universal_history) if get_server_module() else fetch_universal_history)(symbol, timeframe="1m")
        if bars:
            (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).seed_history(symbol, bars)
            logger.info("Dynamically seeded %d bars for new subscription: %s", len(bars), symbol)

    if exchange_guessed:
        item["exchange_guessed"] = True

    (getattr(get_server_module(), 'manager', manager) if get_server_module() else manager).broadcast_nowait({
        "type": "symbol_added",
        "symbol": item
    })

    resp_out = {"status": "subscribed", "symbol": item}
    if exchange_guessed:
        resp_out["exchange_guessed"] = True
    return resp_out


@router.delete("/api/symbols/unsubscribe")
@router.post("/api/symbols/unsubscribe")
async def unsubscribe_symbol(payload: Optional[Dict[str, Any]] = Body(default=None), symbol: Optional[str] = Query(default=None)):
    """Unsubscribes from a symbol, evicts from watchlist, and updates persistent storage."""
    clean_query_sym = symbol if isinstance(symbol, str) else None
    clean_payload = payload if isinstance(payload, dict) else {}
    raw_sym = clean_query_sym or clean_payload.get("symbol") or clean_payload.get("id") or ""
    if not raw_sym:
        return JSONResponse({"error": "Symbol is required"}, status_code=400)
    clean_sym = normalize_symbol_id(str(raw_sym))

    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
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
            await (getattr(get_server_module(), 'tv_client', tv_client) if get_server_module() else tv_client).unsubscribe(clean_sym)
        except Exception as e:
            logger.debug("Error unsubscribing TradingView client for %s: %s", clean_sym, e)

    if (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator) and hasattr((getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator), "remove_symbol"):
        (getattr(get_server_module(), 'aggregator', aggregator) if get_server_module() else aggregator).remove_symbol(clean_sym)

    (getattr(get_server_module(), 'manager', manager) if get_server_module() else manager).broadcast_nowait({
        "type": "symbol_removed",
        "symbol": clean_sym
    })
    return {"status": "unsubscribed", "symbol": clean_sym, "removed": removed}


@router.post("/api/symbols/reorder")
async def reorder_symbols_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Persists a new sort order for the user's watchlist."""
    clean_payload = payload if isinstance(payload, dict) else {}
    order = clean_payload.get("order")
    if not order or not isinstance(order, list):
        return JSONResponse({"error": "Invalid order payload, list of symbol IDs required"}, status_code=400)

    clean_ids = [str(s).strip().upper() for s in order if str(s).strip()]
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
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

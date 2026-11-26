"""Recovery, user preferences, and persisted chart drawings endpoints."""

import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse

from api import get_server_module
from api.deps import WATCHLIST, is_valid_symbol_id, logger, normalize_symbol_id, normalize_timeframe
from api.history import fetch_universal_history
from engine.storage import get_storage

router = APIRouter()

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
    now_ts = int((getattr(get_server_module(), 'time', time) if get_server_module() else time).time())
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
            fetched = await (getattr(get_server_module(), 'fetch_universal_history', fetch_universal_history) if get_server_module() else fetch_universal_history)(clean_sym, timeframe=clean_tf)
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
    except Exception as err:
        logger.warning("Recovery failed reading watchlist from SQLite, falling back to memory: %s", err)
        watchlist = list(WATCHLIST)

    try:
        preferences = storage.get_preferences()
    except Exception as err:
        logger.warning("Recovery failed reading preferences from SQLite: %s", err)
        preferences = {}

    ai_lens_report = None
    try:
        ai_lens_report = storage.get_ai_agent_lens_report(clean_sym, clean_tf)
    except Exception as err:
        logger.warning("Recovery failed reading AI lens cache for %s (%s): %s", clean_sym, clean_tf, err)
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


@router.get("/api/recovery")
async def recovery_endpoint(
    symbol: str = Query(default="BINANCE:BTCUSDT"),
    timeframe: str = Query(default="1D"),
    since_time: Optional[int] = Query(default=None),
    limit: int = Query(default=300),
):
    """Unified terminal state and historical bar recovery endpoint."""
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    clean_sym = normalize_symbol_id(symbol if isinstance(symbol, str) else "BINANCE:BTCUSDT")
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


@router.get("/api/preferences")
async def get_preferences_endpoint():
    """Returns stored terminal preferences."""
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    return {"status": "ok", "preferences": storage.get_preferences()}


@router.post("/api/preferences")
async def save_preferences_endpoint(payload: Dict[str, Any] = Body(default={})):
    """Saves terminal preferences."""
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    clean_payload = payload if isinstance(payload, dict) else {}
    storage.save_preferences(clean_payload)
    return {"status": "ok", "preferences": storage.get_preferences()}


@router.get("/api/drawings")
async def get_drawings_endpoint(symbol: str = Query(..., description="Target stock ticker symbol")):
    """Returns stored drawings for a given symbol (with legacy bare-ticker read fallback)."""
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    clean_sym = normalize_symbol_id(symbol)
    drawings = storage.get_drawings(clean_sym)
    if not drawings and ":" in clean_sym:
        bare_sym = clean_sym.split(":", 1)[1]
        drawings = storage.get_drawings(bare_sym)
    return {"status": "ok", "symbol": clean_sym, "drawings": drawings}


@router.post("/api/drawings")
async def save_drawing_endpoint(payload: Dict[str, Any] = Body(...)):
    """Saves or updates a user drawing with revision and ownership checks."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload, expected JSON object")
    if not payload.get("id") or not payload.get("symbol"):
        raise HTTPException(status_code=422, detail="Missing required fields 'id' or 'symbol'")

    normalized_payload = dict(payload)
    normalized_payload["symbol"] = normalize_symbol_id(str(payload["symbol"]))

    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    try:
        saved_record = storage.save_drawing(normalized_payload)
        return {"status": "ok", "drawing": saved_record}
    except ValueError as val_err:
        raise HTTPException(status_code=409, detail=str(val_err))
    except Exception as exc:
        logger.exception("Failed to persist drawing to SQLite")
        raise HTTPException(status_code=500, detail=f"Failed to persist drawing to SQLite: {exc}")


@router.delete("/api/drawings/{drawing_id}")
async def delete_drawing_endpoint(drawing_id: str):
    """Deletes a drawing by ID."""
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    deleted = storage.delete_drawing(drawing_id, soft_delete=True)
    return {"status": "ok", "id": drawing_id, "deleted": deleted}


@router.delete("/api/drawings")
async def clear_drawings_endpoint(symbol: str = Query(..., description="Symbol whose drawings will be cleared")):
    """Clears all drawings for a symbol (including legacy bare-ticker rows)."""
    storage = (getattr(get_server_module(), 'get_storage', get_storage) if get_server_module() else get_storage)()
    clean_sym = normalize_symbol_id(symbol)
    count = storage.clear_drawings(clean_sym)
    if ":" in clean_sym:
        count += storage.clear_drawings(clean_sym.split(":", 1)[1])
    return {"status": "ok", "symbol": clean_sym, "cleared_count": count}

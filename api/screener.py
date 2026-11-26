"""CANSLIM / Stage 2 stock screener, AI screener summary, and theme heatmap endpoints."""

import asyncio
import math
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

import api.ai as ai_mod
import api.deps as deps
import api.history as history_mod
from api.bootstrap import logger
from api.config import WATCHLIST, _parse_criteria_number
from engine.market_scanner import evaluate_multi_frameworks, scan_all_us_stocks
from engine.market_time import current_session_date, last_completed_session_date
from engine.screener import (
    ScreeningCriteria,
    US_MOMENTUM_UNIVERSE,
    build_theme_heatmap,
    evaluate_canslim_stage,
    fetch_heatmap_universe,
    scan_universe,
)

router = APIRouter()

SCREENER_PRESETS: Dict[str, Dict[str, Any]] = {
    "multi_filter": {
        "id": "multi_filter",
        "name": "🏆 多框架 Tier 1–3 旗艦",
        "desc": "Weinstein + Deepvue + Minervini + CAN SLIM 四重框架交集共振，篩選市場最強動能領頭羊",
        "criteria": {
            "min_price": 15.0,
            "min_avg_vol": 500000,
            "min_eps_yoy": 20.0,
            "min_rev_yoy": 20.0,
            "min_roe": 15.0,
            "min_volume_ratio": 1.2,
            "min_perf_26w": 10.0,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": False,
            "max_atr_percentile": 30.0,
            "expansion_breakout_only": False,
            "mansfield_above_zero": True,
            "min_rev_growth_yoy": None,
            "allow_missing_fundamentals": False,
            "hide_v6_hard": False,
            "min_rs_rating": 80,
            "max_pct_from_52w_high": 15.0,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
    "canslim_standard": {
        "id": "canslim_standard",
        "name": "💎 CAN SLIM 經典高成長",
        "desc": "EPS/營收成長 >= 25% + ROE >= 17% + 距高點 15% 內，嚴格避開 7 日內財報地雷",
        "criteria": {
            "min_price": 15.0,
            "min_avg_vol": 400000,
            "min_eps_yoy": 25.0,
            "min_rev_yoy": 25.0,
            "min_roe": 17.0,
            "min_volume_ratio": 1.0,
            "min_perf_26w": 0.0,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": False,
            "max_atr_percentile": 30.0,
            "expansion_breakout_only": False,
            "mansfield_above_zero": True,
            "min_rev_growth_yoy": 25.0,
            "allow_missing_fundamentals": False,
            "hide_v6_hard": True,
            "min_rs_rating": 80,
            "max_pct_from_52w_high": 15.0,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
    "minervini_sepa": {
        "id": "minervini_sepa",
        "name": "⚡ Minervini SEPA 趨勢範本",
        "desc": "Stage 2 均線多頭排列 + 距 52W 高點 15% 內 + 26 週強勢上漲，允許初創生技與未有獲利公司",
        "criteria": {
            "min_price": 12.0,
            "min_avg_vol": 300000,
            "min_volume_ratio": 1.2,
            "min_perf_26w": 15.0,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": False,
            "max_atr_percentile": 30.0,
            "expansion_breakout_only": False,
            "mansfield_above_zero": True,
            "min_rev_growth_yoy": None,
            "allow_missing_fundamentals": True,
            "hide_v6_hard": False,
            "min_rs_rating": 85,
            "max_pct_from_52w_high": 15.0,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
    "weinstein_s2": {
        "id": "weinstein_s2",
        "name": "🌊 Weinstein 階段二放量突破",
        "desc": "站上 50/150/200 日線並放量 1.5 倍以上 + 26 週正報酬，捕捉轉折初升段",
        "criteria": {
            "min_price": 10.0,
            "min_avg_vol": 500000,
            "min_volume_ratio": 1.5,
            "min_perf_26w": 5.0,
            "stage2_only": True,
            "weekly_stage2_only": False,
            "power_trend_only": False,
            "atr_squeeze_only": False,
            "max_atr_percentile": 30.0,
            "expansion_breakout_only": False,
            "mansfield_above_zero": True,
            "min_rev_growth_yoy": None,
            "allow_missing_fundamentals": True,
            "hide_v6_hard": False,
            "min_rs_rating": 70,
            "max_pct_from_52w_high": 25.0,
            "risk_dollar": 500.0,
            "stop_atr_mult": 2.0,
        }
    },
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
    },
}


@router.get("/api/screener/presets")
async def get_screener_presets():
    """Returns pre-configured institutional momentum screening strategies."""
    return {
        "status": "ok",
        "presets": list(SCREENER_PRESETS.values())
    }


@router.post("/api/screener/scan")
async def scan_screener_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Executes multi-factor stock screening across specified universe."""
    clean_payload = payload if isinstance(payload, dict) else {}
    universe_type = str(clean_payload.get("universe") or "us_momentum").strip().lower()
    raw_criteria = clean_payload.get("criteria") if isinstance(clean_payload.get("criteria"), dict) else {}

    errors: List[str] = []

    # Safe validation of criteria values via _parse_criteria_number
    c_min_rs = _parse_criteria_number(raw_criteria, "min_rs_rating", 70, int, min_value=0, max_value=99, errors=errors)
    c_max_atr = _parse_criteria_number(raw_criteria, "max_atr_percentile", 20.0, float, min_value=0.0, max_value=100.0, errors=errors)
    c_max_52w = _parse_criteria_number(raw_criteria, "max_pct_from_52w_high", 25.0, float, min_value=0.0, max_value=100.0, errors=errors)
    c_min_vol_r = _parse_criteria_number(raw_criteria, "min_volume_ratio", 1.0, float, min_value=0.0, max_value=100.0, errors=errors)
    c_stop_mult = _parse_criteria_number(raw_criteria, "stop_atr_mult", 2.0, float, min_value=0.0, max_value=10.0, errors=errors, exclusive_min=True)
    c_m_len = _parse_criteria_number(raw_criteria, "mansfield_length", 252, int, min_value=10, max_value=500, errors=errors)

    raw_limit_dict = clean_payload if "limit" in clean_payload else raw_criteria
    c_limit = _parse_criteria_number(raw_limit_dict, "limit", 150, int, min_value=1, max_value=500, errors=errors)

    c_min_price = _parse_criteria_number(raw_criteria, "min_price", 10.0, float, min_value=0.0, max_value=None, errors=errors)
    c_min_avg_vol = _parse_criteria_number(raw_criteria, "min_avg_vol", 300000, int, min_value=0, max_value=None, errors=errors)

    if errors:
        return JSONResponse(
            status_code=422,
            content={"error": "Invalid screener criteria", "fields": sorted(list(set(errors)))}
        )

    min_rev_growth: Optional[float] = None
    raw_rev_yoy = raw_criteria.get("min_rev_growth_yoy")
    if raw_rev_yoy is None:
        raw_rev_yoy = raw_criteria.get("min_rev_yoy")
    if raw_rev_yoy is not None:
        try:
            min_rev_growth = float(raw_rev_yoy)
        except (ValueError, TypeError):
            min_rev_growth = None

    try:
        raw_risk = float(clean_payload.get("risk_dollar") or raw_criteria.get("risk_dollar") or 500.0)
        risk_dollar = raw_risk if math.isfinite(raw_risk) and raw_risk > 0 else 500.0
    except (ValueError, TypeError):
        risk_dollar = 500.0

    criteria = ScreeningCriteria(
        min_rs_rating=c_min_rs,
        mansfield_above_zero=bool(raw_criteria.get("mansfield_above_zero", True)),
        stage2_only=bool(raw_criteria.get("stage2_only", True)),
        weekly_stage2_only=bool(raw_criteria.get("weekly_stage2_only", False)),
        power_trend_only=bool(raw_criteria.get("power_trend_only", False)),
        atr_squeeze_only=bool(raw_criteria.get("atr_squeeze_only", False)),
        max_atr_percentile=c_max_atr,
        expansion_breakout_only=bool(raw_criteria.get("expansion_breakout_only", False)),
        max_pct_from_52w_high=c_max_52w,
        min_volume_ratio=c_min_vol_r,
        min_rev_growth_yoy=min_rev_growth,
        risk_dollar=risk_dollar,
        stop_atr_mult=c_stop_mult,
        mansfield_length=c_m_len
    )

    storage = deps.get_storage()

    # Ingest benchmark SPY bars for date alignment & market regime
    bench_bars = await history_mod.fetch_universal_history("AMEX:SPY", timeframe="1D")
    if not bench_bars:
        bench_bars = storage.get_bars("AMEX:SPY", "1D", limit=300)

    bench_stage = evaluate_canslim_stage(bench_bars) if bench_bars else {}
    bench_close = bench_stage.get("close") or 0.0
    bench_regime = bench_stage.get("trend_regime") or "NEUTRAL"

    # Determine scanning candidate pool
    universe_items: List[Dict[str, Any]] = []
    if universe_type == "all_us":
        crit_dict = {
            "min_price": c_min_price,
            "min_avg_vol": c_min_avg_vol,
            "min_eps_yoy": float(raw_criteria["min_eps_yoy"]) if raw_criteria.get("min_eps_yoy") is not None else None,
            "min_rev_yoy": float(raw_criteria["min_rev_yoy"]) if raw_criteria.get("min_rev_yoy") is not None else None,
            "min_roe": float(raw_criteria["min_roe"]) if raw_criteria.get("min_roe") is not None else None,
            "min_volume_ratio": c_min_vol_r,
            "min_perf_26w": float(raw_criteria.get("min_perf_26w", 0.0)),
            "stage2_only": bool(raw_criteria.get("stage2_only", True)),
            "allow_missing_fundamentals": bool(raw_criteria.get("allow_missing_fundamentals", False)),
            "min_rs_rating": c_min_rs,
            "max_pct_from_52w_high": c_max_52w,
            "power_trend_only": bool(raw_criteria.get("power_trend_only", False)),
        }
        hide_v6 = bool(raw_criteria.get("hide_v6_hard", False))
        scan_limit = c_limit
        start_t = time.perf_counter()
        scan_res = await scan_all_us_stocks(
            criteria=crit_dict,
            risk_dollar=risk_dollar,
            limit=scan_limit,
            hide_v6_hard=hide_v6,
        )
        matched_items, total_count = scan_res
        scan_warning = getattr(scan_res, "warning", None)
        elapsed_ms = int((time.perf_counter() - start_t) * 1000)

        today_str = current_session_date().isoformat()
        try:
            storage.record_screener_snapshot(today_str, matched_items)
            symbols_to_check = [it["symbol"] for it in matched_items]
            streaks = storage.get_screener_streaks(symbols_to_check, current_date=today_str)
            for it in matched_items:
                s_info = streaks.get(it["symbol"], {})
                it["streak_days"] = s_info.get("streak_days", 0)
                it["filter_delta"] = s_info.get("filter_delta", "NEW")
        except Exception as exc:
            logger.warning("Failed to record screener snapshot or get streaks: %s", exc)

        matched_items.sort(
            key=lambda x: (
                int(x.get("filter_count") or 0),
                float(x.get("rs_rating") or 0.0),
                float(x.get("change_pct") or 0.0),
            ),
            reverse=True,
        )

        resp_payload = {
            "status": "ok",
            "universe": universe_type,
            "elapsed_ms": elapsed_ms,
            "total_scanned": total_count,
            "matched_count": len(matched_items),
            "benchmark_status": {
                "symbol": "SPY",
                "close": bench_close,
                "regime": bench_regime,
                "mansfield_status": "BULLISH_ABOVE_ZERO" if bench_regime.startswith("STAGE_2") else "NEUTRAL",
            },
            "criteria": crit_dict,
            "items": matched_items,
        }
        if scan_warning:
            resp_payload["warning"] = "全美股遠端掃描連線逾時或異常，請稍後重試（可先切換核心動能庫或自選清單）"
        return resp_payload
    elif universe_type == "watchlist":
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
        fetch_func=history_mod.fetch_universal_history,
        max_concurrency=8
    )
    elapsed_ms = int((time.perf_counter() - start_t) * 1000)

    today_str = current_session_date().isoformat()
    try:
        for it in matched_items:
            if "frameworks" not in it:
                eval_res = evaluate_multi_frameworks(it)
                it.update(eval_res)
        storage.record_screener_snapshot(today_str, matched_items)
        symbols_to_check = [it["symbol"] for it in matched_items]
        streaks = storage.get_screener_streaks(symbols_to_check, current_date=today_str)
        for it in matched_items:
            s_info = streaks.get(it["symbol"], {})
            it["streak_days"] = s_info.get("streak_days", 0)
            it["filter_delta"] = s_info.get("filter_delta", "NEW")
    except Exception as exc:
        logger.debug("Streak/framework enrichment notice: %s", exc)

    matched_items.sort(
        key=lambda x: (
            int(x.get("filter_count") or 0),
            float(x.get("rs_rating") or 0.0),
            float(x.get("change_pct") or 0.0),
        ),
        reverse=True,
    )

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


@router.post("/api/screener/ai-summary")
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
            ai_mod.call_gemini_generate(prompt, temperature=0.3, max_tokens=700),
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


_HEATMAP_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_HEATMAP_TTL: float = 60.0


@router.get("/api/heatmap")
async def get_theme_heatmap_endpoint(universe: str = "themes", refresh: bool = False):
    """Generates thematic sector heatmap with relative performance & volume metrics."""
    universe_type = str(universe).strip().lower()
    if universe_type not in ("themes", "watchlist"):
        universe_type = "themes"

    now = time.time()
    if not refresh and universe_type in _HEATMAP_CACHE:
        cached_time, cached_data = _HEATMAP_CACHE[universe_type]
        if now - cached_time < _HEATMAP_TTL:
            return cached_data

    storage = deps.get_storage()

    universe_items: List[Dict[str, Any]] = []
    if universe_type == "watchlist":
        db_wl = storage.get_watchlist() if storage else []
        universe_items = db_wl if db_wl else list(WATCHLIST)
    else:
        universe_items = list(US_MOMENTUM_UNIVERSE)

    bench_bars = await history_mod.fetch_universal_history("AMEX:SPY", timeframe="1D")
    if not bench_bars and storage:
        bench_bars = storage.get_bars("AMEX:SPY", "1D", limit=300)

    bench_stage = evaluate_canslim_stage(bench_bars) if bench_bars else {}
    bench_change_pct = float(bench_stage.get("change_pct") or 0.0)

    evaluated_items = await fetch_heatmap_universe(
        universe=universe_items,
        storage_obj=storage,
        benchmark_bars=bench_bars,
        fetch_func=history_mod.fetch_universal_history,
        max_concurrency=8
    )

    theme_groups = build_theme_heatmap(
        items_with_metrics=evaluated_items,
        benchmark_change_pct=bench_change_pct
    )

    result = {
        "status": "ok",
        "universe": universe_type,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": {
            "symbol": "SPY",
            "change_pct": round(bench_change_pct, 2)
        },
        "themes": theme_groups
    }

    _HEATMAP_CACHE[universe_type] = (now, result)
    return result

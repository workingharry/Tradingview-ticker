"""Gemini waterfall, fundamentals, deterministic AI Lens, and /api/chat endpoints."""

import asyncio
import contextvars
import json
import math
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx
from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

import api.deps as deps
import api.history as history_mod
from api.bootstrap import logger
from api.config import (
    GEMINI_MODELS_CASCADE,
    is_crypto_symbol,
    is_valid_symbol_id,
    normalize_symbol_id,
    normalize_timeframe,
)
from api.deps import YahooSessionManager

router = APIRouter()

async def call_gemini_generate_with_model(
    prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 800
) -> Tuple[Optional[str], Optional[str]]:
    """Calls Gemini API with automatic model waterfall, returning (text, model_name)."""
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return None, None
    shared = deps.get_shared_client()
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
                            return text.strip(), model
                elif res.status_code in (404, 429, 503):
                    logger.debug("Gemini model %s returned HTTP %d, cascading...", model, res.status_code)
                    continue
            except Exception as e:
                logger.debug("Gemini model %s network exception: %s", model, e)
                continue
    finally:
        if should_close:
            await cli.aclose()
    return None, None


# Per-request holder for the model that actually produced the last Gemini reply.
# A mutable dict is stored (not the model string) so updates survive asyncio.wait_for / gather context copies.
_gemini_model_holder = contextvars.ContextVar("gemini_model_holder", default=None)


async def call_gemini_generate(prompt: str, temperature: float = 0.4, max_tokens: int = 800) -> Optional[str]:
    """Calls Gemini API with automatic model waterfall (backward-compatible, text only).

    Also records the successful model into the active _gemini_model_holder, if any.
    """
    text, model_used = await call_gemini_generate_with_model(prompt, temperature=temperature, max_tokens=max_tokens)
    holder = _gemini_model_holder.get()
    if isinstance(holder, dict):
        holder["model"] = model_used if text else None
    return text


async def _generate_with_model_tracking(
    prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 800
) -> Tuple[Optional[str], Optional[str]]:
    """Endpoint helper: returns (text, actual_model).

    Goes through call_gemini_generate so existing patches of that function keep working.
    When the reply comes from a patched/unknown source, the model is None (unknown) — never a guessed name.
    """
    holder: Dict[str, Optional[str]] = {"model": None}
    token = _gemini_model_holder.set(holder)
    try:
        text = await call_gemini_generate(prompt, temperature=temperature, max_tokens=max_tokens)
    finally:
        _gemini_model_holder.reset(token)
    if isinstance(text, str) and text.strip():
        return text.strip(), holder.get("model")
    return None, None


@router.get("/api/fundamentals")
async def get_fundamentals(symbol: str = Query(...), refresh: bool = Query(default=False)):
    """Fetches key fundamental financial metrics and Gemini AI CANSLIM analysis for a symbol."""
    if not is_valid_symbol_id(symbol):
        return JSONResponse({"error": "Invalid symbol format"}, status_code=400)

    clean_sym = normalize_symbol_id(symbol)
    storage = deps.get_storage()

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
        shared = deps.get_shared_client()
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
            shared_chart = deps.get_shared_client()
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
            if not fb_bars and deps.aggregator:
                fb_bars = deps.aggregator.get_history(clean_sym)
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
    model_name: Optional[str] = None
    source = "gemini"
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
            gen_text, actual_model = await _generate_with_model_tracking(prompt, temperature=0.4, max_tokens=800)
            if gen_text:
                ai_summary = gen_text
                model_name = actual_model
        except Exception as gemini_err:
            logger.debug("Gemini API call failed, generating fallback summary: %s", gemini_err)

    if not ai_summary:
        model_name = None
        source = "rule_based"
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
        "model": model_name,
        "source": source,
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
                    except (TypeError, ValueError, OverflowError) as err:
                        logger.debug("RS rating math fallback for %s: %s", symbol, err)
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
    quant = payload.get("quant_metrics", {}) if isinstance(payload, dict) else {}
    close_p = quant.get("close")
    ema10 = quant.get("ema10")
    ema21 = quant.get("ema21")
    sma50 = quant.get("sma50")
    regime = quant.get("trend_regime", "NEUTRAL")
    pct_from_high = quant.get("pct_from_52w_high", 0.0)
    vol_ratio = quant.get("volume_ratio_50") or 1.0
    rs_rating = quant.get("rs_rating")

    funds = (payload.get("fundamentals") or {}) if isinstance(payload, dict) else {}
    fund_notes = []
    if funds.get("pe_ratio") is not None:
        fund_notes.append(f"PE: {funds['pe_ratio']}")
    if funds.get("revenue_yoy"):
        fund_notes.append(f"營收YoY: {funds['revenue_yoy']}")
    if funds.get("eps") is not None:
        fund_notes.append(f"EPS: {funds['eps']}")
    fund_diag = ("、".join(fund_notes) + "。") if fund_notes else "無基本面資料"

    if close_p is None or ema21 is None or sma50 is None:
        return {
            "bias": "WAIT",
            "confidence": 0,
            "setup_summary": "技術面關鍵數據不足，缺乏足夠K線計算均線體系與趨勢結構。",
            "invalidation_condition": "待完整行情數據補齊後重新評估結構點位與支撐壓力。",
            "failure_mode": "因基礎量化數據不全，無法進行量化推論與風險定界。",
            "fundamental_diagnosis": fund_diag,
            "human_decision_note": "資料不足暫停交易決策，建議觀望或手動檢查標的資料完整性。",
            "data_quality": "insufficient",
            "source": "rule_based",
        }

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
        "data_quality": "ok",
        "source": "rule_based",
    }


@router.get("/api/ai-lens")
async def ai_lens_endpoint(
    symbol: str = Query(default="NASDAQ:AAPL"),
    timeframe: str = Query(default="1D"),
    refresh: bool = Query(default=False),
):
    """Retrieves or generates non-repainting AI Agent Lens quantitative report and decision."""
    clean_sym = normalize_symbol_id(symbol if isinstance(symbol, str) else "NASDAQ:AAPL")
    if not is_valid_symbol_id(clean_sym):
        return JSONResponse({"error": "Invalid symbol format"}, status_code=400)
    clean_tf = normalize_timeframe(timeframe, default="1D")
    storage = deps.get_storage()

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

    target_bars = await history_mod.fetch_universal_history(clean_sym, timeframe=clean_tf)
    if not target_bars:
        target_bars = storage.get_bars(clean_sym, clean_tf, limit=1500)

    bench_sym = "BINANCE:BTCUSDT" if is_crypto_symbol(clean_sym) else "AMEX:SPY"
    bench_bars = await history_mod.fetch_universal_history(bench_sym, timeframe=clean_tf)
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
    model_name: Optional[str] = None
    source = "gemini"
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
            gen_res, actual_model = await _generate_with_model_tracking(prompt, temperature=0.2, max_tokens=800)
            if gen_res:
                clean_text = gen_res.strip().replace("```json", "").replace("```", "").strip()
                parsed = json.loads(clean_text)
                if isinstance(parsed, dict) and "bias" in parsed and "confidence" in parsed:
                    decision = parsed
                    model_name = actual_model
                    decision["source"] = "gemini"
        except Exception as gemini_err:
            logger.debug("Gemini AI Lens call failed, falling back to deterministic engine: %s", gemini_err)

    if not decision:
        decision = generate_deterministic_ai_lens_decision(lens_payload)
        model_name = None
        source = "rule_based"

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
        "model": model_name,
        "source": source,
        "report": {
            "symbol": clean_sym,
            "timeframe": clean_tf,
            "bar_time": confirmed_time,
            "lens": lens_payload,
            "decision": decision,
            "model_used": model_name,
            "source": source,
            "updated_at": now_ts,
        }
    }


@router.post("/api/chat")
async def chat_endpoint(payload: Optional[Dict[str, Any]] = Body(default=None)):
    """Interactive Gemini Market Copilot Chat with rich live context injection."""
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Invalid payload format"}, status_code=400)

    user_msg = str(payload.get("message") or "").strip()
    if not user_msg:
        return JSONResponse({"error": "Message is required"}, status_code=400)

    clean_sym = normalize_symbol_id(str(payload.get("symbol") or "NASDAQ:NVDA"))

    if not is_valid_symbol_id(clean_sym):
        clean_sym = "NASDAQ:NVDA"

    clean_tf = normalize_timeframe(payload.get("timeframe"), default="1D")

    # Context gathering: latest bars & indicators
    storage = deps.get_storage()
    bars = storage.get_bars(clean_sym, clean_tf, limit=30)
    if not bars and deps.aggregator:
        bars = deps.aggregator.get_history(clean_sym)

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

    reply, used_model = await _generate_with_model_tracking(full_prompt, temperature=0.5, max_tokens=1000)
    source = "gemini" if reply else "rule_based"

    if not reply:
        used_model = None
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
        "model": used_model,
        "source": source
    }

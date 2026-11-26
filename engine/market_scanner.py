"""TradingView America Scanner Client & Multi-Framework Evaluation Engine.

Provides:
- build_tv_scan_payload: Compiles TradingView scanner API payload for US common stocks.
- parse_tv_scan_response: Safely parses TV scanner JSON with null and zero-division protection.
- evaluate_multi_frameworks: Evaluates Weinstein (W), Deepvue (D), Minervini (M), CAN SLIM (C) tiers and V6 earnings risk.
- scan_all_us_stocks: High-performance async scan with in-memory caching and fallback.
"""

from datetime import date, datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("tradingview.scanner")

TV_SCANNER_URL = "https://scanner.tradingview.com/america/scan"

TV_SCAN_COLUMNS: List[str] = [
    "name",
    "description",
    "exchange",
    "sector",
    "industry",
    "close",
    "change",
    "volume",
    "average_volume_30d_calc",
    "relative_volume_10d_calc",
    "SMA50",
    "SMA150",
    "SMA200",
    "EMA10",
    "EMA21",
    "price_52_week_high",
    "price_52_week_low",
    "Perf.6M",
    "Perf.Y",
    "ATR",
    "market_cap_basic",
    "earnings_per_share_diluted_yoy_growth_ttm",
    "earnings_per_share_diluted_qoq_growth_fq",
    "total_revenue_yoy_growth_fq",
    "return_on_equity",
    "earnings_release_next_date",
]

# In-memory scan cache: (cache_key) -> (timestamp, data, total_count)
_SCAN_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]], int]] = {}
CACHE_TTL_SECONDS = 60.0


def build_tv_scan_payload(criteria: Optional[Dict[str, Any]] = None, limit: int = 150) -> Dict[str, Any]:
    """Compiles TradingView scanner API query payload for US common stocks."""
    crit = criteria or {}
    capped_limit = max(1, min(limit, 500))

    filters: List[Dict[str, Any]] = [
        {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
        {"left": "type", "operation": "equal", "right": "stock"},
    ]

    min_price = crit.get("min_price")
    if min_price is not None and float(min_price) > 0:
        filters.append({"left": "close", "operation": "greater", "right": float(min_price)})

    min_avg_vol = crit.get("min_avg_vol")
    if min_avg_vol is not None and int(min_avg_vol) > 0:
        filters.append({"left": "average_volume_30d_calc", "operation": "greater", "right": int(min_avg_vol)})

    min_volume_ratio = crit.get("min_volume_ratio")
    if min_volume_ratio is not None and float(min_volume_ratio) > 0:
        filters.append({"left": "relative_volume_10d_calc", "operation": "greater", "right": float(min_volume_ratio)})

    min_perf_26w = crit.get("min_perf_26w")
    if min_perf_26w is not None and float(min_perf_26w) != 0:
        filters.append({"left": "Perf.6M", "operation": "greater", "right": float(min_perf_26w)})

    if crit.get("stage2_only"):
        filters.append({"left": "SMA50", "operation": "less", "right": "close"})
        filters.append({"left": "SMA50", "operation": "greater", "right": "SMA150"})
        filters.append({"left": "SMA150", "operation": "greater", "right": "SMA200"})

    allow_missing = bool(crit.get("allow_missing_fundamentals", False))
    if not allow_missing:
        min_eps_yoy = crit.get("min_eps_yoy")
        if min_eps_yoy is not None and float(min_eps_yoy) > 0:
            filters.append({
                "left": "earnings_per_share_diluted_yoy_growth_ttm",
                "operation": "greater",
                "right": float(min_eps_yoy),
            })

        min_rev_yoy = crit.get("min_rev_yoy")
        if min_rev_yoy is not None and float(min_rev_yoy) > 0:
            filters.append({
                "left": "total_revenue_yoy_growth_fq",
                "operation": "greater",
                "right": float(min_rev_yoy),
            })

        min_roe = crit.get("min_roe")
        if min_roe is not None and float(min_roe) > 0:
            filters.append({
                "left": "return_on_equity",
                "operation": "greater",
                "right": float(min_roe),
            })

    return {
        "filter": filters,
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}},
        "columns": TV_SCAN_COLUMNS,
        "sort": {"sortBy": "relative_volume_10d_calc", "sortOrder": "desc"},
        "range": [0, capped_limit],
    }


def parse_tv_scan_response(
    raw_json: Dict[str, Any],
    columns: Optional[List[str]] = None,
    risk_dollar: float = 500.0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Safely normalizes TradingView scan response with zero-division & null safety."""
    cols = columns or TV_SCAN_COLUMNS
    col_idx = {c: i for i, c in enumerate(cols)}
    total_count = int(raw_json.get("totalCount") or 0)
    data_rows = raw_json.get("data") or []

    def _get(row_list: List[Any], name: str, default: Any = None) -> Any:
        idx = col_idx.get(name)
        if idx is not None and idx < len(row_list):
            val = row_list[idx]
            return val if val is not None else default
        return default

    items: List[Dict[str, Any]] = []
    for entry in data_rows:
        row_d = entry.get("d") or []
        s_tag = str(entry.get("s") or "")
        sym = str(_get(row_d, "name") or (s_tag.split(":")[-1] if ":" in s_tag else s_tag)).strip().upper()
        if not sym:
            continue

        close = float(_get(row_d, "close", 0.0) or 0.0)
        h52 = float(_get(row_d, "price_52_week_high", 0.0) or 0.0)
        l52 = float(_get(row_d, "price_52_week_low", 0.0) or 0.0)
        atr = float(_get(row_d, "ATR", 0.0) or 0.0)
        perf_6m = float(_get(row_d, "Perf.6M", 0.0) or 0.0)
        perf_y = float(_get(row_d, "Perf.Y", 0.0) or 0.0)

        # 52W High / Low offsets
        pct_from_52w_high = round(((h52 - close) / h52) * 100.0, 2) if (h52 > 0 and close > 0) else 0.0
        pct_above_52w_low = round(((close - l52) / l52) * 100.0, 2) if (l52 > 0 and close > 0) else 0.0

        # ATR %
        atr_pct = round((atr / close) * 100.0, 2) if (close > 0 and atr > 0) else 0.0

        # Suggested shares for 2x ATR stop
        suggested_shares = int(risk_dollar / (2.0 * atr)) if (risk_dollar > 0 and atr > 0) else 0

        # RS rating proxy based on 1Y + 6M momentum
        perf_score = (perf_y * 0.6) + (perf_6m * 0.4)
        if perf_score > 0:
            rs_rating = min(99, max(1, int(50 + (perf_y * 0.22) + (perf_6m * 0.18))))
        elif perf_y != 0 or perf_6m != 0:
            rs_rating = max(1, int(50 + perf_score * 0.4))
        else:
            rs_rating = 50

        # Earnings next date
        raw_earn = _get(row_d, "earnings_release_next_date")
        earn_date_str = None
        if isinstance(raw_earn, (int, float)) and raw_earn > 0:
            try:
                earn_date_str = datetime.fromtimestamp(raw_earn, tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OverflowError, OSError) as err:
                logger.debug("Invalid numeric earnings_release_next_date %r for %s: %s", raw_earn, sym, err)
                earn_date_str = None
        elif isinstance(raw_earn, str) and raw_earn.strip():
            earn_date_str = raw_earn.strip()[:10]

        item: Dict[str, Any] = {
            "symbol": sym,
            "company_name": str(_get(row_d, "description") or sym),
            "exchange": str(_get(row_d, "exchange") or ""),
            "sector": str(_get(row_d, "sector") or ""),
            "industry": str(_get(row_d, "industry") or ""),
            "close": close,
            "change_pct": float(_get(row_d, "change", 0.0) or 0.0),
            "volume": int(_get(row_d, "volume", 0) or 0),
            "avg_vol": int(_get(row_d, "average_volume_30d_calc", 0) or 0),
            "volume_ratio_50": float(_get(row_d, "relative_volume_10d_calc", 0.0) or 0.0),
            "sma50": float(_get(row_d, "SMA50", 0.0) or 0.0),
            "sma150": float(_get(row_d, "SMA150", 0.0) or 0.0),
            "sma200": float(_get(row_d, "SMA200", 0.0) or 0.0),
            "ema10": float(_get(row_d, "EMA10", 0.0) or 0.0),
            "ema21": float(_get(row_d, "EMA21", 0.0) or 0.0),
            "price_52_week_high": h52,
            "price_52_week_low": l52,
            "pct_from_52w_high": pct_from_52w_high,
            "pct_above_52w_low": pct_above_52w_low,
            "perf_26w": perf_6m,
            "perf_y": perf_y,
            "atr": atr,
            "atr_pct": atr_pct,
            "market_cap": float(_get(row_d, "market_cap_basic", 0.0) or 0.0),
            "eps_yoy": float(_get(row_d, "earnings_per_share_diluted_yoy_growth_ttm", 0.0) or 0.0),
            "eps_qoq": float(_get(row_d, "earnings_per_share_diluted_qoq_growth_fq", 0.0) or 0.0),
            "rev_yoy": float(_get(row_d, "total_revenue_yoy_growth_fq", 0.0) or 0.0),
            "roe": float(_get(row_d, "return_on_equity", 0.0) or 0.0),
            "earnings_release_next_date": earn_date_str,
            "rs_rating": rs_rating,
            "suggested_shares": suggested_shares,
        }
        items.append(item)

    return items, total_count


def evaluate_multi_frameworks(item: Dict[str, Any], reference_date: Optional[date] = None) -> Dict[str, Any]:
    """Evaluates Weinstein (W), Deepvue (D), Minervini (M), CAN SLIM (C) and V6 earnings risk."""
    from engine.market_time import current_session_date
    ref_d = reference_date or current_session_date()

    close = float(item.get("close") or 0.0)
    sma50 = float(item.get("sma50") or 0.0)
    sma150 = float(item.get("sma150") or 0.0)
    sma200 = float(item.get("sma200") or 0.0)
    perf_26w = float(item.get("perf_26w") or 0.0)
    pct_from_52w_high = float(item.get("pct_from_52w_high") if item.get("pct_from_52w_high") is not None else 999.0)
    pct_above_52w_low = float(item.get("pct_above_52w_low") or 0.0)
    vol_ratio = float(item.get("volume_ratio_50") or 0.0)
    eps_yoy = float(item.get("eps_yoy") or 0.0)
    rev_yoy = float(item.get("rev_yoy") or 0.0)
    roe = float(item.get("roe") or 0.0)

    # 1. Weinstein Stage 2 (W)
    is_w = (close > sma50 > sma150 > sma200 > 0) and (perf_26w > 0)

    # 2. Deepvue (D)
    is_d = (eps_yoy >= 20.0) and (rev_yoy >= 20.0) and (vol_ratio >= 1.5)

    # 3. Minervini SEPA (M)
    is_m = (
        (close > sma50 > sma150 > sma200 > 0)
        and (pct_from_52w_high <= 25.0)
        and (pct_above_52w_low >= 30.0)
        and (perf_26w > 0)
    )

    # 4. CAN SLIM (C)
    is_c = (
        (eps_yoy >= 25.0)
        and (rev_yoy >= 25.0)
        and (roe >= 17.0)
        and (pct_from_52w_high <= 15.0)
        and (perf_26w > 0)
    )

    frameworks: List[str] = []
    if is_w:
        frameworks.append("W")
    if is_d:
        frameworks.append("D")
    if is_m:
        frameworks.append("M")
    if is_c:
        frameworks.append("C")

    filter_count = len(frameworks)
    if filter_count == 4:
        tier = "Tier 1"
    elif filter_count == 3:
        tier = "Tier 2"
    elif filter_count == 2:
        tier = "Tier 3"
    else:
        tier = "Watch"

    # V6 Earnings Risk Rule
    v6_risk = "Clear"
    raw_earn = item.get("earnings_release_next_date")
    if raw_earn:
        try:
            earn_d = datetime.strptime(str(raw_earn)[:10], "%Y-%m-%d").date()
            diff_days = (earn_d - ref_d).days
            if 0 <= diff_days <= 7:
                v6_risk = "V6-Hard"
            elif 8 <= diff_days <= 14:
                v6_risk = "V6-Soft"
            else:
                v6_risk = "Clear"
        except (TypeError, ValueError) as err:
            logger.debug("Invalid earnings date %r for %s: %s", raw_earn, item.get("symbol"), err)
            v6_risk = "Clear"

    res = dict(item)
    res["frameworks"] = frameworks
    res["filter_count"] = filter_count
    res["tier"] = tier
    res["v6_risk"] = v6_risk
    return res


class ScanResult(tuple):
    """2-element tuple (items, total_count) backward compatible with optional warning attribute."""
    items: List[Dict[str, Any]]
    total_count: int
    warning: Optional[str]

    def __new__(cls, items: List[Dict[str, Any]], total_count: int, warning: Optional[str] = None):
        return super().__new__(cls, (items, total_count))

    def __init__(self, items: List[Dict[str, Any]], total_count: int, warning: Optional[str] = None):
        self.items = items
        self.total_count = total_count
        self.warning = warning


def filter_scanned_items(
    items: List[Dict[str, Any]],
    criteria: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Applies local post-filtering for criteria not fully evaluated by remote scan API."""
    crit = criteria or {}
    min_rs = int(crit.get("min_rs_rating") or 0)
    max_pct_from_high = float(crit["max_pct_from_52w_high"]) if crit.get("max_pct_from_52w_high") is not None else 100.0
    power_trend = bool(crit.get("power_trend_only", False))

    filtered: List[Dict[str, Any]] = []
    for it in items:
        if min_rs > 0 and int(it.get("rs_rating") or 0) < min_rs:
            continue
        if max_pct_from_high < 100.0:
            it_high_pct = float(it.get("pct_from_52w_high") if it.get("pct_from_52w_high") is not None else 999.0)
            if it_high_pct > max_pct_from_high:
                continue
        if power_trend:
            c = float(it.get("close") or 0.0)
            e10 = float(it.get("ema10") or 0.0)
            e21 = float(it.get("ema21") or 0.0)
            s50 = float(it.get("sma50") or 0.0)
            if not (c > e10 > e21 > s50 > 0):
                continue
        filtered.append(it)
    return filtered


async def scan_all_us_stocks(
    criteria: Optional[Dict[str, Any]] = None,
    risk_dollar: float = 500.0,
    limit: int = 150,
    hide_v6_hard: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """Performs async scan against TradingView America endpoint with cache and multi-frameworks evaluation."""
    crit = criteria or {}
    payload = build_tv_scan_payload(crit, limit=limit)
    local_cache_tags = (
        int(crit.get("min_rs_rating") or 0),
        float(crit["max_pct_from_52w_high"]) if crit.get("max_pct_from_52w_high") is not None else 100.0,
        bool(crit.get("power_trend_only", False)),
    )
    cache_key = json.dumps(payload, sort_keys=True) + f"_{risk_dollar}_{hide_v6_hard}_{local_cache_tags}"

    now = time.time()
    if cache_key in _SCAN_CACHE:
        ts, cached_items, cached_total = _SCAN_CACHE[cache_key]
        if (now - ts) < CACHE_TTL_SECONDS:
            return ScanResult(cached_items, cached_total)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(TV_SCANNER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            raw_data = resp.json()

        items, total_count = parse_tv_scan_response(raw_data, columns=TV_SCAN_COLUMNS, risk_dollar=risk_dollar)
        from engine.market_time import current_session_date
        today = current_session_date()
        evaluated_items = [evaluate_multi_frameworks(it, reference_date=today) for it in items]

        if hide_v6_hard:
            evaluated_items = [it for it in evaluated_items if it.get("v6_risk") != "V6-Hard"]

        evaluated_items = filter_scanned_items(evaluated_items, criteria=crit)

        _SCAN_CACHE[cache_key] = (now, evaluated_items, total_count)
        return ScanResult(evaluated_items, total_count)
    except Exception as exc:
        logger.warning("TradingView America scanner failed (%s), returning empty list: %s", type(exc).__name__, exc)
        return ScanResult([], 0, warning=f"{type(exc).__name__}: {exc}")

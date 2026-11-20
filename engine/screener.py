"""Institutional-grade Stock Screener Engine (RS + ATR + CANSLIM).

Implements:
1. Mansfield Relative Strength (52-week / 252-day zero-axis oscillator with date alignment).
2. William O'Neil CANSLIM RS Rating (1-99) with anchor mapping for small-sample stability.
3. Mark Minervini SEPA Stage 2 Uptrend evaluation with 200MA slope check.
4. ATR Volatility Squeeze & Expansion Breakout with Zero-Division protection.
5. High-concurrency universe scanning with asyncio.Semaphore rate limiting.
"""

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("tradingview.screener")

# Built-in High-Growth & Momentum Universe (45 core US liquid equities & leading ETFs)
US_MOMENTUM_UNIVERSE: List[Dict[str, str]] = [
    {"id": "NASDAQ:NVDA", "symbol": "NVDA", "name": "NVIDIA Corp.", "sector": "Technology"},
    {"id": "NASDAQ:AAPL", "symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology"},
    {"id": "NASDAQ:MSFT", "symbol": "MSFT", "name": "Microsoft Corp.", "sector": "Technology"},
    {"id": "NASDAQ:TSLA", "symbol": "TSLA", "name": "Tesla, Inc.", "sector": "Consumer Cyclical"},
    {"id": "NASDAQ:AMZN", "symbol": "AMZN", "name": "Amazon.com, Inc.", "sector": "Consumer Cyclical"},
    {"id": "NASDAQ:GOOGL", "symbol": "GOOGL", "name": "Alphabet Inc.", "sector": "Communication Services"},
    {"id": "NASDAQ:META", "symbol": "META", "name": "Meta Platforms, Inc.", "sector": "Communication Services"},
    {"id": "NASDAQ:AMD", "symbol": "AMD", "name": "Advanced Micro Devices", "sector": "Technology"},
    {"id": "NASDAQ:AVGO", "symbol": "AVGO", "name": "Broadcom Inc.", "sector": "Technology"},
    {"id": "NASDAQ:PLTR", "symbol": "PLTR", "name": "Palantir Technologies", "sector": "Technology"},
    {"id": "NASDAQ:MRNA", "symbol": "MRNA", "name": "Moderna, Inc.", "sector": "Healthcare"},
    {"id": "NASDAQ:FAST", "symbol": "FAST", "name": "Fastenal Company", "sector": "Industrials"},
    {"id": "NASDAQ:PRCH", "symbol": "PRCH", "name": "Porch Group Inc.", "sector": "Technology"},
    {"id": "NASDAQ:CART", "symbol": "CART", "name": "Maplebear Inc.", "sector": "Consumer Cyclical"},
    {"id": "NASDAQ:CAST", "symbol": "CAST", "name": "FreeCast, Inc.", "sector": "Communication Services"},
    {"id": "NYSE:CRI", "symbol": "CRI", "name": "Carter's, Inc.", "sector": "Consumer Cyclical"},
    {"id": "NASDAQ:CRWD", "symbol": "CRWD", "name": "CrowdStrike Holdings", "sector": "Technology"},
    {"id": "NASDAQ:PANW", "symbol": "PANW", "name": "Palo Alto Networks", "sector": "Technology"},
    {"id": "NASDAQ:SMCI", "symbol": "SMCI", "name": "Super Micro Computer", "sector": "Technology"},
    {"id": "NASDAQ:CELH", "symbol": "CELH", "name": "Celsius Holdings", "sector": "Consumer Defensive"},
    {"id": "NASDAQ:AXON", "symbol": "AXON", "name": "Axon Enterprise", "sector": "Industrials"},
    {"id": "NASDAQ:APP", "symbol": "APP", "name": "AppLovin Corp.", "sector": "Technology"},
    {"id": "NASDAQ:DUOL", "symbol": "DUOL", "name": "Duolingo, Inc.", "sector": "Technology"},
    {"id": "NYSE:NET", "symbol": "NET", "name": "Cloudflare, Inc.", "sector": "Technology"},
    {"id": "NYSE:SNOW", "symbol": "SNOW", "name": "Snowflake Inc.", "sector": "Technology"},
    {"id": "NASDAQ:MDB", "symbol": "MDB", "name": "MongoDB, Inc.", "sector": "Technology"},
    {"id": "NYSE:UBER", "symbol": "UBER", "name": "Uber Technologies", "sector": "Technology"},
    {"id": "NASDAQ:ARM", "symbol": "ARM", "name": "Arm Holdings plc", "sector": "Technology"},
    {"id": "NASDAQ:QCOM", "symbol": "QCOM", "name": "QUALCOMM Inc.", "sector": "Technology"},
    {"id": "NYSE:ANET", "symbol": "ANET", "name": "Arista Networks", "sector": "Technology"},
    {"id": "NASDAQ:ISRG", "symbol": "ISRG", "name": "Intuitive Surgical", "sector": "Healthcare"},
    {"id": "NYSE:LLY", "symbol": "LLY", "name": "Eli Lilly and Co.", "sector": "Healthcare"},
    {"id": "NYSE:V", "symbol": "V", "name": "Visa Inc.", "sector": "Financial Services"},
    {"id": "NYSE:MA", "symbol": "MA", "name": "Mastercard Inc.", "sector": "Financial Services"},
    {"id": "NYSE:JPM", "symbol": "JPM", "name": "JPMorgan Chase & Co.", "sector": "Financial Services"},
    {"id": "NYSE:GS", "symbol": "GS", "name": "Goldman Sachs Group", "sector": "Financial Services"},
    {"id": "AMEX:SPY", "symbol": "SPY", "name": "SPDR S&P 500 ETF", "sector": "Index ETF"},
    {"id": "NASDAQ:QQQ", "symbol": "QQQ", "name": "Invesco QQQ Trust", "sector": "Index ETF"},
    {"id": "AMEX:IWM", "symbol": "IWM", "name": "iShares Russell 2000 ETF", "sector": "Index ETF"},
]


@dataclass
class ScreeningCriteria:
    min_rs_rating: int = 70                  # CANSLIM RS Rating >= 70
    mansfield_above_zero: bool = True        # Mansfield RS (52W/252D) > 0
    stage2_only: bool = True                 # Stage 2 Uptrend (Close > SMA50 > SMA200 & 200MA rising)
    weekly_stage2_only: bool = False         # Stan Weinstein 1W Stage 2 (Weekly Close > SMA30_W & SMA10_W > SMA30_W & rising)
    power_trend_only: bool = False           # Close > EMA10 > EMA21 > SMA50
    atr_squeeze_only: bool = False           # ATR% percentile <= max_atr_percentile
    max_atr_percentile: float = 20.0         # Threshold for Volatility Squeeze
    expansion_breakout_only: bool = False    # TrueRange expansion + 50D Vol breakout
    max_pct_from_52w_high: float = 25.0      # Within X% of 52-week High
    min_volume_ratio: float = 1.0            # Volume / 50D Vol MA >= X
    min_rev_growth_yoy: Optional[float] = None # Quarterly Revenue YoY growth % (None = skip)
    risk_dollar: float = 500.0               # Position sizing risk per trade
    stop_atr_mult: float = 2.0               # Stop distance in ATR multiples
    mansfield_length: int = 252              # 252 bars for 1D (52 weeks), or 52 for weekly


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def _format_date_key(raw_time: Any) -> str:
    """Standardizes unix seconds, milliseconds, or ISO strings into YYYY-MM-DD."""
    if isinstance(raw_time, (int, float)):
        ts = int(raw_time)
        if ts > 100_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    s = str(raw_time).strip()
    return s[:10] if len(s) >= 10 else s


def calculate_mansfield_rs(
    target_bars: Sequence[Dict[str, Any]],
    benchmark_bars: Sequence[Dict[str, Any]],
    length: int = 252
) -> Optional[float]:
    """Calculates Mansfield Relative Strength oscillator at the latest aligned bar.

    Enforces Date Alignment:
    Matches target and benchmark bars by date (YYYY-MM-DD) via Inner Join to prevent
    index-offset misalignments from market holidays, suspensions, or missing data.
    Formula:
        RP_t = Close_target_t / Close_benchmark_t
        Mansfield_t = ((RP_t / SMA(RP, length)_t) - 1.0) * 100.0
    Returns:
        float value centered at 0.00 (e.g., +7.41), or None if insufficient history.
    """
    if not target_bars or not benchmark_bars or length <= 0:
        return None

    # 1. Index benchmark bars by date
    bench_map: Dict[str, float] = {}
    for b in benchmark_bars:
        d_key = _format_date_key(b.get("time"))
        c = _safe_float(b.get("close"))
        if d_key and c > 0:
            bench_map[d_key] = c

    # 2. Inner-join target bars with benchmark
    aligned_ratios: List[float] = []
    for tb in target_bars:
        d_key = _format_date_key(tb.get("time"))
        tc = _safe_float(tb.get("close"))
        bc = bench_map.get(d_key)
        if bc is not None and bc > 0 and tc > 0:
            aligned_ratios.append(tc / bc)

    if len(aligned_ratios) < length:
        # Fallback to shorter length if history is at least 40 bars (e.g., sub-new stocks)
        if len(aligned_ratios) >= 40:
            length = len(aligned_ratios)
        else:
            return None

    recent_window = aligned_ratios[-length:]
    sma_rp = sum(recent_window) / float(length)
    if sma_rp <= 0:
        return None

    current_rp = aligned_ratios[-1]
    mansfield_val = ((current_rp / sma_rp) - 1.0) * 100.0
    return round(mansfield_val, 2)


def calculate_canslim_rs_rating(
    target_bars: Sequence[Dict[str, Any]],
    benchmark_bars: Optional[Sequence[Dict[str, Any]]] = None
) -> int:
    """Calculates IBD-style Relative Strength Rating (1-99) with Benchmark Anchoring.

    Solves the Small-Sample Distortion Trap:
    Uses standard William O'Neil 4-quarter weighted performance:
        Raw Score = 0.4 * R_63d + 0.2 * R_126d + 0.2 * R_189d + 0.2 * R_252d
    Anchors through a calibrated US equity market logistic distribution so that
    the calculated score remains consistent across any small watchlist or custom ticker list.
    """
    if not target_bars or len(target_bars) < 20:
        return 50

    closes = [_safe_float(b.get("close")) for b in target_bars if _safe_float(b.get("close")) > 0]
    if len(closes) < 20:
        return 50

    def _calc_raw_ibd_score(p_closes: Sequence[float]) -> float:
        pn = len(p_closes)
        if pn < 20:
            return 0.0
        curr_p = p_closes[-1]

        def _pct(lb: int) -> float:
            if pn > lb:
                past_p = p_closes[-lb - 1]
            else:
                past_p = p_closes[0]
            return ((curr_p - past_p) / past_p) * 100.0 if past_p > 0 else 0.0

        r_3m = _pct(63)
        r_6m = _pct(126)
        r_9m = _pct(189)
        r_12m = _pct(252)

        if pn >= 252:
            return 0.4 * r_3m + 0.2 * r_6m + 0.2 * r_9m + 0.2 * r_12m
        elif pn >= 126:
            return 0.6 * r_3m + 0.4 * r_6m
        else:
            return r_3m

    raw_score = _calc_raw_ibd_score(closes)

    # Benchmark relative offset if benchmark bars are available (symmetrical date-aligned deduction)
    if benchmark_bars and len(benchmark_bars) >= 20:
        bench_map: Dict[str, float] = {}
        for b in benchmark_bars:
            d_key = _format_date_key(b.get("time"))
            c = _safe_float(b.get("close"))
            if d_key and c > 0:
                bench_map[d_key] = c

        aligned_b_closes: List[float] = []
        for tb in target_bars:
            d_key = _format_date_key(tb.get("time"))
            if d_key in bench_map:
                aligned_b_closes.append(bench_map[d_key])

        if len(aligned_b_closes) >= 20:
            bench_raw = _calc_raw_ibd_score(aligned_b_closes)
            raw_score -= bench_raw
        else:
            b_closes = [_safe_float(b.get("close")) for b in benchmark_bars if _safe_float(b.get("close")) > 0]
            if len(b_closes) >= 20:
                if len(b_closes) > len(closes):
                    b_closes = b_closes[-len(closes):]
                bench_raw = _calc_raw_ibd_score(b_closes)
                raw_score -= bench_raw

    # Calibrated anchor mapping: logistic sigmoid scaled from 1 to 99
    # Zero excess momentum maps to ~50; +60% momentum maps to ~90; -40% maps to ~15.
    try:
        norm_val = 1.0 / (1.0 + math.exp(-raw_score / 28.0))
        score = int(round(norm_val * 98.0)) + 1
        return max(1, min(99, score))
    except OverflowError:
        return 99 if raw_score > 0 else 1


def calculate_atr_toolkit(
    bars: Sequence[Dict[str, Any]],
    atr_len: int = 14,
    pct_len: int = 126,
    squeeze_thresh: float = 20.0,
    risk_dollar: float = 500.0,
    stop_mult: float = 2.0
) -> Dict[str, Any]:
    """Calculates ATR%, Volatility Squeeze percentile, and Position Sizing.

    Guards:
    Protects against ZeroDivisionError when ATR == 0 (e.g. halt or flat bars)
    or when close <= 0.
    """
    default_result = {
        "atr": 0.0,
        "atr_pct": 0.0,
        "atr_percentile": 50.0,
        "is_squeeze": False,
        "is_breakout": False,
        "expansion_ratio": 0.0,
        "suggested_shares": 0,
        "stop_distance": 0.0
    }
    if not bars or len(bars) < atr_len or atr_len <= 0:
        return default_result

    # 1. Calculate True Range series
    trs: List[float] = []
    closes: List[float] = []
    volumes: List[float] = []
    for i, b in enumerate(bars):
        h = _safe_float(b.get("high"))
        l = _safe_float(b.get("low"))
        c = _safe_float(b.get("close"))
        v = _safe_float(b.get("volume"))
        closes.append(c)
        volumes.append(v)
        if i == 0:
            trs.append(max(0.0, h - l))
        else:
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(max(0.0, tr))

    # 2. Wilder RMA of ATR
    if len(trs) < atr_len:
        return default_result

    atr_val = sum(trs[:atr_len]) / float(atr_len)
    atr_series: List[float] = [atr_val]
    for i in range(atr_len, len(trs)):
        atr_val = (atr_val * (atr_len - 1) + trs[i]) / float(atr_len)
        atr_series.append(atr_val)

    latest_atr = atr_series[-1]
    latest_close = closes[-1]
    if latest_close <= 0 or latest_atr <= 0:
        return default_result

    latest_atr_pct = (latest_atr / latest_close) * 100.0

    # 3. ATR% percentile over trailing pct_len
    raw_atr_pcts: List[float] = []
    offset = len(closes) - len(atr_series)
    for idx, a in enumerate(atr_series):
        c_idx = idx + offset
        c_val = closes[c_idx]
        if c_val > 0 and a > 0:
            raw_atr_pcts.append((a / c_val) * 100.0)

    if not raw_atr_pcts:
        return default_result

    window = raw_atr_pcts[-pct_len:] if len(raw_atr_pcts) > pct_len else raw_atr_pcts
    target_pct = raw_atr_pcts[-1]
    count_le = sum(1 for x in window if x <= target_pct)
    atr_percentile = (count_le / float(len(window))) * 100.0
    is_squeeze = atr_percentile <= squeeze_thresh

    # 4. Breakout Expansion check
    latest_tr = trs[-1]
    expansion_ratio = (latest_tr / latest_atr) if latest_atr > 0 else 0.0
    vol_50 = volumes[-50:] if len(volumes) >= 50 else volumes
    mean_vol = (sum(vol_50) / len(vol_50)) if vol_50 else 0.0
    vol_ratio = (volumes[-1] / mean_vol) if mean_vol > 0 else 1.0

    is_breakout = expansion_ratio >= 1.5 and vol_ratio >= 1.5

    # 5. Position Sizing
    stop_distance = latest_atr * stop_mult
    suggested_shares = 0
    if stop_distance > 0 and risk_dollar > 0:
        suggested_shares = int(math.floor(risk_dollar / stop_distance))

    return {
        "atr": round(latest_atr, 2),
        "atr_pct": round(latest_atr_pct, 2),
        "atr_percentile": round(atr_percentile, 1),
        "is_squeeze": is_squeeze,
        "is_breakout": is_breakout,
        "expansion_ratio": round(expansion_ratio, 2),
        "suggested_shares": suggested_shares,
        "stop_distance": round(stop_distance, 2)
    }


def _resample_1d_to_1w(bars: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resamples 1D daily bars to 1W weekly bars in memory.

    Groups bars chronologically into ISO calendar (year, week) without remote network requests.
    For each week:
        open = first bar open
        high = max(highs)
        low = min(lows)
        close = last bar close
        volume = sum(volumes)
        time = last bar time
    """
    if not bars:
        return []

    def _extract_datetime(b: Dict[str, Any]) -> Tuple[float, Optional[datetime]]:
        t = b.get("time")
        if isinstance(t, datetime):
            dt = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
            return dt.timestamp(), dt
        if isinstance(t, (int, float)):
            f = float(t)
            ts = f / 1000.0 if f > 100_000_000_000 else f
            try:
                return ts, datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                return ts, None
        s = str(t).strip().replace("/", "-")
        try:
            if len(s) >= 10:
                dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return dt.timestamp(), dt
        except Exception as parse_err:
            logger.debug("Failed to parse date string %s: %s", s, parse_err)
        return 0.0, None

    parsed_bars: List[Tuple[float, datetime, Dict[str, Any]]] = []
    for b in bars:
        if b.get("time") is not None:
            ts, dt = _extract_datetime(b)
            if dt is not None:
                parsed_bars.append((ts, dt, b))

    if not parsed_bars:
        return []

    parsed_bars.sort(key=lambda x: x[0])

    # Deduplicate bars by calendar date (YYYY-MM-DD) to prevent upstream duplicates from corrupting volume/OHLC
    seen_dates = set()
    deduped_bars: List[Tuple[float, datetime, Dict[str, Any]]] = []
    for ts, dt, b in parsed_bars:
        d_key = dt.strftime("%Y-%m-%d")
        if d_key in seen_dates:
            continue
        seen_dates.add(d_key)
        deduped_bars.append((ts, dt, b))

    weekly_map: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    weekly_order: List[Tuple[int, int]] = []

    for _, dt, b in deduped_bars:
        iso_year, iso_week, _ = dt.isocalendar()
        key = (iso_year, iso_week)
        if key not in weekly_map:
            weekly_map[key] = []
            weekly_order.append(key)
        weekly_map[key].append(b)

    weekly_bars: List[Dict[str, Any]] = []
    for key in weekly_order:
        w_bars = weekly_map[key]
        if not w_bars:
            continue
        first_bar = w_bars[0]
        last_bar = w_bars[-1]

        highs = [_safe_float(b.get("high"), _safe_float(b.get("close"))) for b in w_bars]
        lows = [_safe_float(b.get("low"), _safe_float(b.get("close"))) for b in w_bars]

        w_open = _safe_float(first_bar.get("open"), _safe_float(first_bar.get("close")))
        w_close = _safe_float(last_bar.get("close"))
        raw_high = max(highs) if highs else w_open
        raw_low = min(lows) if lows else w_open
        w_high = max(raw_high, w_open, w_close)
        w_low = min(raw_low, w_open, w_close)
        w_vol = sum(_safe_float(b.get("volume")) for b in w_bars)

        weekly_bars.append({
            "time": last_bar.get("time"),
            "open": round(w_open, 4),
            "high": round(w_high, 4),
            "low": round(w_low, 4),
            "close": round(w_close, 4),
            "volume": round(w_vol, 2)
        })

    return weekly_bars


def calculate_weekly_stage2(weekly_bars: Sequence[Dict[str, Any]]) -> bool:
    """Evaluates Stan Weinstein Weekly Stage 2 Criteria on weekly bars.

    Conditions:
    1. Weekly Close > 30-week SMA (SMA30_W)
    2. 10-week SMA (SMA10_W) > 30-week SMA (SMA30_W)
    3. SMA30_W is strictly rising compared to 4 weeks ago (index -1 vs index -5)
    """
    if not weekly_bars or len(weekly_bars) < 30:
        return False

    w_closes = [_safe_float(b.get("close")) for b in weekly_bars if _safe_float(b.get("close")) > 0]
    if len(w_closes) < 30:
        return False

    sma30_w = sum(w_closes[-30:]) / 30.0
    sma10_w = sum(w_closes[-10:]) / 10.0
    w_close = w_closes[-1]

    sma30_rising = False
    if len(w_closes) >= 34:
        past_sma30 = sum(w_closes[-34:-4]) / 30.0
        if sma30_w > past_sma30:
            sma30_rising = True
    elif len(w_closes) > 30:
        past_sma30 = sum(w_closes[:30]) / 30.0
        if sma30_w > past_sma30:
            sma30_rising = True
    else:
        # Exactly 30 bars: only 1 SMA30 point exists; slope cannot be determined to be rising
        sma30_rising = False

    return bool(w_close > sma30_w and sma10_w > sma30_w and sma30_rising)


def evaluate_canslim_stage(
    bars: Sequence[Dict[str, Any]],
    fundamentals: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Evaluates Stan Weinstein & Mark Minervini Stage 2 Uptrend Criteria.

    Strict Invariants:
    1. Close > SMA50 > SMA200 (Stage 2 Uptrend Base)
    2. 200MA Rising: SMA200[-1] > SMA200[-21] (Ensures 200MA is trending upward for >= 1 mo)
    3. Off Bottom: Close >= 52W_Low * 1.25 (At least 25% above 52-week low)
    4. Sub-new stock (<200 bars) safe fallback: checks available MAs without IndexError.
    5. In-memory resampled 1W Stage 2 resonance: Close > SMA30_W & SMA10_W > SMA30_W & rising.
    """
    result = {
        "stage2": False,
        "weekly_stage2": False,
        "power_trend": False,
        "trend_regime": "NEUTRAL",
        "close": 0.0,
        "sma50": None,
        "sma200": None,
        "ema10": None,
        "ema21": None,
        "fifty_two_week_high": 0.0,
        "fifty_two_week_low": 0.0,
        "pct_from_52w_high": 0.0,
        "pct_above_52w_low": 0.0,
        "volume_ratio_50": 1.0,
        "revenue_yoy": None
    }
    if not bars or len(bars) < 20:
        return result

    closes = [_safe_float(b.get("close")) for b in bars]
    highs = [_safe_float(b.get("high"), closes[i]) for i, b in enumerate(bars)]
    lows = [_safe_float(b.get("low"), closes[i]) for i, b in enumerate(bars)]
    volumes = [_safe_float(b.get("volume")) for b in bars]

    c = closes[-1]
    result["close"] = round(c, 2)

    # 52-week High & Low (up to 252 bars)
    h_win = highs[-252:] if len(highs) > 252 else highs
    l_win = lows[-252:] if len(lows) > 252 else lows
    h52 = max(h_win) if h_win else c
    l52 = min(l_win) if l_win else c
    result["fifty_two_week_high"] = round(h52, 2)
    result["fifty_two_week_low"] = round(l52, 2)
    result["pct_from_52w_high"] = round(((c - h52) / h52) * 100.0, 2) if h52 > 0 else 0.0
    result["pct_above_52w_low"] = round(((c - l52) / l52) * 100.0, 2) if l52 > 0 else 0.0

    # 50D Volume Ratio (with intraday unconfirmed session protection)
    v_win = volumes[-50:] if len(volumes) >= 50 else volumes
    mean_v = sum(v_win) / float(len(v_win)) if v_win else 0.0
    if len(volumes) >= 2 and mean_v > 0 and volumes[-1] < (0.25 * mean_v):
        eff_vol = max(volumes[-1], volumes[-2])
    else:
        eff_vol = volumes[-1] if volumes else 0.0
    v_ratio = (eff_vol / mean_v) if mean_v > 0 else 1.0
    result["volume_ratio_50"] = round(v_ratio, 2)

    # Moving averages
    def _sma(series: List[float], period: int) -> Optional[float]:
        if len(series) < period or period <= 0:
            return None
        return sum(series[-period:]) / float(period)

    def _ema(series: List[float], period: int) -> Optional[float]:
        if len(series) < period or period <= 0:
            return None
        k = 2.0 / (period + 1.0)
        e = sum(series[:period]) / float(period)
        for val in series[period:]:
            e = val * k + e * (1.0 - k)
        return e

    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    ema10 = _ema(closes, 10)
    ema21 = _ema(closes, 21)

    result["sma50"] = round(sma50, 2) if sma50 is not None else None
    result["sma200"] = round(sma200, 2) if sma200 is not None else None
    result["ema10"] = round(ema10, 2) if ema10 is not None else None
    result["ema21"] = round(ema21, 2) if ema21 is not None else None

    # Power Trend: Close > EMA10 > EMA21 > SMA50
    if ema10 and ema21 and sma50:
        if c > ema10 > ema21 > sma50:
            result["power_trend"] = True

    # 200MA Rising Check (at least 21 days ago)
    sma200_rising = False
    if len(closes) >= 221 and sma200 is not None:
        past_closes = closes[:-21]
        past_sma200 = _sma(past_closes, 200)
        if past_sma200 is not None and sma200 > past_sma200:
            sma200_rising = True
    elif len(closes) > 200 and sma200 is not None:
        # If stock has 201-220 bars, compare with earliest 200-bar window
        past_sma200 = _sma(closes[:200], 200)
        if past_sma200 and sma200 > past_sma200:
            sma200_rising = True

    # Full Stage 2 Qualification
    off_bottom = (c >= l52 * 1.25)
    if sma50 is not None and sma200 is not None:
        if c > sma50 > sma200 and sma200_rising and off_bottom:
            result["stage2"] = True
            result["trend_regime"] = "STAGE_2_STRONG_UPTREND"
        elif c > sma50 and sma50 > sma200:
            result["trend_regime"] = "STAGE_2_UPTREND_PULLBACK"
        elif c < sma200 and sma50 < sma200:
            result["trend_regime"] = "STAGE_4_DOWNTREND"
        else:
            result["trend_regime"] = "NEUTRAL"
    elif sma50 is not None:
        if c > sma50 and off_bottom:
            result["stage2"] = True
            result["trend_regime"] = "STAGE_2_UPTREND_PULLBACK"
        else:
            result["trend_regime"] = "NEUTRAL"

    # Stan Weinstein 1W Stage 2 Evaluation via in-memory 1D -> 1W resampling
    weekly_bars = _resample_1d_to_1w(bars)
    result["weekly_stage2"] = calculate_weekly_stage2(weekly_bars)

    # Fundamentals parsing
    if fundamentals and isinstance(fundamentals, dict):
        result["revenue_yoy"] = (
            fundamentals.get("revenue_yoy")
            or fundamentals.get("revenueGrowth")
            or fundamentals.get("revenue_growth")
        )

    return result


async def scan_symbol_candidate(
    sym_info: Dict[str, Any],
    criteria: ScreeningCriteria,
    storage_obj: Any,
    benchmark_bars: List[Dict[str, Any]],
    fetch_func: Any,
    semaphore: asyncio.Semaphore
) -> Optional[Dict[str, Any]]:
    """Evaluates an individual symbol against multi-factor screening criteria."""
    sym_id = sym_info.get("id") or sym_info.get("symbol") or ""
    clean_sym = sym_id.strip().upper()
    if ":" not in clean_sym:
        clean_sym = f"NASDAQ:{clean_sym}"

    ticker = clean_sym.split(":")[-1]

    # 1. Fetch 1D bars from storage or via semaphore-protected remote fetch
    bars = []
    if storage_obj:
        try:
            bars = storage_obj.get_bars(clean_sym, "1D", limit=300)
        except Exception:
            bars = []

    if len(bars) < 252 and fetch_func:
        async with semaphore:
            try:
                remote_bars = await fetch_func(clean_sym, timeframe="1D")
                if remote_bars and len(remote_bars) > len(bars):
                    bars = remote_bars
            except Exception as fetch_err:
                logger.debug("Screener remote fetch notice for %s: %s", clean_sym, fetch_err)

    if not bars or len(bars) < 20:
        return None

    # 2. Evaluate CANSLIM Stage 2 & MAs
    fund_rep = storage_obj.get_fundamental_report(clean_sym) if storage_obj else None
    fundamentals = fund_rep.get("metrics") if fund_rep else None
    stage_info = evaluate_canslim_stage(bars, fundamentals=fundamentals)

    if criteria.stage2_only and not stage_info["stage2"]:
        return None

    if criteria.weekly_stage2_only and not stage_info.get("weekly_stage2", False):
        return None

    if criteria.power_trend_only and not stage_info["power_trend"]:
        return None

    if criteria.max_pct_from_52w_high is not None:
        if abs(stage_info["pct_from_52w_high"]) > criteria.max_pct_from_52w_high:
            return None

    if criteria.min_volume_ratio is not None:
        if stage_info["volume_ratio_50"] < criteria.min_volume_ratio:
            return None

    # Revenue growth check if enforced
    if criteria.min_rev_growth_yoy is not None:
        rev_raw = stage_info.get("revenue_yoy")
        if rev_raw is None or rev_raw == "":
            return None
        rev_str = str(rev_raw).replace("%", "").strip()
        try:
            rev_num = float(rev_str)
            if rev_num < criteria.min_rev_growth_yoy:
                return None
        except ValueError:
            logger.debug("Revenue YoY metric unparseable for %s: %s", clean_sym, rev_str)
            return None

    # 3. Calculate Mansfield RS (252-day/52-week date-aligned)
    m_rs = calculate_mansfield_rs(bars, benchmark_bars, length=criteria.mansfield_length)
    if criteria.mansfield_above_zero:
        if m_rs is None or m_rs < 0.0:
            return None

    # 4. Calculate CANSLIM RS Rating (1-99 anchored)
    rs_rating = calculate_canslim_rs_rating(bars, benchmark_bars)
    if criteria.min_rs_rating is not None and rs_rating < criteria.min_rs_rating:
        return None

    # 5. Calculate ATR Toolkit (Squeeze, Breakout, Position Sizing)
    atr_info = calculate_atr_toolkit(
        bars,
        atr_len=14,
        pct_len=126,
        squeeze_thresh=criteria.max_atr_percentile,
        risk_dollar=criteria.risk_dollar,
        stop_mult=criteria.stop_atr_mult
    )

    if criteria.atr_squeeze_only and not atr_info["is_squeeze"]:
        return None

    if criteria.expansion_breakout_only and not atr_info["is_breakout"]:
        return None

    # 24H Price change calculation
    last_c = stage_info["close"]
    prev_c = _safe_float(bars[-2].get("close")) if len(bars) >= 2 else last_c
    chg_pct = round(((last_c - prev_c) / prev_c) * 100.0, 2) if prev_c > 0 else 0.0

    has_prefix = sym_info.get("has_prefix", (":" in (sym_info.get("id") or "")))
    export_symbol = clean_sym if has_prefix else ticker

    return {
        "id": clean_sym,
        "symbol": ticker,
        "name": sym_info.get("name") or ticker,
        "sector": sym_info.get("sector") or "Equity",
        "close": last_c,
        "change_pct": chg_pct,
        "mansfield_rs": m_rs if m_rs is not None else 0.0,
        "rs_rating": rs_rating,
        "trend_regime": stage_info["trend_regime"],
        "stage2": stage_info["stage2"],
        "weekly_stage2": stage_info.get("weekly_stage2", False),
        "power_trend": stage_info["power_trend"],
        "pct_from_52w_high": stage_info["pct_from_52w_high"],
        "volume_ratio_50": stage_info["volume_ratio_50"],
        "atr": atr_info["atr"],
        "atr_pct": atr_info["atr_pct"],
        "atr_percentile": atr_info["atr_percentile"],
        "is_squeeze": atr_info["is_squeeze"],
        "is_breakout": atr_info["is_breakout"],
        "suggested_shares": atr_info["suggested_shares"],
        "stop_distance": atr_info["stop_distance"],
        "revenue_yoy": stage_info.get("revenue_yoy") or "N/A",
        "has_prefix": has_prefix,
        "export_symbol": export_symbol
    }


async def scan_universe(
    universe: List[Dict[str, Any]],
    criteria: ScreeningCriteria,
    storage_obj: Any,
    benchmark_bars: List[Dict[str, Any]],
    fetch_func: Any,
    max_concurrency: int = 8
) -> List[Dict[str, Any]]:
    """Scans a list of universe items in parallel with rate-limited semaphore."""
    sem = asyncio.Semaphore(max_concurrency)
    tasks = [
        scan_symbol_candidate(
            sym_info=item,
            criteria=criteria,
            storage_obj=storage_obj,
            benchmark_bars=benchmark_bars,
            fetch_func=fetch_func,
            semaphore=sem
        )
        for item in universe
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    matched: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, dict):
            matched.append(r)
        elif isinstance(r, Exception):
            logger.debug("Screener task exception: %s", r)

    # Default sort by RS Rating descending, then Mansfield RS descending
    matched.sort(key=lambda x: (x.get("rs_rating") or 0, x.get("mansfield_rs") or -999.0), reverse=True)
    return matched

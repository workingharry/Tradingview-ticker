"""Pine Script v5 Indicator & Input Parsing Engine.

Provides exact mathematical calculations for Anchored VWAP (AVWAP) across
6 anchor modes, standard deviation bands, and Pine Script input.* declaration parsing.
"""

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union


def get_source_price(bar: Dict[str, Any], source: str = "hlc3") -> float:
    """Computes source price from bar dictionary based on source identifier."""
    close_p = float(bar.get("close", 0.0) or 0.0)
    high_p = float(bar.get("high", close_p) or close_p)
    low_p = float(bar.get("low", close_p) or close_p)
    open_p = float(bar.get("open", close_p) or close_p)

    src = (source or "hlc3").lower().strip()
    if src == "open":
        return open_p
    elif src == "high":
        return high_p
    elif src == "low":
        return low_p
    elif src == "close":
        return close_p
    elif src == "hl2":
        return (high_p + low_p) / 2.0
    elif src == "ohlc4":
        return (open_p + high_p + low_p + close_p) / 4.0
    else:  # "hlc3" default
        return (high_p + low_p + close_p) / 3.0


def parse_timestamp_to_seconds(ts_val: Union[str, int, float]) -> float:
    """Parses date string or unix timestamp to epoch seconds."""
    if isinstance(ts_val, (int, float)):
        # If timestamp is in milliseconds (> 1e11), convert to seconds
        return float(ts_val / 1000.0 if ts_val > 1e11 else ts_val)

    val_str = str(ts_val).strip()
    # Try various date formats: 'YYYY-MM-DD HH:MM', 'DD Mon YYYY HH:MM', 'YYYY-MM-DD'
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(val_str, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    # Fallback to float timestamp if string is numeric
    try:
        n = float(val_str)
        return n / 1000.0 if n > 1e11 else n
    except ValueError:
        return 0.0


def calc_volume_sma(bars: List[Dict[str, Any]], period: int = 50) -> List[float]:
    """Calculates Simple Moving Average of Volume."""
    n = len(bars)
    if n == 0 or period <= 0:
        return []
    vol_sma: List[float] = [0.0] * n
    rolling_sum = 0.0

    for i in range(n):
        v = float(bars[i].get("volume", 0.0) or 0.0)
        rolling_sum += v
        if i >= period:
            rolling_sum -= float(bars[i - period].get("volume", 0.0) or 0.0)
        effective_len = min(i + 1, period)
        vol_sma[i] = rolling_sum / effective_len

    return vol_sma


def calc_anchored_vwap(
    bars: List[Dict[str, Any]],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calculates Pine Script v5 Anchored VWAP (AVWAP) and Standard Deviation Bands.

    Supports 6 anchor modes:
      1. 'Swing Low': Minimum low over lookback window.
      2. 'Swing High': Maximum high over lookback window.
      3. 'Highest Volume': Maximum volume over lookback window.
      4. 'Gap-Up': Most recent gap-up bar over lookback window.
      5. 'Manual Date': First bar where time >= manualAnchor.
      6. 'Session' or 'Session (intraday)': Daily reset VWAP per calendar day.

    Returns dict with:
      - avwap: list of points {time, value, upper, lower, stdev, isAbove, index}
      - upper: list of points {time, value}
      - lower: list of points {time, value}
      - anchorBar: {index, barsAgo, time, price, mode, label}
      - latest: latest bar metrics
    """
    if not bars:
        return {
            "avwap": [],
            "upper": [],
            "lower": [],
            "anchorBar": None,
            "latest": None,
        }

    opts = options or {}
    mode = str(opts.get("anchorMode") or "Swing Low").strip()
    total_bars = len(bars)
    lookback = min(int(opts.get("anchorLookback") or 252), total_bars)
    if lookback < 1:
        lookback = total_bars

    gap_pct_min = float(opts.get("gapPctMin") if opts.get("gapPctMin") is not None else 5.0)
    gap_vol_min = float(opts.get("gapVolMin") if opts.get("gapVolMin") is not None else 2.0)
    vol_ma_len = int(opts.get("volMaLen") or 50)
    band_mult = float(opts.get("bandMult") if opts.get("bandMult") is not None else 1.0)
    manual_anchor = opts.get("manualAnchor")
    source_name = str(opts.get("source") or "hlc3").strip()

    start_idx = max(0, total_bars - lookback)
    anchor_idx = start_idx

    # Handle 6 Anchor Modes
    if mode == "Swing High":
        max_high = -float("inf")
        for i in range(start_idx, total_bars):
            h = float(bars[i].get("high", bars[i].get("close", 0.0)) or 0.0)
            if h > max_high:
                max_high = h
                anchor_idx = i
    elif mode == "Highest Volume":
        max_vol = -float("inf")
        for i in range(start_idx, total_bars):
            v = float(bars[i].get("volume", 0.0) or 0.0)
            if v > max_vol:
                max_vol = v
                anchor_idx = i
    elif mode == "Gap-Up":
        vol_sma = calc_volume_sma(bars, vol_ma_len)
        found_gap = -1
        # Search backwards from latest bar within lookback
        for gi in range(total_bars - 1, start_idx - 1, -1):
            if gi > 0:
                cur = bars[gi]
                prev = bars[gi - 1]
                cur_open = float(cur.get("open", cur.get("close", 0.0)) or 0.0)
                prior_close = float(prev.get("close", 0.0) or 0.0)
                prior_high = float(prev.get("high", prior_close) or prior_close)
                cur_vol = float(cur.get("volume", 0.0) or 0.0)
                prior_vol_ma = vol_sma[gi - 1] if gi - 1 < len(vol_sma) else 0.0

                gap_pct = ((cur_open - prior_close) / prior_close) * 100.0 if prior_close > 0 else 0.0
                vol_ratio = (cur_vol / prior_vol_ma) if prior_vol_ma > 0 else 0.0

                if cur_open > prior_high and gap_pct >= gap_pct_min and vol_ratio >= gap_vol_min:
                    found_gap = gi
                    break
        anchor_idx = found_gap if found_gap != -1 else start_idx
    elif mode == "Manual Date" and manual_anchor is not None:
        target_ts = parse_timestamp_to_seconds(manual_anchor)
        found_manual = -1
        for mi in range(total_bars):
            bar_ts = parse_timestamp_to_seconds(bars[mi].get("time", 0))
            if bar_ts >= target_ts:
                found_manual = mi
                break
        anchor_idx = found_manual if found_manual != -1 else start_idx
    elif mode in ("Session", "Session (intraday)"):
        # Intraday session mode: VWAP resets daily on each calendar day
        avwap_list: List[Dict[str, Any]] = []
        upper_list: List[Dict[str, Any]] = []
        lower_list: List[Dict[str, Any]] = []

        session_start_idx = 0
        cum_v = 0.0
        cum_pv = 0.0
        cum_pv2 = 0.0

        def get_bar_day(b: Dict[str, Any]) -> str:
            t = parse_timestamp_to_seconds(b.get("time", 0))
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")

        for bi in range(total_bars):
            cur_day = get_bar_day(bars[bi])
            prev_day = get_bar_day(bars[bi - 1]) if bi > 0 else ""
            if bi == 0 or cur_day != prev_day:
                session_start_idx = bi
                cum_v = 0.0
                cum_pv = 0.0
                cum_pv2 = 0.0

            src_val = get_source_price(bars[bi], source_name)
            v = float(bars[bi].get("volume", 0.0) or 0.0)

            cum_v += v
            cum_pv += src_val * v
            cum_pv2 += src_val * src_val * v

            cur_avwap = (cum_pv / cum_v) if cum_v > 0 else src_val
            variance = max(0.0, (cum_pv2 / cum_v) - (cur_avwap * cur_avwap)) if cum_v > 0 else 0.0
            stdev = math.sqrt(variance)
            upper_val = cur_avwap + band_mult * stdev
            lower_val = cur_avwap - band_mult * stdev

            close_p = float(bars[bi].get("close", 0.0) or 0.0)
            is_above = close_p >= cur_avwap
            t = bars[bi].get("time")

            avwap_list.append({
                "time": t,
                "value": round(cur_avwap, 4),
                "upper": round(upper_val, 4),
                "lower": round(lower_val, 4),
                "stdev": round(stdev, 4),
                "isAbove": is_above,
                "index": bi,
            })
            upper_list.append({"time": t, "value": round(upper_val, 4)})
            lower_list.append({"time": t, "value": round(lower_val, 4)})

        latest_pt = avwap_list[-1]
        last_close = float(bars[-1].get("close", 0.0) or 0.0)
        diff_pct = ((last_close / latest_pt["value"]) - 1.0) * 100.0 if latest_pt["value"] > 0 else 0.0
        bars_ago = total_bars - 1 - session_start_idx
        anchor_b = bars[session_start_idx]

        return {
            "avwap": avwap_list,
            "upper": upper_list,
            "lower": lower_list,
            "anchorBar": {
                "index": session_start_idx,
                "barsAgo": bars_ago,
                "time": anchor_b.get("time"),
                "price": float(anchor_b.get("low", anchor_b.get("close", 0.0)) or 0.0),
                "mode": mode,
                "label": f"Session ({bars_ago} bars ago, daily reset)",
            },
            "latest": {
                "avwap": latest_pt["value"],
                "upper": latest_pt["upper"],
                "lower": latest_pt["lower"],
                "stdev": latest_pt["stdev"],
                "diffPct": round(diff_pct, 2),
                "isAbove": latest_pt["isAbove"],
            },
        }
    else:  # "Swing Low" (default)
        min_low = float("inf")
        for i in range(start_idx, total_bars):
            l = float(bars[i].get("low", bars[i].get("close", 0.0)) or 0.0)
            if l < min_low:
                min_low = l
                anchor_idx = i

    # Non-session modes accumulate forward from anchor_idx
    avwap_list = []
    upper_list = []
    lower_list = []
    cum_v = 0.0
    cum_pv = 0.0
    cum_pv2 = 0.0

    for bi in range(anchor_idx, total_bars):
        src_val = get_source_price(bars[bi], source_name)
        v = float(bars[bi].get("volume", 0.0) or 0.0)

        cum_v += v
        cum_pv += src_val * v
        cum_pv2 += src_val * src_val * v

        cur_avwap = (cum_pv / cum_v) if cum_v > 0 else src_val
        variance = max(0.0, (cum_pv2 / cum_v) - (cur_avwap * cur_avwap)) if cum_v > 0 else 0.0
        stdev = math.sqrt(variance)
        upper_val = cur_avwap + band_mult * stdev
        lower_val = cur_avwap - band_mult * stdev

        close_p = float(bars[bi].get("close", 0.0) or 0.0)
        is_above = close_p >= cur_avwap
        t = bars[bi].get("time")

        avwap_list.append({
            "time": t,
            "value": round(cur_avwap, 4),
            "upper": round(upper_val, 4),
            "lower": round(lower_val, 4),
            "stdev": round(stdev, 4),
            "isAbove": is_above,
            "index": bi,
        })
        upper_list.append({"time": t, "value": round(upper_val, 4)})
        lower_list.append({"time": t, "value": round(lower_val, 4)})

    latest_pt = avwap_list[-1]
    last_close = float(bars[-1].get("close", 0.0) or 0.0)
    diff_pct = ((last_close / latest_pt["value"]) - 1.0) * 100.0 if latest_pt["value"] > 0 else 0.0
    bars_ago = total_bars - 1 - anchor_idx
    anchor_b = bars[anchor_idx]

    return {
        "avwap": avwap_list,
        "upper": upper_list,
        "lower": lower_list,
        "anchorBar": {
            "index": anchor_idx,
            "barsAgo": bars_ago,
            "time": anchor_b.get("time"),
            "price": float(anchor_b.get("low", anchor_b.get("close", 0.0)) or 0.0),
            "mode": mode,
            "label": f"{mode} ({bars_ago} bars ago)",
        },
        "latest": {
            "avwap": latest_pt["value"],
            "upper": latest_pt["upper"],
            "lower": latest_pt["lower"],
            "stdev": latest_pt["stdev"],
            "diffPct": round(diff_pct, 2),
            "isAbove": latest_pt["isAbove"],
        },
    }


def parse_pine_inputs(pine_script_code: str) -> Dict[str, Any]:
    """Parses Pine Script v5 input.* variable declarations into structured parameters.

    Supports:
      input.string, input.int, input.float, input.time, input.source, input.bool,
      as well as generic input(...) declarations.
    """
    inputs: Dict[str, Dict[str, Any]] = {}
    if not pine_script_code:
        return inputs

    # Pattern matches opening of: [varName = ] input[.type](
    pattern = re.compile(
        r'(?:([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*)?input(?:\.([a-zA-Z0-9_]+))?\s*\(',
        re.MULTILINE,
    )

    for m in pattern.finditer(pine_script_code):
        var_name = m.group(1) or ""
        input_type = (m.group(2) or "generic").lower()
        start_idx = m.end()
        depth = 1
        i = start_idx
        n = len(pine_script_code)
        in_quote = None
        escape = False

        while i < n and depth > 0:
            char = pine_script_code[i]
            if in_quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == in_quote:
                    in_quote = None
            else:
                if char in ('"', "'"):
                    in_quote = char
                elif char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        break
            i += 1

        raw_args = pine_script_code[start_idx:i].strip()

        # Parse arguments inside input(...)
        item: Dict[str, Any] = {
            "varName": var_name,
            "type": input_type,
            "defval": None,
            "title": "",
            "options": [],
            "minval": None,
            "maxval": None,
            "step": None,
        }

        # Check for options=[...]
        options_match = re.search(r'options\s*=\s*\[([\s\S]*?)\]', raw_args)
        if options_match:
            opts_str = options_match.group(1)
            # Extract quoted strings from options list
            item["options"] = [
                m.strip().strip('"').strip("'")
                for m in re.findall(r'["\']([^"\']+)["\']', opts_str)
            ]

        # Check for minval=...
        min_match = re.search(r'minval\s*=\s*([0-9\.\-]+)', raw_args)
        if min_match:
            item["minval"] = float(min_match.group(1)) if "." in min_match.group(1) else int(min_match.group(1))

        # Check for maxval=...
        max_match = re.search(r'maxval\s*=\s*([0-9\.\-]+)', raw_args)
        if max_match:
            item["maxval"] = float(max_match.group(1)) if "." in max_match.group(1) else int(max_match.group(1))

        # Check for step=...
        step_match = re.search(r'step\s*=\s*([0-9\.\-]+)', raw_args)
        if step_match:
            item["step"] = float(step_match.group(1))

        # Check for title="..."
        title_named = re.search(r'title\s*=\s*["\']([^"\']+)["\']', raw_args)
        if title_named:
            item["title"] = title_named.group(1)

        # Check for defval="..." or defval=...
        defval_named = re.search(r'defval\s*=\s*([^,\)]+)', raw_args)
        if defval_named:
            raw_defval = defval_named.group(1).strip()
            item["defval"] = _coerce_pine_val(raw_defval, input_type)

        # If not named defval/title, extract positional arguments
        if item["defval"] is None or not item["title"]:
            # Tokenize positional arguments before named kwargs
            clean_args = re.sub(r'options\s*=\s*\[[\s\S]*?\]', '', raw_args)
            parts = [p.strip() for p in clean_args.split(',') if '=' not in p and p.strip()]

            if parts and item["defval"] is None:
                item["defval"] = _coerce_pine_val(parts[0], input_type)

            if len(parts) > 1 and not item["title"]:
                second = parts[1].strip().strip('"').strip("'")
                item["title"] = second

        # If title still empty, fallback to var_name
        if not item["title"]:
            item["title"] = var_name

        key = var_name or item["title"]
        inputs[key] = item

    return inputs


def _coerce_pine_val(val_str: str, input_type: str) -> Any:
    """Helper to convert string token from Pine script into Python primitive."""
    val = val_str.strip()
    # Check for timestamp("...")
    ts_match = re.search(r'timestamp\s*\(\s*["\']([^"\']+)["\']\s*\)', val)
    if ts_match:
        return ts_match.group(1)

    # Boolean
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False

    # Quoted string
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]

    # Numeric
    if re.fullmatch(r'[-+]?[0-9]*\.[0-9]+', val):
        return float(val)
    if re.fullmatch(r'[-+]?[0-9]+', val):
        return int(val)

    # Identifier (like hlc3, close)
    return val

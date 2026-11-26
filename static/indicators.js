/**
 * indicators.js - Pure Mathematical Indicator Engine
 * Decoupled, zero DOM dependency, testable in Node.js and browser.
 */

(function (root, factory) {
  if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.Indicators = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function safeNum(val, fallback = 0) {
    if (val === undefined || val === null || val === '') return fallback;
    const n = Number(val);
    return typeof n === 'number' && !isNaN(n) && isFinite(n) ? n : fallback;
  }

  /**
   * Simple Moving Average (SMA)
   * @param {Array<{time: number, close: number}>} bars
   * @param {number} period
   * @returns {Array<{time: number, value: number}>}
   */
  function calcSMA(bars, period) {
    if (!Array.isArray(bars) || bars.length < period || period <= 0) return [];
    const result = [];
    let sum = 0;

    for (let i = 0; i < bars.length; i++) {
      const c = safeNum(bars[i].close);
      sum += c;
      if (i >= period) {
        sum -= safeNum(bars[i - period].close);
      }
      if (i >= period - 1) {
        if (i % 50 === 0) {
          sum = 0;
          for (let j = i - period + 1; j <= i; j++) {
            sum += safeNum(bars[j].close);
          }
        }
        result.push({
          time: bars[i].time,
          value: Number((sum / period).toFixed(4))
        });
      }
    }
    return result;
  }

  /**
   * Exponential Moving Average (EMA)
   * @param {Array<{time: number, close: number}>} bars
   * @param {number} period
   * @returns {Array<{time: number, value: number}>}
   */
  function calcEMA(bars, period) {
    if (!Array.isArray(bars) || bars.length < period || period <= 0) return [];
    const result = [];
    const k = 2 / (period + 1);

    // Initial SMA for first EMA seed
    let sum = 0;
    for (let i = 0; i < period; i++) {
      sum += safeNum(bars[i].close);
    }
    let prevEma = sum / period;
    result.push({
      time: bars[period - 1].time,
      value: Number(prevEma.toFixed(4))
    });

    for (let i = period; i < bars.length; i++) {
      const close = safeNum(bars[i].close);
      prevEma = (close - prevEma) * k + prevEma;
      result.push({
        time: bars[i].time,
        value: Number(prevEma.toFixed(4))
      });
    }
    return result;
  }

  /**
   * Volume Simple Moving Average (Vol SMA)
   * @param {Array<{time: number, volume: number}>} bars
   * @param {number} period
   * @returns {Array<{time: number, value: number}>}
   */
  function calcVolumeSMA(bars, period = 50) {
    if (!Array.isArray(bars) || bars.length < period || period <= 0) return [];
    const result = [];
    let sum = 0;

    for (let i = 0; i < bars.length; i++) {
      const v = safeNum(bars[i].volume);
      sum += v;
      if (i >= period) {
        sum -= safeNum(bars[i - period].volume);
      }
      if (i >= period - 1) {
        if (i % 50 === 0) {
          sum = 0;
          for (let j = i - period + 1; j <= i; j++) {
            sum += safeNum(bars[j].volume);
          }
        }
        result.push({
          time: bars[i].time,
          value: Number((sum / period).toFixed(2))
        });
      }
    }
    return result;
  }

  /**
   * Deepvue Signature Relative Strength (RS) Line
   * Calculates RS = (Price_Target / Price_Benchmark) * 100
   * @param {Array<{time: number, close: number}>} targetBars
   * @param {Array<{time: number, close: number}>} benchmarkBars (e.g. SPY, QQQ, IWM)
   * @returns {Array<{time: number, value: number}>}
   */
  function calcRelativeStrength(targetBars, benchmarkBars) {
    if (!Array.isArray(targetBars) || !Array.isArray(benchmarkBars) || !targetBars.length || !benchmarkBars.length) {
      return [];
    }
    var benchMap = Object.create(null);
    var validBench = [];
    for (var i = 0; i < benchmarkBars.length; i++) {
      var b = benchmarkBars[i];
      if (b && typeof b.time === "number" && typeof b.close === "number" && isFinite(b.close) && b.close > 0) {
        benchMap[b.time] = b.close;
        validBench.push(b);
      }
    }
    if (!validBench.length) return [];

    var out = [];
    var bIdx = 0;
    for (var j = 0; j < targetBars.length; j++) {
      var tb = targetBars[j];
      if (!tb || typeof tb.time !== "number" || typeof tb.close !== "number" || !isFinite(tb.close)) continue;
      while (bIdx + 1 < validBench.length && validBench[bIdx + 1].time <= tb.time) {
        bIdx++;
      }
      var bClose = benchMap[tb.time];
      if (bClose === undefined && validBench[bIdx].time <= tb.time) {
        bClose = validBench[bIdx].close;
      }
      if (typeof bClose === "number" && isFinite(bClose) && bClose > 0) {
        var rsVal = Number(((tb.close / bClose) * 100).toFixed(4));
        if (isFinite(rsVal)) {
          out.push({
            time: tb.time,
            value: rsVal
          });
        }
      }
    }
    return out;
  }

  /**
   * RS Line New High Markers (Blue Dot / RS New High before Price)
   * Signals institutional accumulation when RS makes a new high while price has not yet broken out.
   * @param {Array<{time: number, open: number, high: number, low: number, close: number}>} targetBars
   * @param {Array<{time: number, close: number}>} benchmarkBars
   * @param {Object} [options]
   * @param {number} [options.lookback=252]
   * @param {number} [options.minWarmup=50]
   * @param {number} [options.cooldown=5]
   * @returns {Array<{time: number, position: string, color: string, shape: string, text: string, type: string}>}
   */
  function calcRsNewHighMarkers(targetBars, benchmarkBars, options) {
    if (!Array.isArray(targetBars) || !Array.isArray(benchmarkBars) || !targetBars.length || !benchmarkBars.length) {
      return [];
    }
    var opts = options || {};
    var lookback = typeof opts.lookback === "number" && opts.lookback > 0 ? opts.lookback : 252;
    var minWarmup = typeof opts.minWarmup === "number" && opts.minWarmup > 0 ? opts.minWarmup : 50;
    var cooldown = typeof opts.cooldown === "number" && opts.cooldown > 0 ? opts.cooldown : 5;

    if (targetBars === benchmarkBars) return [];

    var rs = calcRelativeStrength(targetBars, benchmarkBars);
    if (!rs || rs.length < minWarmup) return [];

    var isConstant = true;
    var firstVal = rs[0].value;
    for (var c = 1; c < Math.min(rs.length, 30); c++) {
      if (Math.abs(rs[c].value - firstVal) > 1e-4) {
        isConstant = false;
        break;
      }
    }
    if (isConstant && Math.abs(firstVal - 100) < 1e-3) {
      return [];
    }

    var targetMap = Object.create(null);
    for (var t = 0; t < targetBars.length; t++) {
      var tb = targetBars[t];
      if (tb && typeof tb.time === "number") {
        targetMap[tb.time] = tb;
      }
    }

    var markers = [];
    var lastMarkerIdx = -9999;

    for (var i = minWarmup; i < rs.length; i++) {
      var curRs = rs[i].value;
      var curTime = rs[i].time;
      var curBar = targetMap[curTime];
      if (!curBar || typeof curBar.close !== "number") continue;

      var windowSize = Math.min(lookback, i);
      var maxPriorRs = -Infinity;
      var maxPriorHigh = -Infinity;

      for (var w = 1; w <= windowSize; w++) {
        var priorIdx = i - w;
        if (rs[priorIdx].value > maxPriorRs) {
          maxPriorRs = rs[priorIdx].value;
        }
        var pBar = targetMap[rs[priorIdx].time];
        if (pBar) {
          var pHigh = typeof pBar.high === "number" ? pBar.high : pBar.close;
          if (pHigh > maxPriorHigh) {
            maxPriorHigh = pHigh;
          }
        }
      }

      var isRsNewHigh = curRs > maxPriorRs;
      var isPriceNotAtHigh = curBar.close < maxPriorHigh;

      if (isRsNewHigh && isPriceNotAtHigh) {
        if (i - lastMarkerIdx >= cooldown) {
          markers.push({
            time: curTime,
            position: "aboveBar",
            color: "#2962ff",
            shape: "circle",
            text: "RS NH",
            type: "rs_before_price"
          });
          lastMarkerIdx = i;
        }
      }
    }

    return markers;
  }

  /**
   * Relative Strength Index (RSI)
   * @param {Array<{time: number, close: number}>} bars
   * @param {number} period
   * @returns {Array<{time: number, value: number}>}
   */
  function calcRSI(bars, period = 14) {
    if (!Array.isArray(bars) || bars.length <= period || period <= 0) return [];
    const result = [];
    let gains = 0;
    let losses = 0;

    for (let i = 1; i <= period; i++) {
      const diff = safeNum(bars[i].close) - safeNum(bars[i - 1].close);
      if (diff >= 0) gains += diff;
      else losses -= diff;
    }

    let avgGain = gains / period;
    let avgLoss = losses / period;

    let rs = avgLoss === 0 ? (avgGain === 0 ? 1 : 100) : avgGain / avgLoss;
    let rsi = avgLoss === 0 ? (avgGain === 0 ? 50 : 100) : 100 - (100 / (1 + rs));
    result.push({ time: bars[period].time, value: Number(rsi.toFixed(2)) });

    for (let i = period + 1; i < bars.length; i++) {
      const diff = safeNum(bars[i].close) - safeNum(bars[i - 1].close);
      const gain = diff > 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;

      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;

      rs = avgLoss === 0 ? (avgGain === 0 ? 1 : 100) : avgGain / avgLoss;
      rsi = avgLoss === 0 ? (avgGain === 0 ? 50 : 100) : 100 - (100 / (1 + rs));
      result.push({ time: bars[i].time, value: Number(rsi.toFixed(2)) });
    }
    return result;
  }

  /**
   * Bollinger Bands
   * @param {Array<{time: number, close: number}>} bars
   * @param {number} period
   * @param {number} stdDev
   * @returns {{upper: Array<{time: number, value: number}>, middle: Array<{time: number, value: number}>, lower: Array<{time: number, value: number}>}}
   */
  function calcBollingerBands(bars, period = 20, stdDev = 2) {
    if (!Array.isArray(bars) || bars.length < period) return { upper: [], middle: [], lower: [] };
    const middle = calcSMA(bars, period);
    const upper = [];
    const lower = [];

    const midMap = new Map();
    for (const m of middle) midMap.set(m.time, m.value);

    for (let i = period - 1; i < bars.length; i++) {
      const t = bars[i].time;
      const midVal = midMap.get(t);
      if (midVal === undefined) continue;

      let varianceSum = 0;
      for (let j = i - period + 1; j <= i; j++) {
        const diff = safeNum(bars[j].close) - midVal;
        varianceSum += diff * diff;
      }
      const sd = Math.sqrt(Math.max(0, varianceSum / period));
      const upVal = Number((midVal + stdDev * sd).toFixed(4));
      const lowVal = Number((midVal - stdDev * sd).toFixed(4));
      if (isFinite(upVal) && isFinite(lowVal)) {
        upper.push({ time: t, value: upVal });
        lower.push({ time: t, value: lowVal });
      }
    }

    return { upper, middle, lower };
  }

  /**
   * Deepvue / CANSLIM-style Relative Strength Rating (1-99)
   * Scores target performance relative to benchmark into a 1 to 99 percentile.
   * @param {Array<{time: number, close: number}>} targetBars
   * @param {Array<{time: number, close: number}>} benchmarkBars
   * @returns {number|null}
   */
  function calcRsRating(targetBars, benchmarkBars) {
    if (!Array.isArray(targetBars) || !Array.isArray(benchmarkBars) || targetBars.length < 5 || benchmarkBars.length < 5) {
      return null;
    }
    const startTime = Math.max(targetBars[0].time, benchmarkBars[0].time);
    let tStartIdx = 0;
    while (tStartIdx < targetBars.length && targetBars[tStartIdx].time < startTime) {
      tStartIdx++;
    }
    let bStartIdx = 0;
    while (bStartIdx < benchmarkBars.length && benchmarkBars[bStartIdx].time < startTime) {
      bStartIdx++;
    }
    if (tStartIdx >= targetBars.length || bStartIdx >= benchmarkBars.length) return null;

    const tLast = safeNum(targetBars[targetBars.length - 1].close);
    const tFirst = safeNum(targetBars[tStartIdx].close);
    const bLast = safeNum(benchmarkBars[benchmarkBars.length - 1].close);
    const bFirst = safeNum(benchmarkBars[bStartIdx].close);

    if (tFirst <= 0 || bFirst <= 0 || tLast <= 0 || bLast <= 0) return null;

    const tPerf = (tLast - tFirst) / tFirst;
    const bPerf = (bLast - bFirst) / bFirst;
    const diff = (tPerf - bPerf) * 100;

    const score = Math.round(100 / (1 + Math.exp(-diff / 30)));
    return Math.max(1, Math.min(99, score));
  }

  /**
   * Volume Weighted Average Price (VWAP)
   * Cumulative(TypicalPrice * Volume) / Cumulative(Volume)
   * where TypicalPrice = (High + Low + Close) / 3
   * @param {Array<{time: number, high: number, low: number, close: number, volume: number}>} bars
   * @returns {Array<{time: number, value: number}>}
   */
  var _vwapDayFmtCache = {};
  function _vwapSessionDayKey(epochSec, tz) {
    var t = Number(epochSec);
    if (!isFinite(t) || t <= 0) return "";
    if (t > 1e11) t = Math.floor(t / 1000);
    var zone = tz || "America/New_York";
    try {
      var fmt = _vwapDayFmtCache[zone];
      if (!fmt) {
        fmt = new Intl.DateTimeFormat("en-CA", {
          timeZone: zone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit"
        });
        _vwapDayFmtCache[zone] = fmt;
      }
      return fmt.format(new Date(t * 1000));
    } catch (e) {
      return new Date(t * 1000).toISOString().slice(0, 10);
    }
  }

  function calcVWAP(bars, options) {
    if (!Array.isArray(bars) || !bars.length) return [];
    var opts = (options && typeof options === "object") ? options : {};
    var tf = opts.timeframe ? String(opts.timeframe) : "";
    var isIntradayTf = tf === "1m" || tf === "5m" || tf === "15m" || tf === "1h" || tf === "60m";
    var shouldReset = opts.resetDaily !== undefined ? Boolean(opts.resetDaily) : isIntradayTf;
    var symUpper = opts.symbol ? String(opts.symbol).toUpperCase() : "";
    var isCrypto = symUpper.indexOf("BINANCE:") === 0 || symUpper.indexOf("USDT") !== -1 || symUpper.indexOf("BUSD") !== -1;
    var tz = opts.timeZone || (isCrypto ? "UTC" : "America/New_York");

    var result = [];
    var cumVol = 0;
    var cumVolPrice = 0;
    var lastDayKey = null;

    for (var i = 0; i < bars.length; i++) {
      var b = bars[i];
      if (shouldReset) {
        var dayKey = _vwapSessionDayKey(b.time, tz);
        if (dayKey && dayKey !== lastDayKey) {
          cumVol = 0;
          cumVolPrice = 0;
          lastDayKey = dayKey;
        }
      }
      var high = safeNum(b.high, safeNum(b.close));
      var low = safeNum(b.low, safeNum(b.close));
      var close = safeNum(b.close);
      var vol = safeNum(b.volume);

      var typicalPrice = (high + low + close) / 3;
      cumVol += vol;
      cumVolPrice += typicalPrice * vol;

      var vwapVal = cumVol > 0 ? cumVolPrice / cumVol : typicalPrice;
      result.push({
        time: b.time,
        value: Number(vwapVal.toFixed(4))
      });
    }
    return result;
  }

  /**
   * Average True Range (ATR)
   * Measures volatility using high, low, close
   * @param {Array<{time: number, high: number, low: number, close: number}>} bars
   * @param {number} period (default 14)
   * @returns {Array<{time: number, value: number}>}
   */
  function calcATR(bars, period = 14) {
    if (!Array.isArray(bars) || bars.length < period || period <= 0) return [];
    var trs = [];

    for (var i = 0; i < bars.length; i++) {
      var cur = bars[i];
      var high = safeNum(cur.high, safeNum(cur.close));
      var low = safeNum(cur.low, safeNum(cur.close));
      var tr = high - low;

      if (i > 0) {
        var prevClose = safeNum(bars[i - 1].close);
        var hl = high - low;
        var hc = Math.abs(high - prevClose);
        var lc = Math.abs(low - prevClose);
        tr = Math.max(hl, hc, lc);
      }
      trs.push({ time: cur.time, tr: tr });
    }

    var result = [];
    var trSum = 0;
    for (var j = 0; j < period; j++) {
      trSum += trs[j].tr;
    }
    var prevAtr = trSum / period;
    result.push({
      time: trs[period - 1].time,
      value: Number(prevAtr.toFixed(4))
    });

    for (var k = period; k < trs.length; k++) {
      prevAtr = (prevAtr * (period - 1) + trs[k].tr) / period;
      result.push({
        time: trs[k].time,
        value: Number(prevAtr.toFixed(4))
      });
    }
    return result;
  }

  function getBarSource(b, sourceName) {
    var high = safeNum(b.high, safeNum(b.close));
    var low = safeNum(b.low, safeNum(b.close));
    var close = safeNum(b.close);
    var open = safeNum(b.open, close);
    var s = (sourceName || "hlc3").toLowerCase().trim();
    switch (s) {
      case "open": return open;
      case "high": return high;
      case "low": return low;
      case "close": return close;
      case "hl2": return (high + low) / 2;
      case "ohlc4": return (open + high + low + close) / 4;
      case "hlc3":
      default:
        return (high + low + close) / 3;
    }
  }

  function parsePineDateOrTimestamp(val) {
    if (typeof val === "number") {
      return val > 1e11 ? val : val * 1000;
    }
    if (typeof val === "string") {
      var clean = val.trim();
      if (/^\d{4}-\d{2}-\d{2}(\s|T)\d{2}:\d{2}(:\d{2})?$/.test(clean)) {
        var iso = clean.replace(" ", "T");
        if (iso.length === 16) iso += ":00";
        var utcMs = Date.parse(iso + "Z");
        if (!isNaN(utcMs)) return utcMs;
      }
      var t = new Date(clean.replace(" ", "T")).getTime();
      if (!isNaN(t)) return t;
      var t2 = new Date(clean).getTime();
      if (!isNaN(t2)) return t2;
      var n = Number(clean);
      if (!isNaN(n)) return n > 1e11 ? n : n * 1000;
    }
    return 0;
  }

  function calcAnchoredVWAP(bars, options) {
    if (!bars || !bars.length) {
      return { avwap: [], upper: [], lower: [], anchorBar: null, latest: null };
    }
    options = options || {};
    var mode = options.anchorMode || "Swing Low";
    var lookback = Math.min(options.anchorLookback || 252, bars.length);
    if (lookback < 1) lookback = bars.length;
    var gapPctMin = typeof options.gapPctMin === "number" ? options.gapPctMin : 5.0;
    var gapVolMin = typeof options.gapVolMin === "number" ? options.gapVolMin : 2.0;
    var volMaLen = options.volMaLen || 50;
    var bandMult = typeof options.bandMult === "number" ? options.bandMult : 1.0;
    var manualAnchor = options.manualAnchor;
    var sourceName = options.source || "hlc3";

    var startIdx = Math.max(0, bars.length - lookback);
    var anchorIdx = startIdx;

    if (mode === "Swing High") {
      var maxHigh = -Infinity;
      for (var i = startIdx; i < bars.length; i++) {
        var h = safeNum(bars[i].high, safeNum(bars[i].close));
        if (h > maxHigh) {
          maxHigh = h;
          anchorIdx = i;
        }
      }
    } else if (mode === "Highest Volume") {
      var maxVol = -Infinity;
      for (var vIdx = startIdx; vIdx < bars.length; vIdx++) {
        var volVal = safeNum(bars[vIdx].volume, 0);
        if (volVal > maxVol) {
          maxVol = volVal;
          anchorIdx = vIdx;
        }
      }
    } else if (mode === "Gap-Up") {
      var volSMA = calcVolumeSMA(bars, volMaLen);
      var volMap = {};
      for (var vi = 0; vi < volSMA.length; vi++) {
        volMap[volSMA[vi].time] = volSMA[vi].value;
      }
      var foundGap = -1;
      for (var gi = bars.length - 1; gi >= startIdx; gi--) {
        if (gi > 0) {
          var curBar = bars[gi];
          var prevBar = bars[gi - 1];
          var priorClose = safeNum(prevBar.close);
          var curOpen = safeNum(curBar.open, curBar.close);
          var priorHigh = safeNum(prevBar.high, priorClose);
          var vCur = safeNum(curBar.volume, 0);
          var priorVolMA = volMap[prevBar.time] || 0;
          var gapPct = priorClose > 0 ? ((curOpen - priorClose) / priorClose) * 100 : 0;
          var volRatio = priorVolMA > 0 ? vCur / priorVolMA : 0;
          if (curOpen > priorHigh && gapPct >= gapPctMin && volRatio >= gapVolMin) {
            foundGap = gi;
            break;
          }
        }
      }
      anchorIdx = foundGap !== -1 ? foundGap : startIdx;
    } else if (mode === "Manual Date" && manualAnchor !== undefined && manualAnchor !== null) {
      var targetTs = parsePineDateOrTimestamp(manualAnchor);
      var foundMan = -1;
      for (var mi = 0; mi < bars.length; mi++) {
        var barTs = parsePineDateOrTimestamp(bars[mi].time);
        if (barTs >= targetTs) {
          foundMan = mi;
          break;
        }
      }
      anchorIdx = foundMan !== -1 ? foundMan : startIdx;
    } else if (mode === "Session" || mode === "Session (intraday)") {
      // Session mode: resets at every new calendar day
      var avwapSessionList = [];
      var upperSessionList = [];
      var lowerSessionList = [];
      var sStartIdx = 0;
      var sCumV = 0;
      var sCumPV = 0;
      var sCumPV2 = 0;

      function getBarDayKey(b) {
        var t = b.time;
        var d = typeof t === "number" ? new Date(t > 1e11 ? t : t * 1000) : new Date(t);
        return d.getUTCFullYear() + "-" + (d.getUTCMonth() + 1) + "-" + d.getUTCDate();
      }

      for (var sBarIdx = 0; sBarIdx < bars.length; sBarIdx++) {
        var curDay = getBarDayKey(bars[sBarIdx]);
        var prevDay = sBarIdx > 0 ? getBarDayKey(bars[sBarIdx - 1]) : "";
        if (sBarIdx === 0 || curDay !== prevDay) {
          sStartIdx = sBarIdx;
          sCumV = 0;
          sCumPV = 0;
          sCumPV2 = 0;
        }

        var sSrc = getBarSource(bars[sBarIdx], sourceName);
        var sVol = safeNum(bars[sBarIdx].volume, 0);

        sCumV += sVol;
        sCumPV += sSrc * sVol;
        sCumPV2 += sSrc * sSrc * sVol;

        var sAvwap = sCumV > 0 ? sCumPV / sCumV : sSrc;
        var sVariance = sCumV > 0 ? Math.max(0, (sCumPV2 / sCumV) - (sAvwap * sAvwap)) : 0;
        var sStdev = Math.sqrt(sVariance);

        var sAvVal = Number(sAvwap.toFixed(4));
        var sUpVal = Number((sAvwap + bandMult * sStdev).toFixed(4));
        var sLoVal = Number((sAvwap - bandMult * sStdev).toFixed(4));

        var sClose = safeNum(bars[sBarIdx].close);
        var sIsAbove = sClose >= sAvwap;
        var sTime = bars[sBarIdx].time;

        avwapSessionList.push({
          time: sTime,
          value: sAvVal,
          upper: sUpVal,
          lower: sLoVal,
          stdev: Number(sStdev.toFixed(4)),
          isAbove: sIsAbove,
          index: sBarIdx
        });
        upperSessionList.push({ time: sTime, value: sUpVal });
        lowerSessionList.push({ time: sTime, value: sLoVal });
      }

      var sLatest = avwapSessionList[avwapSessionList.length - 1];
      var sLastClose = safeNum(bars[bars.length - 1].close);
      var sDiffPct = sLatest.value > 0 ? ((sLastClose / sLatest.value) - 1) * 100 : 0;
      var sBarsAgo = bars.length - 1 - sStartIdx;
      var sAnchorBar = bars[sStartIdx];

      return {
        avwap: avwapSessionList,
        upper: upperSessionList,
        lower: lowerSessionList,
        anchorBar: {
          index: sStartIdx,
          barsAgo: sBarsAgo,
          time: sAnchorBar.time,
          price: safeNum(sAnchorBar.low, safeNum(sAnchorBar.close)),
          mode: mode,
          label: "Session (" + sBarsAgo + " bars ago, daily reset)"
        },
        latest: {
          avwap: sLatest.value,
          upper: sLatest.upper,
          lower: sLatest.lower,
          stdev: sLatest.stdev,
          diffPct: Number(sDiffPct.toFixed(2)),
          isAbove: sLatest.isAbove
        }
      };
    } else {
      var minLow = Infinity;
      for (var sli = startIdx; sli < bars.length; sli++) {
        var l = safeNum(bars[sli].low, safeNum(bars[sli].close));
        if (l < minLow) {
          minLow = l;
          anchorIdx = sli;
        }
      }
    }

    var avwapList = [];
    var upperList = [];
    var lowerList = [];
    var cumV = 0;
    var cumPV = 0;
    var cumPV2 = 0;

    for (var bi = anchorIdx; bi < bars.length; bi++) {
      var b = bars[bi];
      var src = getBarSource(b, sourceName);
      var vol = safeNum(b.volume, 0);

      cumV += vol;
      cumPV += src * vol;
      cumPV2 += src * src * vol;

      var currentAVWAP = cumV > 0 ? cumPV / cumV : src;
      var variance = cumV > 0 ? Math.max(0, (cumPV2 / cumV) - (currentAVWAP * currentAVWAP)) : 0;
      var stdev = Math.sqrt(variance);

      var avVal = Number(currentAVWAP.toFixed(4));
      var upVal = Number((currentAVWAP + bandMult * stdev).toFixed(4));
      var loVal = Number((currentAVWAP - bandMult * stdev).toFixed(4));
      var closePrice = safeNum(b.close);
      var isAbove = closePrice >= currentAVWAP;

      avwapList.push({
        time: b.time,
        value: avVal,
        upper: upVal,
        lower: loVal,
        stdev: Number(stdev.toFixed(4)),
        isAbove: isAbove,
        index: bi
      });
      upperList.push({ time: b.time, value: upVal });
      lowerList.push({ time: b.time, value: loVal });
    }

    var aBar = bars[anchorIdx];
    var latestPt = avwapList.length > 0 ? avwapList[avwapList.length - 1] : null;
    var lastClose = safeNum(bars[bars.length - 1].close);
    var diffPct = latestPt && latestPt.value > 0 ? ((lastClose / latestPt.value) - 1) * 100 : 0;
    var barsAgo = bars.length - 1 - anchorIdx;

    return {
      avwap: avwapList,
      upper: upperList,
      lower: lowerList,
      anchorBar: {
        index: anchorIdx,
        barsAgo: barsAgo,
        time: aBar.time,
        price: mode === "Swing High" ? safeNum(aBar.high, safeNum(aBar.close)) : safeNum(aBar.low, safeNum(aBar.close)),
        mode: mode,
        label: mode + " (" + barsAgo + " bars ago)"
      },
      latest: latestPt ? {
        avwap: latestPt.value,
        upper: latestPt.upper,
        lower: latestPt.lower,
        stdev: latestPt.stdev,
        diffPct: Number(diffPct.toFixed(2)),
        isAbove: latestPt.isAbove
      } : null
    };
  }

  /**
   * Parses Pine Script v5 input.* variable declarations into structured parameters.
   * @param {string} pineScriptCode
   * @returns {Object} map of varName/title to parameter metadata
   */
  function parsePineInputs(pineScriptCode) {
    var result = {};
    if (!pineScriptCode || typeof pineScriptCode !== "string") return result;

    var regex = /(?:([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*)?input(?:\.([a-zA-Z0-9_]+))?\s*\(/g;
    var match;

    function coerceVal(valStr, type) {
      if (!valStr) return null;
      var v = valStr.trim();
      var tsMatch = /timestamp\s*\(\s*["']([^"']+)["']\s*\)/.exec(v);
      if (tsMatch) return tsMatch[1];
      if (v.toLowerCase() === "true") return true;
      if (v.toLowerCase() === "false") return false;
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
        return v.slice(1, -1);
      }
      var num = Number(v);
      if (!isNaN(num) && isFinite(num)) return num;
      return v;
    }

    while ((match = regex.exec(pineScriptCode)) !== null) {
      var varName = match[1] || "";
      var inputType = (match[2] || "generic").toLowerCase();
      var startIdx = regex.lastIndex;
      var depth = 1;
      var i = startIdx;
      var n = pineScriptCode.length;
      var inQuote = null;
      var escape = false;

      while (i < n && depth > 0) {
        var ch = pineScriptCode[i];
        if (inQuote) {
          if (escape) {
            escape = false;
          } else if (ch === "\\") {
            escape = true;
          } else if (ch === inQuote) {
            inQuote = null;
          }
        } else {
          if (ch === '"' || ch === "'") {
            inQuote = ch;
          } else if (ch === '(') {
            depth++;
          } else if (ch === ')') {
            depth--;
            if (depth === 0) break;
          }
        }
        i++;
      }

      var rawArgs = pineScriptCode.slice(startIdx, i).trim();
      regex.lastIndex = i + 1;

      var item = {
        varName: varName,
        type: inputType,
        defval: null,
        title: "",
        options: [],
        minval: null,
        maxval: null,
        step: null
      };

      // Extract options=[...]
      var optMatch = /options\s*=\s*\[([\s\S]*?)\]/.exec(rawArgs);
      if (optMatch) {
        var optStr = optMatch[1];
        var optItems = optStr.match(/["']([^"']+)["']/g);
        if (optItems) {
          item.options = optItems.map(function(s) { return s.slice(1, -1); });
        }
      }

      // Extract minval
      var minM = /minval\s*=\s*([0-9\.\-]+)/.exec(rawArgs);
      if (minM) item.minval = Number(minM[1]);

      // Extract maxval
      var maxM = /maxval\s*=\s*([0-9\.\-]+)/.exec(rawArgs);
      if (maxM) item.maxval = Number(maxM[1]);

      // Extract step
      var stepM = /step\s*=\s*([0-9\.\-]+)/.exec(rawArgs);
      if (stepM) item.step = Number(stepM[1]);

      // Extract title="..."
      var titleM = /title\s*=\s*["']([^"']+)["']/.exec(rawArgs);
      if (titleM) item.title = titleM[1];

      // Extract defval=...
      var defvalM = /defval\s*=\s*([^,\)]+)/.exec(rawArgs);
      if (defvalM) {
        item.defval = coerceVal(defvalM[1], inputType);
      }

      // Fallback to positional arguments
      if (item.defval === null || !item.title) {
        var cleanArgs = rawArgs.replace(/options\s*=\s*\[[\s\S]*?\]/g, "");
        var tokens = cleanArgs.split(",").map(function(t) { return t.trim(); }).filter(function(t) {
          return t.length > 0 && t.indexOf("=") === -1;
        });

        if (tokens.length > 0 && item.defval === null) {
          item.defval = coerceVal(tokens[0], inputType);
        }
        if (tokens.length > 1 && !item.title) {
          var t2 = tokens[1].trim();
          if ((t2.startsWith('"') && t2.endsWith('"')) || (t2.startsWith("'") && t2.endsWith("'"))) {
            item.title = t2.slice(1, -1);
          } else {
            item.title = t2;
          }
        }
      }

      if (!item.title) item.title = varName;
      var key = varName || item.title;
      result[key] = item;
    }

    return result;
  }

  function calcATRToolkit(bars, benchmarkBars, options) {
    if (!bars || !bars.length) {
      return {
        atr: [],
        atrPct: [],
        squeeze: [],
        breakouts: [],
        mansfieldRS: [],
        positionSizing: { latestAtr: 0, stopDistance: 0, suggestedShares: 0, riskDollar: 500, stopMult: 2.0 }
      };
    }
    options = options || {};
    var atrLen = options.atrLen || 14;
    var pctLen = options.pctLen || 126;
    var squeezeThresh = typeof options.squeezeThresh === "number" ? options.squeezeThresh : 20.0;
    var expansionThresh = typeof options.expansionThresh === "number" ? options.expansionThresh : 1.5;
    var volMaLen = options.volMaLen || 50;
    var volRatioThresh = typeof options.volRatioThresh === "number" ? options.volRatioThresh : 1.5;
    var riskDollar = typeof options.riskDollar === "number" ? options.riskDollar : 500.0;
    var stopMult = typeof options.stopMult === "number" ? options.stopMult : 2.0;
    var mansfieldLen = options.mansfieldLen || 52;

    var atrSeries = calcATR(bars, atrLen);
    var atrMap = {};
    for (var ai = 0; ai < atrSeries.length; ai++) {
      atrMap[atrSeries[ai].time] = atrSeries[ai].value;
    }

    var volSeries = calcVolumeSMA(bars, volMaLen);
    var volMap = {};
    for (var vi = 0; vi < volSeries.length; vi++) {
      volMap[volSeries[vi].time] = volSeries[vi].value;
    }

    var rawAtrPctList = [];
    var atrPctSeries = [];
    var squeezeSeries = [];
    var breakoutSeries = [];

    var trList = [];
    for (var ti = 0; ti < bars.length; ti++) {
      var curB = bars[ti];
      var h = safeNum(curB.high, safeNum(curB.close));
      var l = safeNum(curB.low, safeNum(curB.close));
      var tr = h - l;
      if (ti > 0) {
        var pC = safeNum(bars[ti - 1].close);
        tr = Math.max(h - l, Math.abs(h - pC), Math.abs(l - pC));
      }
      trList.push(tr);
    }

    for (var bi = 0; bi < bars.length; bi++) {
      var bar = bars[bi];
      var atrVal = atrMap[bar.time];
      var closePrice = safeNum(bar.close);

      if (atrVal !== undefined && closePrice > 0) {
        var atrPct = (atrVal / closePrice) * 100;
        rawAtrPctList.push({ time: bar.time, index: bi, atrPct: atrPct, atr: atrVal, close: closePrice });
      }
    }

    var prevIsSqueeze = false;
    for (var ri = 0; ri < rawAtrPctList.length; ri++) {
      var item = rawAtrPctList[ri];
      var windowStart = Math.max(0, ri - pctLen + 1);
      var count = 0;
      var totalInWindow = ri - windowStart + 1;
      for (var wi = windowStart; wi <= ri; wi++) {
        if (rawAtrPctList[wi].atrPct <= item.atrPct) {
          count++;
        }
      }
      var percentile = (count / totalInWindow) * 100;
      var isSqueeze = percentile <= squeezeThresh;

      atrPctSeries.push({
        time: item.time,
        value: Number(item.atrPct.toFixed(2)),
        percentile: Number(percentile.toFixed(2)),
        isSqueeze: isSqueeze
      });

      squeezeSeries.push({
        time: item.time,
        value: isSqueeze ? 1 : 0,
        isSqueeze: isSqueeze
      });

      var barIdx = item.index;
      var trVal = trList[barIdx];
      var expansionRatio = item.atr > 0 ? trVal / item.atr : 0;
      var volMaVal = volMap[item.time] || 0;
      var curVol = safeNum(bars[barIdx].volume, 0);
      var volRatio = volMaVal > 0 ? curVol / volMaVal : 0;

      var isBreakout = expansionRatio >= expansionThresh && volRatio >= volRatioThresh;
      var isSqueezeBreakout = isBreakout && prevIsSqueeze;

      breakoutSeries.push({
        time: item.time,
        isBreakout: isBreakout,
        isSqueezeBreakout: isSqueezeBreakout,
        expansionRatio: Number(expansionRatio.toFixed(2)),
        volRatio: Number(volRatio.toFixed(2))
      });

      prevIsSqueeze = isSqueeze;
    }

    var latestAtr = atrSeries.length > 0 ? atrSeries[atrSeries.length - 1].value : 0;
    var stopDistance = Number((latestAtr * stopMult).toFixed(2));
    var suggestedShares = stopDistance > 0 ? Math.floor(riskDollar / stopDistance) : 0;

    var mansfieldSeries = [];
    if (benchmarkBars && benchmarkBars.length) {
      var validBench = [];
      for (var bmi = 0; bmi < benchmarkBars.length; bmi++) {
        var vb = benchmarkBars[bmi];
        if (vb && typeof vb.time === "number" && typeof vb.close === "number" && isFinite(vb.close) && vb.close > 0) {
          validBench.push(vb);
        }
      }
      if (validBench.length > 0) {
        var rawRsRatios = [];
        var bIdx = 0;
        for (var rsi = 0; rsi < bars.length; rsi++) {
          var tb = bars[rsi];
          if (!tb || typeof tb.time !== "number" || typeof tb.close !== "number" || !isFinite(tb.close)) continue;
          while (bIdx + 1 < validBench.length && validBench[bIdx + 1].time <= tb.time) {
            bIdx++;
          }
          if (validBench[bIdx].time <= tb.time) {
            var benchClose = validBench[bIdx].close;
            if (benchClose > 0 && tb.close > 0) {
              rawRsRatios.push({ time: tb.time, ratio: tb.close / benchClose });
            }
          }
        }

      if (rawRsRatios.length >= mansfieldLen) {
        var ratioSum = 0;
        for (var mi = 0; mi < mansfieldLen; mi++) {
          ratioSum += rawRsRatios[mi].ratio;
        }
        for (var mIdx = mansfieldLen - 1; mIdx < rawRsRatios.length; mIdx++) {
          if (mIdx >= mansfieldLen) {
            ratioSum += rawRsRatios[mIdx].ratio - rawRsRatios[mIdx - mansfieldLen].ratio;
          }
          var smaRatio = ratioSum / mansfieldLen;
          var curRatio = rawRsRatios[mIdx].ratio;
          var mRs = smaRatio > 0 ? ((curRatio / smaRatio) - 1) * 100 : 0;
          mansfieldSeries.push({
            time: rawRsRatios[mIdx].time,
            value: Number(mRs.toFixed(2)),
            isPositive: mRs >= 0
          });
        }
      }
      }
    }

    return {
      atr: atrSeries,
      atrPct: atrPctSeries,
      squeeze: squeezeSeries,
      breakouts: breakoutSeries,
      mansfieldRS: mansfieldSeries,
      positionSizing: {
        latestAtr: latestAtr,
        stopDistance: stopDistance,
        suggestedShares: suggestedShares,
        riskDollar: riskDollar,
        stopMult: stopMult
      }
    };
  }

  return {
    calcSMA,
    calcEMA,
    calcVolumeSMA,
    calcRelativeStrength,
    calcRsNewHighMarkers,
    calcRsRating,
    calcRSI,
    calcBollingerBands,
    calcVWAP,
    calcATR,
    calcAnchoredVWAP,
    calcATRToolkit,
    parsePineInputs,
    getBarSource
  };
}));


// ==========================================================================
// TradingView & Deepvue Real-Time Terminal Engine
// ==========================================================================

// Global Chart State
  var chart = null;
  var candleSeries = null;
  var candlestickSeries = null;
  var volumeSeries = null;
  var volSmaSeries = null;
  var ema10Series = null;
  var ema21Series = null;
  var ema30Series = null;
  var vwapSeries = null;
  var sma50Series = null;
  var sma200Series = null;
  var rsSeries = null;
  var bbUpperSeries = null;
  var bbLowerSeries = null;
  var resizeObserver = null;
  var watchlistItemsEl = null;
  var subCharts = {};
  var subTvWidgets = {};
  var activeSplitLayout = "1x1";
  var focusedPaneId = 1;
  var mtfSyncEnabled = false;
  var paneSymbols = { 1: "NASDAQ:NVDA", 2: "AMEX:SPY", 3: "NASDAQ:QQQ", 4: "NASDAQ:FAST" };
  var paneTimeframes = { 1: "1D", 2: "1D", 3: "1D", 4: "1D" };
  var paneGen = {};
  var paneAbortControllers = {};

  function disposeSubPane(paneNum) {
    if (paneAbortControllers[paneNum]) {
      try {
        paneAbortControllers[paneNum].abort();
      } catch (e) {}
      delete paneAbortControllers[paneNum];
    }
    if (subCharts[paneNum]) {
      try {
        if (subCharts[paneNum].ro && typeof subCharts[paneNum].ro.disconnect === "function") {
          subCharts[paneNum].ro.disconnect();
        }
      } catch (e) {
        console.warn("[disposeSubPane] ro.disconnect() error for pane " + paneNum, e);
      }
      try {
        if (subCharts[paneNum].rsChart && typeof subCharts[paneNum].rsChart.remove === "function") {
          subCharts[paneNum].rsChart.remove();
        }
      } catch (e) {
        console.warn("[disposeSubPane] rsChart.remove() error for pane " + paneNum, e);
      }
      try {
        if (subCharts[paneNum].chart && typeof subCharts[paneNum].chart.remove === "function") {
          subCharts[paneNum].chart.remove();
        }
      } catch (e) {
        console.warn("[disposeSubPane] chart.remove() error for pane " + paneNum, e);
      }
      delete subCharts[paneNum];
    }
    if (subTvWidgets[paneNum]) {
      try {
        if (typeof subTvWidgets[paneNum].remove === "function") {
          subTvWidgets[paneNum].remove();
        }
      } catch (e) {
        console.warn("[disposeSubPane] tvWidget.remove() error for pane " + paneNum, e);
      }
      delete subTvWidgets[paneNum];
    }
    var rsPane = document.getElementById("splitRsPane" + paneNum);
    if (rsPane) rsPane.style.display = "none";
    var splitPane = document.getElementById("splitPane" + paneNum);
    if (splitPane) splitPane.classList.remove("has-rs");
    var rsContainer = document.getElementById("splitRsChart" + paneNum);
    if (rsContainer) rsContainer.innerHTML = "";
    var container = document.getElementById("splitChart" + paneNum);
    if (container) {
      try {
        var iframes = container.querySelectorAll("iframe");
        for (var i = 0; i < iframes.length; i++) {
          try {
            iframes[i].src = "about:blank";
          } catch (iframeErr) {
            console.warn("[disposeSubPane] iframe cleanup error for pane " + paneNum, iframeErr);
          }
        }
      } catch (e) {
        console.warn("[disposeSubPane] iframes lookup error for pane " + paneNum, e);
      }
      container.innerHTML = "";
    }
  }

function resolveBenchmarkSymbol(tag) {
  if (!tag || tag === "SPY") return "AMEX:SPY";
  if (tag === "QQQ") return "NASDAQ:QQQ";
  if (tag === "IWM") return "AMEX:IWM";
  if (tag === "DIA") return "NYSE:DIA";
  if (tag === "BTCUSDT") return "BINANCE:BTCUSDT";
  if (tag.indexOf(":") !== -1) return tag;
  return "NASDAQ:" + tag;
}

// ==========================================================================
// 主圖與 RS 副圖的 X 軸同步
// ==========================================================================
// 不用時間範圍同步（subscribeVisibleTimeRangeChange + setVisibleRange）：時間範圍只包含「有資料的 K 棒」，
// 捲到最右邊的空白時會被換算成「把剩下幾根撐滿整個寬度」，兩張圖再互相回傳，就會一路放大到最大。
// 改用邏輯範圍同步：會保留右側空白與縮放。為了讓兩張圖的同一個邏輯位置是同一根 K 棒，
// RS 資料先補齊成和主圖相同的時間軸（沒有值的時間點補空白點）。

/** 取得某個 series 目前的時間軸（依序的時間陣列）；取不到時改用 fallbackBars。 */
function getSeriesTimeAxis(series, fallbackBars) {
  var src = null;
  if (series && typeof series.data === "function") {
    try { src = series.data(); } catch (e) { src = null; }
  }
  if (!Array.isArray(src) || src.length === 0) src = fallbackBars;
  var times = [];
  if (!Array.isArray(src)) return times;
  for (var i = 0; i < src.length; i++) {
    var t = src[i] ? src[i].time : null;
    if (typeof t === "number" && isFinite(t) && (times.length === 0 || t > times[times.length - 1])) times.push(t);
  }
  return times;
}

/**
 * 把資料補齊到指定的時間軸：時間軸上的每個時間都有一筆（沒有值的補 { time } 空白點）。
 * 資料中不在時間軸上的點會保留並依時間排序。不修改傳入的陣列。
 */
function padToTimeAxis(data, axisTimes) {
  var src = Array.isArray(data) ? data : [];
  if (!Array.isArray(axisTimes) || axisTimes.length === 0) return src.slice();
  var axisSet = {};
  for (var a = 0; a < axisTimes.length; a++) axisSet[axisTimes[a]] = true;
  var byTime = {};
  var extra = [];
  for (var i = 0; i < src.length; i++) {
    var p = src[i];
    if (!p || typeof p.time !== "number") continue;
    if (axisSet[p.time]) {
      byTime[p.time] = p;
    } else {
      extra.push(p);
    }
  }
  var out = [];
  for (var j = 0; j < axisTimes.length; j++) {
    var t = axisTimes[j];
    out.push(byTime[t] ? byTime[t] : { time: t });
  }
  if (extra.length > 0) {
    out = out.concat(extra);
    out.sort(function(x, y) { return x.time - y.time; });
  }
  return out;
}

/** 把目標圖的可見邏輯範圍設成 range；目前已相同（差距小於 1e-6）時不動作，避免兩張圖互相回傳。 */
function syncLogicalRangeTo(targetChart, range) {
  if (!targetChart || typeof targetChart.timeScale !== "function") return false;
  if (!range || typeof range.from !== "number" || typeof range.to !== "number" || !isFinite(range.from) || !isFinite(range.to)) return false;
  var ts = targetChart.timeScale();
  var cur = null;
  try { cur = ts.getVisibleLogicalRange(); } catch (e) { cur = null; }
  if (cur && Math.abs(cur.from - range.from) < 1e-6 && Math.abs(cur.to - range.to) < 1e-6) return false;
  try {
    ts.setVisibleLogicalRange({ from: range.from, to: range.to });
  } catch (e2) {
    return false;
  }
  return true;
}

/** 雙向綁定兩張圖的邏輯範圍；onPrimaryChange 會在主圖範圍改變時呼叫（例如重畫繪圖層）。 */
function bindLogicalRangeSync(primaryChart, secondaryChart, onPrimaryChange) {
  if (!primaryChart || !secondaryChart) return;
  var syncing = false;
  primaryChart.timeScale().subscribeVisibleLogicalRangeChange(function(range) {
    if (typeof onPrimaryChange === "function") onPrimaryChange();
    if (syncing || !range) return;
    syncing = true;
    try { syncLogicalRangeTo(secondaryChart, range); } finally { syncing = false; }
  });
  secondaryChart.timeScale().subscribeVisibleLogicalRangeChange(function(range) {
    if (syncing || !range) return;
    syncing = true;
    try { syncLogicalRangeTo(primaryChart, range); } finally { syncing = false; }
  });
}

if (typeof window !== "undefined") {
  window.getSeriesTimeAxis = getSeriesTimeAxis;
  window.padToTimeAxis = padToTimeAxis;
  window.syncLogicalRangeTo = syncLogicalRangeTo;
  window.bindLogicalRangeSync = bindLogicalRangeSync;
}
if (typeof globalThis !== "undefined") {
  globalThis.getSeriesTimeAxis = getSeriesTimeAxis;
  globalThis.padToTimeAxis = padToTimeAxis;
  globalThis.syncLogicalRangeTo = syncLogicalRangeTo;
  globalThis.bindLogicalRangeSync = bindLogicalRangeSync;
}

var RIGHT_WHITESPACE_BARS = 10;   // 最後一根 K 棒右邊預設留的空白（根數）
var INITIAL_VISIBLE_BARS = 150;   // 換標的或週期時預設顯示的 K 棒數
var lastChartViewKey = null;      // 上次重設視圖時的 "標的|週期"

/**
 * 把圖表重設成「最後 INITIAL_VISIBLE_BARS 根 + 右邊 RIGHT_WHITESPACE_BARS 根空白」。
 * n 為資料筆數。n <= 0 時不動作。沒有 setVisibleLogicalRange 時退回 fitContent。
 * 回傳設定的範圍（或 null）。
 */
function resetChartView(chartObj, n) {
  if (typeof n !== "number" || !isFinite(n) || n <= 0) return null;
  if (!chartObj || typeof chartObj.timeScale !== "function") return null;
  var from = n - INITIAL_VISIBLE_BARS;
  var to = n - 1 + RIGHT_WHITESPACE_BARS;
  var targetRange = { from: from, to: to };
  var ts = null;
  try { ts = chartObj.timeScale(); } catch (e) { ts = null; }
  if (!ts) return null;
  if (typeof ts.setVisibleLogicalRange === "function") {
    try {
      ts.setVisibleLogicalRange(targetRange);
      return targetRange;
    } catch (e2) {
      if (typeof ts.fitContent === "function") {
        try { ts.fitContent(); } catch (errFit) {}
      }
      return targetRange;
    }
  } else if (typeof ts.fitContent === "function") {
    try { ts.fitContent(); } catch (e3) {}
    return targetRange;
  }
  return null;
}

/**
 * 資料換掉後，用「時間」當錨點換算舊的邏輯範圍。
 * - 最新一根在畫面內（range.to >= 舊的最後索引）：保持與最新一根的距離（新 K 棒會跟上）。
 * - 否則：以畫面右緣那根 K 棒的時間為錨點，找新資料中第一根 time >= 該時間的索引。
 * prevTimes / nextTimes 為遞增的時間陣列。任一為空或 range 無效時回傳 null。
 */
function computePreservedLogicalRange(prevTimes, nextTimes, range) {
  if (!Array.isArray(prevTimes) || prevTimes.length === 0 || !Array.isArray(nextTimes) || nextTimes.length === 0) return null;
  if (!range || !Number.isFinite(range.from) || !Number.isFinite(range.to)) return null;

  var prevLast = prevTimes.length - 1;
  var nextLast = nextTimes.length - 1;

  var prevFirst = prevTimes[0], prevEnd = prevTimes[prevLast];
  var nextFirst = nextTimes[0], nextEnd = nextTimes[nextLast];
  if (!Number.isFinite(prevFirst) || !Number.isFinite(prevEnd) || !Number.isFinite(nextFirst) || !Number.isFinite(nextEnd)) return null;
  if (prevEnd < nextFirst || nextEnd < prevFirst) return null;

  if (range.to >= prevLast - 0.5) {
    var offset = range.to - prevLast;
    var span = range.to - range.from;
    var newTo = nextLast + offset;
    var newFrom = newTo - span;
    return { from: newFrom, to: newTo };
  }

  var anchorIdx = Math.max(0, Math.min(prevLast, Math.round(range.to)));
  var anchorTime = prevTimes[anchorIdx];
  if (!Number.isFinite(anchorTime)) return null;

  var lo = 0, hi = nextLast, newAnchorIdx = -1;
  while (lo <= hi) {
    var mid = (lo + hi) >> 1;
    if (!Number.isFinite(nextTimes[mid])) return null;
    if (nextTimes[mid] >= anchorTime) {
      newAnchorIdx = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  if (newAnchorIdx === -1) {
    newAnchorIdx = nextLast;
  }

  var shift = newAnchorIdx - anchorIdx;
  return { from: range.from + shift, to: range.to + shift };
}

/** clientX 是否落在右側價格軸上。rect 為圖表容器的 getBoundingClientRect()，axisWidth 為價格軸寬度（px）。 */
function isPointInPriceAxis(clientX, rect, axisWidth) {
  if (typeof clientX !== "number" || !rect) return false;
  if (typeof rect.left !== "number" || typeof rect.width !== "number") return false;
  if (typeof axisWidth !== "number" || !isFinite(axisWidth) || axisWidth <= 0) return false;
  return clientX >= rect.left + rect.width - axisWidth && clientX <= rect.left + rect.width;
}

/**
 * 拖動圖表時是否要關閉 AUTO，讓使用者可以上下移動。
 * state: { autoScale, tool, draggingHandle }
 * Lightweight Charts 4.2.0 只在拖動開始時檢查 autoScale，所以 AUTO 開著時第一次上下拖只會關掉 AUTO，第二次拖才會上下移動。
 */
function shouldDisableAutoScaleOnDrag(dx, dy, state) {
  if (!state || typeof state !== "object") return false;
  if (state.autoScale !== true) return false;
  if (state.tool !== "cursor") return false;
  if (state.draggingHandle) return false;
  var absDx = Math.abs(dx);
  var absDy = Math.abs(dy);
  if (absDy <= 6) return false;
  if (absDy <= absDx) return false;
  return true;
}

/** 讀取主圖價格軸實際的 autoScale，同步到 isAutoScale 與 AUTO 按鈕；不呼叫 applyOptions。回傳讀到的值或 null。 */
function syncAutoScaleFromChart() {
  if (!chart || typeof chart.priceScale !== "function") return null;
  var currentScale = null;
  try {
    var ps = chart.priceScale("right");
    if (ps && typeof ps.options === "function") {
      var opts = ps.options();
      if (opts && typeof opts.autoScale === "boolean") {
        currentScale = opts.autoScale;
      }
    }
  } catch (e) {
    currentScale = null;
  }
  if (typeof currentScale === "boolean") {
    isAutoScale = currentScale;
    var autoBtn = document.getElementById("scaleAuto");
    if (autoBtn) {
      autoBtn.classList.toggle("active", isAutoScale);
    }
    return currentScale;
  }
  return null;
}

function handleChartDblClick(e) {
  if (!e || typeof e.clientX !== "number") return;
  var pContainer = (typeof priceContainer !== "undefined" && priceContainer) ? priceContainer : (document.getElementById("chart") || chartContainer);
  if (!pContainer || typeof pContainer.getBoundingClientRect !== "function") return;
  var rect = pContainer.getBoundingClientRect();
  var axisWidth = 0;
  if (chart && typeof chart.priceScale === "function") {
    try {
      var ps = chart.priceScale("right");
      if (ps && typeof ps.width === "function") {
        axisWidth = ps.width() || 0;
      }
    } catch (err) {
      axisWidth = 0;
    }
  }
  if (isPointInPriceAxis(e.clientX, rect, axisWidth)) {
    setAutoScale(true);
  }
}

/** localStorage.tv_debug_range === "1" 時，記錄這張圖所有改變可見範圍的呼叫與呼叫堆疊。 */
function installRangeDebug(chartObj, label) {
  if (!chartObj || typeof chartObj.timeScale !== "function") return chartObj;
  var enabled = false;
  try {
    if (typeof safeGetStorage === "function") {
      enabled = (safeGetStorage("tv_debug_range", "0") === "1");
    } else if (typeof localStorage !== "undefined" && localStorage && typeof localStorage.getItem === "function") {
      enabled = (localStorage.getItem("tv_debug_range") === "1");
    }
  } catch (e) {
    enabled = false;
  }
  if (!enabled) return chartObj;

  var origTimeScale = chartObj.timeScale;
  var cachedTs = null;

  chartObj.timeScale = function() {
    var ts = origTimeScale.apply(chartObj, arguments);
    if (!ts) return ts;
    if (cachedTs && cachedTs._raw === ts) {
      return cachedTs;
    }

    var methods = [
      "setVisibleLogicalRange",
      "setVisibleRange",
      "fitContent",
      "scrollToPosition",
      "scrollToRealTime",
      "resetTimeScale"
    ];

    for (var i = 0; i < methods.length; i++) {
      (function(name) {
        if (typeof ts[name] === "function" && !ts[name]._rangeDebugWrapped) {
          var origFn = ts[name];
          var wrapped = function() {
            var args = Array.prototype.slice.call(arguments);
            var stack = "";
            try { stack = (new Error()).stack || ""; } catch (err) {}
            console.log("[range]", label, name, args, stack);
            return origFn.apply(ts, arguments);
          };
          wrapped._rangeDebugWrapped = true;
          wrapped._orig = origFn;
          ts[name] = wrapped;
        }
      })(methods[i]);
    }

    cachedTs = ts;
    cachedTs._raw = ts;
    return ts;
  };

  return chartObj;
}

if (typeof window !== "undefined") {
  window.RIGHT_WHITESPACE_BARS = RIGHT_WHITESPACE_BARS;
  window.INITIAL_VISIBLE_BARS = INITIAL_VISIBLE_BARS;
  window.resetChartView = resetChartView;
  window.computePreservedLogicalRange = computePreservedLogicalRange;
  window.isPointInPriceAxis = isPointInPriceAxis;
  window.shouldDisableAutoScaleOnDrag = shouldDisableAutoScaleOnDrag;
  window.syncAutoScaleFromChart = syncAutoScaleFromChart;
  window.handleChartDblClick = handleChartDblClick;
  window.installRangeDebug = installRangeDebug;
  window.renderChartData = renderChartData;
  window.clampAndSetVisibleRange = clampAndSetVisibleRange;
}
if (typeof globalThis !== "undefined") {
  globalThis.RIGHT_WHITESPACE_BARS = RIGHT_WHITESPACE_BARS;
  globalThis.INITIAL_VISIBLE_BARS = INITIAL_VISIBLE_BARS;
  globalThis.resetChartView = resetChartView;
  globalThis.computePreservedLogicalRange = computePreservedLogicalRange;
  globalThis.isPointInPriceAxis = isPointInPriceAxis;
  globalThis.shouldDisableAutoScaleOnDrag = shouldDisableAutoScaleOnDrag;
  globalThis.syncAutoScaleFromChart = syncAutoScaleFromChart;
  globalThis.handleChartDblClick = handleChartDblClick;
  globalThis.installRangeDebug = installRangeDebug;
  globalThis.renderChartData = renderChartData;
  globalThis.clampAndSetVisibleRange = clampAndSetVisibleRange;
}

  function renderSubPaneRs(paneNum, bars, benchBars) {
    var splitPaneEl = document.getElementById("splitPane" + paneNum);
    var rsPaneEl = document.getElementById("splitRsPane" + paneNum);
    var rsChartEl = document.getElementById("splitRsChart" + paneNum);
    if (!rsPaneEl || !splitPaneEl) return;

    if (!indicatorConfig.rsLine || currentEngine === "C") {
      rsPaneEl.style.display = "none";
      splitPaneEl.classList.remove("has-rs");
      if (subObj) {
        if (subObj.rsLineSeries && typeof subObj.rsLineSeries.setMarkers === "function") {
          try { subObj.rsLineSeries.setMarkers([]); } catch (e) {}
        }
        if (subObj.series && typeof subObj.series.setMarkers === "function") {
          try { subObj.series.setMarkers([]); } catch (e) {}
        }
      }
      var nhBadgeOff = document.getElementById("splitRsNhBadge" + paneNum);
      if (nhBadgeOff) nhBadgeOff.style.display = "none";
      return;
    }

    rsPaneEl.style.display = "flex";
    splitPaneEl.classList.add("has-rs");

    var subObj = subCharts[paneNum];
    if (!subObj) return;

    var targetBars = bars || subObj.bars;
    if (!targetBars || targetBars.length === 0) return;

    var subTf = paneTimeframes[paneNum] || "1D";
    var bBars = null;
    if (subTf === (activeTimeframe || "1D")) {
      bBars = benchBars || benchmarkRawBars || [];
    } else {
      var cacheKey = (rsBenchmarkTag || "SPY") + "|" + subTf;
      if (benchBars && benchBars !== benchmarkRawBars) {
        bBars = benchBars;
        subPaneBenchmarkCache[cacheKey] = { bars: benchBars, inFlight: false, failedAt: 0 };
      } else if (subPaneBenchmarkCache[cacheKey] && subPaneBenchmarkCache[cacheKey].bars && subPaneBenchmarkCache[cacheKey].bars.length > 0) {
        bBars = subPaneBenchmarkCache[cacheKey].bars;
      } else {
        var subState = subPaneBenchmarkCache[cacheKey] || { inFlight: false, failedAt: 0, bars: [] };
        subPaneBenchmarkCache[cacheKey] = subState;
        var nowMs = Date.now();
        if (!subState.inFlight && (nowMs - subState.failedAt >= 30000)) {
          subState.inFlight = true;
          var benchSym = resolveBenchmarkSymbol(rsBenchmarkTag);
          var subFetchUrl = "/api/history?seed=true&timeframe=" + encodeURIComponent(subTf) + "&symbol=" + encodeURIComponent(benchSym);
          fetch(subFetchUrl)
            .then(function(res) {
              if (!res.ok) throw new Error("HTTP " + res.status);
              return res.json();
            })
            .then(function(data) {
              subState.inFlight = false;
              var fetchedBars = (data && data.bars) || [];
              if (fetchedBars.length === 0) {
                subState.failedAt = Date.now();
              } else {
                subState.failedAt = 0;
                subState.bars = fetchedBars;
                if (subCharts[paneNum] && subCharts[paneNum].bars) {
                  renderSubPaneRs(paneNum, subCharts[paneNum].bars, fetchedBars);
                }
              }
            })
            .catch(function(e) {
              subState.inFlight = false;
              subState.failedAt = Date.now();
            });
        }
        var earlySubRsAxis = (typeof getSeriesTimeAxis === "function") ? getSeriesTimeAxis(subObj.series, targetBars) : [];
        if (subObj.rsLineSeries && typeof subObj.rsLineSeries.setData === "function") {
          subObj.rsLineSeries.setData(padToTimeAxis([], earlySubRsAxis));
        }
        if (subObj.rsEmaSeries && typeof subObj.rsEmaSeries.setData === "function") {
          subObj.rsEmaSeries.setData(padToTimeAxis([], earlySubRsAxis));
        }
        return;
      }
    }

    var titleEl = document.getElementById("splitRsTitle" + paneNum);
    if (titleEl) {
      titleEl.textContent = "RS vs " + (rsBenchmarkTag || "SPY");
    }

    if (!subObj.rsChart && typeof LightweightCharts !== "undefined" && rsChartEl) {
      var subPalette = getChartThemePalette();
      var subRsTimeOpts = makeChartTimeOptions(
        function() { return paneSymbols[paneNum]; },
        function() { return paneTimeframes[paneNum] || "1D"; }
      );
      var rsChartInstance = LightweightCharts.createChart(rsChartEl, {
        width: rsChartEl.clientWidth || 300,
        height: rsChartEl.clientHeight || 65,
        layout: {
          background: { color: subPalette.background },
          textColor: subPalette.subTextColor
        },
        grid: {
          vertLines: { color: subPalette.grid },
          horzLines: { color: subPalette.grid }
        },
        rightPriceScale: { borderColor: subPalette.border },
        timeScale: { borderColor: subPalette.border, timeVisible: true, rightOffset: RIGHT_WHITESPACE_BARS, tickMarkFormatter: subRsTimeOpts.tickMarkFormatter },
        localization: { timeFormatter: subRsTimeOpts.timeFormatter }
      });
      var rsLineSeries = rsChartInstance.addLineSeries({
        color: "#26a69a",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false
      });
      var rsEmaSeries = rsChartInstance.addLineSeries({
        color: "#38bdf8",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false
      });
      subObj.rsChart = rsChartInstance;
      subObj.rsLineSeries = rsLineSeries;
      subObj.rsEmaSeries = rsEmaSeries;

      var subMainChart = subObj.chart;
      if (subMainChart && !subObj.timeSyncBound) {
        subObj.timeSyncBound = true;
        bindLogicalRangeSync(subMainChart, rsChartInstance);
      }
    }

    if (!subObj.rsChart || !subObj.rsLineSeries) return;

    var subRsAxis = getSeriesTimeAxis(subObj.series, targetBars);
    if (subObj.rsLineSeries && typeof subObj.rsLineSeries.setData === "function") {
      subObj.rsLineSeries.setData(padToTimeAxis([], subRsAxis));
    }
    if (subObj.rsEmaSeries && typeof subObj.rsEmaSeries.setData === "function") {
      subObj.rsEmaSeries.setData(padToTimeAxis([], subRsAxis));
    }
    var currentMode = (indicatorConfig && indicatorConfig.rsMode) || "mansfield";
    if (window.Indicators) {
      if (currentMode === "mansfield") {
        if (subObj.rsZeroLine && typeof subObj.rsLineSeries.removePriceLine === "function") {
          try { subObj.rsLineSeries.removePriceLine(subObj.rsZeroLine); } catch (e) {}
          subObj.rsZeroLine = null;
        }
        var toolkit = window.Indicators.calcATRToolkit(targetBars, bBars, {
          riskDollar: 500,
          stopMult: 2.0,
          mansfieldLen: 52
        });
        if (toolkit && toolkit.mansfieldRS && toolkit.mansfieldRS.length > 0) {
          if (typeof subObj.rsLineSeries.applyOptions === "function") {
            subObj.rsLineSeries.applyOptions({ color: "#26a69a", lineWidth: 2 });
          }
          subObj.rsLineSeries.setData(padToTimeAxis(toolkit.mansfieldRS, subRsAxis));
          if (typeof subObj.rsLineSeries.createPriceLine === "function") {
            try {
              subObj.rsZeroLine = subObj.rsLineSeries.createPriceLine({
                price: 0.0,
                color: "#787b86",
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: false
              });
            } catch (e) {}
          }
        }
        if (toolkit && toolkit.atrPct && toolkit.atrPct.length) {
          var atrData = toolkit.atrPct.map(function(item) {
            var numVal = typeof item.value === "number" ? item.value : (item.atrPct || 0);
            return { time: item.time, value: Number(numVal.toFixed(2)) };
          });
          if (typeof subObj.rsEmaSeries.applyOptions === "function") {
            subObj.rsEmaSeries.applyOptions({ color: "#38bdf8", lineWidth: 1, lineStyle: 1 });
          }
          subObj.rsEmaSeries.setData(padToTimeAxis(atrData, subRsAxis));
        }
      } else {
        if (subObj.rsZeroLine && typeof subObj.rsLineSeries.removePriceLine === "function") {
          try { subObj.rsLineSeries.removePriceLine(subObj.rsZeroLine); } catch (e) {}
          subObj.rsZeroLine = null;
        }
        if (bBars && bBars.length > 0) {
          var rsData = window.Indicators.calcRelativeStrength(targetBars, bBars);
          if (rsData) {
            if (typeof subObj.rsLineSeries.applyOptions === "function") {
              subObj.rsLineSeries.applyOptions({ color: "#2962ff", lineWidth: 2 });
            }
            subObj.rsLineSeries.setData(padToTimeAxis(rsData, subRsAxis));
          }
          if (rsData && rsData.length >= 21) {
            var rsBarsForEma = rsData.map(function(r) { return { time: r.time, close: r.value }; });
            var rsEma = window.Indicators.calcEMA(rsBarsForEma, 21);
            if (typeof subObj.rsEmaSeries.applyOptions === "function") {
              subObj.rsEmaSeries.applyOptions({ color: "#f59e0b", lineWidth: 1, lineStyle: 2 });
            }
            subObj.rsEmaSeries.setData(padToTimeAxis(rsEma, subRsAxis));
          }
        }
      }

      if (subObj.chart) {
        try { syncLogicalRangeTo(subObj.rsChart, subObj.chart.timeScale().getVisibleLogicalRange()); } catch (e) {}
      }

      var ratingBadge = document.getElementById("splitRsRating" + paneNum);
      if (ratingBadge) {
        if (bBars && bBars.length > 0 && typeof window.Indicators.calcRsRating === "function") {
          var rsRating = window.Indicators.calcRsRating(targetBars, bBars);
          if (rsRating !== null) {
            ratingBadge.textContent = "RS " + rsRating;
            ratingBadge.className = "rs-rating-badge split-rs-rating " + (rsRating >= 80 ? "rating-high" : (rsRating >= 60 ? "rating-med" : "rating-low"));
          } else {
            ratingBadge.textContent = "RS --";
            ratingBadge.className = "rs-rating-badge split-rs-rating";
          }
        } else {
          ratingBadge.textContent = "RS --";
          ratingBadge.className = "rs-rating-badge split-rs-rating";
        }
      }

      var nhBadge = document.getElementById("splitRsNhBadge" + paneNum);
      if (bBars && bBars.length > 0 && typeof window.Indicators.calcRsNewHighMarkers === "function") {
        var rsMarkers = window.Indicators.calcRsNewHighMarkers(targetBars, bBars);
        if (!Array.isArray(rsMarkers)) rsMarkers = [];
        var last5Time = targetBars.length >= 5 ? targetBars[targetBars.length - 5].time : 0;
        var hasRecentNh = rsMarkers.some(function(m) { return m.time >= last5Time; });
        if (nhBadge) {
          nhBadge.style.display = hasRecentNh ? "inline-block" : "none";
        }
        if (subObj.rsLineSeries && typeof subObj.rsLineSeries.setMarkers === "function") {
          try {
            subObj.rsLineSeries.setMarkers(rsMarkers);
          } catch (e) {}
        }
        if (subObj.series && typeof subObj.series.setMarkers === "function") {
          try {
            subObj.series.setMarkers(rsMarkers);
          } catch (e) {}
        }
      } else if (nhBadge) {
        nhBadge.style.display = "none";
        if (subObj.rsLineSeries && typeof subObj.rsLineSeries.setMarkers === "function") {
          try { subObj.rsLineSeries.setMarkers([]); } catch (e) {}
        }
        if (subObj.series && typeof subObj.series.setMarkers === "function") {
          try { subObj.series.setMarkers([]); } catch (e) {}
        }
      }
    }

    if (rsChartEl && subObj.rsChart) {
      var rw = rsChartEl.clientWidth;
      var rh = rsChartEl.clientHeight;
      if (rw > 0 && rh > 0) {
        subObj.rsChart.applyOptions({ width: rw, height: rh });
      }
    }
  }

  function setFocusedPane(paneNum) {
    focusedPaneId = paneNum;
    var p1 = document.getElementById("chartContainer");
    var p2 = document.getElementById("splitPane2");
    var p3 = document.getElementById("splitPane3");
    var p4 = document.getElementById("splitPane4");
    if (p1) p1.classList.toggle("focused-pane", paneNum === 1);
    if (p2) p2.classList.toggle("focused-pane", paneNum === 2);
    if (p3) p3.classList.toggle("focused-pane", paneNum === 3);
    if (p4) p4.classList.toggle("focused-pane", paneNum === 4);
  }

  function syncMTF(symbol) {
    paneSymbols[1] = symbol;
    paneSymbols[2] = symbol;
    paneSymbols[3] = symbol;
    paneSymbols[4] = symbol;
    paneTimeframes[1] = "1D";
    paneTimeframes[2] = "1W";
    paneTimeframes[3] = "1h";
    paneTimeframes[4] = "15m";
    switchSymbol(symbol);
    if (activeSplitLayout === "1x2" || activeSplitLayout === "2x2") {
      loadSubChart(2, symbol, "1W");
    }
    if (activeSplitLayout === "2x2") {
      loadSubChart(3, symbol, "1h");
      loadSubChart(4, symbol, "15m");
    }
  }

  function loadSubChart(paneNum, symbol, tf) {
    tf = tf || paneTimeframes[paneNum] || "1D";
    paneSymbols[paneNum] = symbol;
    paneTimeframes[paneNum] = tf;
    var container = document.getElementById("splitChart" + paneNum);
    var titleEl = document.getElementById("splitTitle" + paneNum);
    if (!container) return;

    if (titleEl) {
      titleEl.textContent = getSymbolDisplayName(symbol) + " • " + tf;
    }

    var gen = (paneGen[paneNum] = (paneGen[paneNum] || 0) + 1);
    disposeSubPane(paneNum);

    if (currentEngine === "C") {
      var tvSymbol = toTradingViewRealtimeSymbol(symbol);
      var tvInterval = ({ "1m": "1", "5m": "5", "15m": "15", "1h": "60", "60m": "60", "1D": "D", "1W": "W" })[tf] || "D";
      var subTvStudies = [];
      if (indicatorConfig.ema21 !== false) {
        subTvStudies.push({
          id: "MAExp@tv-basicstudies",
          inputs: { length: 21 },
          override: { "plot.color": "#a855f7", "plot.linewidth": 2 }
        });
      }
      if (indicatorConfig.ema30 !== false) {
        subTvStudies.push({
          id: "MAExp@tv-basicstudies",
          inputs: { length: 30 },
          override: { "plot.color": "#f59e0b", "plot.linewidth": 2 }
        });
      }
      if (indicatorConfig.sma200 !== false || indicatorConfig.ema200 !== false) {
        subTvStudies.push({
          id: "MAExp@tv-basicstudies",
          inputs: { length: 200 },
          override: { "plot.color": "#ec4899", "plot.linewidth": 2 }
        });
      }
      if (typeof TradingView !== "undefined" && TradingView.widget) {
        try {
          subTvWidgets[paneNum] = new TradingView.widget({
            autosize: true,
            symbol: tvSymbol,
            interval: tvInterval,
            timezone: "exchange",
            theme: currentTheme === "light" ? "light" : "dark",
            style: "1",
            locale: currentLang === "zh" ? "zh_TW" : "en",
            toolbar_bg: getChartThemePalette().tvToolbarBg,
            enable_publishing: false,
            allow_symbol_change: false,
            hide_side_toolbar: true,
            withdateranges: false,
            details: false,
            hotlist: false,
            calendar: false,
            studies: subTvStudies,
            studies_overrides: {
              "volume.volume ma.display": true,
              "volume.volume ma.length": 50,
              "volume.volume.color.0": "#ef4444",
              "volume.volume.color.1": "#26a69a",
              "moving average exponential.0.plot.color": "#a855f7",
              "moving average exponential.0.plot.linewidth": 2,
              "moving average exponential.1.plot.color": "#f59e0b",
              "moving average exponential.1.plot.linewidth": 2,
              "moving average exponential.2.plot.color": "#ec4899",
              "moving average exponential.2.plot.linewidth": 2
            },
            disabled_features: ["use_localstorage_for_settings"],
            container_id: "splitChart" + paneNum
          });
        } catch (e) {
          console.warn("Sub-pane TradingView widget init error", e);
        }
      }
      return;
    }

    if (typeof LightweightCharts === "undefined") return;
    var subPalette = getChartThemePalette();
    var subTimeOpts = makeChartTimeOptions(
      function() { return symbol; },
      function() { return tf; }
    );
    var subChart = LightweightCharts.createChart(container, {
      width: container.clientWidth || 380,
      height: container.clientHeight || 240,
      layout: {
        background: { color: subPalette.background },
        textColor: subPalette.subTextColor
      },
      grid: {
        vertLines: { color: subPalette.grid },
        horzLines: { color: subPalette.grid }
      },
      rightPriceScale: { borderColor: subPalette.border },
      timeScale: { borderColor: subPalette.border, timeVisible: true, rightOffset: RIGHT_WHITESPACE_BARS, tickMarkFormatter: subTimeOpts.tickMarkFormatter },
      localization: { timeFormatter: subTimeOpts.timeFormatter }
    });
    var subSeries = subChart.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350"
    });
    var ema21Series = subChart.addLineSeries({
      color: "#a855f7",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });
    var ema30Series = subChart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });
    var ema200Series = subChart.addLineSeries({
      color: "#ec4899",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });
    var subObj = {
      chart: subChart,
      series: subSeries,
      ema21: ema21Series,
      ema30: ema30Series,
      ema200: ema200Series,
      lastBar: null,
      ro: null
    };
    if (typeof ResizeObserver !== "undefined") {
      try {
        var ro = new ResizeObserver(function(entries) {
          if (!entries || entries.length === 0) return;
          if (gen !== paneGen[paneNum] || subCharts[paneNum] !== subObj) return;
          var sw = container.clientWidth;
          var sh = container.clientHeight;
          if (sw > 0 && sh > 0 && subObj.chart) {
            subObj.chart.applyOptions({ width: sw, height: sh });
          }
          var rsCont = document.getElementById("splitRsChart" + paneNum);
          if (rsCont && subObj.rsChart) {
            var rw = rsCont.clientWidth;
            var rh = rsCont.clientHeight;
            if (rw > 0 && rh > 0) {
              subObj.rsChart.applyOptions({ width: rw, height: rh });
            }
          }
        });
        ro.observe(container);
        subObj.ro = ro;
      } catch (roErr) {
        console.warn("[loadSubChart] ResizeObserver init error for pane " + paneNum, roErr);
      }
    }
    subCharts[paneNum] = subObj;

    var ac = null;
    if (typeof AbortController !== "undefined") {
      ac = paneAbortControllers[paneNum] = new AbortController();
    }
    var url = "/api/history?seed=true&symbol=" + encodeURIComponent(symbol) + "&timeframe=" + encodeURIComponent(tf);

    fetch(url, { signal: ac ? ac.signal : undefined })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (gen !== paneGen[paneNum] || subCharts[paneNum] !== subObj) return;
        if (!container.isConnected && !document.contains(container)) return;
        if (d && Array.isArray(d.bars) && d.bars.length > 0) {
          subObj.bars = d.bars;
          subObj.series.setData(d.bars);
          subObj.lastBar = d.bars[d.bars.length - 1];
          if (window.Indicators && typeof window.Indicators.calcEMA === "function") {
            try {
              if (subObj.ema21) subObj.ema21.setData(window.Indicators.calcEMA(d.bars, 21));
              if (subObj.ema30) subObj.ema30.setData(window.Indicators.calcEMA(d.bars, 30));
              if (subObj.ema200) subObj.ema200.setData(window.Indicators.calcEMA(d.bars, 200));
            } catch (emaErr) {}
          }
          if (typeof renderSubPaneRs === "function") {
            renderSubPaneRs(paneNum, d.bars, benchmarkRawBars);
          }
          requestAnimationFrame(function() {
            if (gen !== paneGen[paneNum] || subCharts[paneNum] !== subObj) return;
            var sw = container.clientWidth;
            var sh = container.clientHeight;
            if (sw > 0 && sh > 0) {
              subObj.chart.applyOptions({ width: sw, height: sh });
            }
            resetChartView(subObj.chart, (subObj.bars && subObj.bars.length) || 0);
          });
        }
      })
      .catch(function(e) {
        if (e && e.name === "AbortError") return;
        console.warn("[loadSubChart] fetch error for pane " + paneNum, e);
      });
  }

  function applySplitLayout(layout) {
    activeSplitLayout = layout || "1x1";
    var chartWrapper = document.querySelector(".chart-wrapper");
    var splitGrid = document.getElementById("splitChartGrid");
    var splitPane2 = document.getElementById("splitPane2");
    var splitPane3 = document.getElementById("splitPane3");
    var splitPane4 = document.getElementById("splitPane4");
    var splitBtns = document.querySelectorAll ? document.querySelectorAll(".split-btn:not(.mtf-sync-btn)") : [];

    for (var b = 0; b < splitBtns.length; b++) {
      splitBtns[b].classList.toggle("active", splitBtns[b].getAttribute("data-layout") === activeSplitLayout);
    }

    if (chartWrapper) {
      chartWrapper.classList.remove("layout-split-1x2", "layout-split-2x2");
    }

    if (activeSplitLayout === "1x2") {
      if (chartWrapper) chartWrapper.classList.add("layout-split-1x2");
      if (splitGrid) splitGrid.style.display = "contents";
      if (splitPane2) splitPane2.style.display = "flex";
      if (splitPane3) splitPane3.style.display = "none";
      if (splitPane4) splitPane4.style.display = "none";
      disposeSubPane(3);
      disposeSubPane(4);
      loadSubChart(2, paneSymbols[2] || "AMEX:SPY", paneTimeframes[2] || "1D");
    } else if (activeSplitLayout === "2x2") {
      if (chartWrapper) chartWrapper.classList.add("layout-split-2x2");
      if (splitGrid) splitGrid.style.display = "contents";
      if (splitPane2) splitPane2.style.display = "flex";
      if (splitPane3) splitPane3.style.display = "flex";
      if (splitPane4) splitPane4.style.display = "flex";
      loadSubChart(2, paneSymbols[2] || "AMEX:SPY", paneTimeframes[2] || "1D");
      loadSubChart(3, paneSymbols[3] || "NASDAQ:QQQ", paneTimeframes[3] || "1D");
      loadSubChart(4, paneSymbols[4] || ((symbolsList[1] && symbolsList[1].id !== activeSymbol) ? symbolsList[1].id : "NASDAQ:FAST"), paneTimeframes[4] || "1D");
    } else {
      if (splitGrid) splitGrid.style.display = "none";
      disposeSubPane(2);
      disposeSubPane(3);
      disposeSubPane(4);
    }

    setFocusedPane(1);

    setTimeout(function() {
      resizeAllCharts();
      renderDrawingCanvas();
    }, 100);
  }

// Application State
  function safeSetStorage(key, val) {
    try {
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(key, val);
      }
    } catch (e) {
      // Safely ignore Safari private browsing or storage quota errors
    }
  }

  function safeGetStorage(key, fallback) {
    try {
      if (typeof localStorage !== "undefined") {
        var v = localStorage.getItem(key);
        return v !== null ? v : fallback;
      }
    } catch (e) {}
    return fallback;
  }

  function readStoredJson(key, validate, fallback) {
    try {
      if (typeof localStorage === "undefined" || !key) return fallback;
      var raw = localStorage.getItem(key);
      if (raw === null || raw === undefined) return fallback;
      var parsed;
      var parseFailed = false;
      try {
        parsed = JSON.parse(raw);
      } catch (eP) {
        parseFailed = true;
      }
      if (parseFailed) {
        try {
          if (typeof raw === "string" && raw.length > 0 && !localStorage.getItem("tv_bak_" + key)) {
            localStorage.setItem("tv_bak_" + key, raw);
            console.warn("[storage] 無效的儲存設定 " + key + "，已備份至 tv_bak_" + key);
          }
        } catch (eBak) {}
        return fallback;
      }

      var validResult = false;
      try {
        validResult = typeof validate === "function" ? validate(parsed) : true;
      } catch (eV) {
        validResult = false;
      }

      if (!validResult) {
        try {
          if (typeof raw === "string" && raw.length > 0 && !localStorage.getItem("tv_bak_" + key)) {
            localStorage.setItem("tv_bak_" + key, raw);
            console.warn("[storage] 無效的儲存設定 " + key + "，已備份至 tv_bak_" + key);
          }
        } catch (eBak) {}
        return fallback;
      }

      if (validResult && typeof validResult === "object") {
        var cleaned = validResult;
        if (JSON.stringify(cleaned) !== JSON.stringify(parsed)) {
          try {
            if (typeof raw === "string" && raw.length > 0 && !localStorage.getItem("tv_bak_" + key)) {
              localStorage.setItem("tv_bak_" + key, raw);
              console.warn("[storage] 已清理無效設定項 " + key + "，原始資料備份在 tv_bak_" + key);
            }
          } catch (eBak) {}
          return cleaned;
        }
        return parsed;
      }

      return parsed;
    } catch (e) {
      return fallback;
    }
  }
  if (typeof window !== "undefined") window.readStoredJson = readStoredJson;
  if (typeof globalThis !== "undefined") globalThis.readStoredJson = readStoredJson;

  var desktopPort = (function() {
    try {
      if (typeof window !== "undefined") {
        if (window.__DESKTOP_PORT__) return parseInt(window.__DESKTOP_PORT__, 10);
        if (window.location && window.location.search) {
          var params = new URLSearchParams(window.location.search);
          var p = params.get("port");
          if (p && /^\d+$/.test(p)) return parseInt(p, 10);
        }
      }
    } catch (e) {
      // Safely ignore URL or environment read errors
    }
    return null;
  })();

  var apiBase = (function() {
    try {
      if (typeof window !== "undefined") {
        if (window.__DESKTOP_API_BASE__) return String(window.__DESKTOP_API_BASE__).replace(/\/+$/, "");
        if (desktopPort && (window.location.protocol === "tauri:" || !window.location.port || window.location.port !== String(desktopPort))) {
          return "http://127.0.0.1:" + desktopPort;
        }
        if (window.location && (window.location.protocol === "file:" || !window.location.host)) {
          return "http://127.0.0.1:8000";
        }
      }
    } catch (e) {
      // Safely ignore sandbox origin access errors
    }
    return "";
  })();

  var sessionToken = (function() {
    try {
      if (typeof window !== "undefined") {
        var tok = null;
        if (window.location && window.location.search) {
          var params = new URLSearchParams(window.location.search);
          tok = params.get("token");
        }
        if (!tok && window.__DESKTOP_TOKEN__) {
          tok = window.__DESKTOP_TOKEN__;
        }
        if (tok && typeof sessionStorage !== "undefined") {
          try { sessionStorage.setItem("tv_session_token", tok); } catch (e) {}
        } else if (!tok && typeof sessionStorage !== "undefined") {
          try { tok = sessionStorage.getItem("tv_session_token"); } catch (e) {}
        }
        return tok || null;
      }
    } catch (e) {
      // Safely ignore URL or storage access errors in restricted sandbox
    }
    return null;
  })();

  var _backendReadyQueue = [];
  var _isBackendReady = false;

  function isTauriEnvironment() {
    return typeof window !== "undefined" && Boolean(
      window.__TAURI__ || (window.location && window.location.protocol === "tauri:")
    );
  }

  function whenBackendReady(fn) {
    if (typeof fn !== "function") return;
    if (!isTauriEnvironment() || _isBackendReady || apiBase) {
      fn();
    } else {
      _backendReadyQueue.push(fn);
    }
  }

  function flushBackendReadyQueue() {
    _isBackendReady = true;
    while (_backendReadyQueue.length > 0) {
      var cb = _backendReadyQueue.shift();
      try {
        cb();
      } catch (err) {
        console.warn("[whenBackendReady] callback error:", err);
      }
    }
  }

  if (typeof window !== "undefined") {
    window.whenBackendReady = whenBackendReady;
    window._backendReadyQueue = _backendReadyQueue;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.whenBackendReady = whenBackendReady;
    globalThis._backendReadyQueue = _backendReadyQueue;
  }

  if (typeof window !== "undefined") {
    window.initDesktopSession = function(port, token) {
      if (port) {
        desktopPort = parseInt(port, 10);
        apiBase = "http://127.0.0.1:" + desktopPort;
        try { sessionStorage.setItem("tv_desktop_port", String(desktopPort)); } catch (e) {}
      }
      if (token) {
        sessionToken = String(token);
        try { sessionStorage.setItem("tv_session_token", sessionToken); } catch (e) {}
      }
      flushBackendReadyQueue();
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (!ws || ws.readyState !== 1) {
        if (ws) {
          try {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onclose = null;
            ws.onerror = null;
            ws.close();
          } catch (e) {}
          ws = null;
        }
        connectWebSocket();
      }
    };

    try {
      var tauriInvoke = null;
      if (window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.invoke === "function") {
        tauriInvoke = window.__TAURI__.core.invoke;
      } else if (window.__TAURI__ && typeof window.__TAURI__.invoke === "function") {
        tauriInvoke = window.__TAURI__.invoke;
      }
      if (tauriInvoke) {
        var sessionAttempts = 0;
        function tryGetSession() {
          sessionAttempts++;
          try {
            var res = tauriInvoke("get_session_info");
            Promise.resolve(res)
              .then(function(session) {
                if (session && session.port) {
                  window.initDesktopSession(session.port, session.token);
                  if (typeof loadHistory === "function" && activeSymbol) {
                    loadHistory(activeSymbol);
                  }
                } else {
                  retryGetSession();
                }
              })
              .catch(function(e) {
                retryGetSession();
              });
          } catch (e) {
            retryGetSession();
          }
        }
        function retryGetSession() {
          if (sessionAttempts < 10) {
            var retryTimer = setTimeout(tryGetSession, 500);
            if (retryTimer && typeof retryTimer.unref === "function") retryTimer.unref();
          } else {
            if (statusText) statusText.textContent = "無法連線到後端";
            if (statusBadge) statusBadge.className = "status-badge disconnected";
          }
        }
        tryGetSession();
      }
    } catch (e) {}
  }

  if (typeof window !== "undefined" && window.fetch) {
    var _rawFetch = window.fetch;
    window.fetch = function(resource, init) {
      init = init || {};
      var target = resource;
      if (typeof resource === "string") {
        if (apiBase && resource.indexOf("/api/") === 0) {
          target = apiBase + resource;
        }
      } else if (resource && typeof resource.url === "string") {
        if (apiBase && resource.url.indexOf("/api/") === 0) {
          target = new Request(apiBase + resource.url, resource);
        }
      }

      var targetUrlStr = typeof target === "string" ? target : (target && target.url ? target.url : "");
      var isLocalApi = targetUrlStr.indexOf("/api/") === 0 || (apiBase && targetUrlStr.indexOf(apiBase + "/api/") === 0) || targetUrlStr.indexOf(window.location.origin + "/api/") === 0;
      if (sessionToken && isLocalApi) {
        init.headers = init.headers || {};
        if (typeof Headers !== "undefined" && init.headers instanceof Headers) {
          if (!init.headers.has("X-Session-Token")) {
            init.headers.append("X-Session-Token", sessionToken);
          }
        } else if (Array.isArray(init.headers)) {
          init.headers.push(["X-Session-Token", sessionToken]);
        } else {
          init.headers["X-Session-Token"] = sessionToken;
        }
      }
      return _rawFetch.call(this, target, init);
    };
  }

  var renderDrawingCanvas = function() {};
  var drawingRafId = null;
  function scheduleDrawingRender() {
    if (drawingRafId) return;
    var schedule = typeof requestAnimationFrame === "function"
      ? requestAnimationFrame
      : function(cb) { return setTimeout(cb, 16); };
    drawingRafId = schedule(function() {
      drawingRafId = null;
      if (typeof renderDrawingCanvas === "function") {
        renderDrawingCanvas();
      }
    });
  }
  var lastCrosshairPrice = null;
  var magnetMode = (typeof localStorage !== "undefined" && localStorage.getItem("tv_magnet_mode") === "true");
  var isAlwaysOnTop = (typeof localStorage !== "undefined" && localStorage.getItem("tv_always_on_top") === "true");
  var tvPriceLineHandles = [];
  var currentDrawTool = "cursor";
  var drawings = [];
  var drawingInProgress = null;
  var activeDragDraw = null;
  var isDraggingHandle = null;

  var _lastCoordClose = null;
  var _lastCoordPlus = null;
  var _lastCoordMinus = null;
  var _coordLoopTimer = null;

  function checkDrawingCoordinateShift() {
    if (typeof document !== "undefined" && document.hidden) return;
    if (!drawings || drawings.length === 0) return;
    if (!candleSeries || typeof candleSeries.priceToCoordinate !== "function") return;

    var refPrice = (latestBarData && typeof latestBarData.close === "number")
      ? latestBarData.close
      : (typeof lastCrosshairPrice === "number" ? lastCrosshairPrice : null);
    if (refPrice === null || !isFinite(refPrice) || refPrice <= 0) return;

    var cClose = candleSeries.priceToCoordinate(refPrice);
    var cPlus = candleSeries.priceToCoordinate(refPrice * 1.05);
    var cMinus = candleSeries.priceToCoordinate(refPrice * 0.95);

    if (cClose === null || cPlus === null || cMinus === null) return;

    if (_lastCoordClose !== null) {
      if (Math.abs(cClose - _lastCoordClose) > 0.5 ||
          Math.abs(cPlus - _lastCoordPlus) > 0.5 ||
          Math.abs(cMinus - _lastCoordMinus) > 0.5) {
        scheduleDrawingRender();
      }
    }
    _lastCoordClose = cClose;
    _lastCoordPlus = cPlus;
    _lastCoordMinus = cMinus;
  }

  function startDrawingCoordLoop() {
    if (_coordLoopTimer) return;
    if (typeof setInterval === "undefined") return;
    _coordLoopTimer = setInterval(function() {
      checkDrawingCoordinateShift();
    }, 100);
    if (_coordLoopTimer && typeof _coordLoopTimer.unref === "function") {
      _coordLoopTimer.unref();
    }
  }

  function resetDrawToolToCursor() {
    currentDrawTool = "cursor";
    drawingInProgress = null;
    activeDragDraw = null;
    var allDrawBtns = document.querySelectorAll ? document.querySelectorAll(".draw-tool-btn[data-tool]:not(.draw-tool-toggle)") : [];
    for (var dti = 0; dti < allDrawBtns.length; dti++) {
      allDrawBtns[dti].classList.toggle("active", allDrawBtns[dti].getAttribute("data-tool") === "cursor");
    }
    var dwc = document.getElementById("drawingCanvas");
    if (dwc) dwc.classList.remove("drawing-active");
  }

  function restorePriceLinesForSymbol(sym) {
    var lw = window.LightweightCharts || (typeof LightweightCharts !== "undefined" ? LightweightCharts : null);
    if (!candleSeries || !lw || !sym) return;
    try {
      var lines = readStoredJson("tv_price_lines_" + sym, function(val) {
        if (!Array.isArray(val)) return false;
        var cleaned = [];
        for (var i = 0; i < val.length; i++) {
          var item = val[i];
          if (item && typeof item === "object") {
            if (typeof item.price === "number" || (typeof item.price === "string" && item.price.trim().length > 0)) {
              var p = Number(item.price);
              if (typeof p === "number" && isFinite(p)) {
                cleaned.push(Object.assign({}, item, { price: p }));
              }
            }
          }
        }
        return cleaned;
      }, []);
      if (Array.isArray(lines)) {
        for (var i = 0; i < lines.length; i++) {
          var item = lines[i];
          var line = candleSeries.createPriceLine({
            price: item.price,
            color: item.isAlert ? "#f59e0b" : "#3b82f6",
            lineWidth: 1,
            lineStyle: item.isAlert ? lw.LineStyle.Dotted : lw.LineStyle.Dashed,
            axisLabelVisible: true,
            title: item.isAlert ? "ALERT (未啟用提醒)" : "LVL"
          });
          tvPriceLineHandles.push(line);
        }
      }
    } catch (e) {}
  }

  function _addQuickPriceLine(isAlert) {
    var lw = window.LightweightCharts || (typeof LightweightCharts !== "undefined" ? LightweightCharts : null);
    if (!candleSeries || !lw) return;
    var p = (typeof lastCrosshairPrice === "number" && isFinite(lastCrosshairPrice)) ? lastCrosshairPrice : (latestBarData ? latestBarData.close : null);
    if (p === null) return;
    var line = candleSeries.createPriceLine({
      price: p,
      color: isAlert ? "#f59e0b" : "#3b82f6",
      lineWidth: 1,
      lineStyle: isAlert ? lw.LineStyle.Dotted : lw.LineStyle.Dashed,
      axisLabelVisible: true,
      title: isAlert ? "ALERT (未啟用提醒)" : "LVL"
    });
    tvPriceLineHandles.push(line);
    if (activeSymbol) {
      try {
        var existing = safeGetStorage("tv_price_lines_" + activeSymbol, null);
        var list = existing ? JSON.parse(existing) : [];
        if (!Array.isArray(list)) list = [];
        list.push({ price: p, isAlert: Boolean(isAlert) });
        safeSetStorage("tv_price_lines_" + activeSymbol, JSON.stringify(list));
      } catch (e) {}
    }
  }

  /** 清理並驗證繪圖物件陣列，剔除無效屬性或損壞座標 */
  function sanitizeDrawings(list) {
    if (!Array.isArray(list)) return [];
    var clean = [];
    for (var i = 0; i < list.length; i++) {
      var item = list[i];
      if (!item || typeof item !== "object" || typeof item.type !== "string") {
        continue;
      }
      if (item.type === "hline") {
        var p = Number(item.price);
        if (!isFinite(p)) continue;
        var hCopy = Object.assign({}, item);
        hCopy.price = p;
        clean.push(hCopy);
      } else if (item.type === "trendline") {
        if (!isValidChartTime(item.time1) || !isValidChartTime(item.time2)) continue;
        var p1 = Number(item.price1);
        var p2 = Number(item.price2);
        if (!isFinite(p1) || !isFinite(p2)) continue;
        var tCopy = Object.assign({}, item);
        tCopy.price1 = p1;
        tCopy.price2 = p2;
        clean.push(tCopy);
      } else {
        clean.push(Object.assign({}, item));
      }
    }
    return clean;
  }
  if (typeof window !== "undefined") window.sanitizeDrawings = sanitizeDrawings;
  if (typeof globalThis !== "undefined") globalThis.sanitizeDrawings = sanitizeDrawings;

  function loadDrawingsForSymbol(sym) {
    try {
      if (typeof localStorage !== "undefined" && sym) {
        var raw = localStorage.getItem("tv_drawings_" + sym);
        var parsed = [];
        var parseFailed = false;
        if (raw) {
          try {
            parsed = JSON.parse(raw);
          } catch (eP) {
            parseFailed = true;
            parsed = [];
          }
        }
        var clean = sanitizeDrawings(parsed);
        if (raw && (parseFailed || JSON.stringify(clean) !== JSON.stringify(parsed))) {
          var bakKey = "tv_bak_drawings_" + sym;
          if (!localStorage.getItem(bakKey)) {
            localStorage.setItem(bakKey, raw);
          }
          console.warn("[drawings] 已移除無法繪製的繪圖，原始資料備份在 " + bakKey);
        }
        return clean;
      }
    } catch (e) {}
    return [];
  }

  function saveDrawingsForActiveSymbol() {
    try {
      if (typeof localStorage !== "undefined" && activeSymbol) {
        localStorage.setItem("tv_drawings_" + activeSymbol, JSON.stringify(drawings));
      }
      if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
    } catch (e) {}
  }

  function normalizeBarTime(t) {
    if (typeof t === "number") {
      return Math.floor(t > 1e11 ? t / 1000 : t);
    }
    if (typeof t === "string") {
      var num = Number(t);
      if (!isNaN(num) && num > 0) {
        return Math.floor(num > 1e11 ? num / 1000 : num);
      }
      var d = new Date(t);
      if (!isNaN(d.getTime())) {
        return Math.floor(d.getTime() / 1000);
      }
    }
    return 0;
  }

  var DEFAULT_WATCHLIST_SEEDS = [
    { id: "NASDAQ:NVDA", name: "NVIDIA Corp.", exchange: "NASDAQ", pair: "NVDA", type: "stock", sort_order: 0, sector: "Technology", industry: "Semiconductors" },
    { id: "NASDAQ:FAST", name: "Fastenal Company", exchange: "NASDAQ", pair: "FAST", type: "stock", sort_order: 1, sector: "Industrials", industry: "Industrial Distribution" },
    { id: "NASDAQ:PRCH", name: "Porch Group Inc.", exchange: "NASDAQ", pair: "PRCH", type: "stock", sort_order: 2, sector: "Technology", industry: "Software - Application" },
    { id: "AMEX:SPY", name: "SPDR S&P 500 ETF Trust", exchange: "AMEX", pair: "SPY", type: "etf", sort_order: 3, sector: "Index ETF", industry: "Large Cap Blend" },
    { id: "NASDAQ:QQQ", name: "Invesco QQQ Trust", exchange: "NASDAQ", pair: "QQQ", type: "etf", sort_order: 4, sector: "Index ETF", industry: "Large Cap Growth" },
    { id: "NASDAQ:AAPL", name: "Apple Inc.", exchange: "NASDAQ", pair: "AAPL", type: "stock", sort_order: 5, sector: "Technology", industry: "Consumer Electronics" },
    { id: "BINANCE:BTCUSDT", name: "Bitcoin", exchange: "Binance", pair: "BTC/USDT", type: "crypto", sort_order: 6, sector: "Cryptocurrency", industry: "Layer 1" },
    { id: "BINANCE:ETHUSDT", name: "Ethereum", exchange: "Binance", pair: "ETH/USDT", type: "crypto", sort_order: 7, sector: "Cryptocurrency", industry: "Smart Contract" },
    { id: "BINANCE:SOLUSDT", name: "Solana", exchange: "Binance", pair: "SOL/USDT", type: "crypto", sort_order: 8, sector: "Cryptocurrency", industry: "Layer 1" }
  ];

  var symbolsList = (function() {
    var list = readStoredJson("tv_symbols_cache", function(val) {
      return Array.isArray(val) && val.every(function(x) {
        return x && typeof x === "object" && typeof x.id === "string" && x.id.trim().length > 0;
      });
    }, DEFAULT_WATCHLIST_SEEDS.slice());
    if (!Array.isArray(list) || list.length === 0) {
      list = DEFAULT_WATCHLIST_SEEDS.slice();
    }
    // Auto-heal any stale NYSE:PRCH entries to official NASDAQ:PRCH
    var healed = false;
    for (var k = 0; k < list.length; k++) {
      if (list[k] && list[k].id === "NYSE:PRCH") {
        list[k].id = "NASDAQ:PRCH";
        list[k].exchange = "NASDAQ";
        healed = true;
      }
    }
    if (healed) {
      safeSetStorage("tv_symbols_cache", JSON.stringify(list));
    }
    return list;
  })();

  var watchlistGroups = (function() {
    return readStoredJson("tv_watchlist_groups", function(val) {
      if (val === null || typeof val !== "object" || Array.isArray(val)) return false;
      var vals = Object.values ? Object.values(val) : Object.keys(val).map(function(k) { return val[k]; });
      return vals.every(function(v) { return typeof v === "string"; });
    }, {});
  })();
  var pinnedSymbols = (function() {
    return readStoredJson("tv_pinned_symbols", function(val) {
      return Array.isArray(val) && val.every(function(s) {
        return typeof s === "string" && s.trim().length > 0;
      });
    }, []);
  })();
  var activeWlGroup = "all";

  function togglePinSymbol(symbolId) {
    if (!symbolId) return;
    var idx = pinnedSymbols.indexOf(symbolId);
    if (idx >= 0) {
      pinnedSymbols.splice(idx, 1);
    } else {
      pinnedSymbols.push(symbolId);
    }
    safeSetStorage("tv_pinned_symbols", JSON.stringify(pinnedSymbols));
    if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
    if (typeof renderWatchlist === "function") renderWatchlist();
  }

  function getOrderedWatchlistSymbols(filterGroup) {
    var grp = filterGroup || activeWlGroup;
    var list = symbolsList || [];
    if (grp && grp !== "all") {
      list = list.filter(function(s) {
        return watchlistGroups[s.id] === grp;
      });
    }
    var pinSet = new Set(pinnedSymbols || []);
    var pinned = [];
    var unpinned = [];
    for (var i = 0; i < list.length; i++) {
      if (pinSet.has(list[i].id)) {
        pinned.push(list[i]);
      } else {
        unpinned.push(list[i]);
      }
    }
    pinned.sort(function(a, b) {
      return pinnedSymbols.indexOf(a.id) - pinnedSymbols.indexOf(b.id);
    });
    return {
      pinned: pinned,
      unpinned: unpinned,
      all: pinned.concat(unpinned)
    };
  }

  var activeSymbol = "BINANCE:BTCUSDT";
  var activeTimeframe = "1m";
  var rsBenchmarkTag = safeGetStorage("tv_rs_benchmark", "SPY");
  var currentCandleStyle = safeGetStorage("tv_candle_style", "solid");
  var isAutoScale = true;
  var symbolStates = {};
  var currentRawBars = [];
  var loadedHistorySymbol = null;
  if (typeof window !== "undefined") window.loadedHistorySymbol = loadedHistorySymbol;
  if (typeof globalThis !== "undefined") globalThis.loadedHistorySymbol = loadedHistorySymbol;
  var benchmarkRawBars = [];
  var benchmarkLoadState = { key: null, inFlight: false, failedAt: 0, promise: null };
  var benchmarkReqSeq = 0;
  var subPaneBenchmarkCache = {};
  if (typeof window !== "undefined") {
    window.benchmarkLoadState = benchmarkLoadState;
    window.subPaneBenchmarkCache = subPaneBenchmarkCache;
    window.resolveBenchmarkSymbol = resolveBenchmarkSymbol;
  }
  var tickCount = 0;
  var lastPrices = {};
  var sessionHighLow = {};
  var ws = null;
  var rsChart = null;
  var rsLinePaneSeries = null;
  var rsEmaPaneSeries = null;
  var rsZeroPriceLine = null;
  var isSyncingRange = false;

// Reconnection State
  var reconnectTimer = null;
  var reconnectAttempts = 0;
  var BASE_RECONNECT_DELAY = 1000;
  var MAX_RECONNECT_DELAY = 30000;

// Concurrency, History Sequence & Monotonicity State
  var TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1D": 86400,
    "1W": 604800
  };

  function alignBarBucket(barTime, tf) {
    var bt = (typeof barTime === "number" && barTime > 1e11) ? Math.floor(barTime / 1000) : Number(barTime);
    if (!Number.isFinite(bt) || bt <= 0) return barTime;
    if (tf === "1W") {
      // Unix Epoch (1970-01-01) was Thursday. Monday 00:00:00 UTC offset is 3 days (259200 seconds).
      return Math.floor((bt + 259200) / 604800) * 604800 - 259200;
    }
    var tfSec = TF_SECONDS[tf] || 60;
    return Math.floor(bt / tfSec) * tfSec;
  }
  if (typeof window !== "undefined") {
    window.alignBarBucket = alignBarBucket;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.alignBarBucket = alignBarBucket;
  }

  // ==========================================================================
  // K 棒時間與分桶（第 4 段）
  // ==========================================================================
  // 美股（NASDAQ、NYSE、AMEX、ARCA、NYSEARCA、BATS、OTC，或沒有交易所前綴的代號）以紐約交易時段為準：
  // 09:30 開盤、1h K 從 :30 起算、日 K 只計正規交易時段。
  // 其他市場（加密貨幣、外匯、期貨等可能 24 小時交易的市場）以 UTC 為準，沿用 alignBarBucket。
  // 已知限制：不處理美股假日（只依週一至週五判斷）。
  // 假設：美股日 K／週 K 的時間戳落在紐約交易日當天（00:00–16:00 ET），Yahoo、Polygon、Alpaca 都符合。

  var MARKET_TZ_NY = "America/New_York";
  var US_SESSION_OPEN_MIN = 570;   // 09:30
  var US_SESSION_CLOSE_MIN = 960;  // 16:00
  var _zonedFormatterCache = {};
  var _S4_WEEKDAYS = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  var _S4_MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function _s4IsCryptoSymbol(symbol) {
    var s = String(symbol || "").toUpperCase();
    return s.indexOf("BINANCE:") === 0 || s.indexOf("USDT") !== -1 || s.indexOf("BUSD") !== -1;
  }

  var _S4_US_EQUITY_PREFIXES = { NASDAQ: true, NYSE: true, AMEX: true, ARCA: true, NYSEARCA: true, BATS: true, OTC: true };

  /** 是否套用美股交易時段規則：只限美股交易所前綴；沒有前綴的代號視為美股（與後端預設 NASDAQ 一致）。 */
  function _s4IsUsEquitySymbol(symbol) {
    var s = String(symbol || "").toUpperCase();
    if (!s || _s4IsCryptoSymbol(s)) return false;
    var idx = s.indexOf(":");
    if (idx === -1) return true;
    return _S4_US_EQUITY_PREFIXES[s.slice(0, idx)] === true;
  }

  /** 標的的交易所時區：美股為 "America/New_York"，其他（加密貨幣、外匯、期貨等）為 "UTC"。 */
  function getMarketTimeZone(symbol) {
    return _s4IsUsEquitySymbol(symbol) ? MARKET_TZ_NY : "UTC";
  }

  /** 把秒或毫秒時間戳正規化成整數秒；無效時回傳 null。 */
  function _s4NormalizeEpoch(t) {
    var n = (typeof t === "string" && t.trim() !== "") ? Number(t) : t;
    if (typeof n !== "number" || !isFinite(n) || n <= 0) return null;
    if (n > 1e11) n = n / 1000;
    return Math.floor(n);
  }

  /** 回傳某個 UTC 秒數在指定時區的年月日時分秒與星期（0 = 週日）。 */
  function getZonedParts(epochSec, tz) {
    var key = tz || "UTC";
    var fmt = _zonedFormatterCache[key];
    if (!fmt) {
      fmt = new Intl.DateTimeFormat("en-US", {
        timeZone: key,
        hourCycle: "h23",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        weekday: "short"
      });
      _zonedFormatterCache[key] = fmt;
    }
    var parts = fmt.formatToParts(new Date(epochSec * 1000));
    var out = {};
    for (var i = 0; i < parts.length; i++) out[parts[i].type] = parts[i].value;
    var hour = parseInt(out.hour, 10);
    if (hour === 24) hour = 0;
    return {
      year: parseInt(out.year, 10),
      month: parseInt(out.month, 10),
      day: parseInt(out.day, 10),
      hour: hour,
      minute: parseInt(out.minute, 10),
      second: parseInt(out.second, 10),
      weekday: _S4_WEEKDAYS[out.weekday]
    };
  }

  /** 把指定時區的牆上時間轉成 UTC 秒數（會處理夏令時間）。 */
  function zonedWallToEpoch(year, month, day, hour, minute, tz) {
    var wallAsUtc = Date.UTC(year, month - 1, day, hour, minute, 0) / 1000;
    var guess = wallAsUtc;
    for (var i = 0; i < 3; i++) {
      var p = getZonedParts(guess, tz);
      var seenAsUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000;
      var diff = wallAsUtc - seenAsUtc;
      if (diff === 0) break;
      guess += diff;
    }
    return guess;
  }

  function _s4AddDays(year, month, day, delta) {
    var d = new Date(Date.UTC(year, month - 1, day) + delta * 86400000);
    return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
  }

  function computeYtdStart(nowSec, symbol) {
    var tz = getMarketTimeZone(symbol);
    var parts = getZonedParts(nowSec, tz);
    return zonedWallToEpoch(parts.year, 1, 1, 0, 0, tz);
  }
  if (typeof window !== "undefined") {
    window.computeYtdStart = computeYtdStart;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.computeYtdStart = computeYtdStart;
  }

  function _s4DateKey(d) {
    return d.year * 10000 + d.month * 100 + d.day;
  }

  function _s4Compare(bucket, lastBarTime) {
    if (lastBarTime === null) return { action: "new", time: bucket, reason: null };
    if (bucket === lastBarTime) return { action: "update", time: bucket, reason: null };
    if (bucket > lastBarTime) return { action: "new", time: bucket, reason: null };
    return { action: "ignore", time: null, reason: "stale" };
  }

  /**
   * 決定一筆即時資料（1 分鐘 K 棒或報價）屬於目前週期的哪一根 K 棒。
   * @param {{symbol: string, timeframe: string, tickTime: number, lastBarTime: (number|null)}} params
   * @returns {{action: "update"|"new"|"ignore", time: (number|null), reason: (string|null)}}
   *   update：更新最後一根（time 等於 lastBarTime）；new：新增一根（time 為新 K 棒的時間）；
   *   ignore：丟棄（無效時間、比最後一根舊、或美股非正規交易時段且週期不是 1m）。
   */
  function computeLiveBucket(params) {
    var p = params || {};
    var tf = p.timeframe;
    var t = _s4NormalizeEpoch(p.tickTime);
    if (t === null) return { action: "ignore", time: null, reason: "invalid_time" };
    if (!TF_SECONDS[tf]) return { action: "ignore", time: null, reason: "unknown_timeframe" };
    var last = _s4NormalizeEpoch(p.lastBarTime);

    if (tf === "1m") {
      // 以最後一根 K 棒自己的起點為準：[last, last + 60) 屬於同一根（報價與未對齊整分鐘的歷史資料都適用）
      if (last !== null) {
        if (t < last) return { action: "ignore", time: null, reason: "stale" };
        if (t < last + 60) return { action: "update", time: last, reason: null };
        return { action: "new", time: last + Math.floor((t - last) / 60) * 60, reason: null };
      }
      return { action: "new", time: Math.floor(t / 60) * 60, reason: null };
    }
    if (!_s4IsUsEquitySymbol(p.symbol)) return _s4Compare(alignBarBucket(t, tf), last);

    var tz = MARKET_TZ_NY;
    var tp = getZonedParts(t, tz);
    var minOfDay = tp.hour * 60 + tp.minute;
    if (tp.weekday === 0 || tp.weekday === 6 || minOfDay < US_SESSION_OPEN_MIN || minOfDay >= US_SESSION_CLOSE_MIN) {
      return { action: "ignore", time: null, reason: "outside_session" };
    }

    if (tf === "5m" || tf === "15m" || tf === "1h") {
      var tfMin = TF_SECONDS[tf] / 60;
      var startMin = US_SESSION_OPEN_MIN + Math.floor((minOfDay - US_SESSION_OPEN_MIN) / tfMin) * tfMin;
      var bucket = zonedWallToEpoch(tp.year, tp.month, tp.day, Math.floor(startMin / 60), startMin % 60, tz);
      return _s4Compare(bucket, last);
    }

    // 1D / 1W：比對紐約交易日（週 K 比對該週週一）
    var tickDay = { year: tp.year, month: tp.month, day: tp.day };
    if (tf === "1W") tickDay = _s4AddDays(tp.year, tp.month, tp.day, -((tp.weekday + 6) % 7));
    var anchorHour = 9;
    var anchorMinute = 30;
    if (last !== null) {
      var lp = getZonedParts(last, tz);
      var lastDay = { year: lp.year, month: lp.month, day: lp.day };
      if (tf === "1W") lastDay = _s4AddDays(lp.year, lp.month, lp.day, -((lp.weekday + 6) % 7));
      if (_s4DateKey(tickDay) === _s4DateKey(lastDay)) return { action: "update", time: last, reason: null };
      if (_s4DateKey(tickDay) < _s4DateKey(lastDay)) return { action: "ignore", time: null, reason: "stale" };
      // 沿用歷史資料的時間戳慣例（例如 09:30 或 00:00 ET），新 K 棒才會和歷史對齊
      anchorHour = lp.hour;
      anchorMinute = lp.minute;
    }
    var newTime = zonedWallToEpoch(tickDay.year, tickDay.month, tickDay.day, anchorHour, anchorMinute, tz);
    if (last !== null && newTime <= last) return { action: "ignore", time: null, reason: "stale" };
    return { action: "new", time: newTime, reason: null };
  }

  /**
   * 把一根 1 分鐘 K 棒併入目前週期的 K 棒，並正確累加成交量。
   * 1 分鐘 K 棒在同一分鐘內會重複送出（成交量逐步增加），所以同一分鐘用「取代」、換分鐘才「累加」。
   * @param {Object|null} prevState 上一次的狀態（同一根 K 棒時才傳入，否則傳 null）
   * @param {{bucketTime: number, timeframe: string, minute: Object, historyBar: (Object|null)}} params
   *   historyBar：這根 K 棒在圖上原本的資料（來自歷史），用來保留開盤價與已有的成交量
   * @returns {Object|null} 新狀態（含 open/high/low/close/volume）；比目前分鐘還舊的資料回傳 null
   */
  function applyMinuteBarToBucket(prevState, params) {
    var p = params || {};
    var m = p.minute;
    var bucketTime = p.bucketTime;
    if (!m || typeof bucketTime !== "number") return null;
    var mVol = (typeof m.volume === "number" && isFinite(m.volume) && m.volume > 0) ? m.volume : 0;

    if (prevState && prevState.bucketTime === bucketTime) {
      var s = {
        bucketTime: bucketTime,
        open: prevState.open,
        high: Math.max(prevState.high, m.high, m.close),
        low: Math.min(prevState.low, m.low, m.close),
        close: m.close,
        baseVolume: prevState.baseVolume,
        doneVolume: prevState.doneVolume,
        minuteTime: prevState.minuteTime,
        minuteVolume: prevState.minuteVolume
      };
      if (m.time === prevState.minuteTime) {
        s.minuteVolume = mVol;
      } else if (m.time > prevState.minuteTime) {
        s.doneVolume = prevState.doneVolume + prevState.minuteVolume;
        s.minuteTime = m.time;
        s.minuteVolume = mVol;
      } else {
        return null;
      }
      s.volume = s.baseVolume + s.doneVolume + s.minuteVolume;
      return s;
    }

    var h = p.historyBar;
    var useHistory = Boolean(h && h.time === bucketTime);
    // 1m 週期時，歷史的最後一根就是同一分鐘，量不能再加一次
    var base = (useHistory && p.timeframe !== "1m" && typeof h.volume === "number" && isFinite(h.volume) && h.volume > 0) ? h.volume : 0;
    var st = {
      bucketTime: bucketTime,
      open: useHistory ? h.open : m.open,
      high: useHistory ? Math.max(h.high, m.high, m.close) : Math.max(m.high, m.close),
      low: useHistory ? Math.min(h.low, m.low, m.close) : Math.min(m.low, m.close),
      close: m.close,
      baseVolume: base,
      doneVolume: 0,
      minuteTime: m.time,
      minuteVolume: mVol
    };
    st.volume = st.baseVolume + st.doneVolume + st.minuteVolume;
    return st;
  }

  function _s4Pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /** 圖表傳入的時間不是時間戳時（BusinessDay 物件或日期字串），直接顯示日期，不顯示空白。 */
  function _s4FormatNonEpochTime(time) {
    if (time && typeof time === "object" && typeof time.year === "number") {
      return time.year + "-" + _s4Pad2(time.month) + "-" + _s4Pad2(time.day);
    }
    if (typeof time === "string") return time;
    return "";
  }

  /** 十字線上的時間文字：日 K／週 K 只顯示日期；盤中週期加上時區（ET 或 UTC）。 */
  function formatMarketTime(epochSec, symbol, timeframe) {
    var t = _s4NormalizeEpoch(epochSec);
    if (t === null) return _s4FormatNonEpochTime(epochSec);
    var tz = getMarketTimeZone(symbol);
    var p = getZonedParts(t, tz);
    var date = p.year + "-" + _s4Pad2(p.month) + "-" + _s4Pad2(p.day);
    if (timeframe === "1D" || timeframe === "1W") return date;
    return date + " " + _s4Pad2(p.hour) + ":" + _s4Pad2(p.minute) + (tz === "UTC" ? " UTC" : " ET");
  }

  /** 時間軸刻度文字（tickMarkType：0 年、1 月、2 日、3 時間、4 含秒的時間）。無法處理時回傳 null，讓圖表用預設格式。 */
  function formatMarketTickMark(epochSec, tickMarkType, symbol, lang) {
    var t = _s4NormalizeEpoch(epochSec);
    if (t === null) return null;
    var p = getZonedParts(t, getMarketTimeZone(symbol));
    if (tickMarkType === 0) return String(p.year);
    if (tickMarkType === 1) return lang === "en" ? _S4_MONTHS_EN[p.month - 1] : (p.month + "月");
    if (tickMarkType === 2) return String(p.day);
    if (tickMarkType === 4) return _s4Pad2(p.hour) + ":" + _s4Pad2(p.minute) + ":" + _s4Pad2(p.second);
    return _s4Pad2(p.hour) + ":" + _s4Pad2(p.minute);
  }

  /** 建立圖表用的時間格式化函式；getSymbol／getTimeframe 在每次格式化時才讀取，切換標的後自動生效。 */
  function makeChartTimeOptions(getSymbol, getTimeframe) {
    return {
      timeFormatter: function(time) {
        return formatMarketTime(time, getSymbol(), getTimeframe());
      },
      tickMarkFormatter: function(time, tickMarkType) {
        return formatMarketTickMark(time, tickMarkType, getSymbol(), typeof currentLang !== "undefined" ? currentLang : "zh");
      }
    };
  }

  if (typeof window !== "undefined") {
    window.getMarketTimeZone = getMarketTimeZone;
    window.getZonedParts = getZonedParts;
    window.zonedWallToEpoch = zonedWallToEpoch;
    window.computeLiveBucket = computeLiveBucket;
    window.applyMinuteBarToBucket = applyMinuteBarToBucket;
    window.formatMarketTime = formatMarketTime;
    window.formatMarketTickMark = formatMarketTickMark;
  }
  if (typeof globalThis !== "undefined") {
    globalThis.getMarketTimeZone = getMarketTimeZone;
    globalThis.getZonedParts = getZonedParts;
    globalThis.zonedWallToEpoch = zonedWallToEpoch;
    globalThis.computeLiveBucket = computeLiveBucket;
    globalThis.applyMinuteBarToBucket = applyMinuteBarToBucket;
    globalThis.formatMarketTime = formatMarketTime;
    globalThis.formatMarketTickMark = formatMarketTickMark;
  }

  var historyPrefetchCache = new Map();
  var PREFETCH_TTL_MS = 60000;
  var PREFETCH_MAX_SIZE = 12;

  function getCachedHistory(key) {
    var entry = historyPrefetchCache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.timestamp > PREFETCH_TTL_MS) {
      historyPrefetchCache.delete(key);
      return null;
    }
    return entry.data;
  }

  function setCachedHistory(key, data) {
    if (historyPrefetchCache.size >= PREFETCH_MAX_SIZE) {
      var oldestKey = historyPrefetchCache.keys().next().value;
      if (oldestKey) historyPrefetchCache.delete(oldestKey);
    }
    historyPrefetchCache.set(key, { data: data, timestamp: Date.now() });
  }

  function getActiveWatchlistSymbols() {
    return getOrderedWatchlistSymbols(activeWlGroup).all;
  }

  function navigateWatchlist(delta) {
    var list = getActiveWatchlistSymbols();
    if (!list || list.length === 0) return;
    var curIdx = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === activeSymbol) {
        curIdx = i;
        break;
      }
    }
    var nextIdx;
    if (curIdx === -1) {
      nextIdx = delta > 0 ? 0 : list.length - 1;
    } else {
      nextIdx = (curIdx + delta + list.length) % list.length;
    }
    var nextSym = list[nextIdx].id;
    if (nextSym) {
      switchSymbol(nextSym);
      setTimeout(function() {
        var activeEl = document.querySelector("#watchlistItems .wl-item.active");
        if (activeEl && typeof activeEl.scrollIntoView === "function") {
          activeEl.scrollIntoView({ block: "nearest" });
        }
      }, 50);
    }
  }

  function prefetchNextWatchlistSymbols(curSym, tf, engine) {
    var list = getActiveWatchlistSymbols();
    if (!list || list.length <= 1) return;
    var curIdx = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === curSym) {
        curIdx = i;
        break;
      }
    }
    if (curIdx === -1) return;

    var nextSymbols = [
      list[(curIdx + 1) % list.length].id,
      list[(curIdx + 2) % list.length].id
    ];

    for (var n = 0; n < nextSymbols.length; n++) {
      (function(sId) {
        if (!sId || sId === curSym) return;
        var pKey = sId + "|" + tf + "|" + engine;
        if (getCachedHistory(pKey)) return;

        var url = "/api/history?seed=true&timeframe=" + encodeURIComponent(tf) +
                  "&engine=" + encodeURIComponent(engine) +
                  "&symbol=" + encodeURIComponent(sId);
        fetch(url)
          .then(function(res) { return res.ok ? res.json() : null; })
          .then(function(data) {
            if (data && Array.isArray(data.bars) && data.bars.length > 0) {
              setCachedHistory(pKey, data);
            }
          })
          .catch(function() {});
      })(nextSymbols[n]);
    }
  }

  var lastPositionSizing = null;
  var lastIsSqueeze = false;

  var activeHistoryRequestId = 0;
  var historyAbortController = null;
  var isHistoryLoading = false;
  var lastBarTime = 0;
  var lastBarObj = null;
  var latestBarData = null;
  // 第 4 段：主圖目前這根 K 棒的即時累加狀態（成交量分「歷史量 + 已完成分鐘 + 目前分鐘」）
  var liveBucketState = null;
  // 第 4 段：本次啟動後使用者是否手動切換過週期（是的話，後端偏好不覆蓋）
  var userChangedTimeframe = false;

// Indicator Toggle States
  var defaultIndicatorConfig = {
    ema10: true,
    ema21: true,
    ema30: true,
    sma50: true,
    sma200: true,
    volSma: true,
    bollinger: false,
    rsLine: true,
    rsOverlayMain: false,
    vwap: false,
    avwap: true,
    atr: false,
    rsMode: "mansfield",
    showLegend: true,
    collapseLegend: false,
    showDrawingToolbar: false,
    showAnalysisTools: false,
    tvCompareRs: false
  };

  var defaultAvwapConfig = {
    anchorMode: "Swing Low",
    anchorLookback: 252,
    gapPctMin: 5.0,
    gapVolMin: 2.0,
    volMaLen: 50,
    manualAnchorDate: "2026-01-02",
    manualAnchorTime: "00:00",
    source: "hlc3",
    showBands: true,
    bandMult: 1.0,
    showAnchorLbl: true,
    showTable: true,
    displayMode: "overlay",
    colorAbove: "#26a69a",
    colorBelow: "#ef5350",
    bandColor: "#787b86",
    showBandFill: true
  };

  function loadSavedIndicatorConfig() {
    var saved = null;
    try {
      if (typeof localStorage !== "undefined") {
        var str = localStorage.getItem("tv_indicator_config");
        if (str) saved = JSON.parse(str);
      }
    } catch (e) {}
    var merged = {};
    for (var k in defaultIndicatorConfig) {
      if (defaultIndicatorConfig.hasOwnProperty(k)) {
        merged[k] = (saved && saved[k] !== undefined) ? saved[k] : defaultIndicatorConfig[k];
      }
    }
    return merged;
  }

  var prefSaveTimer = null;
  function savePreferencesDebounced() {
    if (prefSaveTimer) clearTimeout(prefSaveTimer);
    prefSaveTimer = setTimeout(function() {
      try {
        var payload = {
          indicatorConfig: indicatorConfig,
          avwapConfig: avwapConfig,
          activeSymbol: activeSymbol,
          activeTimeframe: activeTimeframe,
          rsBenchmarkTag: rsBenchmarkTag,
          currentEngine: currentEngine,
          currentLang: currentLang,
          currentTheme: currentTheme,
          currentCandleStyle: currentCandleStyle,
          alwaysOnTop: isAlwaysOnTop,
          magnetMode: magnetMode,
          watchlistGroups: watchlistGroups,
          pinnedSymbols: pinnedSymbols
        };
        var headers = { "Content-Type": "application/json" };
        if (sessionToken) headers["X-Session-Token"] = sessionToken;
        fetch("/api/preferences", {
          method: "POST",
          headers: headers,
          body: JSON.stringify(payload)
        }).catch(function(err) {
          console.debug("Preferences save non-critical error:", err);
        });
      } catch (e) {}
    }, 300);
  }

  function savePreferencesImmediate() {
    if (prefSaveTimer) {
      clearTimeout(prefSaveTimer);
      prefSaveTimer = null;
    }
    try {
      var payload = {
        indicatorConfig: indicatorConfig,
        avwapConfig: avwapConfig,
        activeSymbol: activeSymbol,
        activeTimeframe: activeTimeframe,
        rsBenchmarkTag: rsBenchmarkTag,
        currentEngine: currentEngine,
        currentLang: currentLang,
        currentTheme: currentTheme,
        currentCandleStyle: currentCandleStyle,
        alwaysOnTop: isAlwaysOnTop,
        magnetMode: magnetMode,
        watchlistGroups: watchlistGroups,
        pinnedSymbols: pinnedSymbols
      };
      var headers = { "Content-Type": "application/json" };
      if (sessionToken) headers["X-Session-Token"] = sessionToken;
      fetch("/api/preferences", {
        method: "POST",
        headers: headers,
        body: JSON.stringify(payload),
        keepalive: true
      }).catch(function(err) {
        console.debug("Preferences save immediate non-critical error:", err);
      });
    } catch (e) {}
  }

  function syncPreferencesFromBackend() {
    var headers = {};
    if (sessionToken) headers["X-Session-Token"] = sessionToken;
    fetch("/api/preferences", { headers: headers })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(res) {
        if (!res || !res.preferences) return;
        var p = res.preferences;
        var needsRefresh = false;
        if (p.indicatorConfig && typeof p.indicatorConfig === "object") {
          for (var k in defaultIndicatorConfig) {
            if (p.indicatorConfig[k] !== undefined) indicatorConfig[k] = p.indicatorConfig[k];
          }
          safeSetStorage("tv_indicator_config", JSON.stringify(indicatorConfig));
          needsRefresh = true;
        }
        if (p.avwapConfig && typeof p.avwapConfig === "object") {
          for (var ak in defaultAvwapConfig) {
            if (p.avwapConfig[ak] !== undefined) avwapConfig[ak] = p.avwapConfig[ak];
          }
          safeSetStorage("tv_avwap_config", JSON.stringify(avwapConfig));
          needsRefresh = true;
        }
        if (p.rsBenchmarkTag && typeof p.rsBenchmarkTag === "string") {
          rsBenchmarkTag = p.rsBenchmarkTag;
          safeSetStorage("tv_rs_benchmark", rsBenchmarkTag);
          var bSpan = document.getElementById("rsActiveBadge");
          if (bSpan) bSpan.textContent = "Ref: " + getSymbolDisplayName(rsBenchmarkTag);
          var rTitle = document.getElementById("rsPaneTitle");
          if (rTitle) rTitle.textContent = "RS Line vs " + getSymbolDisplayName(rsBenchmarkTag);
          var inputBench = document.getElementById("rsBenchmarkCustomInput");
          if (inputBench) inputBench.value = rsBenchmarkTag;
        }
        if (p.activeTimeframe && typeof p.activeTimeframe === "string" && p.activeTimeframe !== activeTimeframe) {
          // 第 4 段：走正規的切換流程（更新按鈕、重新載入圖表），不再只改變數。
          // 使用者本次已手動切換過週期時，以使用者為準，不覆蓋。
          if (!userChangedTimeframe && TF_SECONDS[p.activeTimeframe]) {
            var prefTfBtn = document.querySelector ? document.querySelector('.tf-btn[data-tf="' + p.activeTimeframe + '"]') : null;
            if (prefTfBtn && typeof prefTfBtn.onclick === "function") {
              prefTfBtn.onclick();
            }
          }
        }
        if (p.currentCandleStyle && typeof p.currentCandleStyle === "string" && p.currentCandleStyle !== currentCandleStyle) {
          currentCandleStyle = p.currentCandleStyle;
          safeSetStorage("tv_candle_style", currentCandleStyle);
          if (typeof updateCandleStyleUI === "function") updateCandleStyleUI();
        }
        if (p.currentLang && typeof p.currentLang === "string" && p.currentLang !== currentLang) {
          currentLang = p.currentLang;
          safeSetStorage("tv_terminal_lang", currentLang);
          if (typeof setLanguage === "function") setLanguage(currentLang, true);
        }
        if (p.currentTheme && (p.currentTheme === "light" || p.currentTheme === "dark") && p.currentTheme !== currentTheme) {
          currentTheme = p.currentTheme;
          safeSetStorage("tv_terminal_theme", currentTheme);
          if (typeof setTheme === "function") setTheme(currentTheme, true);
        }
        if (p.alwaysOnTop !== undefined) {
          isAlwaysOnTop = Boolean(p.alwaysOnTop);
          safeSetStorage("tv_always_on_top", String(isAlwaysOnTop));
          var pinBtn = document.getElementById("btnPinWindow");
          if (pinBtn) {
            pinBtn.classList.toggle("active", isAlwaysOnTop);
            pinBtn.setAttribute("title", isAlwaysOnTop ? "取消視窗置頂 (Pinned on Top)" : "視窗置頂 (Always on Top)");
          }
          if (window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.invoke === "function") {
            window.__TAURI__.core.invoke("set_always_on_top", { enabled: isAlwaysOnTop }).catch(function() {});
          }
        }
        if (p.magnetMode !== undefined) {
          magnetMode = Boolean(p.magnetMode);
          safeSetStorage("tv_magnet_mode", String(magnetMode));
          var mgBtn = document.getElementById("btnMagnet");
          if (mgBtn) mgBtn.classList.toggle("active", magnetMode);
        }
        if (p.watchlistGroups && typeof p.watchlistGroups === "object") {
          watchlistGroups = Object.assign({}, p.watchlistGroups);
          safeSetStorage("tv_watchlist_groups", JSON.stringify(watchlistGroups));
          if (typeof renderWatchlist === "function") renderWatchlist();
        }
        if (Array.isArray(p.pinnedSymbols)) {
          pinnedSymbols = p.pinnedSymbols.slice();
          safeSetStorage("tv_pinned_symbols", JSON.stringify(pinnedSymbols));
          if (typeof renderWatchlist === "function") renderWatchlist();
        }
        if (needsRefresh) {
          syncIndicatorCheckboxes();
          applyIndicatorVisibilities();
          if (currentRawBars && currentRawBars.length > 0) {
            calcAndRenderAvwap();
          }
          if (currentEngine === "C" || p.currentEngine === "C" || p.currentEngine === "tradingview") {
            if (typeof initTradingViewWidget === "function") initTradingViewWidget(activeSymbol);
          }
        }
      })
      .catch(function(err) {
        console.debug("Backend preferences fetch skipped:", err);
      });
  }

  function saveIndicatorConfig() {
    safeSetStorage("tv_indicator_config", JSON.stringify(indicatorConfig));
    savePreferencesDebounced();
  }

  function loadSavedAvwapConfig() {
    var saved = null;
    try {
      if (typeof localStorage !== "undefined") {
        var str = localStorage.getItem("tv_avwap_config");
        if (str) saved = JSON.parse(str);
      }
    } catch (e) {}
    var merged = {};
    for (var k in defaultAvwapConfig) {
      if (defaultAvwapConfig.hasOwnProperty(k)) {
        merged[k] = (saved && saved[k] !== undefined) ? saved[k] : defaultAvwapConfig[k];
      }
    }
    return merged;
  }

  function saveAvwapConfig() {
    safeSetStorage("tv_avwap_config", JSON.stringify(avwapConfig));
    savePreferencesDebounced();
  }

  var indicatorConfig = loadSavedIndicatorConfig();
  var avwapConfig = loadSavedAvwapConfig();
  var currentAvwapData = null;

// App Nap Energy-Saving & RAF State
  var isAppNapSuspended = typeof document !== "undefined" && Boolean(document.hidden);
  var appNapHiddenAt = 0;
  var rafRenderId = null;
  var hasPendingChartUpdate = false;

// Internationalization (i18n) Dictionary
  var i18n = {
    en: {
      langLabel: "繁中",
      langToggleTitle: "Switch to Traditional Chinese",
      themeLight: "Light",
      themeDark: "Dark",
      themeToggleTitle: "Toggle Light / Dark theme",
      brandSub: "Deepvue Pro Engine",
      connecting: "CONNECTING",
      connected: "CONNECTED",
      disconnected: "DISCONNECTED",
      reconnecting: "RECONNECTING",
      equityBannerText: "Equity Symbol: Bars accumulate in real time from server launch (market hours only).",
      watchlistTitle: "WATCHLIST",
      searchPlaceholder: "Search stock, crypto or ETF (e.g. AAPL, NVDA, SPY)...",
      indicatorsBtn: "Indicators",
      indicatorsTitle: "Indicators & Overlays",
      candleSolid: "🕯️ Solid",
      candleHollow: "🕯️ Hollow",
      candleSolidTitle: "Solid Candles (Click to switch to Hollow)",
      candleHollowTitle: "Hollow Candles (Click to switch to Solid)",
      rsToggleTitle: "Toggle RS Sub-pane",
      rsBenchmarkLabel: "RS",
      rsModeMansfield: "Mansfield RS (0-Axis)",
      rsModeDeepvue: "Deepvue RS",
      refBadge: "Ref: ",
      hud52w: "52W High",
      hudVol50: "Vol/50D",
      hudAdr: "ADR%",
      change24h: "24h Change",
      high24h: "24h High",
      low24h: "24h Low",
      vol24h: "24h Vol",
      tabAll: "All",
      tabStocks: "Stocks",
      tabEtfs: "ETFs",
      tabCrypto: "Crypto",
      volSqueeze: "Volatility Squeeze",
      posSuggest: "Rec: {shares} shares (${stop} stop)",
      posSuggestEmpty: "Rec: -- shares",
      noResults: "No matching symbols found. Press Enter to add custom ticker.",
      done: "Done",
      setTag: "Set Tag",
      customTag: "+ Tag",
      engineTag: "Engine:",
      enginePro: "Deepvue Pro (Auto Dual-Source)",
      engineTv: "TradingView Official Widget",
      pinTitle: "Pin Window (Always on Top)",
      unpinTitle: "Unpin Window (Pinned on Top)",
      ctxRemove: "Remove from Watchlist",
      ctxPin: "Pin to Top",
      ctxUnpin: "Unpin from Top",
      pinBtnTitle: "Pin to top",
      unpinBtnTitle: "Unpin from top",
      ctxBenchmark: "Set as RS Benchmark",
      ctxCopy: "Copy Symbol",
      rsNewHighBadge: "🔵 RS NH",
      rsNewHighTooltip: "RS New High before Price (Leading Indicator)",
      grpAll: "All",
      grpLeaders: "Leaders",
      grpVcp: "VCP",
      grpPullback: "Pullback",
      grpEarnings: "Earnings",
      ctxGroupHeader: "Assign Watchlist Group",
      ctxGrpNone: "Clear Group",
      ctxGrpLeaders: "Leaders (Relative Strength)",
      ctxGrpVcp: "VCP (Volatility Contraction)",
      ctxGrpPullback: "Pullback (Support Retest)",
      ctxGrpEarnings: "Earnings (Catalyst)",
      heatmapBtn: "Theme",
      heatmapModalTitle: "🔥 Theme Heatmap",
      hmUnivThemes: "Themes",
      hmUnivWatchlist: "Watchlist",
      hmMetricChange: "1D %",
      hmMetricRs: "RS Rating",
      hmSortChange: "Performance",
      hmSortRs: "RS",
      heatmapLoading: "Loading theme metrics...",
      heatmapEmpty: "No theme data found",
      heatmapFooterTip: "💡 Click any ticker box to switch main chart; box width scales with 50D volume ratio, hover for detailed metrics",
      screenerBtn: "Screener",
      batchAddTier12: "⭐ Batch Add Tier 1–2",
    },
    zh: {
      langLabel: "EN",
      langToggleTitle: "切換為英文介面 (English)",
      themeLight: "白天",
      themeDark: "黑夜",
      themeToggleTitle: "切換白天 / 黑夜模式",
      brandSub: "Deepvue 專業成長股引擎",
      connecting: "連線中",
      connected: "已連線",
      disconnected: "連線中斷",
      reconnecting: "重新連線中",
      equityBannerText: "美股標的：伺服器啟動後即時累積分K，盤中持續更新。",
      watchlistTitle: "自選清單",
      searchPlaceholder: "搜尋美股、加密貨幣或 ETF (如 AAPL, NVDA, SPY)...",
      indicatorsBtn: "指標",
      indicatorsTitle: "技術指標與均線疊加",
      candleSolid: "🕯️ 實心",
      candleHollow: "🕯️ 實空心",
      candleSolidTitle: "當前為實心蠟燭 (點擊切換為實空心蠟燭)",
      candleHollowTitle: "當前為實空心蠟燭 (點擊切換為實心蠟燭)",
      rsToggleTitle: "切換底部 RS 副圖",
      rsBenchmarkLabel: "RS",
      rsModeMansfield: "Mansfield RS (0軸)",
      rsModeDeepvue: "Deepvue RS",
      refBadge: "基準: ",
      hud52w: "距52週高",
      hudVol50: "50天均量比",
      hudAdr: "日均振幅 ADR",
      change24h: "24小時漲跌",
      high24h: "24小時最高",
      low24h: "24小時最低",
      vol24h: "24小時成交量",
      tabAll: "全部",
      tabStocks: "美股",
      tabEtfs: "ETF",
      tabCrypto: "加密貨幣",
      volSqueeze: "波動收縮 (Volatility Squeeze)",
      posSuggest: "建議: {shares} 股 (${stop} 停損)",
      posSuggestEmpty: "建議: -- 股",
      noResults: "查無相符標的。按 Enter 可自訂新增代碼。",
      done: "完成",
      setTag: "設定",
      customTag: "+ 標籤",
      engineTag: "引擎:",
      enginePro: "Deepvue 專業圖表 (自動高可用)",
      engineTv: "TradingView 官方組件",
      pinTitle: "視窗置頂 (Always on Top)",
      unpinTitle: "取消視窗置頂 (Pinned on Top)",
      ctxRemove: "從自選清單移除",
      ctxPin: "釘選置頂",
      ctxUnpin: "取消釘選",
      pinBtnTitle: "釘選到頂部",
      unpinBtnTitle: "取消釘選",
      ctxBenchmark: "設為 RS 基準",
      ctxCopy: "複製代碼",
      rsNewHighBadge: "🔵 RS NH",
      rsNewHighTooltip: "RS 領先股價創新高 (強勢潛力訊號)",
      grpAll: "全部",
      grpLeaders: "領頭羊",
      grpVcp: "收縮型態",
      grpPullback: "拉回支撐",
      grpEarnings: "財報催化",
      ctxGroupHeader: "指派自選分組",
      ctxGrpNone: "無分組 (清除)",
      ctxGrpLeaders: "領頭羊 (Leaders)",
      ctxGrpVcp: "收縮型態 (VCP)",
      ctxGrpPullback: "拉回支撐 (Pullback)",
      ctxGrpEarnings: "財報催化 (Earnings)",
      heatmapBtn: "主題熱圖",
      heatmapModalTitle: "🔥 美股主題板塊熱力圖",
      hmUnivThemes: "美股主題",
      hmUnivWatchlist: "自選清單",
      hmMetricChange: "1D 漲跌%",
      hmMetricRs: "RS 相對強度",
      hmSortChange: "漲幅",
      hmSortRs: "RS",
      heatmapLoading: "正在載入主題板塊數據...",
      heatmapEmpty: "未發現板塊資料",
      heatmapFooterTip: "💡 點選任一個股方塊可直接切換主圖；方塊寬度依 50D 成交量放大，滑鼠懸停可查看完整量化指標",
      screenerBtn: "智能選股",
      batchAddTier12: "⭐ 批次加入 Tier 1–2 至自選",
    }
  };

  var CHART_THEMES = {
    dark: {
      background: "#131722",
      textColor: "#787b86",
      subTextColor: "#9ca3af",
      grid: "#1e222d",
      border: "#2a2e39",
      crosshair: "#363c4e",
      crosshairLabelBg: "#2a2e39",
      tvToolbarBg: "#131722",
      anchorBg: "rgba(30, 34, 45, 0.95)",
      anchorText: "#ffffff",
      rrTitle: "#ffffff"
    },
    light: {
      background: "#f6f7f9",
      textColor: "#5f6b7c",
      subTextColor: "#475569",
      grid: "#e6ebf2",
      border: "#d4dce7",
      crosshair: "#94a3b8",
      crosshairLabelBg: "#1e293b",
      tvToolbarBg: "#ffffff",
      anchorBg: "rgba(255, 255, 255, 0.95)",
      anchorText: "#0f172a",
      rrTitle: "#0f172a"
    }
  };

  var currentLang = (typeof localStorage !== "undefined" && localStorage.getItem("tv_terminal_lang")) ? localStorage.getItem("tv_terminal_lang") : "zh";
  var currentTheme = (typeof localStorage !== "undefined" && localStorage.getItem("tv_terminal_theme") === "light") ? "light" : "dark";

  function getChartThemePalette() {
    return CHART_THEMES[currentTheme] || CHART_THEMES.dark;
  }

  function updateThemeToggleUI() {
    var dict = i18n[currentLang] || i18n.en;
    var btn = document.getElementById("themeToggleBtn");
    var iconEl = document.getElementById("themeIcon");
    var labelEl = document.getElementById("themeLabel");
    var isLight = currentTheme === "light";
    if (iconEl) iconEl.textContent = isLight ? "🌙" : "☀️";
    if (labelEl) labelEl.textContent = isLight ? dict.themeDark : dict.themeLight;
    if (btn) btn.setAttribute("title", dict.themeToggleTitle);
  }

  function applyThemeToCharts() {
    var p = getChartThemePalette();
    var commonOpts = {
      layout: { background: { color: p.background }, textColor: p.textColor },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      crosshair: {
        vertLine: { color: p.crosshair, labelBackgroundColor: p.crosshairLabelBg },
        horzLine: { color: p.crosshair, labelBackgroundColor: p.crosshairLabelBg }
      },
      rightPriceScale: { borderColor: p.border },
      timeScale: { borderColor: p.border }
    };
    if (chart && typeof chart.applyOptions === "function") {
      try { chart.applyOptions(commonOpts); } catch (e) {}
    }
    if (rsChart && typeof rsChart.applyOptions === "function") {
      try { rsChart.applyOptions(commonOpts); } catch (e) {}
    }
    for (var paneKey in subCharts) {
      if (subCharts[paneKey] && subCharts[paneKey].chart && typeof subCharts[paneKey].chart.applyOptions === "function") {
        try {
          subCharts[paneKey].chart.applyOptions({
            layout: { background: { color: p.background }, textColor: p.subTextColor },
            grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
            rightPriceScale: { borderColor: p.border },
            timeScale: { borderColor: p.border }
          });
        } catch (e) {}
      }
      if (subCharts[paneKey] && subCharts[paneKey].rsChart && typeof subCharts[paneKey].rsChart.applyOptions === "function") {
        try {
          subCharts[paneKey].rsChart.applyOptions({
            layout: { background: { color: p.background }, textColor: p.subTextColor },
            grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
            rightPriceScale: { borderColor: p.border },
            timeScale: { borderColor: p.border }
          });
        } catch (e) {}
      }
    }
    if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
  }

  function setTheme(theme, skipSave) {
    currentTheme = (theme === "light") ? "light" : "dark";
    safeSetStorage("tv_terminal_theme", currentTheme);
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.setAttribute("data-theme", currentTheme);
    }
    updateThemeToggleUI();
    applyThemeToCharts();
    if (typeof renderHeatmap === "function" && heatmapDataCache) {
      renderHeatmap();
    }
    if (!skipSave) {
      if (currentEngine === "C" && typeof initTradingViewWidget === "function") {
        initTradingViewWidget(activeSymbol);
        if (activeSplitLayout !== "1x1" && typeof applySplitLayout === "function") {
          applySplitLayout(activeSplitLayout);
        }
      }
      if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
    }
  }
  var currentEngine = (typeof localStorage !== "undefined" && localStorage.getItem("tv_chart_engine")) ? localStorage.getItem("tv_chart_engine") : "A";
  var currentPriceScaleMode = "normal";
  var tvWidgetInstance = null;

// Cached DOM Elements
  var chartContainer = document.getElementById("chartContainer");
  var priceContainer = document.getElementById("chart") || chartContainer;
  var tickerTape = document.getElementById("tickerTape");
  var statusBadge = document.getElementById("statusBadge");
  var statusText = document.getElementById("statusText");
  var tickCounter = document.getElementById("tickCounter");

// Active Symbol Metrics
  var activeExchangeEl = document.getElementById("activeExchange");
  var activeNameEl = document.getElementById("activeName");
  var activePriceEl = document.getElementById("activePrice");
  var activeChangeEl = document.getElementById("activeChange");
  var activeHighEl = document.getElementById("activeHigh");
  var activeLowEl = document.getElementById("activeLow");
  var activeVolumeEl = document.getElementById("activeVolume");
  var equityBanner = document.getElementById("equityBanner");
  var omniboxDisplay = document.getElementById("omniboxDisplay");
  var rsActiveBadge = document.getElementById("rsActiveBadge");

// Crosshair Legend Elements
  var legendSymbolEl = document.getElementById("legendSymbol");
  var legendIntervalEl = document.getElementById("legendInterval");
  var legendOpenEl = document.getElementById("legendOpen");
  var legendHighEl = document.getElementById("legendHigh");
  var legendLowEl = document.getElementById("legendLow");
  var legendCloseEl = document.getElementById("legendClose");
  var legendVolEl = document.getElementById("legendVol");
  var chartWatermarkEl = document.getElementById("chartWatermark");

  var legEma10 = document.getElementById("legEma10");
  var legEma21 = document.getElementById("legEma21");
  var legSma50 = document.getElementById("legSma50");
  var legSma200 = document.getElementById("legSma200");
  var legRs = document.getElementById("legRs");

// Sidebar & Modals
  watchlistItemsEl = document.getElementById("watchlistItems");
  var openSearchBtn = document.getElementById("openSearchBtn");
  var addSymbolBtn = document.getElementById("addSymbolBtn");
  var searchModal = document.getElementById("searchModal");
  var searchInput = document.getElementById("searchInput");
  var closeSearchModal = document.getElementById("closeSearchModal");
  var searchResultsList = document.getElementById("searchResultsList");
  var indicatorsBtn = document.getElementById("indicatorsBtn");
  var indicatorModal = document.getElementById("indicatorModal");
  var closeIndicatorModal = document.getElementById("closeIndicatorModal");
  var closeIndicatorModalBottom = document.getElementById("closeIndicatorModalBottom");
  var rsBenchmarkCustomInput = document.getElementById("rsBenchmarkCustomInput");
  var applyRsBenchmarkBtn = document.getElementById("applyRsBenchmarkBtn");

// Fundamentals Modal & Deepvue Split-Pane Elements
  var fundamentalsBtn = document.getElementById("fundamentalsBtn");
  var fundamentalsModal = document.getElementById("fundamentalsModal");
  var closeFundamentalsModal = document.getElementById("closeFundamentalsModal");
  var refreshFundamentalsBtn = document.getElementById("refreshFundamentalsBtn");
  var fundamentalsStatus = document.getElementById("fundamentalsStatus");
  var fundamentalsSymbolTag = document.getElementById("fundamentalsSymbolTag");
  var fund52wHigh = document.getElementById("fund52wHigh");
  var fund52wLow = document.getElementById("fund52wLow");
  var fundPctFromHigh = document.getElementById("fundPctFromHigh");
  var fundPE = document.getElementById("fundPE");
  var fundEPS = document.getElementById("fundEPS");
  var fundRevGrowth = document.getElementById("fundRevGrowth");
  var fundamentalsModelTag = document.getElementById("fundamentalsModelTag");
  var fundamentalsAiContent = document.getElementById("fundamentalsAiContent");
  var rsPane = document.getElementById("rsPane");
  var rsPaneTitle = document.getElementById("rsPaneTitle");
  var rsRatingBadge = document.getElementById("rsRatingBadge");
  var rsChartEl = document.getElementById("rsChart");

// AI Agent Lens Modal Elements
  var aiLensBtn = document.getElementById("aiLensBtn");
  var aiLensBtnLabel = document.getElementById("aiLensBtnLabel");
  var aiLensModal = document.getElementById("aiLensModal");
  var aiLensModalTitle = document.getElementById("aiLensModalTitle");
  var aiLensSymbolBadge = document.getElementById("aiLensSymbolBadge");
  var closeAiLensModal = document.getElementById("closeAiLensModal");
  var closeAiLensFooterBtn = document.getElementById("closeAiLensFooterBtn");
  var refreshAiLensBtn = document.getElementById("refreshAiLensBtn");
  var aiLensStatus = document.getElementById("aiLensStatus");
  var aiLensBias = document.getElementById("aiLensBias");
  var aiLensConfidence = document.getElementById("aiLensConfidence");
  var aiLensConfidenceFill = document.getElementById("aiLensConfidenceFill");
  var aiLensRegime = document.getElementById("aiLensRegime");
  var aiLensBarTime = document.getElementById("aiLensBarTime");
  var aiLensEma = document.getElementById("aiLensEma");
  var aiLensSma = document.getElementById("aiLensSma");
  var aiLensAdr = document.getElementById("aiLensAdr");
  var aiLensVolRatio = document.getElementById("aiLensVolRatio");
  var aiLensHighDist = document.getElementById("aiLensHighDist");
  var aiLensRsRating = document.getElementById("aiLensRsRating");
  var aiLensSetup = document.getElementById("aiLensSetup");
  var aiLensInvalidation = document.getElementById("aiLensInvalidation");
  var aiLensFailureMode = document.getElementById("aiLensFailureMode");
  var aiLensFundamentals = document.getElementById("aiLensFundamentals");
  var aiLensHumanNote = document.getElementById("aiLensHumanNote");
  var aiLensModelNotice = document.getElementById("aiLensModelNotice");

// Stock Theme Heatmap Elements & State
  var heatmapBtn = document.getElementById("heatmapBtn");
  var heatmapBtnLabel = document.getElementById("heatmapBtnLabel");
  var heatmapModal = document.getElementById("heatmapModal");
  var heatmapModalTitle = document.getElementById("heatmapModalTitle");
  var heatmapBenchBadge = document.getElementById("heatmapBenchBadge");
  var closeHeatmapModal = document.getElementById("closeHeatmapModal");
  var closeHeatmapFooterBtn = document.getElementById("closeHeatmapFooterBtn");
  var heatmapRefreshBtn = document.getElementById("heatmapRefreshBtn");
  var heatmapLoading = document.getElementById("heatmapLoading");
  var heatmapLoadingText = document.getElementById("heatmapLoadingText");
  var heatmapGrid = document.getElementById("heatmapGrid");
  var heatmapEmptyState = document.getElementById("heatmapEmptyState");
  var heatmapEmptyTitle = document.getElementById("heatmapEmptyTitle");
  var heatmapFooterTip = document.getElementById("heatmapFooterTip");
  var activeHeatmapUniverse = "themes";
  var activeHeatmapMetric = "change";
  var activeHeatmapSort = "change";
  var heatmapDataCache = null;
  var isHeatmapLoading = false;

// Indicator checkboxes
  var chkEma10 = document.getElementById("chkEma10");
  var chkEma21 = document.getElementById("chkEma21");
  var chkSma50 = document.getElementById("chkSma50");
  var chkSma200 = document.getElementById("chkSma200");
  var chkVolSma = document.getElementById("chkVolSma");
  var chkBollinger = document.getElementById("chkBollinger");
  var chkRsLine = document.getElementById("chkRsLine");

// Persistent Element Maps for O(1) in-place DOM updates
  var tapeElements = {};
  var sidebarElements = {};

// ==========================================================================
// Safe Numerical Helpers
// ==========================================================================

function isValidPrice(v) {
  return typeof v === "number" && !isNaN(v) && isFinite(v) && v > 0;
}
if (typeof window !== "undefined") {
  window.isValidPrice = isValidPrice;
}

function safeNum(val, fallback) {
  if (fallback === undefined) fallback = 0;
  if (val === undefined || val === null || val === "") return fallback;
  var n = Number(val);
  return typeof n === "number" && !isNaN(n) && isFinite(n) ? n : fallback;
}

function formatPrice(price, symbol) {
  var n = Number(price);
  if (typeof n !== "number" || isNaN(n) || !isFinite(n) || n <= 0) return "--";

  if (symbol && symbol.indexOf("BTC") !== -1) {
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (n >= 1000) {
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (n < 0.1) {
    return n.toFixed(4);
  }
  return n.toFixed(2);
}

function formatVolume(vol) {
  var v = safeNum(vol, 0);
  if (v <= 0) return "--";
  if (v >= 1000000) return (v / 1000000).toFixed(2) + "M";
  if (v >= 1000) return (v / 1000).toFixed(1) + "k";
  return v.toFixed(2);
}

function getSymbolDisplayName(symbol) {
  if (!symbol) return "";
  var parts = symbol.split(":");
  return parts.length > 1 ? parts[1] : symbol;
}

function getSymbolItem(symbol) {
  for (var i = 0; i < symbolsList.length; i++) {
    if (symbolsList[i].id === symbol) return symbolsList[i];
  }
  return null;
}

// ==========================================================================
// 共用數值工具（第 0 段）
// ==========================================================================

/**
 * 將輸入轉成數字：只接受 number 或非空的數字字串；布林、物件、空字串一律視為 NaN。
 */
function _s0ToNumber(v) {
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() !== "") return Number(v);
  return NaN;
}

/**
 * 回傳數字的十進位小數位數（支援 1e-7 這類科學記號）。無法判斷時回傳 -1。
 */
function _s0DecimalPlaces(x) {
  var s = String(x).toLowerCase();
  var m = /^-?(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/.exec(s);
  if (!m) return -1;
  var frac = m[2] ? m[2].length : 0;
  var exp = m[3] ? parseInt(m[3], 10) : 0;
  return Math.max(0, frac - exp);
}

/**
 * 以十進位整數相減計算停損距離，避免 2.02 - 2.01 = 0.010000000000000009 這類浮點誤差。
 * 回傳 { distance, scale, units }；無法安全縮放時 scale 為 0，distance 為原始浮點差。
 */
function _s0ExactDistance(a, b) {
  var d = Math.max(_s0DecimalPlaces(a), _s0DecimalPlaces(b));
  if (d >= 0 && d <= 10) {
    var scale = Math.pow(10, d);
    var ia = Math.round(a * scale);
    var ib = Math.round(b * scale);
    if (Number.isSafeInteger(ia) && Number.isSafeInteger(ib)) {
      var units = Math.abs(ia - ib);
      return { distance: units / scale, scale: scale, units: units };
    }
  }
  return { distance: Math.abs(a - b), scale: 0, units: 0 };
}

/**
 * 向下取整，但容忍浮點誤差：若結果與最接近的整數相差在容忍值以內，視為該整數。
 * relTol：整數單位相除時用 1e-12（只剩一次除法的誤差），否則用 1e-9。
 * 容忍值上限 1e-6，確保大數量（例如 1.6e10 單位的小幣）不會被錯誤進位而超出風險金額。
 */
function _s0FloorWithTolerance(x, relTol) {
  var nearest = Math.round(x);
  var tol = Math.min(1e-6, Math.max(1, Math.abs(x)) * relTol);
  if (Math.abs(x - nearest) <= tol) return nearest;
  return Math.floor(x);
}

/**
 * 計算部位股數與停損距離
 *
 * @param {Object} params
 * @param {number} params.entry - 進場價格 (> 0)
 * @param {number} params.stop - 停損價格 (> 0)
 * @param {number} params.riskDollar - 預期承受之風險金額 (> 0)
 * @param {"long"|"short"} [params.side="long"] - 交易方向
 * @returns {{shares: number, stopDistance: number, riskDollar: number, error: string|null}}
 *   stopDistance 以十進位整數相減計算；shares 向下取整並容忍浮點誤差（相對 1e-9）。
 *   entry、stop、riskDollar 只接受 number 或數字字串，布林等其他型別回傳 error。
 */
function computePositionSize(params) {
  if (!params || typeof params !== "object") {
    return { shares: 0, stopDistance: 0, riskDollar: 0, error: "Invalid parameters: expected object" };
  }

  var entry = _s0ToNumber(params.entry);
  var stop = _s0ToNumber(params.stop);
  var risk = _s0ToNumber(params.riskDollar);
  var rawSide = typeof params.side === "string" ? params.side.trim().toLowerCase() : "long";
  var side = rawSide || "long";

  if (!Number.isFinite(entry) || entry <= 0) {
    return { shares: 0, stopDistance: 0, riskDollar: Number.isFinite(risk) ? risk : 0, error: "Invalid entry price: must be positive finite number" };
  }
  if (!Number.isFinite(stop) || stop <= 0) {
    return { shares: 0, stopDistance: 0, riskDollar: Number.isFinite(risk) ? risk : 0, error: "Invalid stop price: must be positive finite number" };
  }
  if (!Number.isFinite(risk) || risk <= 0) {
    return { shares: 0, stopDistance: 0, riskDollar: 0, error: "Invalid risk dollar: must be positive finite number" };
  }
  if (side !== "long" && side !== "short") {
    return { shares: 0, stopDistance: 0, riskDollar: risk, error: "Invalid side: must be \"long\" or \"short\"" };
  }
  if (entry === stop) {
    return { shares: 0, stopDistance: 0, riskDollar: risk, error: "Entry price and stop price cannot be equal" };
  }

  if (side === "long") {
    if (stop >= entry) {
      return { shares: 0, stopDistance: 0, riskDollar: risk, error: "Stop price must be strictly below entry price for long positions" };
    }
  } else {
    if (stop <= entry) {
      return { shares: 0, stopDistance: 0, riskDollar: risk, error: "Stop price must be strictly above entry price for short positions" };
    }
  }

  var dist = _s0ExactDistance(entry, stop);
  var stopDistance = dist.distance;
  if (stopDistance <= 0) {
    return { shares: 0, stopDistance: 0, riskDollar: risk, error: "Stop distance must be strictly greater than 0" };
  }

  // 有十進位縮放時用整數單位相除（500 / 0.01 → 500 * 100 / 1），否則直接相除
  var ratio = dist.scale > 0 ? (risk * dist.scale) / dist.units : risk / stopDistance;
  var shares = _s0FloorWithTolerance(ratio, dist.scale > 0 ? 1e-12 : 1e-9);
  if (shares < 0) shares = 0;

  return {
    shares: shares,
    stopDistance: stopDistance,
    riskDollar: risk,
    error: null
  };
}

/**
 * 規範價格格式化工具
 * 依價格大小決定小數位數與有效位數，無效值回傳 "—"
 *
 * @param {number|string|null|undefined} value - 欲格式化之數值
 * @param {string} [symbol] - 標的識別碼（保留介面，目前不影響格式，未來可依 pricescale 擴充）
 * @returns {string} 格式化後的價格字串或 "—"
 */
function canonicalFormatPrice(value, symbol) {
  if (value === null || value === undefined || value === "") return "—";
  var n = _s0ToNumber(value);
  if (!Number.isFinite(n) || n === 0) return "—";

  var abs = Math.abs(n);
  // 依「四捨五入後」的值決定區間，避免 0.99996 顯示成 "1.0000"
  var band;
  if (abs >= 1 || Math.round(abs * 1e4) / 1e4 >= 1) {
    band = "2dp";
  } else if (abs >= 0.1 || Number(abs.toPrecision(4)) >= 0.1) {
    band = "4dp";
  } else {
    band = "4sig";
  }

  if (band === "2dp") {
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(n);
  } else if (band === "4dp") {
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 4,
      maximumFractionDigits: 4
    }).format(n);
  }
  // |value| < 0.1: 4 位有效數字 (例如 0.00001234, 0.05000)
  return new Intl.NumberFormat("en-US", {
    minimumSignificantDigits: 4,
    maximumSignificantDigits: 4
  }).format(n);
}

function computePriceFormatForPrice(price) {
  var p = Number(price);
  if (isNaN(p) || !isFinite(p) || p <= 0) {
    return { precision: 2, minMove: 0.01 };
  }
  var absP = Math.abs(p);
  if (absP >= 1) {
    return { precision: 2, minMove: 0.01 };
  }
  if (absP >= 0.1) {
    return { precision: 4, minMove: 0.0001 };
  }
  var exp = Math.floor(Math.log10(absP));
  var prec = Math.min(10, Math.max(4, -exp + 3));
  var minMove = Number(Math.pow(10, -prec).toFixed(prec));
  return { precision: prec, minMove: minMove };
}

function applyPriceFormatToCandleSeries(price) {
  if (!candleSeries || typeof candleSeries.applyOptions !== "function") return;
  var fmt = computePriceFormatForPrice(price);
  try {
    candleSeries.applyOptions({
      priceFormat: {
        type: "price",
        precision: fmt.precision,
        minMove: fmt.minMove
      }
    });
  } catch (e) {}
}

if (typeof window !== "undefined") {
  window.computePositionSize = computePositionSize;
  window.canonicalFormatPrice = canonicalFormatPrice;
  window.computePriceFormatForPrice = computePriceFormatForPrice;
  window.applyPriceFormatToCandleSeries = applyPriceFormatToCandleSeries;
}
if (typeof globalThis !== "undefined") {
  globalThis.computePositionSize = computePositionSize;
  globalThis.canonicalFormatPrice = canonicalFormatPrice;
  globalThis.computePriceFormatForPrice = computePriceFormatForPrice;
  globalThis.applyPriceFormatToCandleSeries = applyPriceFormatToCandleSeries;
}

// ==========================================================================
// 部位計算與 R:R（第 11 段）
// ==========================================================================

var lastAtrSizingInput = null;

function parseRiskDollar(raw) {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "boolean" || typeof raw === "object") return null;
  var n;
  if (typeof raw === "string") {
    var trimmed = raw.trim();
    if (trimmed === "") return null;
    n = Number(trimmed);
  } else if (typeof raw === "number") {
    n = raw;
  } else {
    return null;
  }
  if (!isFinite(n) || isNaN(n) || n <= 0 || n > 10000000) return null;
  return n;
}

function getRiskDollar() {
  if (typeof document !== "undefined" && typeof document.getElementById === "function") {
    var inputEl = document.getElementById("scRiskDollar");
    if (inputEl && inputEl.value !== undefined) {
      var fromInput = parseRiskDollar(inputEl.value);
      if (fromInput !== null) return fromInput;
    }
  }
  if (typeof safeGetStorage === "function") {
    var fromStorage = parseRiskDollar(safeGetStorage("tv_risk_dollar", ""));
    if (fromStorage !== null) return fromStorage;
  }
  return 500;
}

function setRiskDollar(value) {
  var parsed = parseRiskDollar(value);
  if (parsed === null) return false;
  if (typeof safeSetStorage === "function") {
    safeSetStorage("tv_risk_dollar", String(parsed));
  }
  return true;
}

function computeRiskRewardView(params) {
  function invalid(errMsg) {
    return {
      side: null,
      error: errMsg,
      rewardPct: null,
      riskPct: null,
      rr: null,
      shares: 0,
      stopDistance: null
    };
  }

  if (!params || typeof params !== "object") {
    return invalid("invalid_input");
  }

  var entry = params.entry;
  var target = params.target;
  var stop = params.stop;
  var riskDollar = params.riskDollar;

  if (
    typeof entry !== "number" || !isFinite(entry) || entry <= 0 ||
    typeof target !== "number" || !isFinite(target) || target <= 0 ||
    typeof stop !== "number" || !isFinite(stop) || stop <= 0
  ) {
    return invalid("invalid_price");
  }

  var side = null;
  if (target > entry && stop < entry) {
    side = "long";
  } else if (target < entry && stop > entry) {
    side = "short";
  } else {
    return invalid("停損與目標必須在進場價兩側");
  }

  var pos = computePositionSize({
    entry: entry,
    stop: stop,
    riskDollar: riskDollar,
    side: side
  });

  if (!pos || pos.error) {
    return invalid(pos && pos.error ? pos.error : "invalid_position_size");
  }

  var rewardPct = (Math.abs(target - entry) / entry) * 100;
  var riskPct = (Math.abs(entry - stop) / entry) * 100;
  if (!isFinite(riskPct) || riskPct <= 0) {
    return invalid("invalid_risk");
  }

  return {
    side: side,
    error: null,
    rewardPct: rewardPct,
    riskPct: riskPct,
    rr: rewardPct / riskPct,
    shares: pos.shares,
    stopDistance: pos.stopDistance
  };
}

function computeAtrPositionSizing(params) {
  if (!params || typeof params !== "object") {
    return { shares: 0, stopDistance: null, error: "invalid_input" };
  }

  if (params.timeframe !== "1D") {
    return { shares: 0, stopDistance: null, error: "daily_only" };
  }

  var close = params.close;
  var atr = params.atr;
  var stopMult = (params.stopMult !== undefined && params.stopMult !== null) ? params.stopMult : 2.0;

  if (
    typeof close !== "number" || !isFinite(close) || close <= 0 ||
    typeof atr !== "number" || !isFinite(atr) || atr <= 0 ||
    typeof stopMult !== "number" || !isFinite(stopMult) || stopMult <= 0
  ) {
    return { shares: 0, stopDistance: null, error: "invalid_input" };
  }

  var stopPrice = close - atr * stopMult;
  if (!isFinite(stopPrice) || stopPrice <= 0) {
    return { shares: 0, stopDistance: null, error: "stop_below_zero" };
  }

  var pos = computePositionSize({
    entry: close,
    stop: stopPrice,
    riskDollar: params.riskDollar,
    side: "long"
  });

  return {
    shares: pos.shares,
    stopDistance: pos.error ? null : pos.stopDistance,
    error: pos.error
  };
}

function parseManualQuantity(raw) {
  if (typeof raw !== "number" && typeof raw !== "string") return null;
  if (typeof raw === "number" && (!isFinite(raw) || isNaN(raw))) return null;
  var s = String(raw).trim();
  if (!/^\d+(\.\d{1,8})?$/.test(s)) return null;
  var n = Number(s);
  if (!isFinite(n) || isNaN(n) || n <= 0 || n > 1e12) return null;
  return n;
}

function formatPriceForInput(value) {
  if (typeof value !== "number" || !isFinite(value) || isNaN(value) || value <= 0) {
    return "";
  }
  return String(Number(value.toPrecision(10)));
}

function computeManualRiskView(params) {
  if (!params || typeof params !== "object") {
    return { quantity: null, riskAmount: null, overRisk: false, error: "invalid_view" };
  }
  var quantity = params.quantity;
  if (quantity === null || quantity === undefined) {
    return { quantity: null, riskAmount: null, overRisk: false, error: null };
  }
  var view = params.view;
  if (
    !view ||
    view.error ||
    !view.side ||
    typeof view.stopDistance !== "number" ||
    !isFinite(view.stopDistance) ||
    view.stopDistance <= 0
  ) {
    return { quantity: quantity, riskAmount: null, overRisk: false, error: "invalid_view" };
  }
  if (typeof quantity !== "number" || !isFinite(quantity) || isNaN(quantity) || quantity <= 0) {
    return { quantity: quantity, riskAmount: null, overRisk: false, error: "invalid_quantity" };
  }
  var riskAmount = quantity * view.stopDistance;
  var riskDollar = params.riskDollar;
  var overRisk = false;
  if (typeof riskDollar === "number" && isFinite(riskDollar) && riskDollar > 0) {
    overRisk = riskAmount > riskDollar + 1e-9 * Math.max(1, riskDollar);
  }
  return {
    quantity: quantity,
    riskAmount: riskAmount,
    overRisk: overRisk,
    error: null
  };
}

function describeRrEditInputs(params) {
  function parsePositivePrice(rawText) {
    if (rawText === null || rawText === undefined || typeof rawText === "boolean" || typeof rawText === "object") {
      return null;
    }
    var s = String(rawText).trim();
    if (s === "") return null;
    var n = Number(s);
    if (!isFinite(n) || isNaN(n) || n <= 0) return null;
    return n;
  }

  if (!params || typeof params !== "object") {
    return {
      ok: false,
      error: "價格必須是大於 0 的數字",
      entry: null,
      stop: null,
      target: null,
      quantity: null,
      view: null,
      manual: null
    };
  }

  var entry = parsePositivePrice(params.entryText);
  var stop = parsePositivePrice(params.stopText);
  var target = parsePositivePrice(params.targetText);

  if (entry === null || stop === null || target === null) {
    return {
      ok: false,
      error: "價格必須是大於 0 的數字",
      entry: entry,
      stop: stop,
      target: target,
      quantity: null,
      view: null,
      manual: null
    };
  }

  var rawQty = (params.qtyText === null || params.qtyText === undefined) ? "" : String(params.qtyText).trim();
  var quantity = null;
  if (rawQty !== "") {
    quantity = parseManualQuantity(rawQty);
    if (quantity === null) {
      return {
        ok: false,
        error: "數量必須是大於 0 的數字（最多 8 位小數），或留空",
        entry: entry,
        stop: stop,
        target: target,
        quantity: null,
        view: null,
        manual: null
      };
    }
  }

  var view = computeRiskRewardView({
    entry: entry,
    target: target,
    stop: stop,
    riskDollar: params.riskDollar
  });

  if (!view || view.error) {
    var errMsg = (view && view.error === "停損與目標必須在進場價兩側")
      ? view.error
      : "價格必須是大於 0 的數字";
    return {
      ok: false,
      error: errMsg,
      entry: entry,
      stop: stop,
      target: target,
      quantity: quantity,
      view: view,
      manual: null
    };
  }

  var manual = computeManualRiskView({
    view: view,
    quantity: quantity,
    riskDollar: params.riskDollar
  });

  return {
    ok: true,
    error: null,
    entry: entry,
    stop: stop,
    target: target,
    quantity: quantity,
    view: view,
    manual: manual
  };
}

var rrEditDrawing = null;

function openRrEditModal(drawing) {
  if (!drawing) return;
  var modal = document.getElementById("rrEditModal");
  if (!modal) return;
  var entryIn = document.getElementById("rrEditEntry");
  var stopIn = document.getElementById("rrEditStop");
  var targetIn = document.getElementById("rrEditTarget");
  var qtyIn = document.getElementById("rrEditQty");

  if (entryIn) entryIn.value = formatPriceForInput(drawing.entryPrice);
  if (stopIn) stopIn.value = formatPriceForInput(drawing.stopPrice);
  if (targetIn) targetIn.value = formatPriceForInput(drawing.targetPrice);
  if (qtyIn) {
    qtyIn.value = (drawing.manualQuantity !== undefined && drawing.manualQuantity !== null)
      ? String(drawing.manualQuantity)
      : "";
  }

  rrEditDrawing = drawing;
  modal.style.display = "flex";
  updateRrEditPreview();
  if (entryIn) {
    entryIn.focus();
    if (typeof entryIn.select === "function") entryIn.select();
  }
}

function closeRrEditModal() {
  var modal = document.getElementById("rrEditModal");
  if (modal) modal.style.display = "none";
  rrEditDrawing = null;
}

function updateRrEditPreview() {
  var entryIn = document.getElementById("rrEditEntry");
  var stopIn = document.getElementById("rrEditStop");
  var targetIn = document.getElementById("rrEditTarget");
  var qtyIn = document.getElementById("rrEditQty");
  var previewEl = document.getElementById("rrEditPreview");
  var errEl = document.getElementById("rrEditError");
  var applyBtn = document.getElementById("btnRrEditApply");

  var riskDollar = getRiskDollar();
  var result = describeRrEditInputs({
    entryText: entryIn ? entryIn.value : "",
    stopText: stopIn ? stopIn.value : "",
    targetText: targetIn ? targetIn.value : "",
    qtyText: qtyIn ? qtyIn.value : "",
    riskDollar: riskDollar
  });

  if (previewEl) {
    while (previewEl.firstChild) {
      previewEl.removeChild(previewEl.firstChild);
    }
    if (result.ok && result.view) {
      var sideText = result.view.side === "short" ? "做空" : "做多";
      var line1 = document.createElement("div");
      line1.textContent = sideText + "｜R:R 1 : " + result.view.rr.toFixed(2) + "｜建議 " + result.view.shares + " 股（$" + riskDollar + " 風控）";
      previewEl.appendChild(line1);

      if (result.manual && result.manual.quantity !== null && !result.manual.error && result.manual.riskAmount !== null) {
        var line2 = document.createElement("div");
        var manualLine = "手動 " + String(result.manual.quantity) + " 股 → 風險 $" + result.manual.riskAmount.toFixed(2);
        if (result.manual.overRisk) {
          manualLine += "  ⚠ 超過風控";
          line2.style.color = "#f87171";
        }
        line2.textContent = manualLine;
        previewEl.appendChild(line2);
      }
    }
  }

  if (errEl) {
    errEl.textContent = result.ok ? "" : (result.error || "");
  }
  if (applyBtn) {
    applyBtn.disabled = !result.ok;
  }
}

function applyRrEdit() {
  if (!rrEditDrawing || !Array.isArray(drawings) || drawings.indexOf(rrEditDrawing) === -1) {
    closeRrEditModal();
    return;
  }

  var entryIn = document.getElementById("rrEditEntry");
  var stopIn = document.getElementById("rrEditStop");
  var targetIn = document.getElementById("rrEditTarget");
  var qtyIn = document.getElementById("rrEditQty");
  var errEl = document.getElementById("rrEditError");
  var applyBtn = document.getElementById("btnRrEditApply");

  var result = describeRrEditInputs({
    entryText: entryIn ? entryIn.value : "",
    stopText: stopIn ? stopIn.value : "",
    targetText: targetIn ? targetIn.value : "",
    qtyText: qtyIn ? qtyIn.value : "",
    riskDollar: getRiskDollar()
  });

  if (!result.ok) {
    if (errEl) errEl.textContent = result.error || "";
    if (applyBtn) applyBtn.disabled = true;
    return;
  }

  rrEditDrawing.entryPrice = result.entry;
  rrEditDrawing.stopPrice = result.stop;
  rrEditDrawing.targetPrice = result.target;
  if (result.quantity === null) {
    delete rrEditDrawing.manualQuantity;
  } else {
    rrEditDrawing.manualQuantity = result.quantity;
  }

  saveDrawingsForActiveSymbol();
  closeRrEditModal();
  if (typeof scheduleDrawingRender === "function") scheduleDrawingRender();
}

if (typeof window !== "undefined") {
  window.parseRiskDollar = parseRiskDollar;
  window.getRiskDollar = getRiskDollar;
  window.setRiskDollar = setRiskDollar;
  window.computeRiskRewardView = computeRiskRewardView;
  window.computeAtrPositionSizing = computeAtrPositionSizing;
  window.parseManualQuantity = parseManualQuantity;
  window.formatPriceForInput = formatPriceForInput;
  window.computeManualRiskView = computeManualRiskView;
  window.describeRrEditInputs = describeRrEditInputs;
}
if (typeof globalThis !== "undefined") {
  globalThis.parseRiskDollar = parseRiskDollar;
  globalThis.getRiskDollar = getRiskDollar;
  globalThis.setRiskDollar = setRiskDollar;
  globalThis.computeRiskRewardView = computeRiskRewardView;
  globalThis.computeAtrPositionSizing = computeAtrPositionSizing;
  globalThis.parseManualQuantity = parseManualQuantity;
  globalThis.formatPriceForInput = formatPriceForInput;
  globalThis.computeManualRiskView = computeManualRiskView;
  globalThis.describeRrEditInputs = describeRrEditInputs;
}

// ==========================================================================
// DOM Initializers & Strict Safe Text Ingestion (Zero XSS innerHTML)
// ==========================================================================

function buildInitialDOM() {
  buildTickerTape();
  renderWatchlist();
}

function buildTickerTape() {
  if (!tickerTape) return;
  tickerTape.innerHTML = "";
  tapeElements = {};

  for (var i = 0; i < symbolsList.length; i++) {
    (function(item) {
      var tapeItem = document.createElement("div");
      tapeItem.className = "tape-item";
      tapeItem.setAttribute("data-symbol", item.id);

      var symSpan = document.createElement("span");
      symSpan.className = "tape-symbol";
      symSpan.textContent = item.pair;

      var priceSpan = document.createElement("span");
      priceSpan.className = "tape-price";
      priceSpan.textContent = "--";

      var changeSpan = document.createElement("span");
      changeSpan.className = "tape-change";
      changeSpan.textContent = "--";

      tapeItem.appendChild(symSpan);
      tapeItem.appendChild(priceSpan);
      tapeItem.appendChild(changeSpan);

      tapeItem.onclick = function() {
        if (mtfSyncEnabled) {
    syncMTF(item.id);
  } else if (focusedPaneId > 1 && subCharts[focusedPaneId]) {
    loadSubChart(focusedPaneId, item.id);
  } else {
    switchSymbol(item.id);
  }
      };

      tickerTape.appendChild(tapeItem);
      tapeElements[item.id] = {
        item: tapeItem,
        price: priceSpan,
        change: changeSpan
      };
    })(symbolsList[i]);
  }
}

function updateTapeItem(symbol, direction) {
  var els = tapeElements[symbol];
  if (!els) return;
  var state = symbolStates[symbol] || {};

  if (state.price !== undefined) {
    els.price.textContent = canonicalFormatPrice(state.price, symbol);
  }
  if (state.changePercentage !== undefined) {
    var chp = Number(state.changePercentage);
    var sign = chp >= 0 ? "+" : "";
    els.change.textContent = sign + chp.toFixed(2) + "%";
    els.change.className = "tape-change " + (chp >= 0 ? "up" : "down");
  }

  if (direction) {
    els.item.classList.remove("flash-up", "flash-down");
    void els.item.offsetWidth;
    els.item.classList.add("flash-" + direction);
  }
}

function updateQuote(symbol, quoteData) {
  if (!quoteData) return;
  var oldPrice = lastPrices[symbol] || 0;
  var newPrice = quoteData.price;
  var direction = null;

  if (newPrice !== undefined && oldPrice > 0) {
    if (newPrice > oldPrice) direction = "up";
    else if (newPrice < oldPrice) direction = "down";
  }
  if (newPrice !== undefined) {
    lastPrices[symbol] = newPrice;
  }

  var mergedState = Object.assign({}, symbolStates[symbol] || {}, quoteData);
  symbolStates[symbol] = mergedState;
  updateTapeItem(symbol, direction);
  updateWatchlistPrice(symbol, mergedState);

  if (symbol === activeSymbol) {
    updateSymbolMetrics(symbol, mergedState, latestBarData);
    if (quoteData && quoteData.price) {
      updateActiveCandleWithPrice(quoteData.price, quoteData.volume, quoteData.time);
    }
  }
}

var _liveIndicatorsTimer = null;
var _lastLiveIndicatorsTime = 0;

function updateLiveIndicatorsThrottled() {
  var now = Date.now();
  if (now - _lastLiveIndicatorsTime >= 250) {
    if (_liveIndicatorsTimer) {
      clearTimeout(_liveIndicatorsTimer);
      _liveIndicatorsTimer = null;
    }
    _lastLiveIndicatorsTime = now;
    _runLiveIndicatorsUpdate();
  } else if (!_liveIndicatorsTimer) {
    var delay = Math.max(16, 250 - (now - _lastLiveIndicatorsTime));
    _liveIndicatorsTimer = setTimeout(function() {
      _liveIndicatorsTimer = null;
      _lastLiveIndicatorsTime = Date.now();
      _runLiveIndicatorsUpdate();
    }, delay);
  }
}

function _runLiveIndicatorsUpdate() {
  if (!currentRawBars || currentRawBars.length === 0) return;
  try {
    if (window.Indicators && currentRawBars.length > 0) {
      if (ema10Series && indicatorConfig.ema10) {
        var e10 = window.Indicators.calcEMA(currentRawBars, 10);
        if (e10.length > 0) ema10Series.update(e10[e10.length - 1]);
      }
      if (ema21Series && indicatorConfig.ema21) {
        var e21 = window.Indicators.calcEMA(currentRawBars, 21);
        if (e21.length > 0) ema21Series.update(e21[e21.length - 1]);
      }
      if (ema30Series && indicatorConfig.ema30) {
        var e30 = window.Indicators.calcEMA(currentRawBars, 30);
        if (e30.length > 0) ema30Series.update(e30[e30.length - 1]);
      }
      if (vwapSeries && indicatorConfig.vwap && window.Indicators.calcVWAP) {
        var vw = window.Indicators.calcVWAP(currentRawBars, { timeframe: activeTimeframe, symbol: activeSymbol });
        if (vw.length > 0) vwapSeries.update(vw[vw.length - 1]);
      }
      if (sma50Series && indicatorConfig.sma50) {
        var s50 = window.Indicators.calcSMA(currentRawBars, 50);
        if (s50.length > 0) sma50Series.update(s50[s50.length - 1]);
      }
      if (sma200Series && indicatorConfig.sma200) {
        var s200 = window.Indicators.calcSMA(currentRawBars, 200);
        if (s200.length > 0) sma200Series.update(s200[s200.length - 1]);
      }
      if (volSmaSeries && indicatorConfig.volSma) {
        var vs = window.Indicators.calcVolumeSMA(currentRawBars, 50);
        if (vs.length > 0) volSmaSeries.update(vs[vs.length - 1]);
      }
    }

    if (indicatorConfig && indicatorConfig.avwap) {
      calcAndRenderAvwap();
    }

    updateDeepvueHud(currentRawBars);
  } catch (err) {
    console.debug("Throttled live indicators update skipped:", err);
  }
}

function updateActiveCandleWithPrice(price, volume, tickTime) {
  if (!candleSeries || isHistoryLoading || !price || isNaN(price) || price <= 0) return;
  if (!currentRawBars || currentRawBars.length === 0) return;

  var lastIdx = currentRawBars.length - 1;
  var curBar = currentRawBars[lastIdx];
  if (!curBar || typeof curBar.time !== "number") return;

  // 第 4 段：只有報價時間屬於目前這根 K 棒時才更新。換時段時由 1 分鐘 K 棒訊息建立新 K 棒，
  // 避免換分鐘後的價格寫進上一根，也避免美股盤前盤後的報價改動日 K。
  var placement = computeLiveBucket({
    symbol: activeSymbol,
    timeframe: activeTimeframe,
    tickTime: tickTime,
    lastBarTime: curBar.time
  });
  if (placement.action !== "update" || placement.time !== curBar.time) return;

  var updatedOpen = curBar.open;
  var updatedHigh = Math.max(curBar.high, price);
  var updatedLow = Math.min(curBar.low, price);
  var updatedClose = price;
  // 第 4 段：報價的成交量是 24h／當日累計量，不能當成這根 K 棒的量；K 棒量只由 1 分鐘 K 棒訊息累加。
  var updatedVolume = curBar.volume || 0;

  var liveBar = {
    time: curBar.time,
    open: updatedOpen,
    high: updatedHigh,
    low: updatedLow,
    close: updatedClose,
    volume: updatedVolume
  };

  currentRawBars[lastIdx] = liveBar;
  latestBarData = liveBar;
  lastBarTime = curBar.time;
  lastBarObj = Object.assign({}, liveBar);
  if (liveBucketState && liveBucketState.lastWritten === curBar && liveBucketState.bucketTime === curBar.time) {
    liveBucketState.high = updatedHigh;
    liveBucketState.low = updatedLow;
    liveBucketState.close = updatedClose;
    liveBucketState.lastWritten = liveBar;
  }

  if (isAppNapSuspended || (typeof document !== "undefined" && document.hidden)) {
    hasPendingChartUpdate = true;
    return;
  }

  try {
    if (currentCandleStyle === "hollow") {
      var prevClose = (currentRawBars.length > 1) ? currentRawBars[currentRawBars.length - 2].close : updatedOpen;
      var isBullishClose = updatedClose >= prevClose;
      var strokeColor = isBullishClose ? "#26a69a" : "#ef5350";
      var isHollowBody = updatedClose >= updatedOpen;
      candleSeries.update({
        time: curBar.time,
        open: updatedOpen,
        high: updatedHigh,
        low: updatedLow,
        close: updatedClose,
        color: isHollowBody ? "transparent" : strokeColor,
        borderColor: strokeColor,
        wickColor: strokeColor
      });
    } else {
      candleSeries.update({
        time: curBar.time,
        open: updatedOpen,
        high: updatedHigh,
        low: updatedLow,
        close: updatedClose
      });
    }

    if (volumeSeries && updatedVolume !== undefined) {
      volumeSeries.update({
        time: curBar.time,
        value: updatedVolume,
        color: updatedClose >= updatedOpen ? "rgba(38, 166, 154, 0.45)" : "rgba(239, 83, 80, 0.45)"
      });
    }

    updateCrosshairLegend(liveBar);
    updateLiveIndicatorsThrottled();
  } catch (chartErr) {
    console.debug("Quote-to-candle update skipped:", chartErr);
  }
}

// ==========================================================================
// Chart Setup & Indicators Management
// ==========================================================================

function initChart() {
  if (typeof LightweightCharts === "undefined") {
    if (chartContainer) {
      chartContainer.innerHTML = "";
      var errDiv = document.createElement("div");
      errDiv.style.color = "#ef5350";
      errDiv.style.padding = "24px";
      errDiv.style.fontFamily = "monospace";
      errDiv.textContent = "Unable to load Lightweight Charts CDN.";
      chartContainer.appendChild(errDiv);
    }
    return;
  }

  if (chart) {
    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }
    chart.remove();
    chart = null;
  }
  if (rsChart) {
    rsChart.remove();
    rsChart = null;
  }

  var priceContainer = document.getElementById("chart") || chartContainer;
  var rsChartContainer = document.getElementById("rsChart");

  var initialWidth = priceContainer.clientWidth || chartContainer.clientWidth || 800;
  var initialHeight = priceContainer.clientHeight || 400;

  var mainPalette = getChartThemePalette();
  // 第 4 段：時間軸與十字線顯示交易所時間（美股 ET、加密貨幣 UTC）
  var mainTimeOpts = makeChartTimeOptions(
    function() { return activeSymbol; },
    function() { return activeTimeframe; }
  );
  chart = LightweightCharts.createChart(priceContainer, {
    width: initialWidth,
    height: initialHeight,
    layout: {
      background: { color: mainPalette.background },
      textColor: mainPalette.textColor,
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    },
    grid: {
      vertLines: { color: mainPalette.grid },
      horzLines: { color: mainPalette.grid }
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {
        color: mainPalette.crosshair,
        width: 1,
        style: LightweightCharts.LineStyle.Dashed,
        labelBackgroundColor: mainPalette.crosshairLabelBg
      },
      horzLine: {
        color: mainPalette.crosshair,
        width: 1,
        style: LightweightCharts.LineStyle.Dashed,
        labelBackgroundColor: mainPalette.crosshairLabelBg
      }
    },
    rightPriceScale: {
      borderColor: mainPalette.border,
      scaleMargins: { top: 0.15, bottom: 0.22 },
      autoScale: true
    },
    timeScale: {
      borderColor: mainPalette.border,
      timeVisible: true,
      secondsVisible: false,
      rightOffset: RIGHT_WHITESPACE_BARS,
      tickMarkFormatter: mainTimeOpts.tickMarkFormatter
    },
    localization: {
      timeFormatter: mainTimeOpts.timeFormatter
    }
  });

  installRangeDebug(chart, "main");

  candleSeries = chart.addCandlestickSeries({
    upColor: "#26a69a",
    downColor: "#ef5350",
    borderVisible: true,
    borderUpColor: "#26a69a",
    borderDownColor: "#ef5350",
    wickUpColor: "#26a69a",
    wickDownColor: "#ef5350",
    priceFormat: { type: "price", precision: 2, minMove: 0.01 }
  });
  candlestickSeries = candleSeries;

  volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "vol_overlay"
  });
  volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.82, bottom: 0 }
  });

  volSmaSeries = chart.addLineSeries({
    color: "#9ca3af",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    priceScaleId: "vol_overlay",
    priceLineVisible: false,
    lastValueVisible: false
  });

  ema10Series = chart.addLineSeries({
    color: "#00e5ff",
    lineWidth: 1,
    title: "EMA 10",
    priceLineVisible: false,
    lastValueVisible: false
  });

  ema21Series = chart.addLineSeries({
    color: "#a855f7",
    lineWidth: 1,
    title: "EMA 21",
    priceLineVisible: false,
    lastValueVisible: false
  });

  ema30Series = chart.addLineSeries({
    color: "#eab308",
    lineWidth: 1,
    title: "EMA 30",
    priceLineVisible: false,
    lastValueVisible: false
  });

  vwapSeries = chart.addLineSeries({
    color: "#ec4899",
    lineWidth: 2,
    title: "VWAP",
    priceLineVisible: false,
    lastValueVisible: false
  });

  sma50Series = chart.addLineSeries({
    color: "#f59e0b",
    lineWidth: 2,
    title: "SMA 50",
    priceLineVisible: false,
    lastValueVisible: false
  });

  sma200Series = chart.addLineSeries({
    color: "#ef4444",
    lineWidth: 2,
    title: "SMA 200",
    priceLineVisible: false,
    lastValueVisible: false
  });

  rsSeries = chart.addLineSeries({
    color: "#2962ff",
    lineWidth: 2,
    title: "RS (" + rsBenchmarkTag + ")",
    priceScaleId: "rs_scale",
    priceLineVisible: false,
    lastValueVisible: false
  });
  rsSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.15, bottom: 0.4 }
  });

  bbUpperSeries = chart.addLineSeries({
    color: "#38bdf8",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    priceLineVisible: false,
    lastValueVisible: false
  });
  bbLowerSeries = chart.addLineSeries({
    color: "#38bdf8",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    priceLineVisible: false,
    lastValueVisible: false
  });

  // Deepvue RS Independent Sub-Chart Pane
  if (rsChartContainer) {
    var rsInitWidth = rsChartContainer.clientWidth || initialWidth;
    var rsInitHeight = rsChartContainer.clientHeight || 120;

    rsChart = LightweightCharts.createChart(rsChartContainer, {
      width: rsInitWidth,
      height: rsInitHeight,
      layout: {
        background: { color: mainPalette.background },
        textColor: mainPalette.textColor,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
      },
      grid: {
        vertLines: { color: mainPalette.grid },
        horzLines: { color: mainPalette.grid }
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
          color: mainPalette.crosshair,
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: mainPalette.crosshairLabelBg
        },
        horzLine: {
          color: mainPalette.crosshair,
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: mainPalette.crosshairLabelBg
        }
      },
      rightPriceScale: {
        borderColor: mainPalette.border,
        scaleMargins: { top: 0.1, bottom: 0.1 }
      },
      timeScale: {
        borderColor: mainPalette.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: RIGHT_WHITESPACE_BARS,
        tickMarkFormatter: mainTimeOpts.tickMarkFormatter
      },
      localization: {
        timeFormatter: mainTimeOpts.timeFormatter
      }
    });

    installRangeDebug(rsChart, "rs");

    rsLinePaneSeries = rsChart.addLineSeries({
      color: "#2962ff",
      lineWidth: 2,
      title: "RS",
      priceLineVisible: false,
      lastValueVisible: false
    });

    rsEmaPaneSeries = rsChart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      title: "RS 21 EMA",
      priceLineVisible: false,
      lastValueVisible: false
    });

    // Bidirectional range synchronization（邏輯範圍；RS 資料已補齊成主圖的時間軸，見 padToTimeAxis）
    bindLogicalRangeSync(chart, rsChart, function() {
      if (typeof scheduleDrawingRender === "function") scheduleDrawingRender();
    });
  }

  chart.subscribeCrosshairMove(function(param) {
    if (!param || !param.time || !param.point || !param.seriesData || !param.seriesData.has(candleSeries)) {
      updateCrosshairLegend(latestBarData);
      return;
    }
    var candleData = param.seriesData.get(candleSeries);
    var volData = param.seriesData.get(volumeSeries);
    if (candleSeries && param.point) { lastCrosshairPrice = candleSeries.coordinateToPrice(param.point.y); }
    var ema10Val = param.seriesData.get(ema10Series);
    var ema21Val = param.seriesData.get(ema21Series);
    var ema30Val = typeof ema30Series !== "undefined" && ema30Series ? param.seriesData.get(ema30Series) : null;
    var sma50Val = param.seriesData.get(sma50Series);
    var sma200Val = param.seriesData.get(sma200Series);
    var vwapVal = typeof vwapSeries !== "undefined" && vwapSeries ? param.seriesData.get(vwapSeries) : null;
    var rsVal = param.seriesData.get(rsSeries);

    if (candleData) {
      var isHist = false;
      if (latestBarData && latestBarData.time) {
        if (typeof param.time === "object" && typeof latestBarData.time === "object") {
          isHist = (param.time.year !== latestBarData.time.year || param.time.month !== latestBarData.time.month || param.time.day !== latestBarData.time.day);
        } else {
          isHist = (param.time !== latestBarData.time);
        }
      }
      updateCrosshairLegend({
        time: param.time,
        open: candleData.open,
        high: candleData.high,
        low: candleData.low,
        close: candleData.close,
        volume: volData ? volData.value : undefined,
        ema10: ema10Val ? ema10Val.value : undefined,
        ema21: ema21Val ? ema21Val.value : undefined,
        ema30: ema30Val ? ema30Val.value : undefined,
        sma50: sma50Val ? sma50Val.value : undefined,
        sma200: sma200Val ? sma200Val.value : undefined,
        vwap: vwapVal ? vwapVal.value : undefined,
        rs: rsVal ? rsVal.value : undefined,
        isHistorical: isHist
      });
    }
  });

  if (chartContainer && !chartContainer._hasMouseLeaveReset) {
    chartContainer._hasMouseLeaveReset = true;
    chartContainer.addEventListener("mouseleave", function() {
      updateCrosshairLegend(latestBarData);
    });
  }
  // Escape legend reset consolidated in global keydown handler

  if (window.ResizeObserver) {
    resizeObserver = new ResizeObserver(function(entries) {
      if (!entries || entries.length === 0) return;
      resizeAllCharts();
    });
    resizeObserver.observe(chartContainer);
  } else if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("resize", resizeAllCharts);
  }

  applyIndicatorVisibilities();
  setPriceScaleMode(currentPriceScaleMode);
}

function resizeAllCharts() {
  if (currentEngine !== "C") {
    var priceContainer = document.getElementById("chart");
    var rsChartContainer = document.getElementById("rsChart");
    if (chart && priceContainer) {
      var pw = priceContainer.clientWidth;
      var ph = priceContainer.clientHeight;
      if (pw > 0 && ph > 0) {
        chart.applyOptions({ width: pw, height: ph });
      }
    }
    if (rsChart && rsChartContainer && indicatorConfig.rsLine) {
      var rw = rsChartContainer.clientWidth;
      var rh = rsChartContainer.clientHeight;
      if (rw > 0 && rh > 0) {
        rsChart.applyOptions({ width: rw, height: rh });
      }
    }
  }
  for (var p in subCharts) {
    var cEl = document.getElementById("splitChart" + p);
    if (cEl && subCharts[p] && subCharts[p].chart) {
      var sw = cEl.clientWidth;
      var sh = cEl.clientHeight;
      if (sw > 0 && sh > 0) {
        subCharts[p].chart.applyOptions({ width: sw, height: sh });
      }
    }
    var rsEl = document.getElementById("splitRsChart" + p);
    if (rsEl && subCharts[p] && subCharts[p].rsChart && indicatorConfig.rsLine) {
      var rw = rsEl.clientWidth;
      var rh = rsEl.clientHeight;
      if (rw > 0 && rh > 0) {
        subCharts[p].rsChart.applyOptions({ width: rw, height: rh });
      }
    }
  }
}

function setPriceScaleMode(mode) {
  currentPriceScaleMode = mode;
  var scaleLog = document.getElementById("scaleLog");
  var scalePercent = document.getElementById("scalePercent");
  if (scaleLog) scaleLog.classList.toggle("active", mode === "log");
  if (scalePercent) scalePercent.classList.toggle("active", mode === "percent");

  if (chart) {
    var modeNum = 0;
    if (mode === "log") modeNum = 1;
    else if (mode === "percent") modeNum = 2;
    try {
      chart.priceScale("right").applyOptions({ mode: modeNum });
    } catch (e) {}
  }
  if (typeof scheduleDrawingRender === "function") scheduleDrawingRender();
}

function setAutoScale(enabled) {
  isAutoScale = !!enabled;
  var scaleAuto = document.getElementById("scaleAuto");
  if (scaleAuto) scaleAuto.classList.toggle("active", isAutoScale);
  if (chart) {
    try {
      chart.priceScale("right").applyOptions({ autoScale: isAutoScale });
    } catch (e) {}
  }
  if (typeof scheduleDrawingRender === "function") scheduleDrawingRender();
}

function updateCandleStyleUI() {
  var btn = document.getElementById("candleStyleBtn");
  if (!btn) return;
  var dict = i18n[currentLang] || i18n.en;
  if (currentCandleStyle === "hollow") {
    btn.textContent = dict.candleHollow;
    btn.classList.add("hollow");
    btn.setAttribute("title", dict.candleHollowTitle);
  } else {
    btn.textContent = dict.candleSolid;
    btn.classList.remove("hollow");
    btn.setAttribute("title", dict.candleSolidTitle);
  }
}

function calcAndRenderAvwap() {
  if (!currentRawBars || currentRawBars.length === 0 || !window.Indicators || !window.Indicators.calcAnchoredVWAP) {
    currentAvwapData = null;
    updateAvwapHudAndLegend(null);
    return;
  }
  if (!indicatorConfig.avwap) {
    currentAvwapData = null;
    updateAvwapHudAndLegend(null);
    if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
    return;
  }

  var manualAnchor = avwapConfig.manualAnchorDate || "2026-01-02";
  if (avwapConfig.manualAnchorTime) {
    manualAnchor += " " + avwapConfig.manualAnchorTime;
  }

  try {
    currentAvwapData = window.Indicators.calcAnchoredVWAP(currentRawBars, {
      anchorMode: avwapConfig.anchorMode || "Swing Low",
      anchorLookback: avwapConfig.anchorLookback || 252,
      gapPctMin: avwapConfig.gapPctMin !== undefined ? avwapConfig.gapPctMin : 5.0,
      gapVolMin: avwapConfig.gapVolMin !== undefined ? avwapConfig.gapVolMin : 2.0,
      volMaLen: avwapConfig.volMaLen || 50,
      manualAnchor: manualAnchor,
      source: avwapConfig.source || "hlc3",
      bandMult: avwapConfig.bandMult !== undefined ? avwapConfig.bandMult : 1.0
    });
  } catch (e) {
    currentAvwapData = null;
  }

  updateAvwapHudAndLegend(currentAvwapData);
  if (typeof renderDrawingCanvas === "function") {
    renderDrawingCanvas();
  }
}

function updateAvwapHudAndLegend(data, activeBarTime) {
  var legAvwap = document.getElementById("legAvwap");
  var legTitle = document.getElementById("legAvwapTitle");
  var legVal = document.getElementById("legAvwapVal");
  var legBands = document.getElementById("legAvwapBands");
  var infoTbl = document.getElementById("avwapInfoTable");
  var tblAnchor = document.getElementById("avwapTblAnchor");
  var tblVwap = document.getElementById("avwapTblVwap");
  var tblDiff = document.getElementById("avwapTblDiff");
  var tblPos = document.getElementById("avwapTblPos");

  if (!indicatorConfig.avwap || !data || !data.latest) {
    if (legAvwap) legAvwap.style.display = "none";
    if (infoTbl) infoTbl.style.display = "none";
    return;
  }

  if (legAvwap) legAvwap.style.display = "inline-flex";

  if (legTitle) {
    legTitle.textContent = "AVWAP " + (avwapConfig.anchorMode || "Swing Low") + " " +
      (avwapConfig.anchorLookback || 252) + " " +
      (avwapConfig.gapPctMin !== undefined ? avwapConfig.gapPctMin : 5) + " " +
      (avwapConfig.gapVolMin !== undefined ? avwapConfig.gapVolMin : 2) + " " +
      (avwapConfig.volMaLen || 50) + " " +
      (avwapConfig.source || "hlc3") + " " +
      (avwapConfig.bandMult !== undefined ? avwapConfig.bandMult : 1);
  }

  var targetPt = data.latest;
  if (activeBarTime !== undefined && data.avwap && data.avwap.length > 0) {
    for (var i = 0; i < data.avwap.length; i++) {
      if (data.avwap[i].time === activeBarTime) {
        targetPt = data.avwap[i];
        break;
      }
    }
  }

  var color = targetPt.isAbove ? (avwapConfig.colorAbove || "#26a69a") : (avwapConfig.colorBelow || "#ef5350");

  var avwapVal = targetPt.value !== undefined ? targetPt.value : targetPt.avwap;
  if (legVal) {
    legVal.textContent = typeof avwapVal === "number" && isFinite(avwapVal) ? avwapVal.toFixed(2) : "--";
    legVal.style.color = color;
  }

  if (legBands) {
    if (avwapConfig.showBands && targetPt.upper !== undefined && targetPt.lower !== undefined) {
      legBands.textContent = " [" + targetPt.lower.toFixed(2) + " - " + targetPt.upper.toFixed(2) + "]";
      legBands.style.display = "inline";
    } else {
      legBands.textContent = "";
      legBands.style.display = "none";
    }
  }

  // Right-Bottom Table (4 rows x 2 cols)
  if (infoTbl) {
    if (avwapConfig.showTable) {
      infoTbl.style.display = "flex";
      if (tblAnchor && data.anchorBar) {
        tblAnchor.textContent = data.anchorBar.label || (avwapConfig.anchorMode + " (" + data.anchorBar.barsAgo + " bars ago)");
      }
      if (tblVwap) {
        tblVwap.textContent = data.latest.avwap.toFixed(2);
        tblVwap.style.color = data.latest.isAbove ? (avwapConfig.colorAbove || "#26a69a") : (avwapConfig.colorBelow || "#ef5350");
      }
      if (tblDiff) {
        tblDiff.textContent = (data.latest.diffPct >= 0 ? "+" : "") + data.latest.diffPct.toFixed(2) + "%";
        tblDiff.style.color = data.latest.isAbove ? (avwapConfig.colorAbove || "#26a69a") : (avwapConfig.colorBelow || "#ef5350");
      }
      if (tblPos) {
        tblPos.textContent = data.latest.isAbove ? "Above" : "Below";
        tblPos.style.color = data.latest.isAbove ? (avwapConfig.colorAbove || "#26a69a") : (avwapConfig.colorBelow || "#ef5350");
      }
    } else {
      infoTbl.style.display = "none";
    }
  }
}

function applyIndicatorVisibilities() {
  if (typeof syncIndicatorCheckboxes === "function") syncIndicatorCheckboxes();
  if (ema10Series) ema10Series.applyOptions({ visible: indicatorConfig.ema10, priceLineVisible: false, lastValueVisible: false });
  if (ema21Series) ema21Series.applyOptions({ visible: indicatorConfig.ema21, priceLineVisible: false, lastValueVisible: false });
  if (ema30Series) ema30Series.applyOptions({ visible: indicatorConfig.ema30, priceLineVisible: false, lastValueVisible: false });
  if (vwapSeries) vwapSeries.applyOptions({ visible: indicatorConfig.vwap, priceLineVisible: false, lastValueVisible: false });
  if (sma50Series) sma50Series.applyOptions({ visible: indicatorConfig.sma50, priceLineVisible: false, lastValueVisible: false });
  if (sma200Series) sma200Series.applyOptions({ visible: indicatorConfig.sma200, priceLineVisible: false, lastValueVisible: false });
  if (volSmaSeries) volSmaSeries.applyOptions({ visible: indicatorConfig.volSma, priceLineVisible: false, lastValueVisible: false });
  if (rsSeries) rsSeries.applyOptions({ visible: Boolean(indicatorConfig.rsOverlayMain), priceLineVisible: false, lastValueVisible: false });
  if (rsLinePaneSeries) rsLinePaneSeries.applyOptions({ visible: indicatorConfig.rsLine, priceLineVisible: false, lastValueVisible: false });
  if (rsEmaPaneSeries) rsEmaPaneSeries.applyOptions({ visible: indicatorConfig.rsLine, priceLineVisible: false, lastValueVisible: false });
  if (bbUpperSeries) bbUpperSeries.applyOptions({ visible: indicatorConfig.bollinger, priceLineVisible: false, lastValueVisible: false });
  if (bbLowerSeries) bbLowerSeries.applyOptions({ visible: indicatorConfig.bollinger, priceLineVisible: false, lastValueVisible: false });

  var rsToggleBtn = document.getElementById("rsToggleBtn");
  if (rsToggleBtn) {
    if (indicatorConfig.rsLine) {
      rsToggleBtn.classList.add("active");
    } else {
      rsToggleBtn.classList.remove("active");
    }
  }

  var rsPaneEl = document.getElementById("rsPane");
  var priceContainerEl = document.getElementById("chart");
  if (rsPaneEl) {
    rsPaneEl.style.display = indicatorConfig.rsLine ? "flex" : "none";
    rsPaneEl.style.height = indicatorConfig.rsLine ? "28%" : "0%";
    rsPaneEl.style.flex = indicatorConfig.rsLine ? "0 0 28%" : "0 0 0%";
  }
  if (priceContainerEl && priceContainerEl.parentElement) {
    priceContainerEl.parentElement.style.height = indicatorConfig.rsLine ? "72%" : "100%";
    priceContainerEl.parentElement.style.flex = indicatorConfig.rsLine ? "0 0 72%" : "0 0 100%";
  }
  if (!indicatorConfig.rsLine) {
    if (rsLinePaneSeries && typeof rsLinePaneSeries.setMarkers === "function") {
      try { rsLinePaneSeries.setMarkers([]); } catch (e) {}
    }
    if (candleSeries && typeof candleSeries.setMarkers === "function") {
      try { candleSeries.setMarkers([]); } catch (e) {}
    }
    var nhBadgeOff = document.getElementById("rsNewHighBadge");
    if (nhBadgeOff) nhBadgeOff.style.display = "none";
  }

  for (var spIdx = 2; spIdx <= 4; spIdx++) {
    if (typeof renderSubPaneRs === "function") {
      renderSubPaneRs(spIdx, subCharts[spIdx] ? subCharts[spIdx].bars : null, benchmarkRawBars);
    }
  }

  var legEma30 = document.getElementById("legEma30");
  var legVwap = document.getElementById("legVwap");
  if (legEma10) legEma10.style.display = indicatorConfig.ema10 ? "inline" : "none";
  if (legEma21) legEma21.style.display = indicatorConfig.ema21 ? "inline" : "none";
  if (legEma30) legEma30.style.display = indicatorConfig.ema30 ? "inline" : "none";
  if (legSma50) legSma50.style.display = indicatorConfig.sma50 ? "inline" : "none";
  if (legSma200) legSma200.style.display = indicatorConfig.sma200 ? "inline" : "none";
  if (legVwap) legVwap.style.display = indicatorConfig.vwap ? "inline" : "none";
  if (legRs) legRs.style.display = indicatorConfig.rsLine ? "inline" : "none";

  var legAvwap = document.getElementById("legAvwap");
  var avwapInfoTable = document.getElementById("avwapInfoTable");
  var chkAvwap = document.getElementById("chkAvwap");
  if (chkAvwap) chkAvwap.checked = Boolean(indicatorConfig.avwap);

  if (legAvwap) {
    legAvwap.style.display = indicatorConfig.avwap ? "inline-flex" : "none";
  }
  if (avwapInfoTable) {
    avwapInfoTable.style.display = (indicatorConfig.avwap && avwapConfig.showTable) ? "flex" : "none";
  }

  var avwapPaneEl = document.getElementById("avwapPane");
  if (avwapPaneEl) {
    avwapPaneEl.style.display = (indicatorConfig.avwap && avwapConfig.displayMode === "pane") ? "flex" : "none";
  }

  var chartLegend = document.getElementById("chartLegend");
  if (chartLegend) {
    if (indicatorConfig.showLegend === false) {
      chartLegend.style.display = "none";
    } else {
      chartLegend.style.display = (currentEngine === "C") ? "none" : "flex";
      chartLegend.classList.toggle("collapsed", Boolean(indicatorConfig.collapseLegend));
    }
  }
  var btnToggleLegendCollapse = document.getElementById("btnToggleLegendCollapse");
  if (btnToggleLegendCollapse) {
    btnToggleLegendCollapse.textContent = indicatorConfig.collapseLegend ? "👁️‍🗨️" : "👁️";
    btnToggleLegendCollapse.title = indicatorConfig.collapseLegend ? "展開圖例數值 (Expand Legend Values)" : "收起圖例數值 (Collapse Legend Values)";
  }

  var drawingToolbar = document.getElementById("drawingToolbar");
  if (drawingToolbar) {
    var isDtbVis = Boolean(indicatorConfig && indicatorConfig.showDrawingToolbar && currentEngine !== "C");
    drawingToolbar.style.display = isDtbVis ? "flex" : "none";
    drawingToolbar.classList.toggle("visible", isDtbVis);
  }

  var isToolsVis = Boolean(indicatorConfig && indicatorConfig.showAnalysisTools);
  var toolBtnIds = ["fundamentalsBtn", "aiLensBtn"];
  for (var ti = 0; ti < toolBtnIds.length; ti++) {
    var tEl = document.getElementById(toolBtnIds[ti]);
    if (tEl) {
      tEl.style.display = isToolsVis ? "inline-flex" : "none";
      tEl.classList.toggle("visible", isToolsVis);
    }
  }

  calcAndRenderAvwap();
  if (typeof resizeAllCharts === "function") {
    resizeAllCharts();
  }
}

function updateCrosshairLegend(bar) {
  var modeBadge = document.getElementById("legendModeBadge");
  if (!bar) {
    if (legendOpenEl) legendOpenEl.textContent = "--";
    if (legendHighEl) legendHighEl.textContent = "--";
    if (legendLowEl) legendLowEl.textContent = "--";
    if (legendCloseEl) legendCloseEl.textContent = "--";
    if (legendVolEl) legendVolEl.textContent = "--";
    if (modeBadge) {
      modeBadge.style.display = "none";
    }
    return;
  }

  if (modeBadge) {
    modeBadge.style.display = "inline-block";
    if (bar.isHistorical) {
      modeBadge.className = "legend-badge historical";
      var dateStr = "";
      if (bar.time) {
        if (typeof bar.time === "object" && bar.time.year) {
          dateStr = bar.time.year + "-" + String(bar.time.month).padStart(2, "0") + "-" + String(bar.time.day).padStart(2, "0");
        } else if (typeof bar.time === "number") {
          var d = new Date(bar.time * 1000);
          dateStr = d.toISOString().slice(0, 10);
        } else {
          dateStr = String(bar.time);
        }
      }
      modeBadge.textContent = "HISTORICAL" + (dateStr ? " (" + dateStr + ")" : "");
    } else {
      modeBadge.className = "legend-badge live";
      modeBadge.textContent = "LIVE";
    }
  }

  if (legendOpenEl) legendOpenEl.textContent = canonicalFormatPrice(bar.open, activeSymbol);
  if (legendHighEl) legendHighEl.textContent = canonicalFormatPrice(bar.high, activeSymbol);
  if (legendLowEl) legendLowEl.textContent = canonicalFormatPrice(bar.low, activeSymbol);
  if (legendCloseEl) legendCloseEl.textContent = canonicalFormatPrice(bar.close, activeSymbol);
  var legendChgEl = document.getElementById("legendChg");
  if (legendChgEl && typeof bar.close === "number" && typeof bar.open === "number" && bar.open > 0) {
    var chgPct = ((bar.close - bar.open) / bar.open) * 100;
    legendChgEl.textContent = (chgPct >= 0 ? "+" : "") + chgPct.toFixed(2) + "%";
    legendChgEl.style.color = chgPct >= 0 ? "#26a69a" : "#ef5350";
  }
  if (legendVolEl) legendVolEl.textContent = formatVolume(bar.volume);

  if (legEma10 && bar.ema10 !== undefined) {
    var v10 = legEma10.querySelector(".val");
    if (v10) v10.textContent = formatPrice(bar.ema10, activeSymbol);
  }
  if (legEma21 && bar.ema21 !== undefined) {
    var v21 = legEma21.querySelector(".val");
    if (v21) v21.textContent = formatPrice(bar.ema21, activeSymbol);
  }
  var legEma30 = document.getElementById("legEma30");
  if (legEma30 && bar.ema30 !== undefined) {
    var v30 = legEma30.querySelector(".val");
    if (v30) v30.textContent = formatPrice(bar.ema30, activeSymbol);
  }
  if (legSma50 && bar.sma50 !== undefined) {
    var v50 = legSma50.querySelector(".val");
    if (v50) v50.textContent = formatPrice(bar.sma50, activeSymbol);
  }
  if (legSma200 && bar.sma200 !== undefined) {
    var v200 = legSma200.querySelector(".val");
    if (v200) v200.textContent = formatPrice(bar.sma200, activeSymbol);
  }
  var legVwap = document.getElementById("legVwap");
  if (legVwap && bar.vwap !== undefined) {
    var vW = legVwap.querySelector(".val");
    if (vW) vW.textContent = formatPrice(bar.vwap, activeSymbol);
  }
  if (legRs && bar.rs !== undefined) {
    var vRs = legRs.querySelector(".val");
    if (vRs) vRs.textContent = Number(bar.rs).toFixed(2);
  }

  var legAvwap = document.getElementById("legAvwap");
  if (legAvwap && currentAvwapData) {
    updateAvwapHudAndLegend(currentAvwapData, bar ? bar.time : undefined);
  }
}

// ==========================================================================
// Historical Bars Ingestion & RS Benchmark Alignment
// ==========================================================================

function switchSymbol(symbol) {
  if (!symbol) return;
  if (activeSymbol === symbol && currentRawBars.length > 0 && loadedHistorySymbol === symbol) return;

  try {
    if (activeSymbol && typeof saveDrawingsForActiveSymbol === "function") {
      saveDrawingsForActiveSymbol();
    }
  } catch (e) { console.error("[switchSymbol] saveDrawings 失敗", e); }

  try {
    resetDrawToolToCursor();
  } catch (e) { console.error("[switchSymbol] resetDrawTool 失敗", e); }

  try {
    if (candleSeries && tvPriceLineHandles && tvPriceLineHandles.length > 0) {
      for (var pi = 0; pi < tvPriceLineHandles.length; pi++) {
        try { candleSeries.removePriceLine(tvPriceLineHandles[pi]); } catch (eLine) {}
      }
    }
    tvPriceLineHandles = [];
  } catch (e) { console.error("[switchSymbol] removePriceLines 失敗", e); }

  try {
    restorePriceLinesForSymbol(symbol);
  } catch (e) { console.error("[switchSymbol] restorePriceLines 失敗", e); }

  activeSymbol = symbol;
  safeSetStorage("tv_active_symbol", symbol);
  drawings = loadDrawingsForSymbol(symbol);
  isHistoryLoading = true;
  currentRawBars = [];
  loadedHistorySymbol = null;
  if (typeof window !== "undefined") window.loadedHistorySymbol = null;
  if (typeof globalThis !== "undefined") globalThis.loadedHistorySymbol = null;

  try {
    var isSubscribed = symbolsList && symbolsList.some(function(s) {
      return s.id === symbol || s.symbol === symbol;
    });
    var unBadge = document.getElementById("unsubscribedBadge");
    if (unBadge) {
      if (isSubscribed) {
        unBadge.style.display = "none";
        unBadge.textContent = "";
      } else {
        unBadge.textContent = "未訂閱即時報價 ";
        var subBtn = document.createElement("button");
        subBtn.id = "btnSubscribeActive";
        subBtn.className = "btn-subscribe-active";
        subBtn.textContent = "加入自選";
        subBtn.onclick = function(e) {
          if (e && e.stopPropagation) e.stopPropagation();
          subscribeSymbol(symbol, function(ok) {
            if (ok && unBadge) {
              unBadge.style.display = "none";
            }
          });
        };
        unBadge.appendChild(subBtn);
        unBadge.style.display = "inline-flex";
      }
    }
  } catch (e) { console.error("[switchSymbol] unsubscribedBadge 失敗", e); }

  try {
    if (candleSeries) candleSeries.setData([]);
    if (volumeSeries) volumeSeries.setData([]);
    if (ema10Series) ema10Series.setData([]);
    if (ema21Series) ema21Series.setData([]);
    if (ema30Series) ema30Series.setData([]);
    if (sma50Series) sma50Series.setData([]);
    if (sma200Series) sma200Series.setData([]);
    if (volSmaSeries) volSmaSeries.setData([]);
    if (bbUpperSeries) bbUpperSeries.setData([]);
    if (bbLowerSeries) bbLowerSeries.setData([]);
    if (vwapSeries) vwapSeries.setData([]);
    if (rsSeries) rsSeries.setData([]);
    if (rsLinePaneSeries) rsLinePaneSeries.setData([]);
    if (rsEmaPaneSeries) rsEmaPaneSeries.setData([]);
  } catch (e) { console.error("[switchSymbol] clearSeries 失敗", e); }

  try {
    if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
  } catch (e) { console.error("[switchSymbol] renderDrawingCanvas 失敗", e); }

  try {
    renderWatchlist();
  } catch (e) { console.error("[switchSymbol] renderWatchlist 失敗", e); }

  try {
    updateSymbolMetrics(symbol, symbolStates[symbol] || {}, null);
  } catch (e) { console.error("[switchSymbol] updateSymbolMetrics 失敗", e); }

  try {
    if ((!benchmarkRawBars || benchmarkRawBars.length === 0) && typeof loadBenchmarkHistory === "function") {
      loadBenchmarkHistory(rsBenchmarkTag);
    }
  } catch (e) { console.error("[switchSymbol] loadBenchmarkHistory 失敗", e); }

  if (currentEngine === "C") {
    if (typeof initTradingViewWidget === "function") initTradingViewWidget(symbol);
  } else {
    loadHistory(symbol);
  }
}

function switchChartEngine(engineMode) {
  currentEngine = engineMode;
  safeSetStorage("tv_chart_engine", engineMode);
  disposeSubPane(2);
  disposeSubPane(3);
  disposeSubPane(4);
  var tvWidgetContainer = document.getElementById("tvWidgetContainer");
  var chartContainer = document.getElementById("chartContainer");
  var deepvueHud = document.getElementById("deepvueHud");
  var chartLegend = document.getElementById("chartLegend");
  var dateRangeBar = document.getElementById("dateRangeBar");

  if (engineMode === "C") {
    if (chartContainer) chartContainer.style.display = "none";
    if (chartLegend) chartLegend.style.display = "none";
    if (deepvueHud) deepvueHud.style.display = "none";
    if (dateRangeBar) dateRangeBar.style.display = "none";
    if (tvWidgetContainer) {
      tvWidgetContainer.style.display = "block";
      initTradingViewWidget(activeSymbol);
    }
    applySplitLayout(activeSplitLayout || "1x1");
  } else {
    if (tvWidgetContainer) {
      tvWidgetContainer.style.display = "none";
      tvWidgetContainer.innerHTML = "";
      tvWidgetInstance = null;
    }
    if (chartContainer) chartContainer.style.display = "block";
    if (chartLegend) chartLegend.style.display = "flex";
    if (deepvueHud) deepvueHud.style.display = "flex";
    if (dateRangeBar) dateRangeBar.style.display = "flex";
    applySplitLayout(activeSplitLayout || "1x1");
    applyIndicatorVisibilities();
    if (typeof setPriceScaleMode === "function") {
      setPriceScaleMode(currentPriceScaleMode);
    }
    loadHistory(activeSymbol);
  }
}

function toTradingViewRealtimeSymbol(symbol) {
  if (!symbol) return "BATS:NVDA";
  var s = String(symbol).trim().toUpperCase();
  if (s.indexOf("BATS:") === 0 || s.indexOf("CBOE:") === 0) return s;
  if (s.indexOf("BINANCE:") === 0 || s.indexOf("COINBASE:") === 0 || s.indexOf("BYBIT:") === 0 || s.indexOf("OKX:") === 0) {
    return s;
  }
  // For US Equities & ETFs (NASDAQ, NYSE, AMEX, ARCA or plain ticker)
  // Cboe BZX Exchange (BATS) provides official real-time (0-delay) streaming on TradingView widgets
  var ticker = s.indexOf(":") !== -1 ? s.split(":").pop() : s;
  return "BATS:" + ticker;
}

function initTradingViewWidget(symbol) {
  var tvWidgetContainer = document.getElementById("tvWidgetContainer");
  if (!tvWidgetContainer) return;
  tvWidgetContainer.innerHTML = "";

  // Clear stale cached TradingView study properties so fresh distinct colors apply immediately
  // CAUTION: Strictly protect terminal application preferences (tv_chart_engine, tv_terminal_lang, etc.)
  try {
    var tvKeys = [];
    for (var k = 0; k < localStorage.length; k++) {
      var lk = localStorage.key(k);
      if (
        lk &&
        lk !== "tv_chart_engine" &&
        lk !== "tv_terminal_lang" &&
        lk !== "tv_terminal_theme" &&
        lk !== "tv_active_timeframe" &&
        lk !== "tv_price_scale_mode" &&
        lk !== "tv_indicator_config" &&
        lk !== "tv_symbols_cache" &&
        lk !== "tv_watchlist_groups" &&
        lk !== "tv_pinned_symbols" &&
        lk !== "tv_custom_screener_presets" &&
        lk !== "tv_session_token" &&
        (lk.indexOf("tradingview") !== -1 || lk.indexOf("tv_chart_studies") !== -1 || lk.indexOf("tv_chart_settings") !== -1)
      ) {
        tvKeys.push(lk);
      }
    }
    tvKeys.forEach(function(k) { localStorage.removeItem(k); });
  } catch (e) {}

  var pairName = getSymbolDisplayName(symbol);
  if (legendSymbolEl) legendSymbolEl.textContent = pairName;
  if (omniboxDisplay) omniboxDisplay.textContent = pairName + " • " + (getSymbolItem(symbol) ? getSymbolItem(symbol).name : symbol);

  var tvSymbol = toTradingViewRealtimeSymbol(symbol);

  var tvInterval = ({ "1m": "1", "5m": "5", "15m": "15", "1h": "60", "60m": "60", "1D": "D", "1W": "W" })[activeTimeframe] || "D";

  // User specification: Scheme C TradingView widget exclusively loads 21 EMA, 30 EMA, 200 EMA with distinct colors.
  // SPDR S&P 500 Compare study removed from default view per user request.
  var tvStudies = [];
  if (indicatorConfig.ema21 !== false) {
    tvStudies.push({
      id: "MAExp@tv-basicstudies",
      inputs: { length: 21 },
      override: { "plot.color": "#a855f7", "plot.linewidth": 2 },
      overrides: { "plot.color": "#a855f7", "plot.linewidth": 2 }
    });
  }
  if (indicatorConfig.ema30 !== false) {
    tvStudies.push({
      id: "MAExp@tv-basicstudies",
      inputs: { length: 30 },
      override: { "plot.color": "#f59e0b", "plot.linewidth": 2 },
      overrides: { "plot.color": "#f59e0b", "plot.linewidth": 2 }
    });
  }
  if (indicatorConfig.sma200 !== false || indicatorConfig.ema200 !== false) {
    tvStudies.push({
      id: "MAExp@tv-basicstudies",
      inputs: { length: 200 },
      override: { "plot.color": "#ec4899", "plot.linewidth": 2 },
      overrides: { "plot.color": "#ec4899", "plot.linewidth": 2 }
    });
  }
  if (indicatorConfig.tvCompareRs) {
    tvStudies.push({
      id: "Compare@tv-basicstudies",
      inputs: { symbol: toTradingViewRealtimeSymbol(rsBenchmarkTag || "SPY") },
      override: { "plot.color": "#3b82f6", "plot.linewidth": 2 },
      overrides: { "plot.color": "#3b82f6", "plot.linewidth": 2 }
    });
  }

  if (typeof TradingView !== "undefined" && TradingView.widget) {
    try {
      tvWidgetInstance = new TradingView.widget({
        autosize: true,
        symbol: tvSymbol,
        interval: tvInterval,
        timezone: "exchange",
        theme: currentTheme === "light" ? "light" : "dark",
        style: "1",
        locale: currentLang === "zh" ? "zh_TW" : "en",
        toolbar_bg: getChartThemePalette().tvToolbarBg,
        enable_publishing: false,
        allow_symbol_change: true,
        hide_side_toolbar: false,
        withdateranges: true,
        details: true,
        hotlist: true,
        calendar: true,
        studies: tvStudies,
        studies_overrides: {
          "volume.volume ma.display": true,
          "volume.volume ma.length": 50,
          "volume.volume.color.0": "#ef4444",
          "volume.volume.color.1": "#26a69a",
          "moving average exponential.0.plot.color": "#a855f7",
          "moving average exponential.0.plot.linewidth": 2,
          "moving average exponential.0.color": "#a855f7",
          "moving average exponential.1.plot.color": "#f59e0b",
          "moving average exponential.1.plot.linewidth": 2,
          "moving average exponential.1.color": "#f59e0b",
          "moving average exponential.2.plot.color": "#ec4899",
          "moving average exponential.2.plot.linewidth": 2,
          "moving average exponential.2.color": "#ec4899",
          "moving average exponential.plot.color": "#a855f7",
          "moving average exponential.plot.linewidth": 2,
          "moving average.0.plot.color": "#ec4899",
          "moving average.0.plot.linewidth": 2,
          "moving average.plot.color": "#ec4899",
          "moving average.plot.linewidth": 2,
          "vwap.plot.color": "#26a69a",
          "vwap.plot.linewidth": 2,
          "compare.plot.color": "#3b82f6",
          "compare.plot.linewidth": 2
        },
        disabled_features: ["use_localstorage_for_settings"],
        container_id: "tvWidgetContainer"
      });

      if (typeof tvWidgetInstance.onChartReady === "function") {
        tvWidgetInstance.onChartReady(function() {
          try {
            var chart = typeof tvWidgetInstance.activeChart === "function" ? tvWidgetInstance.activeChart() : null;
            if (chart && typeof chart.getAllStudies === "function") {
              var allStudies = chart.getAllStudies();
              var emaColors = ["#a855f7", "#f59e0b", "#ec4899"]; // 21 EMA (purple), 30 EMA (amber), 200 EMA (pink/red)
              var emaIdx = 0;
              for (var si = 0; si < allStudies.length; si++) {
                var st = allStudies[si];
                if (st.name && st.name.indexOf("Moving Average Exponential") !== -1) {
                  var cColor = emaColors[emaIdx % emaColors.length];
                  emaIdx++;
                  if (typeof chart.getStudyById === "function") {
                    var studyApi = chart.getStudyById(st.id);
                    if (studyApi && typeof studyApi.applyOverrides === "function") {
                      studyApi.applyOverrides({ "plot.color": cColor });
                    }
                  }
                } else if (st.name && st.name.indexOf("Compare") !== -1) {
                  if (typeof chart.getStudyById === "function") {
                    var compApi = chart.getStudyById(st.id);
                    if (compApi && typeof compApi.applyOverrides === "function") {
                      compApi.applyOverrides({ "plot.color": "#3b82f6" });
                    }
                  }
                }
              }
            }
          } catch (stErr) {
            console.debug("TV studies custom color override non-critical:", stErr);
          }
        });
      }
    } catch (e) {
      console.warn("TradingView widget init error", e);
    }
  }
}

function renderPositionSizingBadge() {
  var dict = i18n[currentLang] || i18n.en;
  var suggestedBadge = document.getElementById("rsSuggestedBadge");
  if (suggestedBadge) {
    if (lastAtrSizingInput) {
      var sizing = computeAtrPositionSizing({
        close: lastAtrSizingInput.close,
        atr: lastAtrSizingInput.atr,
        stopMult: 2.0,
        riskDollar: getRiskDollar(),
        timeframe: lastAtrSizingInput.timeframe
      });
      if (!sizing.error && sizing.shares > 0) {
        var template = dict.posSuggest || "Rec: {shares} shares (${stop} stop)";
        var str = template
          .replace("{shares}", String(sizing.shares))
          .replace("{stop}", canonicalFormatPrice(sizing.stopDistance));
        suggestedBadge.textContent = str;
        suggestedBadge.title = "";
      } else if (sizing.error === "daily_only") {
        suggestedBadge.textContent = dict.posSuggestEmpty || "Rec: -- shares";
        suggestedBadge.title = currentLang === "en"
          ? "Position size uses daily ATR only — switch to 1D"
          : "建議股數僅以日線 ATR 計算，請切換到日線";
      } else {
        suggestedBadge.textContent = dict.posSuggestEmpty || "Rec: -- shares";
        suggestedBadge.title = "";
      }
    } else {
      suggestedBadge.textContent = dict.posSuggestEmpty || "Rec: -- shares";
      suggestedBadge.title = "";
    }
  }
  var atrPctBadge = document.getElementById("rsAtrPctBadge");
  if (atrPctBadge && lastIsSqueeze) {
    atrPctBadge.title = dict.volSqueeze || "Volatility Squeeze";
  }
}

function updateRsPane(bars, benchBars) {
  if (!bars || bars.length === 0) return;
  if (!window.Indicators) return;

  var bBars = benchBars || benchmarkRawBars || [];
  var currentKey = (rsBenchmarkTag || "SPY") + "|" + (activeTimeframe || "1D");
  var now = Date.now();
  var isSameKey = benchmarkLoadState.key === currentKey;
  var isInFlight = isSameKey && benchmarkLoadState.inFlight;
  var isCoolingDown = isSameKey && (now - benchmarkLoadState.failedAt < 30000);
  if ((!bBars || bBars.length === 0) && !isInFlight && !isCoolingDown && typeof loadBenchmarkHistory === "function") {
    loadBenchmarkHistory(rsBenchmarkTag);
  }

  var currentMode = (indicatorConfig && indicatorConfig.rsMode) || "mansfield";

  var rsPaneTitleEl = document.getElementById("rsPaneTitle");
  if (rsPaneTitleEl) {
    var modeLabel = currentMode === "mansfield" ? "Mansfield 52" : "Deepvue Ratio";
    var titleText = "RS Line (" + modeLabel + ") vs " + (rsBenchmarkTag || "SPY");
    if ((!bBars || bBars.length === 0) && isSameKey && benchmarkLoadState.failedAt > 0) {
      titleText += " (基準資料載入失敗)";
    }
    rsPaneTitleEl.textContent = titleText;
  }

  // Calculate ATR Toolkit (ATR%, Squeeze, and Position Sizing)
  // Always calculated from target stock bars, independent of benchmark
  var toolkit = window.Indicators.calcATRToolkit(bars, bBars, {
    riskDollar: 500,
    stopMult: 2.0,
    mansfieldLen: 52
  });
  // RS 副圖的時間軸要和主圖相同，邏輯範圍同步才會對齊
  var rsAxis = getSeriesTimeAxis(candleSeries, bars);
  if (rsLinePaneSeries && typeof rsLinePaneSeries.setData === "function") {
    rsLinePaneSeries.setData(padToTimeAxis([], rsAxis));
  }
  if (rsEmaPaneSeries && typeof rsEmaPaneSeries.setData === "function") {
    rsEmaPaneSeries.setData(padToTimeAxis([], rsAxis));
  }

  // 1. Update ATR% badge & Squeeze indicator
  var atrPctBadge = document.getElementById("rsAtrPctBadge");
  if (atrPctBadge && toolkit.atrPct && toolkit.atrPct.length) {
    var latestAtrPct = toolkit.atrPct[toolkit.atrPct.length - 1];
    var latestNum = typeof latestAtrPct.value === "number" ? latestAtrPct.value : (latestAtrPct.atrPct || 0);
    atrPctBadge.textContent = "ATR%: " + Number(latestNum).toFixed(2) + "%";
    lastIsSqueeze = Boolean(latestAtrPct.isSqueeze);
    if (lastIsSqueeze) {
      atrPctBadge.className = "rs-metric-badge squeeze";
      var dict = i18n[currentLang] || i18n.en;
      atrPctBadge.title = dict.volSqueeze || "Volatility Squeeze";
    } else {
      atrPctBadge.className = "rs-metric-badge";
      atrPctBadge.title = "";
    }
  }

  // 2. Update Position Sizing (建議股數)
  var latestBar = bars[bars.length - 1];
  lastAtrSizingInput = {
    close: latestBar ? latestBar.close : null,
    atr: (toolkit && toolkit.positionSizing) ? toolkit.positionSizing.latestAtr : null,
    timeframe: activeTimeframe
  };
  if (toolkit.positionSizing) {
    lastPositionSizing = toolkit.positionSizing;
  }
  renderPositionSizingBadge();

  if (currentMode === "mansfield") {
    if (rsLinePaneSeries && toolkit.mansfieldRS && toolkit.mansfieldRS.length > 0) {
      if (typeof rsLinePaneSeries.applyOptions === "function") {
        rsLinePaneSeries.applyOptions({
          color: "#26a69a",
          lineWidth: 2,
          title: "Mansfield RS (52)"
        });
      }
      rsLinePaneSeries.setData(padToTimeAxis(toolkit.mansfieldRS, rsAxis));

      if (!rsZeroPriceLine && typeof rsLinePaneSeries.createPriceLine === "function") {
        try {
          rsZeroPriceLine = rsLinePaneSeries.createPriceLine({
            price: 0.0,
            color: "#787b86",
            lineWidth: 1,
            lineStyle: (typeof LightweightCharts !== "undefined" && LightweightCharts.LineStyle) ? LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: "0.00"
          });
        } catch (e) {}
      }
    }

    if (rsEmaPaneSeries && toolkit.atrPct && toolkit.atrPct.length) {
      var atrLineData = toolkit.atrPct.map(function(item) {
        var numVal = typeof item.value === "number" ? item.value : (item.atrPct || 0);
        return { time: item.time, value: Number(numVal.toFixed(2)) };
      });
      if (typeof rsEmaPaneSeries.applyOptions === "function") {
        rsEmaPaneSeries.applyOptions({
          color: "#38bdf8",
          lineWidth: 1,
          lineStyle: (typeof LightweightCharts !== "undefined" && LightweightCharts.LineStyle) ? LightweightCharts.LineStyle.Dotted : 1,
          title: "ATR%"
        });
      }
      rsEmaPaneSeries.setData(padToTimeAxis(atrLineData, rsAxis));
    }
  } else {
    // Deepvue RS Mode
    if (rsZeroPriceLine && rsLinePaneSeries && typeof rsLinePaneSeries.removePriceLine === "function") {
      try {
        rsLinePaneSeries.removePriceLine(rsZeroPriceLine);
      } catch (e) {}
      rsZeroPriceLine = null;
    }

    if (bBars && bBars.length > 0) {
      var rsData = window.Indicators.calcRelativeStrength(bars, bBars);
      if (rsLinePaneSeries && rsData) {
        if (typeof rsLinePaneSeries.applyOptions === "function") {
          rsLinePaneSeries.applyOptions({
            color: "#2962ff",
            lineWidth: 2,
            title: "RS"
          });
        }
        rsLinePaneSeries.setData(padToTimeAxis(rsData, rsAxis));
      }
      if (rsEmaPaneSeries && rsData && rsData.length >= 21) {
        var rsBarsForEma = rsData.map(function(r) { return { time: r.time, close: r.value }; });
        var rsEma = window.Indicators.calcEMA(rsBarsForEma, 21);
        if (typeof rsEmaPaneSeries.applyOptions === "function") {
          rsEmaPaneSeries.applyOptions({
            color: "#f59e0b",
            lineWidth: 1,
            lineStyle: (typeof LightweightCharts !== "undefined" && LightweightCharts.LineStyle) ? LightweightCharts.LineStyle.Dashed : 2,
            title: "RS 21 EMA"
          });
        }
        rsEmaPaneSeries.setData(padToTimeAxis(rsEma, rsAxis));
      }
    }
  }

  // RS 資料剛換過時 RS 圖會自己調整範圍，這裡對齊回主圖
  if (chart && rsChart) {
    try { syncLogicalRangeTo(rsChart, chart.timeScale().getVisibleLogicalRange()); } catch (e) {}
  }

  // 3. Update RS Rating
  if (bBars && bBars.length > 0) {
    var rsRating = window.Indicators.calcRsRating(bars, bBars);
    var badge = document.getElementById("rsRatingBadge");
    if (badge) {
      if (rsRating !== null) {
        badge.textContent = "RS Rating: " + rsRating;
        badge.className = "rs-rating-badge " + (rsRating >= 80 ? "rating-high" : (rsRating >= 60 ? "rating-med" : "rating-low"));
      } else {
        badge.textContent = "RS Rating: --";
        badge.className = "rs-rating-badge";
      }
    }
  }

  // 4. RS Line New High (Blue Dot) Markers
  if (!indicatorConfig.rsLine) {
    if (rsLinePaneSeries && typeof rsLinePaneSeries.setMarkers === "function") {
      try { rsLinePaneSeries.setMarkers([]); } catch (e) {}
    }
    if (candleSeries && typeof candleSeries.setMarkers === "function") {
      try { candleSeries.setMarkers([]); } catch (e) {}
    }
    var nhBadgeOff = document.getElementById("rsNewHighBadge");
    if (nhBadgeOff) nhBadgeOff.style.display = "none";
    return;
  }

  var rsMarkers = [];
  if (bBars && bBars.length > 0 && window.Indicators && typeof window.Indicators.calcRsNewHighMarkers === "function") {
    rsMarkers = window.Indicators.calcRsNewHighMarkers(bars, bBars);
  }
  if (rsLinePaneSeries && typeof rsLinePaneSeries.setMarkers === "function") {
    try {
      rsLinePaneSeries.setMarkers(rsMarkers);
    } catch (e) {}
  }
  if (candleSeries && typeof candleSeries.setMarkers === "function") {
    try {
      var candleMarkers = rsMarkers.map(function(m) {
        return {
          time: m.time,
          position: "aboveBar",
          color: "#2962ff",
          shape: "circle",
          text: "RS NH"
        };
      });
      candleSeries.setMarkers(candleMarkers);
    } catch (e) {}
  }

  // Update #rsNewHighBadge
  var nhBadge = document.getElementById("rsNewHighBadge");
  if (nhBadge) {
    var dict = i18n[currentLang] || i18n.en;
    var last5BarsTime = (bars && bars.length >= 5) ? bars[bars.length - 5].time : 0;
    var hasRecentNh = rsMarkers.some(function(m) { return m.time >= last5BarsTime; });
    if (hasRecentNh) {
      nhBadge.style.display = "inline-flex";
      nhBadge.textContent = dict.rsNewHighBadge || "🔵 RS NH";
      nhBadge.title = dict.rsNewHighTooltip || "RS New High before Price (Leading Indicator)";
    } else {
      nhBadge.style.display = "none";
    }
  }
}

function loadBenchmarkHistory(benchTag) {
  // Cascades to subCharts via renderSubPaneRs
  var targetTag = benchTag || rsBenchmarkTag || "SPY";
  var benchSym = resolveBenchmarkSymbol(targetTag);
  var benchTf = activeTimeframe || "1D";
  var key = targetTag + "|" + benchTf;
  var now = Date.now();

  if (benchmarkLoadState.key === key && benchmarkLoadState.inFlight && benchmarkLoadState.promise) {
    return benchmarkLoadState.promise;
  }

  if (benchmarkLoadState.key === key && !benchmarkLoadState.inFlight && (now - benchmarkLoadState.failedAt < 30000)) {
    return Promise.resolve({ bars: [] });
  }

  benchmarkReqSeq++;
  var reqSeq = benchmarkReqSeq;
  var expectedTf = benchTf;
  var expectedTag = targetTag;

  benchmarkLoadState.key = key;
  benchmarkLoadState.inFlight = true;

  var fetchUrl = "/api/history?seed=true&timeframe=" + encodeURIComponent(benchTf) + "&symbol=" + encodeURIComponent(benchSym);

  var p = fetch(fetchUrl)
    .then(function(res) {
      if (!res.ok) {
        throw new Error("Benchmark HTTP " + res.status);
      }
      return res.json();
    })
    .then(function(data) {
      if (reqSeq !== benchmarkReqSeq || activeTimeframe !== expectedTf || rsBenchmarkTag !== expectedTag) {
        return;
      }
      var bars = data && data.bars ? data.bars : [];
      if (bars.length === 0) {
        benchmarkLoadState.inFlight = false;
        benchmarkLoadState.failedAt = Date.now();
        benchmarkLoadState.promise = null;
        benchmarkRawBars = [];
        var rsPaneTitleEl = document.getElementById("rsPaneTitle");
        if (rsPaneTitleEl) {
          var modeLabel = (indicatorConfig && indicatorConfig.rsMode === "deepvue") ? "Deepvue Ratio" : "Mansfield 52";
          rsPaneTitleEl.textContent = "RS Line (" + modeLabel + ") vs " + (rsBenchmarkTag || "SPY") + " (基準資料載入失敗)";
        }
        return { bars: [] };
      }

      benchmarkLoadState.inFlight = false;
      benchmarkLoadState.failedAt = 0;
      benchmarkLoadState.promise = null;
      benchmarkRawBars = bars;

      if (rsSeries && currentRawBars && currentRawBars.length > 0 && window.Indicators && benchmarkRawBars.length > 0) {
        var rsData = window.Indicators.calcRelativeStrength(currentRawBars, benchmarkRawBars);
        rsSeries.setData(rsData);
      }
      updateRsPane(currentRawBars, benchmarkRawBars);

      for (var sp = 2; sp <= 4; sp++) {
        var spTf = paneTimeframes[sp] || "1D";
        if (spTf === expectedTf) {
          if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
            renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
          }
        }
      }
      return data;
    })
    .catch(function(e) {
      console.debug("Failed to load benchmark history:", e);
      benchmarkLoadState.inFlight = false;
      benchmarkLoadState.failedAt = Date.now();
      benchmarkLoadState.promise = null;
      benchmarkRawBars = [];
      var rsPaneTitleEl = document.getElementById("rsPaneTitle");
      if (rsPaneTitleEl) {
        var modeLabel = (indicatorConfig && indicatorConfig.rsMode === "deepvue") ? "Deepvue Ratio" : "Mansfield 52";
        rsPaneTitleEl.textContent = "RS Line (" + modeLabel + ") vs " + (rsBenchmarkTag || "SPY") + " (基準資料載入失敗)";
      }
    });

  benchmarkLoadState.promise = p;
  return p;
}

function applyHistoryPayload(data, symbol, currentRequestId) {
  if (currentRequestId !== activeHistoryRequestId || symbol !== activeSymbol) return;

  var rawBars = data.bars || [];
  if (rawBars.length > 0) {
    rawBars.sort(function(a, b) { return a.time - b.time; });

    var uniqueBars = [];
    for (var i = 0; i < rawBars.length; i++) {
      var bar = rawBars[i];
      if (!bar) continue;
      var barTime = normalizeBarTime(bar.time);
      if (barTime <= 0) continue;
      var cleanBar = {
        time: barTime,
        open: safeNum(bar.open),
        high: safeNum(bar.high),
        low: safeNum(bar.low),
        close: safeNum(bar.close),
        volume: safeNum(bar.volume)
      };
      if (uniqueBars.length === 0 || cleanBar.time > uniqueBars[uniqueBars.length - 1].time) {
        uniqueBars.push(cleanBar);
      } else if (cleanBar.time === uniqueBars[uniqueBars.length - 1].time) {
        uniqueBars[uniqueBars.length - 1] = cleanBar;
      }
    }

    currentRawBars = uniqueBars;
    liveBucketState = null;
    if (uniqueBars.length > 0) {
      var lastRawBar = uniqueBars[uniqueBars.length - 1];
      lastBarTime = lastRawBar.time;
      lastBarObj = {
        time: lastRawBar.time,
        open: lastRawBar.open,
        high: lastRawBar.high,
        low: lastRawBar.low,
        close: lastRawBar.close,
        volume: lastRawBar.volume || 0
      };
      latestBarData = Object.assign({}, lastBarObj);
    } else {
      lastBarTime = null;
      lastBarObj = null;
      latestBarData = null;
    }
    try {
      renderChartData(uniqueBars, { viewKey: String(symbol) + "|" + String(activeTimeframe) + "|" + String(currentEngine) });
      loadedHistorySymbol = symbol;
      if (typeof window !== "undefined") window.loadedHistorySymbol = symbol;
      if (typeof globalThis !== "undefined") globalThis.loadedHistorySymbol = symbol;
    } catch (e) {
      console.error("[applyHistoryPayload] 畫圖失敗 " + symbol, e);
    }
    if (typeof setPriceScaleMode === "function") {
      setPriceScaleMode(currentPriceScaleMode);
    }
  } else {
    if (candleSeries) candleSeries.setData([]);
    if (volumeSeries) volumeSeries.setData([]);
    if (ema10Series) ema10Series.setData([]);
    if (ema21Series) ema21Series.setData([]);
    if (ema30Series) ema30Series.setData([]);
    if (sma50Series) sma50Series.setData([]);
    if (sma200Series) sma200Series.setData([]);
    if (volSmaSeries) volSmaSeries.setData([]);
    if (bbUpperSeries) bbUpperSeries.setData([]);
    if (bbLowerSeries) bbLowerSeries.setData([]);
    if (vwapSeries) vwapSeries.setData([]);
    if (rsSeries) rsSeries.setData([]);
    if (rsLinePaneSeries) rsLinePaneSeries.setData([]);
    if (rsEmaPaneSeries) rsEmaPaneSeries.setData([]);
    currentRawBars = [];
    lastBarTime = null;
    lastBarObj = null;
    loadedHistorySymbol = null;
    if (typeof window !== "undefined") window.loadedHistorySymbol = null;
    if (typeof globalThis !== "undefined") globalThis.loadedHistorySymbol = null;
  }
}

function loadHistory(symbol, tf, isBackfill) {
  var targetTf = tf || activeTimeframe || "1m";
  var currentRequestId = ++activeHistoryRequestId;
  isHistoryLoading = true;

  if (candleSeries && typeof candleSeries.setMarkers === "function") {
    try { candleSeries.setMarkers([]); } catch (e) {}
  }
  if (rsLinePaneSeries && typeof rsLinePaneSeries.setMarkers === "function") {
    try { rsLinePaneSeries.setMarkers([]); } catch (e) {}
  }
  var nhBadge = document.getElementById("rsNewHighBadge");
  if (nhBadge) nhBadge.style.display = "none";

  if (historyAbortController) {
    historyAbortController.abort();
  }
  historyAbortController = new AbortController();

  if (equityBanner) {
    var isCrypto = /^(BINANCE|COINBASE|BYBIT|OKX):/i.test(symbol) || /USDT$|BUSD$/i.test(symbol);
    var isUsEquity = !isCrypto;
    equityBanner.style.display = (targetTf === "1m" && isUsEquity) ? "flex" : "none";
  }

  var isIntraday = (targetTf !== "1D" && targetTf !== "1W");
  if (chart && typeof chart.timeScale === "function") {
    try {
      chart.timeScale().applyOptions({ timeVisible: isIntraday, secondsVisible: false });
    } catch (e) {}
  }
  if (rsChart && typeof rsChart.timeScale === "function") {
    try {
      rsChart.timeScale().applyOptions({ timeVisible: isIntraday, secondsVisible: false });
    } catch (e) {}
  }

  if ((!benchmarkRawBars || benchmarkRawBars.length === 0) && typeof loadBenchmarkHistory === "function") {
    loadBenchmarkHistory(rsBenchmarkTag);
  }

  var pairName = getSymbolDisplayName(symbol);
  if (legendSymbolEl) legendSymbolEl.textContent = pairName;
  if (legendIntervalEl) legendIntervalEl.textContent = targetTf.toUpperCase();
  if (chartWatermarkEl) chartWatermarkEl.textContent = pairName + " • " + targetTf.toUpperCase();
  if (omniboxDisplay) omniboxDisplay.textContent = pairName + " • " + (getSymbolItem(symbol) ? getSymbolItem(symbol).name : symbol);

  var reqEngine = currentEngine;
  var cacheKey = symbol + "|" + targetTf + "|" + reqEngine;
  if (!isBackfill) {
    var cachedData = getCachedHistory(cacheKey);
    if (cachedData) {
      try {
        applyHistoryPayload(cachedData, symbol, currentRequestId);
      } finally {
        isHistoryLoading = false;
      }
      prefetchNextWatchlistSymbols(symbol, targetTf, reqEngine);
      return Promise.resolve(cachedData);
    }
  }

  var targetUrl = "/api/history?seed=true&timeframe=" + encodeURIComponent(targetTf) +
                  "&engine=" + encodeURIComponent(reqEngine) +
                  "&symbol=" + encodeURIComponent(symbol) +
                  (isBackfill ? "&backfill=true" : "");

  return fetch(targetUrl, { signal: historyAbortController.signal })
    .then(function(targetRes) {
      if (!targetRes.ok) throw new Error("HTTP " + targetRes.status);
      return targetRes.json();
    })
    .then(function(data) {
      if (!isBackfill && data && Array.isArray(data.bars) && data.bars.length > 0) {
        setCachedHistory(cacheKey, data);
      }
      applyHistoryPayload(data, symbol, currentRequestId);
      prefetchNextWatchlistSymbols(symbol, targetTf, reqEngine);
    })
    .catch(function(err) {
      if (err && err.name !== "AbortError") {
        console.error("[loadHistory] " + symbol + " " + targetTf, err);
      }
    })
    .finally(function() {
      if (currentRequestId === activeHistoryRequestId) {
        isHistoryLoading = false;
      }
    });
}

function renderChartData(bars, options) {
  if (!candleSeries || !bars || bars.length === 0) return;

  var initialRange = (chart && typeof chart.timeScale === "function" && typeof chart.timeScale().getVisibleLogicalRange === "function") ? chart.timeScale().getVisibleLogicalRange() : null;
  var prevTimes = getSeriesTimeAxis(candleSeries, null);

  var candleData = [];
  var isHollow = (currentCandleStyle === "hollow");
  for (var i = 0; i < bars.length; i++) {
    var b = bars[i];
    if (isHollow) {
      var prevClose = (i > 0) ? bars[i - 1].close : b.open;
      var isBullishClose = b.close >= prevClose;
      var strokeColor = isBullishClose ? "#26a69a" : "#ef5350";
      var isHollowBody = b.close >= b.open;
      candleData.push({
        time: b.time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        color: isHollowBody ? "transparent" : strokeColor,
        borderColor: strokeColor,
        wickColor: strokeColor
      });
    } else {
      candleData.push({
        time: b.time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close
      });
    }
  }

  var volumeData = bars.map(function(b) {
    return {
      time: b.time,
      value: b.volume,
      color: b.close >= b.open ? "rgba(38, 166, 154, 0.45)" : "rgba(239, 83, 80, 0.45)"
    };
  });

  candleSeries.setData(candleData);
  volumeSeries.setData(volumeData);

  if (window.Indicators) {
    var ema10 = window.Indicators.calcEMA(bars, 10);
    var ema21 = window.Indicators.calcEMA(bars, 21);
    var ema30 = window.Indicators.calcEMA(bars, 30);
    var sma50 = window.Indicators.calcSMA(bars, 50);
    var sma200 = window.Indicators.calcSMA(bars, 200);
    var volSma = window.Indicators.calcVolumeSMA(bars, 50);
    var bb = window.Indicators.calcBollingerBands(bars, 20, 2);

    if (ema10Series) ema10Series.setData(ema10);
    if (ema21Series) ema21Series.setData(ema21);
    if (ema30Series) ema30Series.setData(ema30);
    if (vwapSeries && window.Indicators.calcVWAP) {
      var vwapData = window.Indicators.calcVWAP(bars, { timeframe: activeTimeframe, symbol: activeSymbol });
      vwapSeries.setData(vwapData);
    }
    if (sma50Series) sma50Series.setData(sma50);
    if (sma200Series) sma200Series.setData(sma200);
    if (volSmaSeries) volSmaSeries.setData(volSma);
    if (bbUpperSeries) bbUpperSeries.setData(bb.upper);
    if (bbLowerSeries) bbLowerSeries.setData(bb.lower);

    if (rsSeries && indicatorConfig.rsOverlayMain && benchmarkRawBars && benchmarkRawBars.length > 0) {
      var rsData = window.Indicators.calcRelativeStrength(bars, benchmarkRawBars);
      rsSeries.setData(rsData);
    } else if (rsSeries) {
      rsSeries.setData([]);
    }
    updateRsPane(bars, benchmarkRawBars);
    calcAndRenderAvwap();
  }

  var lastBar = bars[bars.length - 1];
  lastBarTime = lastBar.time;
  lastBarObj = { time: lastBar.time, open: lastBar.open, high: lastBar.high, low: lastBar.low, close: lastBar.close, volume: lastBar.volume };
  latestBarData = lastBar;
  updateCrosshairLegend(lastBar);
  updateSymbolMetrics(activeSymbol, symbolStates[activeSymbol] || {}, lastBar);
  updateDeepvueHud(bars);

  var viewKey = (options && typeof options.viewKey === "string" && options.viewKey)
    ? options.viewKey
    : (String(activeSymbol) + "|" + String(activeTimeframe) + "|" + String(currentEngine));

  if (options && options.keepView === true) {
    if (initialRange) {
      var nextTimes = (typeof getSeriesTimeAxis === "function") ? getSeriesTimeAxis(null, bars) : [];
      var preservedRange = computePreservedLogicalRange(prevTimes, nextTimes, initialRange);
      if (preservedRange) {
        if (chart && typeof chart.timeScale === "function") {
          try {
            var tsK = chart.timeScale();
            if (tsK && typeof tsK.setVisibleLogicalRange === "function") {
              tsK.setVisibleLogicalRange({ from: preservedRange.from, to: preservedRange.to });
            }
          } catch (eK) {}
        }
      } else if ((!prevTimes || prevTimes.length === 0) && Number.isFinite(initialRange.from) && Number.isFinite(initialRange.to)) {
        if (chart && typeof chart.timeScale === "function") {
          try {
            var tsFallbackK = chart.timeScale();
            if (tsFallbackK && typeof tsFallbackK.setVisibleLogicalRange === "function") {
              tsFallbackK.setVisibleLogicalRange({ from: initialRange.from, to: initialRange.to });
            }
          } catch (eFK) {}
        }
      } else {
        resetChartView(chart, bars.length);
      }
    } else {
      resetChartView(chart, bars.length);
    }
  } else if ((options && options.resetView === true) || viewKey !== lastChartViewKey || !initialRange) {
    // 換標的、週期或引擎時價格軸回到自動
    if (viewKey !== lastChartViewKey && typeof setAutoScale === "function") {
      setAutoScale(true);
    }
    resetChartView(chart, bars.length);
    lastChartViewKey = viewKey;
  } else {
    var nextTimes = (typeof getSeriesTimeAxis === "function") ? getSeriesTimeAxis(null, bars) : [];
    var preservedRange = computePreservedLogicalRange(prevTimes, nextTimes, initialRange);
    if (preservedRange) {
      if (chart && typeof chart.timeScale === "function") {
        try {
          var tsCurrent = chart.timeScale();
          if (tsCurrent && typeof tsCurrent.setVisibleLogicalRange === "function") {
            tsCurrent.setVisibleLogicalRange({ from: preservedRange.from, to: preservedRange.to });
          }
        } catch (e) {}
      }
    } else if ((!prevTimes || prevTimes.length === 0) && Number.isFinite(initialRange.from) && Number.isFinite(initialRange.to)) {
      if (chart && typeof chart.timeScale === "function") {
        try {
          var tsFallbackCurrent = chart.timeScale();
          if (tsFallbackCurrent && typeof tsFallbackCurrent.setVisibleLogicalRange === "function") {
            tsFallbackCurrent.setVisibleLogicalRange({ from: initialRange.from, to: initialRange.to });
          }
        } catch (eFC) {}
      }
    } else {
      resetChartView(chart, bars.length);
    }
  }
  if (typeof setPriceScaleMode === "function") {
    setPriceScaleMode(currentPriceScaleMode);
  }
}

// ==========================================================================
// Real-Time Bar Processing & Invariants Enforcement
// ==========================================================================

function getHudHighLabelMeta(timeframe, lang) {
  var tf = timeframe || "1D";
  var isEn = (lang || (typeof currentLang !== "undefined" ? currentLang : "zh")) === "en";
  if (tf === "1D" || tf === "1W") {
    return {
      is52w: true,
      windowBars: tf === "1W" ? 52 : 252,
      label: isEn ? "52W High" : "距52週高",
      title: isEn ? "Distance from 52-week high" : "距離近 52 週最高價百分比"
    };
  }
  return {
    is52w: false,
    windowBars: null,
    label: isEn ? "Range High" : "距區間高",
    title: isEn
      ? "Distance from loaded intraday range high (not 52-week high)"
      : "距離目前盤中載入區間最高價（非 52 週高點）"
  };
}

if (typeof window !== "undefined") {
  window.getHudHighLabelMeta = getHudHighLabelMeta;
}
if (typeof globalThis !== "undefined") {
  globalThis.getHudHighLabelMeta = getHudHighLabelMeta;
}

function updateDeepvueHud(bars) {
  if (!bars || bars.length === 0) return;
  var hud52wLabel = document.getElementById("hud52wLabel");
  var hud52wVal = document.getElementById("hud52wVal");
  var hudVol50Val = document.getElementById("hudVol50Val");
  var hudAdrVal = document.getElementById("hudAdrVal");

  var highMeta = getHudHighLabelMeta(activeTimeframe, currentLang);
  if (hud52wLabel) {
    hud52wLabel.textContent = highMeta.label;
    hud52wLabel.title = highMeta.title;
  }

  var highStartIdx = (highMeta.windowBars && bars.length > highMeta.windowBars)
    ? (bars.length - highMeta.windowBars)
    : 0;
  var maxHigh = 0;
  var sumVol50 = 0;
  var volCount = 0;
  var sumRangePct = 0;
  var adrCount = Math.min(bars.length, 20);

  for (var i = 0; i < bars.length; i++) {
    if (i >= highStartIdx && bars[i].high > maxHigh) maxHigh = bars[i].high;
    if (i >= bars.length - 50) {
      sumVol50 += (bars[i].volume || 0);
      volCount++;
    }
    if (i >= bars.length - adrCount) {
      var rng = (bars[i].high - bars[i].low) / (bars[i].low || 1);
      sumRangePct += rng;
    }
  }

  var lastBar = bars[bars.length - 1];
  if (hud52wVal && maxHigh > 0) {
    var diff52w = ((lastBar.close - maxHigh) / maxHigh) * 100;
    hud52wVal.textContent = diff52w >= 0 ? "NEW HIGH" : diff52w.toFixed(1) + "%";
    hud52wVal.className = "hud-val " + (diff52w >= -5 ? "up" : "down");
  }

  var hudVol50Label = document.getElementById("hudVol50Label");
  var hudAdrLabel = document.getElementById("hudAdrLabel");

  if (activeTimeframe === "1D") {
    if (hudVol50Label) hudVol50Label.title = "";
    if (hudAdrLabel) hudAdrLabel.title = "";

    if (hudVol50Val && volCount > 0) {
      var avgVol50 = sumVol50 / volCount;
      var ratio = avgVol50 > 0 ? (lastBar.volume / avgVol50) : 1;
      hudVol50Val.textContent = ratio.toFixed(2) + "x";
      hudVol50Val.className = "hud-val " + (ratio >= 1.5 ? "up" : "");
      hudVol50Val.title = "";
    }
    if (hudAdrVal && adrCount > 0) {
      var adrPct = (sumRangePct / adrCount) * 100;
      hudAdrVal.textContent = adrPct.toFixed(1) + "%";
      hudAdrVal.title = "";
    }
  } else {
    var notDailyTitle = "Daily only — switch to 1D";
    if (hudVol50Label) hudVol50Label.title = notDailyTitle;
    if (hudAdrLabel) hudAdrLabel.title = notDailyTitle;
    if (hudVol50Val) {
      hudVol50Val.textContent = "—";
      hudVol50Val.className = "hud-val";
      hudVol50Val.title = notDailyTitle;
    }
    if (hudAdrVal) {
      hudAdrVal.textContent = "—";
      hudAdrVal.className = "hud-val";
      hudAdrVal.title = notDailyTitle;
    }
  }
}

function handleLiveBar(symbol, bar) {
  if (isHistoryLoading) return;
  if (symbol !== activeSymbol || !candleSeries || !bar) return;

  var price = safeNum(bar.close);
  if (!isValidPrice(price)) return;

  var minuteBar = {
    time: normalizeBarTime(bar.time),
    open: safeNum(bar.open),
    high: safeNum(bar.high),
    low: safeNum(bar.low),
    close: price,
    volume: Math.max(0, safeNum(bar.volume))
  };
  if (minuteBar.time <= 0) return;
  if (!isValidPrice(minuteBar.open) || !isValidPrice(minuteBar.high) || !isValidPrice(minuteBar.low)) return;

  // 第 4 段：依交易所時段決定這根 1 分鐘 K 棒屬於哪一根 K 棒（美股以紐約時間、加密貨幣以 UTC），
  // 並用「同一分鐘取代、換分鐘累加」的方式計算成交量。
  var lastRawBar = (currentRawBars.length > 0) ? currentRawBars[currentRawBars.length - 1] : null;
  var placement = computeLiveBucket({
    symbol: symbol,
    timeframe: activeTimeframe,
    tickTime: minuteBar.time,
    lastBarTime: lastRawBar ? lastRawBar.time : null
  });

  var mainBar = null;
  if (placement.action !== "ignore") {
    var prevState = null;
    if (liveBucketState && liveBucketState.symbol === symbol && liveBucketState.timeframe === activeTimeframe &&
        liveBucketState.bucketTime === placement.time && lastRawBar && lastRawBar === liveBucketState.lastWritten) {
      prevState = liveBucketState;
    }
    var nextState = applyMinuteBarToBucket(prevState, {
      bucketTime: placement.time,
      timeframe: activeTimeframe,
      minute: minuteBar,
      historyBar: (placement.action === "update") ? lastRawBar : null
    });
    if (nextState) {
      mainBar = {
        time: nextState.bucketTime,
        open: nextState.open,
        high: nextState.high,
        low: nextState.low,
        close: nextState.close,
        volume: nextState.volume
      };
      nextState.symbol = symbol;
      nextState.timeframe = activeTimeframe;
      nextState.lastWritten = mainBar;
      liveBucketState = nextState;
    }
  }

  if (mainBar) {
    _renderLiveMainBar(mainBar);
  }
  if (isAppNapSuspended || (typeof document !== "undefined" && document.hidden)) {
    hasPendingChartUpdate = true;
    return;
  }
  forwardLiveBarToSubCharts(symbol, minuteBar, mainBar);
}

function _renderLiveMainBar(liveBar) {
  var barTime = liveBar.time;
  var open = liveBar.open;
  var high = liveBar.high;
  var low = liveBar.low;
  var close = liveBar.close;
  var volume = liveBar.volume;

  if (currentRawBars.length > 0 && currentRawBars[currentRawBars.length - 1].time === barTime) {
    currentRawBars[currentRawBars.length - 1] = liveBar;
  } else if (currentRawBars.length === 0 || barTime > currentRawBars[currentRawBars.length - 1].time) {
    currentRawBars.push(liveBar);
  }

  lastBarTime = barTime;
  lastBarObj = { time: barTime, open: open, high: high, low: low, close: close, volume: volume };
  latestBarData = liveBar;

  if (isAppNapSuspended || (typeof document !== "undefined" && document.hidden)) {
    hasPendingChartUpdate = true;
    return;
  }

  if (currentCandleStyle === "hollow") {
    var prevClose = (currentRawBars.length > 1) ? currentRawBars[currentRawBars.length - 2].close : open;
    var isBullishClose = close >= prevClose;
    var strokeColor = isBullishClose ? "#26a69a" : "#ef5350";
    var isHollowBody = close >= open;
    candleSeries.update({
      time: barTime,
      open: open,
      high: high,
      low: low,
      close: close,
      color: isHollowBody ? "transparent" : strokeColor,
      borderColor: strokeColor,
      wickColor: strokeColor
    });
  } else {
    candleSeries.update({ time: barTime, open: open, high: high, low: low, close: close });
  }
  volumeSeries.update({
    time: barTime,
    value: volume,
    color: close >= open ? "rgba(38, 166, 154, 0.45)" : "rgba(239, 83, 80, 0.45)"
  });

  if (window.Indicators && currentRawBars.length > 0) {
    if (ema10Series && indicatorConfig.ema10) {
      var e10 = window.Indicators.calcEMA(currentRawBars, 10);
      if (e10.length > 0) ema10Series.update(e10[e10.length - 1]);
    }
    if (ema21Series && indicatorConfig.ema21) {
      var e21 = window.Indicators.calcEMA(currentRawBars, 21);
      if (e21.length > 0) ema21Series.update(e21[e21.length - 1]);
    }
    if (ema30Series && indicatorConfig.ema30) {
      var e30 = window.Indicators.calcEMA(currentRawBars, 30);
      if (e30.length > 0) ema30Series.update(e30[e30.length - 1]);
    }
    if (vwapSeries && indicatorConfig.vwap && window.Indicators.calcVWAP) {
      var vw = window.Indicators.calcVWAP(currentRawBars, { timeframe: activeTimeframe, symbol: activeSymbol });
      if (vw.length > 0) vwapSeries.update(vw[vw.length - 1]);
    }
    if (sma50Series && indicatorConfig.sma50) {
      var s50 = window.Indicators.calcSMA(currentRawBars, 50);
      if (s50.length > 0) sma50Series.update(s50[s50.length - 1]);
    }
    if (sma200Series && indicatorConfig.sma200) {
      var s200 = window.Indicators.calcSMA(currentRawBars, 200);
      if (s200.length > 0) sma200Series.update(s200[s200.length - 1]);
    }
    if (volSmaSeries && indicatorConfig.volSma) {
      var vs = window.Indicators.calcVolumeSMA(currentRawBars, 50);
      if (vs.length > 0) volSmaSeries.update(vs[vs.length - 1]);
    }
    if (rsSeries && indicatorConfig.rsOverlayMain && benchmarkRawBars && benchmarkRawBars.length > 0) {
      var rs = window.Indicators.calcRelativeStrength([liveBar], benchmarkRawBars);
      if (rs.length > 0) rsSeries.update(rs[rs.length - 1]);
    }
    if (indicatorConfig.avwap) calcAndRenderAvwap();
  }

  updateCrosshairLegend(liveBar);
  updateDeepvueHud(currentRawBars);
}

// Forward live 1m bars to matching sub-charts in multi-pane split layout.
// 第 4 段：每個子圖依自己的週期分桶（美股以紐約時段），新 K 棒保留 1 分鐘 K 棒的開高低。
function forwardLiveBarToSubCharts(symbol, minuteBar, mainBar) {
  for (var pId in subCharts) {
    if (paneSymbols[pId] === symbol && subCharts[pId] && subCharts[pId].series) {
      try {
        var scObj = subCharts[pId];
        var scTf = paneTimeframes[pId] || "1D";
        if (scTf === activeTimeframe) {
          if (!mainBar) continue;
          var sameBar = {
            time: mainBar.time,
            open: mainBar.open,
            high: mainBar.high,
            low: mainBar.low,
            close: mainBar.close
          };
          subCharts[pId].series.update(sameBar);
          scObj.lastBar = sameBar;
        } else if (scObj.lastBar) {
          var lb = scObj.lastBar;
          // 只處理數字時間戳；字串或 BusinessDay 格式無法安全對齊，略過
          if (typeof lb.time !== "number") continue;
          var subPlacement = computeLiveBucket({
            symbol: symbol,
            timeframe: scTf,
            tickTime: minuteBar.time,
            lastBarTime: lb.time
          });
          if (subPlacement.action === "ignore") continue;
          var bucketTime = subPlacement.time;
          if (bucketTime === lb.time) {
            lb.high = Math.max(lb.high, minuteBar.high, minuteBar.close);
            lb.low = Math.min(lb.low, minuteBar.low, minuteBar.close);
            lb.close = minuteBar.close;
            subCharts[pId].series.update(lb);
          } else if (bucketTime > lb.time) {
            var newCandle = {
              time: bucketTime,
              open: minuteBar.open,
              high: Math.max(minuteBar.high, minuteBar.close),
              low: Math.min(minuteBar.low, minuteBar.close),
              close: minuteBar.close
            };
            scObj.lastBar = newCandle;
            subCharts[pId].series.update(newCandle);
          }
        }
      } catch (subErr) {
        console.warn("[handleLiveBar] sub-chart update error for pane " + pId, subErr);
      }
    }
  }
}

function formatQuoteDelayBadge(quote, lang) {
  if (!quote || typeof quote !== "object") {
    return { visible: false, text: "", shortText: "", title: "", minutes: null };
  }
  var mode = quote.update_mode ? String(quote.update_mode) : "";
  var isDelayed = false;
  if (quote.is_delayed === true) {
    isDelayed = true;
  } else if (quote.is_delayed === false) {
    isDelayed = false;
  } else if (mode && mode.toLowerCase().indexOf("delayed") !== -1) {
    isDelayed = true;
  }
  if (!isDelayed) {
    return { visible: false, text: "", shortText: "", title: "", minutes: null };
  }
  var minutes = null;
  var secMatch = /_(\d+)$/.exec(mode);
  if (secMatch) {
    var secs = parseInt(secMatch[1], 10);
    if (isFinite(secs) && secs >= 60) {
      minutes = Math.round(secs / 60);
    }
  }
  var effLang = lang || (typeof currentLang !== "undefined" ? currentLang : "zh");
  var text = minutes !== null ? ("DELAYED " + minutes + "m") : "DELAYED";
  var shortText = minutes !== null ? (minutes + "m") : "D";
  var modeSuffix = mode ? (" (" + mode + ")") : "";
  var title;
  if (effLang === "en") {
    title = minutes !== null
      ? ("Delayed quote: ~" + minutes + " min" + modeSuffix)
      : ("Delayed quote" + modeSuffix);
  } else {
    title = minutes !== null
      ? ("延遲行情：約 " + minutes + " 分鐘" + modeSuffix)
      : ("延遲行情" + modeSuffix);
  }
  return {
    visible: true,
    text: text,
    shortText: shortText,
    title: title,
    minutes: minutes
  };
}

function formatModelSourceLabel(modelUsed, source) {
  if (source === "rule_based" || !modelUsed) {
    return "Rule-Based (Offline)";
  }
  return String(modelUsed);
}

if (typeof window !== "undefined") {
  window.formatQuoteDelayBadge = formatQuoteDelayBadge;
  window.formatModelSourceLabel = formatModelSourceLabel;
}
if (typeof globalThis !== "undefined") {
  globalThis.formatQuoteDelayBadge = formatQuoteDelayBadge;
  globalThis.formatModelSourceLabel = formatModelSourceLabel;
}

function updateSymbolMetrics(symbol, quote, bar) {
  if (symbol !== activeSymbol) return;

  var q = quote || {};
  var price = q.price !== undefined ? q.price : (bar ? bar.close : 0);
  var ch = q.change;
  var chp = q.changePercentage;
  var vol = (q.volume !== undefined && q.volume !== null) ? q.volume : null;

  if (activePriceEl) activePriceEl.textContent = canonicalFormatPrice(price, symbol);
  var delayedBadgeEl = document.getElementById("delayedQuoteBadge");
  if (delayedBadgeEl) {
    var delayInfo = formatQuoteDelayBadge(q, currentLang);
    delayedBadgeEl.style.display = delayInfo.visible ? "inline-flex" : "none";
    if (delayInfo.visible) {
      delayedBadgeEl.textContent = delayInfo.text;
      delayedBadgeEl.title = delayInfo.title;
    } else {
      delayedBadgeEl.textContent = "";
      delayedBadgeEl.title = "";
    }
  }

  if (activeChangeEl) {
    if (chp !== undefined && chp !== null) {
      var sign = chp >= 0 ? "+" : "";
      activeChangeEl.textContent = sign + chp.toFixed(2) + "%";
      activeChangeEl.className = "metric-num " + (chp >= 0 ? "up" : "down");
    } else {
      activeChangeEl.textContent = "--";
    }
  }

  if (activeVolumeEl) activeVolumeEl.textContent = vol !== null ? formatVolume(vol) : "—";

  var rawHigh = q.high !== undefined ? q.high : q.high24h;
  var rawLow = q.low !== undefined ? q.low : q.low24h;
  var hasHighLow = false;
  if (rawHigh !== undefined && rawHigh !== null && rawLow !== undefined && rawLow !== null) {
    var numHigh = Number(rawHigh);
    var numLow = Number(rawLow);
    if (!isNaN(numHigh) && !isNaN(numLow) && numHigh > 0) {
      hasHighLow = true;
      if (activeHighEl) activeHighEl.textContent = formatPrice(numHigh, symbol);
      if (activeLowEl) activeLowEl.textContent = formatPrice(numLow, symbol);
    }
  }

  if (activeHighEl) {
    var highBox = activeHighEl.closest ? activeHighEl.closest(".metric-box") : activeHighEl.parentElement;
    if (highBox) highBox.style.display = hasHighLow ? "flex" : "none";
  }
  if (activeLowEl) {
    var lowBox = activeLowEl.closest ? activeLowEl.closest(".metric-box") : activeLowEl.parentElement;
    if (lowBox) lowBox.style.display = hasHighLow ? "flex" : "none";
  }

  var symItem = getSymbolItem(symbol);
  if (activeNameEl && symItem) activeNameEl.textContent = symItem.name;
  if (activeExchangeEl && symItem) activeExchangeEl.textContent = symItem.exchange;
}

// ==========================================================================
// Watchlist Sidebar Component
// ==========================================================================

var _wlDragEndTime = 0;

function renderWatchlist() {
  if (!watchlistItemsEl) return;
  watchlistItemsEl.innerHTML = "";
  sidebarElements = {};

  var draggedItemId = null;
  var draggedItemIndex = null;
  var ordered = getOrderedWatchlistSymbols(activeWlGroup);
  var pinnedList = ordered.pinned;
  var unpinnedList = ordered.unpinned;

  function renderItem(s, idx) {
    var state = symbolStates[s.id] || {};
    var div = document.createElement("div");
    div.className = "wl-item" + (s.id === activeSymbol ? " active" : "");
    div.id = "wl_" + s.id.replace(/[^a-zA-Z0-9]/g, "_");
    div.setAttribute("data-index", String(idx));
    div.setAttribute("data-id", s.id);

    var colLeft = document.createElement("div");
    colLeft.className = "wl-symbol-col";

    var dragGrip = document.createElement("span");
    dragGrip.className = "wl-drag-handle";
    dragGrip.textContent = "⋮⋮";
    dragGrip.setAttribute("title", "拖動以調整自選順序 (Drag to reorder)");

    var tickerSpan = document.createElement("span");
    tickerSpan.className = "wl-ticker";
    tickerSpan.textContent = s.pair;

    var wlDelayBadge = document.createElement("span");
    wlDelayBadge.className = "wl-delayed-badge";
    var wlDelayInfo = formatQuoteDelayBadge(state, currentLang);
    wlDelayBadge.style.display = wlDelayInfo.visible ? "inline-flex" : "none";
    if (wlDelayInfo.visible) {
      wlDelayBadge.textContent = wlDelayInfo.shortText;
      wlDelayBadge.setAttribute("title", wlDelayInfo.title);
    }
    tickerSpan.appendChild(wlDelayBadge);

    var grp = watchlistGroups[s.id];
    if (grp) {
      var grpTag = document.createElement("span");
      grpTag.className = "wl-group-tag " + grp;
      var dict = i18n[currentLang] || i18n.en;
      var grpKey = "grp" + grp.charAt(0).toUpperCase() + grp.slice(1);
      grpTag.textContent = dict[grpKey] || grp.toUpperCase();
      tickerSpan.appendChild(grpTag);
    }

    if (s.sector) {
      var secTag = document.createElement("span");
      secTag.className = "sector-tag";
      secTag.textContent = s.sector;
      tickerSpan.appendChild(secTag);
    }

    var nameSpan = document.createElement("span");
    nameSpan.className = "wl-name";
    nameSpan.textContent = s.name;

    colLeft.appendChild(dragGrip);
    colLeft.appendChild(tickerSpan);
    colLeft.appendChild(nameSpan);

    var colRight = document.createElement("div");
    colRight.className = "wl-price-col";

    var priceSpan = document.createElement("span");
    priceSpan.className = "wl-price";
    priceSpan.textContent = canonicalFormatPrice(state.price, s.id);

    var chp = safeNum(state.changePercentage, 0);
    var sign = chp >= 0 ? "+" : "";
    var chpSpan = document.createElement("span");
    chpSpan.className = "wl-chp " + (chp >= 0 ? "up" : "down");
    chpSpan.textContent = sign + chp.toFixed(2) + "%";

    colRight.appendChild(priceSpan);
    colRight.appendChild(chpSpan);

    var isPinned = pinnedSymbols.indexOf(s.id) >= 0;
    var pinBtn = document.createElement("button");
    pinBtn.className = "wl-pin-btn" + (isPinned ? " pinned" : "");
    var curDict = i18n[currentLang] || i18n.en;
    pinBtn.setAttribute("title", isPinned ? (curDict.unpinBtnTitle || "取消釘選") : (curDict.pinBtnTitle || "釘選到頂部"));
    pinBtn.textContent = "📌";
    pinBtn.onclick = function(e) {
      e.stopPropagation();
      togglePinSymbol(s.id);
    };

    var removeBtn = document.createElement("button");
    removeBtn.className = "wl-remove-btn";
    removeBtn.setAttribute("title", "從自選清單移除");
    removeBtn.textContent = "✕";
    removeBtn.onclick = function(e) {
      e.stopPropagation();
      unsubscribeSymbol(s.id);
    };

    div.appendChild(colLeft);
    div.appendChild(colRight);
    div.appendChild(pinBtn);
    div.appendChild(removeBtn);

    var startX = 0;
    var startY = 0;
    var isDragging = false;
    var pointerActive = false;

    var onPointerDown = function(e) {
      if (e.button !== 0) return;
      if (e.target && e.target.closest && (e.target.closest(".wl-pin-btn") || e.target.closest(".wl-remove-btn"))) {
        return;
      }
      var isHandle = Boolean(e.target && e.target.closest && e.target.closest(".wl-drag-handle"));
      startX = e.clientX;
      startY = e.clientY;
      isDragging = false;
      pointerActive = true;

      var onPointerMove = function(ev) {
        if (!pointerActive) return;
        var diffX = Math.abs(ev.clientX - startX);
        var diffY = Math.abs(ev.clientY - startY);
        if (!isDragging) {
          var dragThreshold = isHandle ? 3 : 6;
          if (diffY > dragThreshold || diffX > dragThreshold) {
            isDragging = true;
            draggedItemId = s.id;
            draggedItemIndex = idx;
            div.classList.add("dragging");
            document.body.style.userSelect = "none";
          }
        }
        if (isDragging) {
          var targetEl = document.elementFromPoint(ev.clientX, ev.clientY);
          var targetItem = targetEl && targetEl.closest ? targetEl.closest(".wl-item") : null;
          var allWl = watchlistItemsEl.querySelectorAll ? watchlistItemsEl.querySelectorAll(".wl-item") : [];
          for (var k = 0; k < allWl.length; k++) {
            allWl[k].classList.remove("drag-over-top");
            allWl[k].classList.remove("drag-over-bottom");
          }
          if (targetItem && targetItem !== div) {
            var targetId = targetItem.getAttribute("data-id");
            var fromPinned = pinnedSymbols.indexOf(s.id) >= 0;
            var toPinned = targetId ? pinnedSymbols.indexOf(targetId) >= 0 : false;
            // Boundary partition protection: do not drag across pinned / unpinned boundary
            if (fromPinned === toPinned) {
              var rect = targetItem.getBoundingClientRect();
              var isTop = (ev.clientY - rect.top) < (rect.height / 2);
              if (isTop) {
                targetItem.classList.add("drag-over-top");
              } else {
                targetItem.classList.add("drag-over-bottom");
              }
            }
          }
        }
      };

      var onPointerUp = function(ev) {
        if (!pointerActive) return;
        pointerActive = false;
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("pointercancel", onPointerUp);
        document.body.style.userSelect = "";

        if (isDragging) {
          _wlDragEndTime = Date.now();
          var targetEl = document.elementFromPoint(ev.clientX, ev.clientY);
          var targetItem = targetEl && targetEl.closest ? targetEl.closest(".wl-item") : null;
          var allWl = watchlistItemsEl.querySelectorAll ? watchlistItemsEl.querySelectorAll(".wl-item") : [];
          for (var k = 0; k < allWl.length; k++) {
            allWl[k].classList.remove("dragging");
            allWl[k].classList.remove("drag-over-top");
            allWl[k].classList.remove("drag-over-bottom");
          }
          if (targetItem && targetItem !== div) {
            var fromId = s.id;
            var toId = targetItem.getAttribute("data-id");
            if (fromId && toId && fromId !== toId) {
              var fromPinned = pinnedSymbols.indexOf(fromId) >= 0;
              var toPinned = pinnedSymbols.indexOf(toId) >= 0;
              if (fromPinned === toPinned) {
                var rect = targetItem.getBoundingClientRect();
                var isTop = (ev.clientY - rect.top) < (rect.height / 2);

                if (fromPinned && toPinned) {
                  var fpIdx = pinnedSymbols.indexOf(fromId);
                  var tpIdx = pinnedSymbols.indexOf(toId);
                  if (fpIdx >= 0 && tpIdx >= 0 && fpIdx !== tpIdx) {
                    var movedPin = pinnedSymbols.splice(fpIdx, 1)[0];
                    var targetPinIdx = pinnedSymbols.indexOf(toId);
                    if (!isTop) targetPinIdx += 1;
                    pinnedSymbols.splice(targetPinIdx, 0, movedPin);
                    safeSetStorage("tv_pinned_symbols", JSON.stringify(pinnedSymbols));
                    if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
                  }
                }

                var fromIdx = -1;
                var toIdx = -1;
                for (var m = 0; m < symbolsList.length; m++) {
                  if (symbolsList[m].id === fromId) fromIdx = m;
                  if (symbolsList[m].id === toId) toIdx = m;
                }
                if (fromIdx !== -1 && toIdx !== -1 && fromIdx !== toIdx) {
                  var moved = symbolsList.splice(fromIdx, 1)[0];
                  var targetIdx = -1;
                  for (var m = 0; m < symbolsList.length; m++) {
                    if (symbolsList[m].id === toId) { targetIdx = m; break; }
                  }
                  if (!isTop) targetIdx += 1;
                  symbolsList.splice(targetIdx, 0, moved);
                  safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
                  renderWatchlist();
                  fetch("/api/symbols/reorder", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ order: symbolsList.map(function(item) { return item.id; }) })
                  }).catch(function(err) {
                    console.warn("Watchlist reorder sync error:", err);
                  });
                }
              }
            }
          }
          isDragging = false;
          draggedItemId = null;
          draggedItemIndex = null;
        }
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
      window.addEventListener("pointercancel", onPointerUp);
    };

    div.addEventListener("pointerdown", onPointerDown);

    div.onclick = function() {
      if (Date.now() - _wlDragEndTime < 200) return;
      if (mtfSyncEnabled) {
        syncMTF(s.id);
      } else if (focusedPaneId > 1 && subCharts[focusedPaneId]) {
        loadSubChart(focusedPaneId, s.id);
      } else {
        switchSymbol(s.id);
      }
    };

    div.oncontextmenu = function(e) {
      e.preventDefault();
      e.stopPropagation();
      openWlContextMenu(e, s.id);
    };

    watchlistItemsEl.appendChild(div);
    sidebarElements[s.id] = {
      item: div,
      price: priceSpan,
      chp: chpSpan,
      delayedBadge: wlDelayBadge
    };
  }

  for (var p = 0; p < pinnedList.length; p++) {
    renderItem(pinnedList[p], p);
  }

  if (pinnedList.length > 0 && unpinnedList.length > 0) {
    var divider = document.createElement("div");
    divider.className = "wl-pin-divider";
    watchlistItemsEl.appendChild(divider);
  }

  for (var u = 0; u < unpinnedList.length; u++) {
    renderItem(unpinnedList[u], pinnedList.length + u);
  }
}

var activeContextSymbol = null;
function openWlContextMenu(e, symbolId) {
  activeContextSymbol = symbolId;
  var menu = document.getElementById("wlContextMenu");
  if (!menu) return;
  var ctxPinWlText = document.getElementById("ctxPinWlText");
  if (ctxPinWlText) {
    var isPinned = pinnedSymbols.indexOf(symbolId) >= 0;
    var dict = i18n[currentLang] || i18n.en;
    ctxPinWlText.textContent = isPinned ? (dict.ctxUnpin || "取消釘選") : (dict.ctxPin || "釘選置頂");
  }
  menu.style.display = "block";
  var x = Math.min(e.clientX, window.innerWidth - 200);
  var y = Math.min(e.clientY, window.innerHeight - 150);
  menu.style.left = x + "px";
  menu.style.top = y + "px";
}

document.addEventListener("click", function() {
  var menu = document.getElementById("wlContextMenu");
  if (menu) menu.style.display = "none";
});

var ctxPinWl = document.getElementById("ctxPinWl");
if (ctxPinWl) {
  ctxPinWl.onclick = function() {
    if (activeContextSymbol) togglePinSymbol(activeContextSymbol);
  };
}

var ctxRemoveWl = document.getElementById("ctxRemoveWl");
if (ctxRemoveWl) {
  ctxRemoveWl.onclick = function() {
    if (activeContextSymbol) unsubscribeSymbol(activeContextSymbol);
  };
}
var ctxSetBenchmark = document.getElementById("ctxSetBenchmark");
if (ctxSetBenchmark) {
  ctxSetBenchmark.onclick = function() {
    if (activeContextSymbol) {
      setRsBenchmark(activeContextSymbol);
    }
  };
}
var ctxCopySymbol = document.getElementById("ctxCopySymbol");
if (ctxCopySymbol) {
  ctxCopySymbol.onclick = function() {
    if (activeContextSymbol && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(activeContextSymbol);
    }
  };
}

var ctxGroupItems = document.querySelectorAll ? document.querySelectorAll(".ctx-group-item") : [];
for (var cgi = 0; cgi < ctxGroupItems.length; cgi++) {
  (function(item) {
    item.onclick = function() {
      if (!activeContextSymbol) return;
      var grpName = item.getAttribute("data-assign-group") || "";
      if (grpName) {
        watchlistGroups[activeContextSymbol] = grpName;
      } else {
        delete watchlistGroups[activeContextSymbol];
      }
      safeSetStorage("tv_watchlist_groups", JSON.stringify(watchlistGroups));
      renderWatchlist();
      if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
    };
  })(ctxGroupItems[cgi]);
}

var wlGroupButtons = document.querySelectorAll ? document.querySelectorAll(".wl-group-btn") : [];
for (var wbi = 0; wbi < wlGroupButtons.length; wbi++) {
  (function(btn) {
    btn.onclick = function() {
      var grp = btn.getAttribute("data-group") || "all";
      activeWlGroup = grp;
      for (var b = 0; b < wlGroupButtons.length; b++) {
        wlGroupButtons[b].classList.toggle("active", wlGroupButtons[b] === btn);
      }
      renderWatchlist();
    };
  })(wlGroupButtons[wbi]);
}

function unsubscribeSymbol(symbolId) {
  if (!symbolId) return;
  symbolsList = symbolsList.filter(function(s) { return s.id !== symbolId; });
  safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
  delete symbolStates[symbolId];
  delete lastPrices[symbolId];
  renderWatchlist();
  if (activeSymbol === symbolId && symbolsList.length > 0) {
    switchSymbol(symbolsList[0].id);
  }
  var headers = {};
  if (sessionToken) headers["X-Session-Token"] = sessionToken;
  fetch("/api/symbols/unsubscribe?symbol=" + encodeURIComponent(symbolId), {
    method: "POST",
    headers: headers
  }).catch(function(err) {
    console.warn("Unsubscribe error:", err);
  });
  if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
  if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
}

function updateWatchlistPrice(symbol, quote) {
  var els = sidebarElements[symbol];
  if (!els) return;

  if (quote.price !== undefined) {
    els.price.textContent = canonicalFormatPrice(quote.price, symbol);
  }
  if (quote.changePercentage !== undefined) {
    var chp = Number(quote.changePercentage);
    var sign = chp >= 0 ? "+" : "";
    els.chp.textContent = sign + chp.toFixed(2) + "%";
    els.chp.className = "wl-chp " + (chp >= 0 ? "up" : "down");
  }
  if (els.delayedBadge) {
    var wlDelay = formatQuoteDelayBadge(quote, currentLang);
    els.delayedBadge.style.display = wlDelay.visible ? "inline-flex" : "none";
    if (wlDelay.visible) {
      els.delayedBadge.textContent = wlDelay.shortText;
      els.delayedBadge.title = wlDelay.title;
    } else {
      els.delayedBadge.textContent = "";
      els.delayedBadge.title = "";
    }
  }
}

// ==========================================================================
// Omnibox Search Modal & Dynamic Subscriptions
// ==========================================================================

  var searchDebounceTimer = null;
  var searchReqSeq = 0;
  var lastNotificationMessage = "";

function executeSearch(query, filter) {
  if (filter === undefined) filter = "all";
  var currentSeq = ++searchReqSeq;
  fetch("/api/symbols/search?q=" + encodeURIComponent(query))
    .then(function(res) {
      if (currentSeq !== searchReqSeq) return null;
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      if (currentSeq !== searchReqSeq || !data) return;
      var items = data.results || [];
      if (filter !== "all") {
        items = items.filter(function(it) { return it.type === filter; });
      }
      renderSearchResults(items);
    })
    .catch(function(e) {
      if (currentSeq !== searchReqSeq) return;
      if (searchResultsList) {
        searchResultsList.innerHTML = "";
        var errDiv = document.createElement("div");
        errDiv.className = "search-error-state";
        errDiv.style.cssText = "padding:20px;text-align:center;color:#ef5350;";
        errDiv.textContent = "搜尋失敗";
        searchResultsList.appendChild(errDiv);
      }
    });
}

function renderSearchResults(items) {
  if (!searchResultsList) return;
  searchResultsList.innerHTML = "";

  if (!items || items.length === 0) {
    var emptyDiv = document.createElement("div");
    emptyDiv.style.cssText = "padding:20px;text-align:center;color:#787b86;";
    var dict = i18n[currentLang] || i18n.en;
    emptyDiv.textContent = dict.noResults || "No matching symbols found. Press Enter to add custom ticker.";
    searchResultsList.appendChild(emptyDiv);
    return;
  }

  for (var i = 0; i < items.length; i++) {
    (function(it) {
      var div = document.createElement("div");
      div.className = "search-res-item";

      var leftDiv = document.createElement("div");
      leftDiv.className = "sr-left";

      var badgeSpan = document.createElement("span");
      badgeSpan.className = "sr-type-badge";
      badgeSpan.textContent = it.type || "stock";

      var textWrap = document.createElement("div");

      var tickerSpan = document.createElement("span");
      tickerSpan.className = "sr-ticker";
      tickerSpan.textContent = it.pair;

      var nameSpan = document.createElement("span");
      nameSpan.className = "sr-name";
      nameSpan.textContent = it.name || "";

      textWrap.appendChild(tickerSpan);
      textWrap.appendChild(nameSpan);

      leftDiv.appendChild(badgeSpan);
      leftDiv.appendChild(textWrap);

      var exchSpan = document.createElement("span");
      exchSpan.className = "sr-exch";
      exchSpan.textContent = it.exchange || "US";

      div.appendChild(leftDiv);
      div.appendChild(exchSpan);

      div.onclick = function() {
        selectAndSubscribeSymbol(it);
      };

      searchResultsList.appendChild(div);
    })(items[i]);
  }
}

function subscribeSymbol(symbolId, onResult) {
  var defExch = symbolId.indexOf(":") !== -1 ? symbolId.split(":")[0] : "NASDAQ";
  var defTicker = symbolId.indexOf(":") !== -1 ? symbolId.split(":")[1] : symbolId;
  return selectAndSubscribeSymbol({
    id: symbolId,
    pair: defTicker,
    name: defTicker,
    exchange: defExch,
    type: "stock"
  }, onResult);
}

function selectAndSubscribeSymbol(item, onResult) {
  closeSearch();
  var payload = {
    symbol: item.id || item.pair || "",
    id: item.id || item.pair || "",
    name: item.name || item.pair || "",
    exchange: item.exchange || "US",
    pair: item.pair || item.id || "",
    type: item.type || "stock"
  };
  var subPromise = fetch("/api/symbols/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      if (data && data.exchange_guessed) {
        lastNotificationMessage = "交易所是推測的（預設 NASDAQ）";
        if (typeof showScreenerFeedback === "function") {
          showScreenerFeedback("交易所是推測的（預設 NASDAQ）");
        }
      }
      if (data && data.symbol) {
        var subscribed = data.symbol;
        var exists = false;
        for (var i = 0; i < symbolsList.length; i++) {
          if (symbolsList[i].id === subscribed.id) { exists = true; break; }
        }
        if (!exists) {
          symbolsList.push(subscribed);
          safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
          renderWatchlist();
        }
        if (subscribed.id === activeSymbol) {
          var unBadge = document.getElementById("unsubscribedBadge");
          if (unBadge) unBadge.style.display = "none";
        }
      }
      if (typeof onResult === "function") onResult(true, data);
      return data;
    })
    .catch(function(e) {
      console.warn("[subscribe] error:", e);
      if (typeof onResult === "function") onResult(false, null, e);
      throw e;
    });

  if (mtfSyncEnabled) {
    syncMTF(item.id);
  } else if (focusedPaneId > 1 && subCharts[focusedPaneId]) {
    loadSubChart(focusedPaneId, item.id);
  } else {
    switchSymbol(item.id);
  }

  return subPromise;
}

function openSearch(seedChar) {
  if (searchModal) {
    var q = typeof seedChar === "string" ? seedChar : "";
    searchModal.style.display = "flex";
    if (searchInput) {
      searchInput.value = q;
      searchInput.focus();
    }
    executeSearch(q);
  }
}

function closeSearch() {
  if (searchModal) searchModal.style.display = "none";
}

// ==========================================================================
// WebSocket Lifecycle & Exponential Backoff
// ==========================================================================

var wsHasConnectedBefore = false;
var lastBarSeqBySymbol = {};
var _gapRecoveryInFlight = false;

function checkBarSequenceGap(symbol, seq) {
  if (!symbol || typeof seq !== "number" || !isFinite(seq) || seq <= 0) {
    return { gap: false, expected: null, received: null };
  }
  var key = String(symbol).trim().toUpperCase();
  var prev = lastBarSeqBySymbol[key];
  if (prev === undefined) {
    lastBarSeqBySymbol[key] = seq;
    return { gap: false, expected: seq, received: seq };
  }
  if (seq > prev + 1) {
    lastBarSeqBySymbol[key] = seq;
    return { gap: true, expected: prev + 1, received: seq };
  }
  if (seq < prev) {
    lastBarSeqBySymbol[key] = seq;
  } else if (seq === prev + 1) {
    lastBarSeqBySymbol[key] = seq;
  }
  return { gap: false, expected: prev + 1, received: seq };
}

// 第 7 段：伺服器佇列塞車時會連續掉訊息；缺口恢復最多每 15 秒一次，避免整張圖一直重畫
var _lastGapRecoveryAt = 0;
var GAP_RECOVERY_MIN_INTERVAL_MS = 15000;

function triggerGapRecovery(force) {
  if (_gapRecoveryInFlight) return;
  if (typeof recoverTerminalState !== "function" || !activeSymbol) return;
  var nowMs = Date.now();
  if (!force && nowMs - _lastGapRecoveryAt < GAP_RECOVERY_MIN_INTERVAL_MS) return;
  _lastGapRecoveryAt = nowMs;
  _gapRecoveryInFlight = true;
  Promise.resolve(recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe, includeWatchlist: false }))
    .catch(function() {})
    .finally(function() {
      _gapRecoveryInFlight = false;
    });
}

if (typeof window !== "undefined") {
  window.checkBarSequenceGap = checkBarSequenceGap;
  window.triggerGapRecovery = triggerGapRecovery;
}
if (typeof globalThis !== "undefined") {
  globalThis.checkBarSequenceGap = checkBarSequenceGap;
  globalThis.triggerGapRecovery = triggerGapRecovery;
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectAttempts++;
  var base = Math.min(MAX_RECONNECT_DELAY, BASE_RECONNECT_DELAY * Math.pow(1.5, reconnectAttempts - 1));
  var jitter = Math.random() * 1000;
  var delay = Math.floor(base + jitter);

  reconnectTimer = setTimeout(function() {
    reconnectTimer = null;
    connectWebSocket();
  }, delay);
}

var lastWsMessageAt = 0;
var _wsHeartbeatTimer = null;

function checkWsHeartbeatTimeout(nowMs) {
  var t = (typeof nowMs === "number") ? nowMs : Date.now();
  if (ws && ws.readyState === 1) {
    if (lastWsMessageAt > 0 && (t - lastWsMessageAt > 45000)) {
      console.warn("[ws] No message received for >45s, closing connection for reconnect");
      try {
        ws.close();
      } catch (e) {}
    }
  }
}

function initWsHeartbeatCheck() {
  if (_wsHeartbeatTimer) return;
  if (typeof setInterval === "undefined") return;
  _wsHeartbeatTimer = setInterval(function() {
    checkWsHeartbeatTimeout();
  }, 5000);
  if (_wsHeartbeatTimer && typeof _wsHeartbeatTimer.unref === "function") {
    _wsHeartbeatTimer.unref();
  }
}

if (typeof window !== "undefined") {
  window.checkWsHeartbeatTimeout = checkWsHeartbeatTimeout;
  window.initWsHeartbeatCheck = initWsHeartbeatCheck;
}
if (typeof globalThis !== "undefined") {
  globalThis.checkWsHeartbeatTimeout = checkWsHeartbeatTimeout;
  globalThis.initWsHeartbeatCheck = initWsHeartbeatCheck;
}

function connectWebSocket() {
  initWsHeartbeatCheck();
  if (!desktopPort && typeof window !== "undefined" && (window.__TAURI__ || (window.location && window.location.protocol === "tauri:"))) {
    return;
  }
  var host = window.location.host;
  var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  if (!host || window.location.protocol === "file:") {
    host = desktopPort ? ("127.0.0.1:" + desktopPort) : "127.0.0.1:8000";
    protocol = "ws:";
  } else if (desktopPort && (host === "localhost" || window.location.protocol === "tauri:")) {
    host = "127.0.0.1:" + desktopPort;
    protocol = "ws:";
  }
  var wsUrl = protocol + "//" + host + "/ws/live";
  if (sessionToken) {
    wsUrl += (wsUrl.indexOf("?") === -1 ? "?" : "&") + "token=" + encodeURIComponent(sessionToken);
  }

  try {
    if (ws) {
      try {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      } catch (e) {}
      ws = null;
    }
    ws = new WebSocket(wsUrl);
  } catch (e) {
    scheduleReconnect();
    return;
  }

  ws.onopen = function() {
    lastWsMessageAt = Date.now();
    var isReconnect = wsHasConnectedBefore || reconnectAttempts > 0;
    wsHasConnectedBefore = true;
    reconnectAttempts = 0;
    var dict = i18n[currentLang] || i18n.en;
    if (statusBadge) statusBadge.className = "status-badge connected";
    if (statusText) statusText.textContent = dict.connected;
    if (isReconnect) {
      lastBarSeqBySymbol = {};
      triggerGapRecovery(true);
    }
  };

  ws.onmessage = function(evt) {
    try {
      lastWsMessageAt = Date.now();
      var msg = JSON.parse(evt.data);

      if (msg.type === "heartbeat") {
        return;
      }

      if (msg.type === "status") {
        if (statusBadge && statusText) {
          var dict = i18n[currentLang] || i18n.en;
          var st = String(msg.status || "").toLowerCase();
          if (st === "connected") {
            statusBadge.className = "status-badge connected";
            statusText.textContent = dict.connected;
          } else if (st === "connecting" || st === "initializing") {
            statusBadge.className = "status-badge connecting";
            statusText.textContent = dict.connecting;
          } else {
            statusBadge.className = "status-badge disconnected";
            statusText.textContent = dict.disconnected;
          }
        }
        return;
      }

      tickCount++;
      if (tickCounter) tickCounter.textContent = tickCount.toLocaleString();

      if (msg.type === "quote") {
        updateQuote(msg.symbol, msg.data || {});
      } else if (msg.type === "bar") {
        var seqCheck = checkBarSequenceGap(msg.symbol, msg.seq);
        if (seqCheck.gap && msg.symbol === activeSymbol) {
          triggerGapRecovery();
        }
        handleLiveBar(msg.symbol, msg.bar);
      } else if (msg.type === "symbol_added") {
        var newSym = msg.symbol;
        var exists = false;
        for (var i = 0; i < symbolsList.length; i++) {
          if (symbolsList[i].id === newSym.id) { exists = true; break; }
        }
        if (!exists) {
          symbolsList.push(newSym);
          safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
          renderWatchlist();
        }
      } else if (msg.type === "symbol_removed") {
        var remSymId = typeof msg.symbol === "object" ? msg.symbol.id : msg.symbol;
        if (remSymId) {
          delete symbolStates[remSymId];
          delete lastPrices[remSymId];
          symbolsList = symbolsList.filter(function(s) { return s.id !== remSymId; });
          safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
          renderWatchlist();
          if (remSymId === activeSymbol) {
            var unBadge = document.getElementById("unsubscribedBadge");
            if (!unBadge) {
              unBadge = document.createElement("span");
              unBadge.id = "unsubscribedBadge";
              unBadge.className = "status-badge disconnected";
              var delayedBadgeEl = document.getElementById("delayedQuoteBadge");
              if (delayedBadgeEl && delayedBadgeEl.parentNode) {
                delayedBadgeEl.parentNode.insertBefore(unBadge, delayedBadgeEl.nextSibling);
              }
            }
            if (unBadge) {
              unBadge.style.display = "inline-flex";
              unBadge.textContent = "已從自選清單移除，即時更新已停止";
            }
          }
        }
      }
    } catch (e) {
      console.warn("[ws] message handling error:", e);
    }
  };

  ws.onclose = function() {
    var dict = i18n[currentLang] || i18n.en;
    if (statusBadge) statusBadge.className = "status-badge disconnected";
    if (statusText) statusText.textContent = dict.disconnected;
    scheduleReconnect();
  };

  ws.onerror = function() {
    if (ws) ws.close();
  };
}

// ==========================================================================
// Event Listeners & Bootstrapping
// ==========================================================================

function setLanguage(lang, skipSave) {
  currentLang = (lang === "en") ? "en" : "zh";
  safeSetStorage("tv_terminal_lang", currentLang);
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.lang = currentLang === "zh" ? "zh-Hant" : "en";
  }
  var dict = i18n[currentLang] || i18n.en;

  var langLabelEl = document.getElementById("langLabel");
  if (langLabelEl) langLabelEl.textContent = dict.langLabel;
  var langBtnEl = document.getElementById("langToggleBtn");
  if (langBtnEl) langBtnEl.setAttribute("title", dict.langToggleTitle);

  var brandSubEl = document.querySelector(".brand-sub");
  if (brandSubEl) brandSubEl.textContent = dict.brandSub;

  var sidebarTitleEl = document.querySelector(".sidebar-title");
  if (sidebarTitleEl) sidebarTitleEl.textContent = dict.watchlistTitle;

  var searchInputEl = document.getElementById("searchInput");
  if (searchInputEl) searchInputEl.placeholder = dict.searchPlaceholder;

  var indicatorsBtnLabel = document.getElementById("indicatorsBtnLabel");
  if (indicatorsBtnLabel) indicatorsBtnLabel.textContent = dict.indicatorsBtn;

  var rsLabelEl = document.querySelector(".rs-label");
  if (rsLabelEl) rsLabelEl.textContent = dict.rsBenchmarkLabel;
  var rsToggleEl = document.getElementById("rsToggleBtn");
  if (rsToggleEl) rsToggleEl.setAttribute("title", dict.rsToggleTitle);

  var btnModeMansfield = document.getElementById("btnModeMansfield");
  if (btnModeMansfield) btnModeMansfield.textContent = dict.rsModeMansfield;
  var btnModeDeepvue = document.getElementById("btnModeDeepvue");
  if (btnModeDeepvue) btnModeDeepvue.textContent = dict.rsModeDeepvue;

  var hud52wLabel = document.getElementById("hud52wLabel");
  if (hud52wLabel) {
    var hudHighMeta = getHudHighLabelMeta(activeTimeframe, currentLang);
    hud52wLabel.textContent = hudHighMeta.label;
    hud52wLabel.title = hudHighMeta.title;
  }
  var hudVol50Label = document.getElementById("hudVol50Label");
  if (hudVol50Label) hudVol50Label.textContent = dict.hudVol50;
  var hudAdrLabel = document.getElementById("hudAdrLabel");
  if (hudAdrLabel) hudAdrLabel.textContent = dict.hudAdr;

  var metricTagChange = document.getElementById("metricTagChange");
  if (metricTagChange) metricTagChange.textContent = dict.change24h;
  var metricTagHigh = document.getElementById("metricTagHigh");
  if (metricTagHigh) metricTagHigh.textContent = dict.high24h;
  var metricTagLow = document.getElementById("metricTagLow");
  if (metricTagLow) metricTagLow.textContent = dict.low24h;
  var metricTagVol = document.getElementById("metricTagVol");
  if (metricTagVol) metricTagVol.textContent = dict.vol24h;

  var equityBannerText = document.getElementById("equityBannerText");
  if (equityBannerText) equityBannerText.textContent = dict.equityBannerText;

  var searchTabs = document.querySelectorAll ? document.querySelectorAll(".search-tab") : [];
  var tabTextMap = { all: dict.tabAll, stock: dict.tabStocks, etf: dict.tabEtfs, crypto: dict.tabCrypto };
  for (var stIdx = 0; stIdx < searchTabs.length; stIdx++) {
    var filterKey = searchTabs[stIdx].getAttribute("data-filter");
    if (filterKey && tabTextMap[filterKey]) searchTabs[stIdx].textContent = tabTextMap[filterKey];
  }

  var modalTitleEl = document.querySelector("#indicatorModal .modal-title") || document.querySelector(".modal-title");
  if (modalTitleEl) modalTitleEl.textContent = dict.indicatorsTitle;

  var closeBottomBtn = document.getElementById("closeIndicatorModalBottom");
  if (closeBottomBtn) closeBottomBtn.textContent = dict.done;

  var applyRsBtn = document.getElementById("applyRsBenchmarkBtn");
  if (applyRsBtn) applyRsBtn.textContent = dict.setTag;

  if (rsActiveBadge) {
    rsActiveBadge.textContent = dict.refBadge + rsBenchmarkTag;
  }

  var engineTagLabel = document.getElementById("engineTagLabel");
  if (engineTagLabel) engineTagLabel.textContent = dict.engineTag;

  var engineSelect = document.getElementById("engineSelect");
  if (engineSelect && engineSelect.options && engineSelect.options.length >= 2) {
    engineSelect.options[0].textContent = dict.enginePro;
    engineSelect.options[1].textContent = dict.engineTv;
  }

  var pinBtn = document.getElementById("btnPinWindow");
  if (pinBtn) pinBtn.setAttribute("title", isAlwaysOnTop ? dict.unpinTitle : dict.pinTitle);

  var ctxPinEl = document.getElementById("ctxPinWlText");
  if (ctxPinEl) {
    var isPinned = activeContextSymbol && pinnedSymbols.indexOf(activeContextSymbol) >= 0;
    ctxPinEl.textContent = isPinned ? dict.ctxUnpin : dict.ctxPin;
  }

  var ctxRemoveEl = document.getElementById("ctxRemoveWlText");
  if (ctxRemoveEl) ctxRemoveEl.textContent = dict.ctxRemove;
  var ctxBenchEl = document.getElementById("ctxSetBenchmarkText");
  if (ctxBenchEl) ctxBenchEl.textContent = dict.ctxBenchmark;
  var ctxCopyEl = document.getElementById("ctxCopySymbolText");
  if (ctxCopyEl) ctxCopyEl.textContent = dict.ctxCopy;

  var wlGrpAll = document.getElementById("wlGrpAll");
  if (wlGrpAll) wlGrpAll.textContent = dict.grpAll;
  var wlGrpLeaders = document.getElementById("wlGrpLeaders");
  if (wlGrpLeaders) wlGrpLeaders.textContent = dict.grpLeaders;
  var wlGrpVcp = document.getElementById("wlGrpVcp");
  if (wlGrpVcp) wlGrpVcp.textContent = dict.grpVcp;
  var wlGrpPullback = document.getElementById("wlGrpPullback");
  if (wlGrpPullback) wlGrpPullback.textContent = dict.grpPullback;
  var wlGrpEarnings = document.getElementById("wlGrpEarnings");
  if (wlGrpEarnings) wlGrpEarnings.textContent = dict.grpEarnings;

  var ctxGrpHeader = document.getElementById("ctxGroupHeader");
  if (ctxGrpHeader) ctxGrpHeader.textContent = dict.ctxGroupHeader;
  var ctxGrpNone = document.getElementById("ctxGrpNone");
  if (ctxGrpNone) ctxGrpNone.textContent = dict.ctxGrpNone;
  var ctxGrpLeaders = document.getElementById("ctxGrpLeaders");
  if (ctxGrpLeaders) ctxGrpLeaders.textContent = dict.ctxGrpLeaders;
  var ctxGrpVcp = document.getElementById("ctxGrpVcp");
  if (ctxGrpVcp) ctxGrpVcp.textContent = dict.ctxGrpVcp;
  var ctxGrpPullback = document.getElementById("ctxGrpPullback");
  if (ctxGrpPullback) ctxGrpPullback.textContent = dict.ctxGrpPullback;
  var ctxGrpEarnings = document.getElementById("ctxGrpEarnings");
  if (ctxGrpEarnings) ctxGrpEarnings.textContent = dict.ctxGrpEarnings;

  var nhBadgeEl = document.getElementById("rsNewHighBadge");
  if (nhBadgeEl) nhBadgeEl.setAttribute("title", dict.rsNewHighTooltip || "");

  var hmBtnLabel = document.getElementById("heatmapBtnLabel");
  if (hmBtnLabel) hmBtnLabel.textContent = dict.heatmapBtn;
  var hmModalTitle = document.getElementById("heatmapModalTitle");
  if (hmModalTitle) hmModalTitle.textContent = dict.heatmapModalTitle;
  var hmUnivThemes = document.getElementById("hmUnivThemes");
  if (hmUnivThemes) hmUnivThemes.textContent = dict.hmUnivThemes;
  var hmUnivWatchlist = document.getElementById("hmUnivWatchlist");
  if (hmUnivWatchlist) hmUnivWatchlist.textContent = dict.hmUnivWatchlist;
  var hmMetricChange = document.getElementById("hmMetricChange");
  if (hmMetricChange) hmMetricChange.textContent = dict.hmMetricChange;
  var hmMetricRs = document.getElementById("hmMetricRs");
  if (hmMetricRs) hmMetricRs.textContent = dict.hmMetricRs;
  var hmSortChange = document.getElementById("hmSortChange");
  if (hmSortChange) hmSortChange.textContent = dict.hmSortChange;
  var hmSortRs = document.getElementById("hmSortRs");
  if (hmSortRs) hmSortRs.textContent = dict.hmSortRs;
  var hmLoadingText = document.getElementById("heatmapLoadingText");
  if (hmLoadingText) hmLoadingText.textContent = dict.heatmapLoading;
  var hmEmptyTitle = document.getElementById("heatmapEmptyTitle");
  if (hmEmptyTitle) hmEmptyTitle.textContent = dict.heatmapEmpty;
  var hmFooterTip = document.getElementById("heatmapFooterTip");
  if (hmFooterTip) hmFooterTip.textContent = dict.heatmapFooterTip;
  if (typeof renderHeatmap === "function" && heatmapDataCache) {
    renderHeatmap();
  }

  var scBtnLabel = document.getElementById("screenerBtnLabel");
  if (scBtnLabel) scBtnLabel.textContent = dict.screenerBtn || (currentLang === "en" ? "Screener" : "智能選股");
  var scBatchAddBtn = document.getElementById("screenerBatchAddBtn");
  if (scBatchAddBtn) scBatchAddBtn.textContent = dict.batchAddTier12 || (currentLang === "en" ? "⭐ Batch Add Tier 1–2" : "⭐ 批次加入 Tier 1–2 至自選");

  if (statusBadge && statusText) {
    if (statusBadge.classList.contains("connected")) statusText.textContent = dict.connected;
    else if (statusBadge.classList.contains("disconnected")) statusText.textContent = dict.disconnected;
    else if (statusBadge.classList.contains("reconnecting")) statusText.textContent = dict.reconnecting;
    else statusText.textContent = dict.connecting;
  }

  renderPositionSizingBadge();
  if (typeof renderWatchlist === "function") renderWatchlist();
  if (activeSymbol) updateSymbolMetrics(activeSymbol, symbolStates[activeSymbol] || {}, latestBarData);
  updateCandleStyleUI();
  updateThemeToggleUI();

  if (!skipSave) {
    if (currentEngine === "C" && typeof initTradingViewWidget === "function") {
      initTradingViewWidget(activeSymbol);
      if (activeSplitLayout !== "1x1" && typeof applySplitLayout === "function") {
        applySplitLayout(activeSplitLayout);
      }
    }
    if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
  }
}

var RANGE_TF_MAP = {
  "1D": "1m",
  "5D": "5m",
  "1M": "1h",
  "3M": "1h",
  "6M": "1D",
  "YTD": "1D",
  "1Y": "1D",
  "ALL": "1D"
};

function applyDateRange(range) {
  var targetTf = RANGE_TF_MAP[range] || "1m";
  if (targetTf !== activeTimeframe) {
    userChangedTimeframe = true;
    activeTimeframe = targetTf;
    var tfButtons = document.querySelectorAll ? document.querySelectorAll(".tf-btn") : [];
    for (var i = 0; i < tfButtons.length; i++) {
      tfButtons[i].classList.toggle("active", tfButtons[i].getAttribute("data-tf") === targetTf);
    }
    loadHistory(activeSymbol).then(function() {
      clampAndSetVisibleRange(range);
    });
    loadBenchmarkHistory(rsBenchmarkTag);
    return;
  }
  clampAndSetVisibleRange(range);
}

/** 主圖目前實際的時間軸（以 candleSeries 的資料為準，取不到時用 currentRawBars）。 */
function getMainTimeAxis() {
  return getSeriesTimeAxis(candleSeries, currentRawBars);
}
if (typeof window !== "undefined") window.getMainTimeAxis = getMainTimeAxis;
if (typeof globalThis !== "undefined") globalThis.getMainTimeAxis = getMainTimeAxis;

/** 驗證時間格式是否可安全傳入 Lightweight Charts */
function isValidChartTime(t) {
  if (typeof t === "number" && isFinite(t) && t > 0) return true;
  if (typeof t === "string" && /^\d{4}-\d{2}-\d{2}$/.test(t)) return true;
  if (
    typeof t === "object" &&
    t !== null &&
    typeof t.year === "number" &&
    isFinite(t.year) &&
    t.year > 0 &&
    typeof t.month === "number" &&
    isFinite(t.month) &&
    t.month >= 1 &&
    t.month <= 12 &&
    typeof t.day === "number" &&
    isFinite(t.day) &&
    t.day >= 1 &&
    t.day <= 31
  ) {
    return true;
  }
  return false;
}
if (typeof window !== "undefined") window.isValidChartTime = isValidChartTime;
if (typeof globalThis !== "undefined") globalThis.isValidChartTime = isValidChartTime;

function getActiveTimeframeSeconds() {
  return (typeof TF_SECONDS !== "undefined" && typeof activeTimeframe === "string" && TF_SECONDS[activeTimeframe])
    ? TF_SECONDS[activeTimeframe]
    : 86400;
}

function toDrawingEpochSec(t) {
  if (t && typeof t === "object" && typeof t.year === "number" && typeof t.month === "number" && typeof t.day === "number") {
    return Math.floor(Date.UTC(t.year, t.month - 1, t.day) / 1000);
  }
  var n = typeof normalizeBarTime === "function" ? normalizeBarTime(t) : 0;
  return (typeof n === "number" && isFinite(n) && n > 0) ? n : 0;
}

/** 安全的時間轉座標轉換（含右側留白延伸與 K 棒區間線性內插） */
function safeTimeToCoordinate(ts, t, axisTimes, tfSec) {
  if (!isValidChartTime(t) || !ts || typeof ts.timeToCoordinate !== "function") return null;
  try {
    var coord = ts.timeToCoordinate(t);
    if (coord !== null && typeof coord === "number" && isFinite(coord)) return coord;
  } catch (e) {}

  var times = Array.isArray(axisTimes) ? axisTimes : (typeof getMainTimeAxis === "function" ? getMainTimeAxis() : null);
  if (!Array.isArray(times) || times.length === 0 || typeof ts.logicalToCoordinate !== "function") return null;

  var tNum = toDrawingEpochSec(t);
  var lastIdx = times.length - 1;
  var firstTime = toDrawingEpochSec(times[0]);
  var lastTime = toDrawingEpochSec(times[lastIdx]);
  if (tNum <= 0 || firstTime <= 0 || lastTime <= 0) return null;

  var step = (typeof tfSec === "number" && isFinite(tfSec) && tfSec > 0) ? tfSec : getActiveTimeframeSeconds();
  var logical;
  if (tNum > lastTime) {
    logical = lastIdx + (tNum - lastTime) / step;
  } else if (tNum < firstTime) {
    logical = -(firstTime - tNum) / step;
  } else {
    var lo = 0;
    var hi = lastIdx;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      var mTime = toDrawingEpochSec(times[mid]);
      if (mTime === tNum) {
        lo = mid;
        hi = mid;
        break;
      } else if (mTime < tNum) {
        lo = mid;
      } else {
        hi = mid;
      }
    }
    var tLo = toDrawingEpochSec(times[lo]);
    var tHi = toDrawingEpochSec(times[hi]);
    if (lo === hi || tHi <= tLo || tNum <= tLo) {
      logical = lo;
    } else if (tNum >= tHi) {
      logical = hi;
    } else {
      logical = lo + (tNum - tLo) / (tHi - tLo);
    }
  }
  try {
    var fallbackCoord = ts.logicalToCoordinate(logical);
    return (fallbackCoord !== null && typeof fallbackCoord === "number" && isFinite(fallbackCoord)) ? fallbackCoord : null;
  } catch (e2) {
    return null;
  }
}
if (typeof window !== "undefined") window.safeTimeToCoordinate = safeTimeToCoordinate;
if (typeof globalThis !== "undefined") globalThis.safeTimeToCoordinate = safeTimeToCoordinate;

/** 從座標吸附最近之有效時間（傳入 tfSec 時，右側留白區依週期秒數推算絕對時間） */
function snapTimeFromCoordinate(ts, x, axisTimes, tfSec) {
  if (ts && typeof ts.coordinateToTime === "function") {
    try {
      var t = ts.coordinateToTime(x);
      if (isValidChartTime(t)) return t;
    } catch (e) {}
  }
  if (!Array.isArray(axisTimes) || axisTimes.length === 0) return null;
  if (ts && typeof ts.coordinateToLogical === "function") {
    try {
      var logical = ts.coordinateToLogical(x);
      if (typeof logical === "number" && isFinite(logical)) {
        var lastIdx = axisTimes.length - 1;
        var idx = Math.round(logical);
        if (typeof tfSec === "number" && isFinite(tfSec) && tfSec > 0 && idx > lastIdx) {
          var lastEpoch = toDrawingEpochSec(axisTimes[lastIdx]);
          if (lastEpoch > 0) return lastEpoch + (idx - lastIdx) * tfSec;
        }
        if (idx < 0) idx = 0;
        if (idx > lastIdx) idx = lastIdx;
        var resolved = axisTimes[idx];
        if (isValidChartTime(resolved)) return resolved;
      }
    } catch (e) {}
  }
  return null;
}
if (typeof window !== "undefined") window.snapTimeFromCoordinate = snapTimeFromCoordinate;
if (typeof globalThis !== "undefined") globalThis.snapTimeFromCoordinate = snapTimeFromCoordinate;

/**
 * 從 fromTime 起（第一根 time >= fromTime 的 K 棒）到最後一根，右邊再留 rightPad 根空白的邏輯範圍。
 * bars 須依時間排序。沒有 K 棒時回傳 null；fromTime 晚於最後一根時從最後一根開始。
 */
function computeLogicalRangeForTime(bars, fromTime, rightPad) {
  if (!Array.isArray(bars) || bars.length === 0) return null;
  var pad = (typeof rightPad === "number" && isFinite(rightPad) && rightPad > 0) ? rightPad : 0;
  var n = bars.length;
  var lo = 0, hi = n - 1, idx = n - 1;
  while (lo <= hi) {
    var mid = (lo + hi) >> 1;
    if (bars[mid] && bars[mid].time >= fromTime) { idx = mid; hi = mid - 1; } else { lo = mid + 1; }
  }
  return { from: idx, to: n - 1 + pad };
}
if (typeof window !== "undefined") window.computeLogicalRangeForTime = computeLogicalRangeForTime;
if (typeof globalThis !== "undefined") globalThis.computeLogicalRangeForTime = computeLogicalRangeForTime;

function clampAndSetVisibleRange(range) {
  var axis = getMainTimeAxis();
  var n = axis.length;
  if (!chart || n === 0) return;
  if (range === "ALL") {
    try {
      var tsAll = (chart && typeof chart.timeScale === "function") ? chart.timeScale() : null;
      if (tsAll && typeof tsAll.setVisibleLogicalRange === "function") {
        tsAll.setVisibleLogicalRange({ from: 0, to: n - 1 + RIGHT_WHITESPACE_BARS });
      } else if (tsAll && typeof tsAll.fitContent === "function") {
        tsAll.fitContent();
      }
    } catch (e) {
      try { if (chart && typeof chart.timeScale === "function" && typeof chart.timeScale().fitContent === "function") chart.timeScale().fitContent(); } catch (err) {}
    }
    return;
  }
  var now = axis[n - 1];
  var from = now;
  if (range === "1D") from = now - 86400;
  else if (range === "5D") from = now - 86400 * 5;
  else if (range === "1M") from = now - 86400 * 30;
  else if (range === "3M") from = now - 86400 * 90;
  else if (range === "6M") from = now - 86400 * 180;
  else if (range === "YTD") from = computeYtdStart(now, activeSymbol);
  else if (range === "1Y") from = now - 86400 * 365;

  var earliest = axis[0];
  if (from < earliest) from = earliest;
  if (from >= now) {
    resetChartView(chart, n);
    return;
  }

  // Lightweight Charts 4.2.0 的 setVisibleRange / setVisibleLogicalRange 要到下一個畫面才生效，
  // 設定後立刻 getVisibleLogicalRange 會讀到舊範圍；所以直接從 K 棒算出邏輯範圍，只設定一次。
  try {
    var logical = computeLogicalRangeForTime(axis.map(function(t) { return { time: t }; }), from, RIGHT_WHITESPACE_BARS);
    var tsRange = (chart && typeof chart.timeScale === "function") ? chart.timeScale() : null;
    if (tsRange && logical && typeof tsRange.setVisibleLogicalRange === "function") {
      tsRange.setVisibleLogicalRange(logical);
    } else if (tsRange && typeof tsRange.setVisibleRange === "function") {
      tsRange.setVisibleRange({ from: from, to: now });
    }
  } catch (e) {
    try { if (chart && typeof chart.timeScale === "function" && typeof chart.timeScale().fitContent === "function") chart.timeScale().fitContent(); } catch (err) {}
  }
  if (typeof setPriceScaleMode === "function") {
    setPriceScaleMode(currentPriceScaleMode);
  }
}

function setRsBenchmark(tag) {
  if (!tag || !/^[A-Z0-9:._\-]{1,24}$/.test(String(tag).trim().toUpperCase())) return;
  tag = String(tag).trim().toUpperCase();
  rsBenchmarkTag = tag;
  safeSetStorage("tv_rs_benchmark", tag);
  var dict = i18n[currentLang] || i18n.en;
  if (rsActiveBadge) rsActiveBadge.textContent = dict.refBadge + tag;
  if (rsBenchmarkCustomInput) rsBenchmarkCustomInput.value = tag;
  if (rsSeries) rsSeries.applyOptions({ title: "RS (" + tag + ")" });

  var rsPills = document.querySelectorAll ? document.querySelectorAll(".rs-pill") : [];
  for (var p = 0; p < rsPills.length; p++) {
    rsPills[p].classList.toggle("active", rsPills[p].getAttribute("data-bench") === tag);
  }

  if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();

  if (currentEngine === "C" && typeof initTradingViewWidget === "function") {
    initTradingViewWidget(activeSymbol);
  }

  loadBenchmarkHistory(tag).then(function() {
    if (currentRawBars && currentRawBars.length > 0) {
      renderChartData(currentRawBars, { keepView: true });
    }
    for (var sp = 2; sp <= 4; sp++) {
      if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
        renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
      }
    }
  });
}

function openFundamentalsModal() {
  if (!fundamentalsModal) return;
  fundamentalsModal.style.display = "flex";
  fetchFundamentals(activeSymbol, false);
}

function closeFundamentalsModalHandler() {
  if (fundamentalsModal) {
    fundamentalsModal.style.display = "none";
  }
}

var _fundSeq = 0;
var _fundAbort = null;

function fetchFundamentals(symbol, refresh) {
  if (!symbol) return;
  if (_fundAbort) {
    try { _fundAbort.abort(); } catch (e) {}
  }
  var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  _fundAbort = ctrl;
  var seq = ++_fundSeq;

  var tagEl = document.getElementById("fundamentalsSymbolTag");
  var statusEl = document.getElementById("fundamentalsStatus");
  var modelTagEl = document.getElementById("fundamentalsModelTag");
  var aiContentEl = document.getElementById("fundamentalsAiContent");
  var h52El = document.getElementById("fund52wHigh");
  var l52El = document.getElementById("fund52wLow");
  var pctHighEl = document.getElementById("fundPctFromHigh");
  var peEl = document.getElementById("fundPE");
  var epsEl = document.getElementById("fundEPS");
  var revGrowthEl = document.getElementById("fundRevGrowth");

  if (tagEl) tagEl.textContent = getSymbolDisplayName(symbol);
  if (statusEl) statusEl.textContent = refresh ? "正在重新分析與診斷..." : "正在讀取財報數據...";
  if (aiContentEl) aiContentEl.textContent = "正在透過 AI 投資長模型進行全方位量化與基本面診斷...";

  var url = "/api/fundamentals?symbol=" + encodeURIComponent(symbol) + (refresh ? "&refresh=true" : "");
  fetch(url, ctrl ? { signal: ctrl.signal } : undefined)
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      if (seq !== _fundSeq || symbol !== activeSymbol) return;
      var metrics = data.metrics || {};
      if (statusEl) {
        var updateTimeStr = data.updated_at ? new Date(data.updated_at * 1000).toLocaleTimeString() : "";
        statusEl.textContent = (data.cached ? "📦 快取報告" : "⚡ 最新診斷") + (updateTimeStr ? " (" + updateTimeStr + ")" : "");
      }
      if (modelTagEl) {
        modelTagEl.textContent = formatModelSourceLabel(data.model_used || data.model, data.source);
      }

      if (h52El) h52El.textContent = metrics.fifty_two_week_high ? formatPrice(metrics.fifty_two_week_high, symbol) : "--";
      if (l52El) l52El.textContent = metrics.fifty_two_week_low ? formatPrice(metrics.fifty_two_week_low, symbol) : "--";
      if (pctHighEl) {
        if (metrics.pct_from_high !== null && metrics.pct_from_high !== undefined) {
          var val = Number(metrics.pct_from_high);
          pctHighEl.textContent = (val > 0 ? "+" : "") + val.toFixed(2) + "%";
          pctHighEl.style.color = val >= -15 ? "#22c55e" : "#ef4444";
        } else {
          pctHighEl.textContent = "--";
          pctHighEl.style.color = "";
        }
      }
      if (peEl) {
        var peStr = metrics.pe_ratio ? Number(metrics.pe_ratio).toFixed(1) : "--";
        var fPeStr = metrics.forward_pe ? Number(metrics.forward_pe).toFixed(1) : "--";
        peEl.textContent = peStr + " / " + fPeStr;
      }
      if (epsEl) {
        epsEl.textContent = metrics.eps ? "$" + Number(metrics.eps).toFixed(2) : "--";
      }
      if (revGrowthEl) {
        revGrowthEl.textContent = metrics.revenue_yoy || "--";
      }

      if (aiContentEl) {
        aiContentEl.textContent = data.ai_summary || "暫無 AI 診斷內容。";
      }
    })
    .catch(function(err) {
      if (err && err.name === "AbortError") return;
      if (seq !== _fundSeq) return;
      if (statusEl) statusEl.textContent = "讀取失敗: " + err.message;
      if (aiContentEl) aiContentEl.textContent = "無法取得基本面報告，請確認網路連線或稍後再試。";
    });
}

function openAiLensModal() {
  if (!aiLensModal) return;
  aiLensModal.style.display = "flex";
  fetchAiLens(activeSymbol, activeTimeframe, false);
}

function closeAiLensModalHandler() {
  if (aiLensModal) {
    aiLensModal.style.display = "none";
  }
}

var _aiLensSeq = 0;
var _aiLensAbort = null;

function fetchAiLens(symbol, timeframe, refresh) {
  if (!symbol) return;
  if (_aiLensAbort) {
    try { _aiLensAbort.abort(); } catch (e) {}
  }
  var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  _aiLensAbort = ctrl;
  var seq = ++_aiLensSeq;

  var sym = symbol;
  var tf = timeframe || activeTimeframe || "1D";

  var symTagEl = document.getElementById("aiLensSymbolBadge");
  var statusEl = document.getElementById("aiLensStatus");
  var biasEl = document.getElementById("aiLensBias");
  var confEl = document.getElementById("aiLensConfidence");
  var confFillEl = document.getElementById("aiLensConfidenceFill");
  var regimeEl = document.getElementById("aiLensRegime");
  var barTimeEl = document.getElementById("aiLensBarTime");
  var emaEl = document.getElementById("aiLensEma");
  var smaEl = document.getElementById("aiLensSma");
  var adrEl = document.getElementById("aiLensAdr");
  var volRatioEl = document.getElementById("aiLensVolRatio");
  var highDistEl = document.getElementById("aiLensHighDist");
  var rsRatingEl = document.getElementById("aiLensRsRating");
  var setupEl = document.getElementById("aiLensSetup");
  var invalidationEl = document.getElementById("aiLensInvalidation");
  var failureModeEl = document.getElementById("aiLensFailureMode");
  var fundamentalsEl = document.getElementById("aiLensFundamentals");
  var humanNoteEl = document.getElementById("aiLensHumanNote");
  var modelNoticeEl = document.getElementById("aiLensModelNotice");

  if (symTagEl) symTagEl.textContent = getSymbolDisplayName(sym) + " (" + tf + ")";
  if (statusEl) statusEl.textContent = refresh ? "正在透過量化快照與 AI 模型重新診斷..." : "正在讀取 AI Agent Lens 數據...";
  if (setupEl) setupEl.textContent = "正在分析多維度均線、動能型態與關鍵支撐壓力...";
  if (invalidationEl) invalidationEl.textContent = "計算中...";
  if (failureModeEl) failureModeEl.textContent = "評估中...";
  if (fundamentalsEl) fundamentalsEl.textContent = "綜整中...";
  if (humanNoteEl) humanNoteEl.textContent = "生成中...";

  var url = "/api/ai-lens?symbol=" + encodeURIComponent(sym) + "&timeframe=" + encodeURIComponent(tf) + (refresh ? "&refresh=true" : "");
  fetch(url, ctrl ? { signal: ctrl.signal } : undefined)
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      if (seq !== _aiLensSeq || sym !== activeSymbol) return;
      var rep = data.report || {};
      var lens = rep.lens || {};
      var quant = lens.quant_metrics || {};
      var decision = rep.decision || {};

      if (statusEl) {
        var updateTimeStr = rep.updated_at ? new Date(rep.updated_at * 1000).toLocaleTimeString() : "";
        statusEl.textContent = (data.cached ? "📦 快取快照" : "⚡ 最新分析") + (updateTimeStr ? " (" + updateTimeStr + ")" : "");
      }
      if (modelNoticeEl) {
        var effectiveSource = data.source || decision.source;
        modelNoticeEl.textContent = "Model: " + formatModelSourceLabel(rep.model_used || data.model, effectiveSource);
      }

      if (biasEl) {
        var b = (decision.bias || "WAIT").toUpperCase();
        biasEl.textContent = b;
        biasEl.className = "bias-badge " + (b === "LONG" ? "bias-long" : (b === "SHORT" ? "bias-short" : "bias-wait"));
      }

      var conf = typeof decision.confidence === "number" ? decision.confidence : 50;
      if (confEl) confEl.textContent = conf + " / 100";
      if (confFillEl) {
        confFillEl.style.width = Math.max(0, Math.min(100, conf)) + "%";
        confFillEl.style.background = conf >= 75 ? "#10b981" : (conf >= 50 ? "#38bdf8" : "#f59e0b");
      }

      if (regimeEl) regimeEl.textContent = quant.trend_regime || "NEUTRAL";
      if (barTimeEl) {
        var bTime = rep.bar_time || (lens.confirmed_bar ? lens.confirmed_bar.time : null);
        barTimeEl.textContent = bTime ? new Date(bTime * 1000).toLocaleString() : "--";
      }

      if (emaEl) {
        var e10 = quant.ema10 !== null && quant.ema10 !== undefined ? formatPrice(quant.ema10, sym) : "--";
        var e21 = quant.ema21 !== null && quant.ema21 !== undefined ? formatPrice(quant.ema21, sym) : "--";
        emaEl.textContent = e10 + " / " + e21;
      }
      if (smaEl) {
        var s50 = quant.sma50 !== null && quant.sma50 !== undefined ? formatPrice(quant.sma50, sym) : "--";
        var s200 = quant.sma200 !== null && quant.sma200 !== undefined ? formatPrice(quant.sma200, sym) : "--";
        smaEl.textContent = s50 + " / " + s200;
      }
      if (adrEl) {
        adrEl.textContent = quant.adr_pct_20 !== null && quant.adr_pct_20 !== undefined ? quant.adr_pct_20 + "%" : "--";
      }
      if (volRatioEl) {
        volRatioEl.textContent = quant.volume_ratio_50 !== null && quant.volume_ratio_50 !== undefined ? quant.volume_ratio_50 + "x" : "--";
      }
      if (highDistEl) {
        if (quant.pct_from_52w_high !== null && quant.pct_from_52w_high !== undefined) {
          var hVal = Number(quant.pct_from_52w_high);
          highDistEl.textContent = (hVal > 0 ? "+" : "") + hVal.toFixed(2) + "%";
          highDistEl.style.color = hVal >= -15 ? "#22c55e" : "#ef4444";
        } else {
          highDistEl.textContent = "--";
          highDistEl.style.color = "";
        }
      }
      if (rsRatingEl) {
        rsRatingEl.textContent = quant.rs_rating ? quant.rs_rating + " / 99" : "--";
      }

      if (setupEl) setupEl.textContent = decision.setup_summary || "暫無型態摘要。";
      if (invalidationEl) invalidationEl.textContent = decision.invalidation_condition || "收盤跌破關鍵支撐均線。";
      if (failureModeEl) failureModeEl.textContent = decision.failure_mode || "未明確標記失敗風險。";
      if (fundamentalsEl) fundamentalsEl.textContent = decision.fundamental_diagnosis || "無額外基本面診斷。";
      if (humanNoteEl) humanNoteEl.textContent = decision.human_decision_note || "嚴格遵循停損停利紀律。";
    })
    .catch(function(err) {
      if (err && err.name === "AbortError") return;
      if (seq !== _aiLensSeq) return;
      if (statusEl) statusEl.textContent = "讀取失敗: " + err.message;
      if (setupEl) setupEl.textContent = "無法取得 AI Agent Lens 數據，請確認後端服務正常。";
    });
}

/**
 * Unified State & History Recovery Engine
 * Recovers terminal state and heals candle history gaps.
 */
/**
 * 把恢復回來的新 K 棒合併進現有 K 棒（純函式，不修改傳入的陣列）。
 * 時間相同的取代、比最後一根新的依序加在後面、比最後一根舊的忽略；無效的 K 棒略過。
 */
function mergeRecoveryDeltaBars(existingBars, deltaBars) {
  var result = Array.isArray(existingBars) ? existingBars.slice() : [];
  if (!Array.isArray(deltaBars) || deltaBars.length === 0) return result;
  var cleaned = [];
  for (var i = 0; i < deltaBars.length; i++) {
    var b = deltaBars[i];
    if (!b) continue;
    var t = normalizeBarTime(b.time);
    var o = safeNum(b.open), h = safeNum(b.high), l = safeNum(b.low), c = safeNum(b.close);
    if (t <= 0 || !isValidPrice(o) || !isValidPrice(h) || !isValidPrice(l) || !isValidPrice(c)) continue;
    cleaned.push({ time: t, open: o, high: h, low: l, close: c, volume: Math.max(0, safeNum(b.volume)) });
  }
  cleaned.sort(function(a, b) { return a.time - b.time; });
  for (var j = 0; j < cleaned.length; j++) {
    var db = cleaned[j];
    var lastIdx = result.length - 1;
    if (lastIdx >= 0 && result[lastIdx].time === db.time) {
      result[lastIdx] = db;
    } else if (lastIdx < 0 || db.time > result[lastIdx].time) {
      result.push(db);
    }
  }
  return result;
}

/**
 * 重畫主圖但保留使用者的縮放與捲動位置（以距離最右邊的 K 棒數為準，新增的 K 棒會出現在原本的右緣）。
 * prevLen：重畫前的 K 棒數量；保留當前邏輯範圍與縮放位置。
 */
function renderChartDataKeepView(bars, prevLen) {
  var ts = (chart && typeof chart.timeScale === "function") ? chart.timeScale() : null;
  var range = null;
  try {
    range = (ts && typeof ts.getVisibleLogicalRange === "function") ? ts.getVisibleLogicalRange() : null;
  } catch (e) {
    range = null;
  }
  var prevTimes = (typeof getSeriesTimeAxis === "function" && candleSeries) ? getSeriesTimeAxis(candleSeries, null) : null;
  var barsTimeArray = (typeof getSeriesTimeAxis === "function") ? getSeriesTimeAxis(null, bars) : [];
  var preserved = (range && prevTimes && prevTimes.length > 0 && barsTimeArray && barsTimeArray.length > 0)
    ? computePreservedLogicalRange(prevTimes, barsTimeArray, range)
    : null;
  renderChartData(bars, { keepView: true });
  if (ts && typeof ts.setVisibleLogicalRange === "function") {
    if (preserved) {
      try {
        ts.setVisibleLogicalRange({ from: preserved.from, to: preserved.to });
      } catch (e1) {}
    } else if (range && Number.isFinite(range.from) && Number.isFinite(range.to) && typeof prevLen === "number" && prevLen > 0 && Array.isArray(bars) && bars.length > 0) {
      var shift = bars.length - prevLen;
      try {
        ts.setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift });
      } catch (e2) {}
    }
  }
}

function recoverTerminalState(options) {
  var opts = options || {};
  var sym = opts.symbol || activeSymbol || "BINANCE:BTCUSDT";
  var tf = opts.timeframe || activeTimeframe || "1D";
  // 第 7 段：由 seq 缺口觸發的恢復只補 K 棒，不覆蓋自選清單
  var includeWatchlist = opts.includeWatchlist !== false;
  var activeBars = (sym === activeSymbol && tf === activeTimeframe && Array.isArray(currentRawBars)) ? currentRawBars : [];
  var lastLocalTime = activeBars.length ? activeBars[activeBars.length - 1].time : null;
  var sinceTime = opts.sinceTime || null;
  if (!sinceTime && typeof lastLocalTime === "number" && lastLocalTime > 1) {
    // 1m：連最後一根一起要（它可能在斷線期間才收完）；較大週期：只要更新的 K 棒，避免被較舊的快取蓋掉
    sinceTime = (tf === "1m") ? lastLocalTime - 1 : lastLocalTime;
  }

  var url = "/api/recovery?symbol=" + encodeURIComponent(sym) + "&timeframe=" + encodeURIComponent(tf);
  if (sinceTime) {
    url += "&since_time=" + encodeURIComponent(sinceTime);
  }

  return fetch(url)
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      if (!data || data.status !== "ok") return data;

      if (includeWatchlist && Array.isArray(data.watchlist) && data.watchlist.length > 0) {
        symbolsList = data.watchlist;
        renderWatchlist();
      }

      // 回應回來時標的或週期已經換了，就不要把這批 K 棒畫上去
      var stillCurrent = (activeSymbol === sym && activeTimeframe === tf);
      if (stillCurrent) {
        var prevLen = Array.isArray(currentRawBars) ? currentRawBars.length : 0;
        var nextBars = null;
        if (sinceTime && prevLen > 0) {
          // 已有 K 棒：只合併新的部分，不整張替換（指標起點與縮放位置不會跳）
          if (Array.isArray(data.delta_bars) && data.delta_bars.length > 0) {
            nextBars = mergeRecoveryDeltaBars(currentRawBars, data.delta_bars);
          }
        } else if (Array.isArray(data.bars) && data.bars.length > 0) {
          nextBars = data.bars;
        }
        if (nextBars) {
          currentRawBars = nextBars;
          liveBucketState = null;
          if (currentEngine !== "C") renderChartDataKeepView(currentRawBars, prevLen);
        }
      }

      var benchSym = resolveBenchmarkSymbol(rsBenchmarkTag);
      if (indicatorConfig && indicatorConfig.rsLine && stillCurrent) {
        fetch("/api/history?symbol=" + encodeURIComponent(benchSym) + "&timeframe=" + encodeURIComponent(tf))
          .then(function(r) { return r.ok ? r.json() : null; })
          .then(function(bData) {
            if (activeSymbol !== sym || activeTimeframe !== tf) return;
            if (bData && Array.isArray(bData.bars) && bData.bars.length > 0 && typeof updateRsPane === "function") {
              updateRsPane(currentRawBars, bData.bars);
            }
          })
          .catch(function(e) {
            console.debug("Failed to recover RS benchmark bars:", e);
          });
      }

      return data;
    })
    .catch(function(err) {
      console.warn("Terminal recovery warning:", err);
      return null;
    });
}

// 第 7 段：回到前景時 focus 與 visibilitychange 會同時觸發，2 秒內只恢復一次
var _focusRecoveryAt = 0;
function requestFocusRecovery() {
  if (!activeSymbol) return;
  var nowMs = Date.now();
  if (nowMs - _focusRecoveryAt < 2000) return;
  _focusRecoveryAt = nowMs;
  recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe, includeWatchlist: false });
}

// ==========================================================================
// Institutional Stock Screener Controller (RS + ATR + CANSLIM Engine)
// ==========================================================================

var screenerModal = null;
var isScreenerDocked = false;
var lastScreenerResults = [];
var lastBenchmarkStatus = { symbol: "SPY", close: 0, regime: "NEUTRAL" };
var currentScreenerSort = { col: "filter_count", asc: false };
var activeCustomPresetName = null;

var SCREENER_PRESET_CONFIGS = {
  multi_filter: {
    min_price: 15.0,
    min_avg_vol: 500000,
    min_eps_yoy: 20.0,
    min_rev_yoy: 20.0,
    min_roe: 15.0,
    allow_missing_fundamentals: false,
    min_volume_ratio: 1.2,
    min_perf_26w: 10.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 80,
    max_pct_from_52w_high: 15.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  },
  canslim_standard: {
    min_price: 15.0,
    min_avg_vol: 400000,
    min_eps_yoy: 25.0,
    min_rev_yoy: 25.0,
    min_roe: 17.0,
    allow_missing_fundamentals: false,
    min_volume_ratio: 1.0,
    min_perf_26w: 0.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 80,
    max_pct_from_52w_high: 15.0,
    hide_v6_hard: true,
    risk_dollar: 500.0,
  },
  minervini_sepa: {
    min_price: 12.0,
    min_avg_vol: 300000,
    min_eps_yoy: null,
    min_rev_yoy: null,
    min_roe: null,
    allow_missing_fundamentals: true,
    min_volume_ratio: 1.2,
    min_perf_26w: 15.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 85,
    max_pct_from_52w_high: 15.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  },
  weinstein_s2: {
    min_price: 10.0,
    min_avg_vol: 500000,
    min_eps_yoy: null,
    min_rev_yoy: null,
    min_roe: null,
    allow_missing_fundamentals: true,
    min_volume_ratio: 1.5,
    min_perf_26w: 5.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 70,
    max_pct_from_52w_high: 25.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  },
  stage2_vcp: {
    min_price: 12.0,
    min_avg_vol: 300000,
    min_eps_yoy: null,
    min_rev_yoy: null,
    min_roe: null,
    allow_missing_fundamentals: true,
    min_volume_ratio: 1.1,
    min_perf_26w: 10.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 80,
    max_pct_from_52w_high: 15.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  },
  rs_leaders: {
    min_price: 12.0,
    min_avg_vol: 300000,
    min_eps_yoy: null,
    min_rev_yoy: null,
    min_roe: null,
    allow_missing_fundamentals: true,
    min_volume_ratio: 1.2,
    min_perf_26w: 15.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: true,
    min_rs_rating: 85,
    max_pct_from_52w_high: 20.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  },
  canslim_growth: {
    min_price: 15.0,
    min_avg_vol: 400000,
    min_eps_yoy: 20.0,
    min_rev_yoy: 20.0,
    min_roe: 15.0,
    allow_missing_fundamentals: false,
    min_volume_ratio: 1.0,
    min_perf_26w: 0.0,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 75,
    max_pct_from_52w_high: 20.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  },
  low_vol_base: {
    min_price: 10.0,
    min_avg_vol: 200000,
    min_eps_yoy: null,
    min_rev_yoy: null,
    min_roe: null,
    allow_missing_fundamentals: true,
    min_volume_ratio: 0.5,
    min_perf_26w: 0.0,
    stage2_only: false,
    weekly_stage2_only: false,
    power_trend_only: false,
    min_rs_rating: 65,
    max_pct_from_52w_high: 30.0,
    hide_v6_hard: false,
    risk_dollar: 500.0,
  }
};

function getCustomScreenerPresets() {
  try {
    if (typeof localStorage !== "undefined") {
      var raw = localStorage.getItem("tv_custom_screener_presets");
      if (raw) return JSON.parse(raw);
    }
  } catch (e) {}
  return {};
}

function renderCustomPresetPills() {
  var bar = document.getElementById("screenerPresetsBar");
  if (!bar) return;
  var existingCustoms = bar.querySelectorAll(".screener-custom-pill");
  existingCustoms.forEach(function(el) { el.remove(); });

  var actionWrap = bar.querySelector(".screener-custom-presets-actions");
  var presets = getCustomScreenerPresets();
  for (var name in presets) {
    var pill = document.createElement("button");
    pill.className = "screener-preset-pill screener-custom-pill" + (activeCustomPresetName === name ? " active" : "");
    pill.setAttribute("data-preset", "custom_" + name);
    pill.textContent = "⚙️ " + name;
    (function(pName, pBtn) {
      pBtn.onclick = function() {
        applyScreenerPreset("custom_" + pName);
      };
    })(name, pill);
    if (actionWrap) {
      bar.insertBefore(pill, actionWrap);
    } else {
      bar.appendChild(pill);
    }
  }
}

function saveCurrentAsCustomPreset() {
  var name = prompt(currentLang === "en" ? "Enter custom strategy name:" : "請輸入自訂策略名稱：");
  if (!name || !name.trim()) return;
  name = name.trim();
  var presets = getCustomScreenerPresets();

  var rsInput = document.getElementById("scMinRsRating");
  var stage2Chk = document.getElementById("scStage2Only");
  var weeklyStage2Chk = document.getElementById("scWeeklyStage2Only");
  var powerTrendChk = document.getElementById("scPowerTrendOnly");
  var maxHighPct = document.getElementById("scMaxPctFromHigh");
  var minVol = document.getElementById("scMinVolRatio");
  var minPriceInput = document.getElementById("scMinPrice");
  var minAvgVolInput = document.getElementById("scMinAvgVol");
  var minEpsInput = document.getElementById("scMinEpsYoY");
  var minRevInput = document.getElementById("scMinRevYoY");
  var minRoeInput = document.getElementById("scMinRoe");
  var allowMissingChk = document.getElementById("scAllowMissingFundamentals");
  var minPerf26wInput = document.getElementById("scMinPerf26W");
  var hideV6Chk = document.getElementById("scHideV6Hard");
  var riskInput = document.getElementById("scRiskDollar");

  presets[name] = {
    name: name,
    min_price: minPriceInput ? parseFloat(minPriceInput.value) : 10,
    min_avg_vol: minAvgVolInput ? parseInt(minAvgVolInput.value, 10) : 300000,
    min_eps_yoy: minEpsInput && minEpsInput.value ? parseFloat(minEpsInput.value) : null,
    min_rev_yoy: minRevInput && minRevInput.value ? parseFloat(minRevInput.value) : null,
    min_roe: minRoeInput && minRoeInput.value ? parseFloat(minRoeInput.value) : null,
    allow_missing_fundamentals: allowMissingChk ? allowMissingChk.checked : false,
    min_volume_ratio: minVol ? parseFloat(minVol.value) : 1.0,
    min_perf_26w: minPerf26wInput ? parseFloat(minPerf26wInput.value) : 0,
    stage2_only: stage2Chk ? stage2Chk.checked : true,
    weekly_stage2_only: weeklyStage2Chk ? weeklyStage2Chk.checked : false,
    power_trend_only: powerTrendChk ? powerTrendChk.checked : false,
    min_rs_rating: rsInput ? parseInt(rsInput.value, 10) : 70,
    max_pct_from_52w_high: maxHighPct ? parseFloat(maxHighPct.value) : 25,
    hide_v6_hard: hideV6Chk ? hideV6Chk.checked : false,
    risk_dollar: getRiskDollar(),
  };

  safeSetStorage("tv_custom_screener_presets", JSON.stringify(presets));
  renderCustomPresetPills();
  applyScreenerPreset("custom_" + name);
}

function deleteCurrentCustomPreset() {
  if (!activeCustomPresetName) return;
  var ok = confirm((currentLang === "en" ? "Delete strategy " : "確定要刪除策略 ") + activeCustomPresetName + "？");
  if (!ok) return;
  var presets = getCustomScreenerPresets();
  delete presets[activeCustomPresetName];
  safeSetStorage("tv_custom_screener_presets", JSON.stringify(presets));
  activeCustomPresetName = null;
  renderCustomPresetPills();
  applyScreenerPreset("multi_filter");
}

function batchAddTier12ToWatchlist() {
  if (!lastScreenerResults || lastScreenerResults.length === 0) {
    alert(currentLang === "en" ? "No screened candidates available." : "目前尚無篩選結果可加入。");
    return;
  }
  var addedCount = 0;
  lastScreenerResults.forEach(function(item) {
    if (item.tier === "Tier 1" || item.tier === "Tier 2" || (item.filter_count && item.filter_count >= 3)) {
      var symId = item.id || (item.exchange ? item.exchange + ":" + item.symbol : "NASDAQ:" + item.symbol);
      var already = symbolsList.some(function(s) { return s.id === symId || s.symbol === item.symbol; });
      if (!already) {
        symbolsList.push({
          id: symId,
          pair: item.symbol,
          symbol: item.symbol,
          name: item.company_name || item.name || item.symbol,
          exchange: symId.indexOf(":") !== -1 ? symId.split(":")[0] : "NASDAQ",
          type: "stock"
        });
        addedCount++;
      }
      watchlistGroups[symId] = "leaders";
    }
  });

  safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
  safeSetStorage("tv_watchlist_groups", JSON.stringify(watchlistGroups));
  if (typeof savePreferencesDebounced === "function") savePreferencesDebounced();
  if (typeof renderWatchlist === "function") renderWatchlist();
  if (typeof renderScreenerTable === "function") renderScreenerTable(lastScreenerResults);

  var btn = document.getElementById("screenerBatchAddBtn");
  if (btn) {
    var oldText = btn.textContent;
    btn.textContent = currentLang === "en" ? ("✓ Added " + addedCount + " leaders") : ("✓ 已加入 " + addedCount + " 檔領頭羊");
    setTimeout(function() { btn.textContent = oldText; }, 2500);
  }
}

function openScreenerModal() {
  screenerModal = document.getElementById("screenerModal");
  if (!screenerModal) return;
  screenerModal.style.display = "flex";
  renderCustomPresetPills();
  if (lastScreenerResults.length === 0) {
    runStockScreen();
  }
}

function closeScreenerModal() {
  screenerModal = document.getElementById("screenerModal");
  if (screenerModal) {
    screenerModal.style.display = "none";
  }
}

function toggleScreenerModal() {
  screenerModal = document.getElementById("screenerModal");
  if (!screenerModal) return;
  if (screenerModal.style.display === "none" || !screenerModal.style.display) {
    openScreenerModal();
  } else {
    closeScreenerModal();
  }
}

function toggleScreenerDock() {
  isScreenerDocked = !isScreenerDocked;
  var overlay = document.getElementById("screenerModal");
  var dialog = document.getElementById("screenerDialog");
  var dockBtn = document.getElementById("screenerDockBtn");
  if (overlay && dialog) {
    if (isScreenerDocked) {
      overlay.classList.add("docked");
      dialog.classList.add("docked");
      if (dockBtn) dockBtn.textContent = "🗖 恢復浮動";
    } else {
      overlay.classList.remove("docked");
      dialog.classList.remove("docked");
      if (dockBtn) dockBtn.textContent = "🗗 停靠底部";
    }
  }
}

function applyScreenerPreset(presetId) {
  var delBtn = document.getElementById("screenerDeletePresetBtn");
  var cfg = null;
  if (presetId.startsWith("custom_")) {
    var cName = presetId.replace("custom_", "");
    activeCustomPresetName = cName;
    var cPresets = getCustomScreenerPresets();
    cfg = cPresets[cName];
    if (delBtn) delBtn.style.display = "inline-flex";
  } else {
    activeCustomPresetName = null;
    cfg = SCREENER_PRESET_CONFIGS[presetId];
    if (delBtn) delBtn.style.display = "none";
  }
  if (!cfg) return;

  var rsInput = document.getElementById("scMinRsRating");
  var stage2Chk = document.getElementById("scStage2Only");
  var weeklyStage2Chk = document.getElementById("scWeeklyStage2Only");
  var powerTrendChk = document.getElementById("scPowerTrendOnly");
  var maxHighPct = document.getElementById("scMaxPctFromHigh");
  var minVol = document.getElementById("scMinVolRatio");
  var minPriceInput = document.getElementById("scMinPrice");
  var minAvgVolInput = document.getElementById("scMinAvgVol");
  var minEpsInput = document.getElementById("scMinEpsYoY");
  var minRevInput = document.getElementById("scMinRevYoY");
  var minRoeInput = document.getElementById("scMinRoe");
  var allowMissingChk = document.getElementById("scAllowMissingFundamentals");
  var minPerf26wInput = document.getElementById("scMinPerf26W");
  var hideV6Chk = document.getElementById("scHideV6Hard");
  var riskInput = document.getElementById("scRiskDollar");

  if (minPriceInput && cfg.min_price !== undefined) minPriceInput.value = cfg.min_price;
  if (minAvgVolInput && cfg.min_avg_vol !== undefined) minAvgVolInput.value = cfg.min_avg_vol;
  if (minEpsInput && cfg.min_eps_yoy !== undefined) minEpsInput.value = cfg.min_eps_yoy !== null ? cfg.min_eps_yoy : 0;
  if (minRevInput && cfg.min_rev_yoy !== undefined) minRevInput.value = cfg.min_rev_yoy !== null ? cfg.min_rev_yoy : 0;
  if (minRoeInput && cfg.min_roe !== undefined) minRoeInput.value = cfg.min_roe !== null ? cfg.min_roe : 0;
  if (allowMissingChk && cfg.allow_missing_fundamentals !== undefined) allowMissingChk.checked = Boolean(cfg.allow_missing_fundamentals);
  if (minVol && cfg.min_volume_ratio !== undefined) minVol.value = cfg.min_volume_ratio;
  if (minPerf26wInput && cfg.min_perf_26w !== undefined) minPerf26wInput.value = cfg.min_perf_26w;
  if (stage2Chk && cfg.stage2_only !== undefined) stage2Chk.checked = Boolean(cfg.stage2_only);
  if (weeklyStage2Chk && cfg.weekly_stage2_only !== undefined) weeklyStage2Chk.checked = Boolean(cfg.weekly_stage2_only);
  if (powerTrendChk && cfg.power_trend_only !== undefined) powerTrendChk.checked = Boolean(cfg.power_trend_only);
  if (rsInput && cfg.min_rs_rating !== undefined) rsInput.value = cfg.min_rs_rating;
  if (maxHighPct && cfg.max_pct_from_52w_high !== undefined) maxHighPct.value = cfg.max_pct_from_52w_high;
  if (hideV6Chk && cfg.hide_v6_hard !== undefined) hideV6Chk.checked = Boolean(cfg.hide_v6_hard);
  // 風險金額屬於個人帳戶設定，載入策略預設時不覆蓋

  var pills = document.querySelectorAll(".screener-preset-pill");
  pills.forEach(function(p) {
    if (p.getAttribute("data-preset") === presetId) p.classList.add("active");
    else p.classList.remove("active");
  });

  if (presetId !== "custom" && !presetId.startsWith("custom_")) {
    var universeSel = document.getElementById("screenerUniverseSelect");
    if (universeSel) universeSel.value = "all_us";
    var sWrap = document.getElementById("screenerSearchWrap");
    if (sWrap) sWrap.style.display = "none";
    var cIn = document.getElementById("screenerCustomInput");
    if (cIn) cIn.style.display = "none";
  }

  runStockScreen();
}

var _scanSeq = 0;
var _scanAbort = null;

function runStockScreen() {
  var scanBtn = document.getElementById("runScreenerScanBtn");
  var statsBadge = document.getElementById("screenerStatsBadge");
  var benchBadge = document.getElementById("screenerBenchBadge");
  var emptyState = document.getElementById("screenerEmptyState");
  var tableBody = document.getElementById("screenerTableBody");

  if (_scanAbort) {
    try {
      _scanAbort.abort();
    } catch (e) {}
  }
  var ctrl = (_scanAbort = new AbortController());
  var seq = ++_scanSeq;
  var timer = setTimeout(function() {
    try {
      ctrl.abort();
    } catch (e) {}
  }, 25000);

  if (scanBtn) {
    scanBtn.disabled = true;
    scanBtn.textContent = "⏳ 篩選計算中...";
  }
  if (statsBadge) statsBadge.textContent = "正在量化計算 (Semaphore=8)...";

  var universeSel = document.getElementById("screenerUniverseSelect");
  var universeType = universeSel ? universeSel.value : "all_us";
  var customInput = document.getElementById("screenerCustomInput");
  var customSyms = (universeType === "custom" && customInput) ? customInput.value : "";

  if (universeType === "custom" && (!customSyms || !customSyms.trim())) {
    clearTimeout(timer);
    if (scanBtn) {
      scanBtn.disabled = false;
      scanBtn.textContent = "🚀 開始篩選";
    }
    if (statsBadge) {
      statsBadge.textContent = "⚠️ 請在自訂代碼框中輸入至少一檔股票代碼（如 NVDA, TSLA, MRNA, CART）";
    }
    if (emptyState) emptyState.style.display = "flex";
    if (tableBody) tableBody.innerHTML = "";
    return;
  }

  var rsInput = document.getElementById("scMinRsRating");
  var stage2Chk = document.getElementById("scStage2Only");
  var weeklyStage2Chk = document.getElementById("scWeeklyStage2Only");
  var powerTrendChk = document.getElementById("scPowerTrendOnly");
  var maxHighPct = document.getElementById("scMaxPctFromHigh");
  var minVol = document.getElementById("scMinVolRatio");
  var minPriceInput = document.getElementById("scMinPrice");
  var minAvgVolInput = document.getElementById("scMinAvgVol");
  var minEpsInput = document.getElementById("scMinEpsYoY");
  var minRevInput = document.getElementById("scMinRevYoY");
  var minRoeInput = document.getElementById("scMinRoe");
  var allowMissingChk = document.getElementById("scAllowMissingFundamentals");
  var minPerf26wInput = document.getElementById("scMinPerf26W");
  var hideV6Chk = document.getElementById("scHideV6Hard");
  var riskInput = document.getElementById("scRiskDollar");

  var riskDollar = getRiskDollar();
  var minEpsVal = minEpsInput ? parseFloat(minEpsInput.value) : null;
  var minRevVal = minRevInput ? parseFloat(minRevInput.value) : null;
  var minRoeVal = minRoeInput ? parseFloat(minRoeInput.value) : null;

  var payload = {
    universe: universeType,
    custom_symbols: customSyms,
    risk_dollar: riskDollar,
    criteria: {
      min_price: minPriceInput ? parseFloat(minPriceInput.value) : 10.0,
      min_avg_vol: minAvgVolInput ? parseInt(minAvgVolInput.value, 10) : 300000,
      min_eps_yoy: minEpsVal && minEpsVal > 0 ? minEpsVal : null,
      min_rev_yoy: minRevVal && minRevVal > 0 ? minRevVal : null,
      min_roe: minRoeVal && minRoeVal > 0 ? minRoeVal : null,
      allow_missing_fundamentals: allowMissingChk ? allowMissingChk.checked : false,
      min_volume_ratio: minVol ? parseFloat(minVol.value) : 1.0,
      min_perf_26w: minPerf26wInput ? parseFloat(minPerf26wInput.value) : 0.0,
      stage2_only: stage2Chk ? stage2Chk.checked : true,
      weekly_stage2_only: weeklyStage2Chk ? weeklyStage2Chk.checked : false,
      power_trend_only: powerTrendChk ? powerTrendChk.checked : false,
      min_rs_rating: rsInput ? parseInt(rsInput.value, 10) : 70,
      max_pct_from_52w_high: maxHighPct ? parseFloat(maxHighPct.value) : 25.0,
      hide_v6_hard: hideV6Chk ? hideV6Chk.checked : false,
      risk_dollar: riskDollar,
      stop_atr_mult: 2.0,
      mansfield_length: 252
    }
  };

  fetch("/api/screener/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: ctrl.signal
  })
  .then(function(res) {
    if (!res.ok) {
      return res.text().then(function(t) {
        throw new Error("HTTP " + res.status + " " + (t || "").slice(0, 100));
      });
    }
    return res.json();
  })
  .then(function(data) {
    if (seq !== _scanSeq) return;
    lastScreenerResults = data.items || [];
    lastBenchmarkStatus = data.benchmark_status || { symbol: "SPY", close: 0, regime: "NEUTRAL" };

    if (statsBadge) {
      if (data.warning) {
        statsBadge.textContent = "⚠️ " + data.warning;
      } else {
        statsBadge.textContent = "耗時: " + (data.elapsed_ms || 0) + "ms | 掃描: " + (data.total_scanned || 0) + " | 入榜: " + (data.matched_count || 0);
      }
    }
    if (benchBadge && data.benchmark_status) {
      benchBadge.textContent = "基準 " + data.benchmark_status.symbol + ": $" + (data.benchmark_status.close || 0) + " (" + data.benchmark_status.regime + ")";
    }

    renderScreenerTable(lastScreenerResults);
  })
  .catch(function(err) {
    if (err.name === "AbortError" || seq !== _scanSeq) return;
    if (statsBadge) statsBadge.textContent = "篩選異常: " + err.message;
    if (tableBody) tableBody.innerHTML = "";
    if (emptyState) emptyState.style.display = "flex";
  })
  .finally(function() {
    clearTimeout(timer);
    if (seq === _scanSeq) {
      if (scanBtn) {
        scanBtn.disabled = false;
        scanBtn.textContent = "🚀 開始篩選";
      }
    }
  });
}

function renderScreenerTable(items) {
  var tableBody = document.getElementById("screenerTableBody");
  var emptyState = document.getElementById("screenerEmptyState");
  if (!tableBody) return;

  tableBody.innerHTML = "";
  if (!items || items.length === 0) {
    if (emptyState) emptyState.style.display = "flex";
    return;
  }
  if (emptyState) emptyState.style.display = "none";

  items.forEach(function(item) {
    var symId = item.id || (item.exchange ? item.exchange + ":" + item.symbol : "NASDAQ:" + item.symbol);
    var tr = document.createElement("tr");
    tr.className = "screener-row" + (symId === activeSymbol ? " active-row" : "");
    tr.setAttribute("data-symbol", symId);

    var chgClass = (item.change_pct || 0) >= 0 ? "val-green" : "val-red";
    var chgSign = (item.change_pct || 0) >= 0 ? "+" : "";

    var isAlreadyInWl = symbolsList.some(function(s) { return s.id === symId || s.symbol === item.symbol; });
    var btnText = isAlreadyInWl ? "✓ 已加入" : "+ 自選";
    var btnClass = "btn-table-add" + (isAlreadyInWl ? " added" : "");

    // 1. Tier / Frameworks
    var tdTier = document.createElement("td");
    var tierName = item.tier || "Watch";
    var tierCls = "tier-watch";
    if (tierName === "Tier 1") tierCls = "tier-1";
    else if (tierName === "Tier 2") tierCls = "tier-2";
    else if (tierName === "Tier 3") tierCls = "tier-3";
    var tierBadge = document.createElement("span");
    tierBadge.className = "tier-badge " + tierCls;
    tierBadge.textContent = tierName;
    tdTier.appendChild(tierBadge);
    if (Array.isArray(item.frameworks)) {
      item.frameworks.forEach(function(fw) {
        var fwTag = document.createElement("span");
        fwTag.className = "fw-tag";
        fwTag.textContent = fw;
        tdTier.appendChild(fwTag);
      });
    }
    tr.appendChild(tdTier);

    // 2. Symbol
    var tdSym = document.createElement("td");
    tdSym.className = "sym-col";
    tdSym.textContent = item.symbol || "";
    tr.appendChild(tdSym);

    // 3. Name
    var tdName = document.createElement("td");
    tdName.className = "name-col";
    var compName = item.company_name || item.name || item.symbol || "";
    tdName.title = compName;
    tdName.textContent = compName;
    tr.appendChild(tdName);

    // 4. Close Price
    var tdPrice = document.createElement("td");
    tdPrice.style.fontFamily = "var(--font-mono, monospace)";
    tdPrice.style.fontWeight = "600";
    tdPrice.textContent = item.close ? "$" + item.close.toFixed(2) : "--";
    tr.appendChild(tdPrice);

    // 5. 24H Change%
    var tdChg = document.createElement("td");
    tdChg.className = chgClass;
    tdChg.textContent = chgSign + (item.change_pct !== undefined ? item.change_pct.toFixed(2) : "0.00") + "%";
    tr.appendChild(tdChg);

    // 6. Streak (Consecutive days >= 2 filters)
    var tdStreak = document.createElement("td");
    var sDays = item.streak_days || 0;
    var streakBadge = document.createElement("span");
    streakBadge.className = "streak-badge" + (sDays >= 3 ? " streak-hot" : "");
    streakBadge.textContent = sDays + "d";
    tdStreak.appendChild(streakBadge);
    var delta = item.filter_delta || "NEW";
    var deltaSpan = document.createElement("span");
    var deltaCls = "delta-tag";
    if (delta === "NEW") deltaCls += " new";
    else if (delta.startsWith("+")) deltaCls += " pos";
    else if (delta.startsWith("-")) deltaCls += " neg";
    deltaSpan.className = deltaCls;
    deltaSpan.textContent = " (" + delta + ")";
    tdStreak.appendChild(deltaSpan);
    tr.appendChild(tdStreak);

    // 7. RS Rating
    var tdRs = document.createElement("td");
    var rsSpan = document.createElement("span");
    if ((item.rs_rating || 0) >= 80) rsSpan.className = "badge-rs-high";
    rsSpan.textContent = item.rs_rating !== undefined ? String(item.rs_rating) : "--";
    tdRs.appendChild(rsSpan);
    tr.appendChild(tdRs);

    // 8. EPS / Revenue YoY
    var tdFund = document.createElement("td");
    tdFund.style.fontFamily = "var(--font-mono, monospace)";
    var epsStr = item.eps_yoy !== undefined && item.eps_yoy !== null && item.eps_yoy !== 0 ? item.eps_yoy.toFixed(1) + "%" : "--";
    var revStr = item.rev_yoy !== undefined && item.rev_yoy !== null && item.rev_yoy !== 0 ? item.rev_yoy.toFixed(1) + "%" : "--";
    tdFund.textContent = epsStr + " / " + revStr;
    tr.appendChild(tdFund);

    // 9. V6 Earnings Risk
    var tdV6 = document.createElement("td");
    var v6Risk = item.v6_risk || "Clear";
    var v6Tag = document.createElement("span");
    var v6Cls = "v6-clear";
    if (v6Risk === "V6-Hard") v6Cls = "v6-hard";
    else if (v6Risk === "V6-Soft") v6Cls = "v6-soft";
    v6Tag.className = "v6-tag " + v6Cls;
    v6Tag.textContent = v6Risk;
    if (item.earnings_release_next_date) {
      v6Tag.title = "Next: " + item.earnings_release_next_date;
    }
    tdV6.appendChild(v6Tag);
    tr.appendChild(tdV6);

    // 10. 52W High Pct
    var tdPct52w = document.createElement("td");
    tdPct52w.style.fontFamily = "var(--font-mono, monospace)";
    tdPct52w.textContent = item.pct_from_52w_high !== undefined ? item.pct_from_52w_high.toFixed(2) + "%" : "--";
    tr.appendChild(tdPct52w);

    // 11. 50D Volume Ratio
    var tdVolRatio = document.createElement("td");
    tdVolRatio.style.fontFamily = "var(--font-mono, monospace)";
    tdVolRatio.textContent = item.volume_ratio_50 !== undefined ? item.volume_ratio_50.toFixed(2) + "x" : "--";
    tr.appendChild(tdVolRatio);

    // 12. Suggested Shares
    var tdShares = document.createElement("td");
    tdShares.className = "shares-col cell-suggested-shares";
    tdShares.setAttribute("data-atr", String(item.atr || 0));
    tdShares.setAttribute("data-close", String(item.close || 0));
    var initialShares = computeAtrPositionSizing({
      close: item.close,
      atr: item.atr,
      stopMult: 2.0,
      riskDollar: getRiskDollar(),
      timeframe: "1D"
    }).shares;
    tdShares.textContent = initialShares + " 股";
    tr.appendChild(tdShares);

    // 13. Actions
    var tdAction = document.createElement("td");
    var addBtn = document.createElement("button");
    addBtn.className = btnClass;
    addBtn.setAttribute("data-id", symId);
    addBtn.textContent = btnText;
    tdAction.appendChild(addBtn);
    tr.appendChild(tdAction);

    tr.onclick = function() {
      document.querySelectorAll(".screener-table tr.screener-row").forEach(function(r) {
        r.classList.remove("active-row");
      });
      tr.classList.add("active-row");
      switchSymbol(symId);
    };

    if (addBtn && !isAlreadyInWl) {
      addBtn.onclick = function(e) {
        e.stopPropagation();
        var origText = addBtn.textContent;
        addBtn.disabled = true;
        addBtn.textContent = "加入中...";
        var symToSub = {
          id: symId,
          pair: item.symbol,
          name: compName,
          exchange: symId.indexOf(":") !== -1 ? symId.split(":")[0] : "NASDAQ",
          type: "stock"
        };
        selectAndSubscribeSymbol(symToSub, function(success, data, err) {
          addBtn.disabled = false;
          if (success) {
            watchlistGroups[symId] = "leaders";
            safeSetStorage("tv_watchlist_groups", JSON.stringify(watchlistGroups));
            addBtn.textContent = "✓ 已加入";
            addBtn.classList.add("added");
            if (typeof renderWatchlist === "function") renderWatchlist();
          } else {
            addBtn.textContent = "加入失敗";
            addBtn.classList.remove("added");
            setTimeout(function() {
              addBtn.textContent = origText;
            }, 2000);
          }
        });
      };
    }

    tableBody.appendChild(tr);
  });
}

function onRiskDollarChanged() {
  var riskInput = document.getElementById("scRiskDollar");
  if (riskInput) {
    var parsedRisk = parseRiskDollar(riskInput.value);
    if (parsedRisk !== null) {
      setRiskDollar(parsedRisk);
    }
  }
  var risk = getRiskDollar();

  var th = document.getElementById("thSuggestedShares");
  if (th) {
    th.textContent = '建議股數 ($' + Math.round(risk) + ') ';
    var sortSpan = document.createElement("span");
    sortSpan.className = "sort-icon";
    sortSpan.textContent = "↕";
    th.appendChild(sortSpan);
  }

  if (Array.isArray(lastScreenerResults)) {
    for (var i = 0; i < lastScreenerResults.length; i++) {
      var it = lastScreenerResults[i];
      it.suggested_shares = computeAtrPositionSizing({
        close: it.close,
        atr: it.atr,
        stopMult: 2.0,
        riskDollar: risk,
        timeframe: "1D"
      }).shares;
    }
  }

  var cells = document.querySelectorAll(".cell-suggested-shares");
  cells.forEach(function(td) {
    var closeVal = parseFloat(td.getAttribute("data-close"));
    var atrVal = parseFloat(td.getAttribute("data-atr"));
    var shares = computeAtrPositionSizing({
      close: closeVal,
      atr: atrVal,
      stopMult: 2.0,
      riskDollar: risk,
      timeframe: "1D"
    }).shares;
    td.textContent = shares + " 股";
  });

  renderPositionSizingBadge();
  if (typeof scheduleDrawingRender === "function") scheduleDrawingRender();
}

function sortScreenerTable(col) {
  if (currentScreenerSort.col === col) {
    currentScreenerSort.asc = !currentScreenerSort.asc;
  } else {
    currentScreenerSort.col = col;
    currentScreenerSort.asc = false;
  }
  var asc = currentScreenerSort.asc;
  lastScreenerResults.sort(function(a, b) {
    var va = a[col];
    var vb = b[col];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === "string") {
      return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    return asc ? (va - vb) : (vb - va);
  });
  renderScreenerTable(lastScreenerResults);
}

function fetchScreenerAiSummary() {
  var aiBox = document.getElementById("screenerAiBox");
  var aiContent = document.getElementById("screenerAiContent");
  var aiBtn = document.getElementById("screenerAiBtn");

  if (!lastScreenerResults || lastScreenerResults.length === 0) {
    if (aiBox) aiBox.style.display = "block";
    if (aiContent) aiContent.textContent = "當前暫無篩選入榜標的，請先點擊「🚀 開始篩選」。";
    return;
  }

  if (aiBox) aiBox.style.display = "block";
  if (aiContent) aiContent.textContent = "正在連線 Gemini Pro 進行大盤趨勢與入榜動能龍頭之深度風控綜述...";
  if (aiBtn) aiBtn.disabled = true;

  var riskInput = document.getElementById("scRiskDollar");
  var currentRisk = getRiskDollar();

  fetch("/api/screener/ai-summary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      items: lastScreenerResults,
      benchmark_status: lastBenchmarkStatus,
      risk_dollar: currentRisk
    })
  })
  .then(function(res) {
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  })
  .then(function(data) {
    if (aiContent) aiContent.textContent = data.summary || "暫無分析報告。";
  })
  .catch(function(err) {
    if (aiContent) aiContent.textContent = "AI 分析產出異常 (" + err.message + ")，請稍後重試。";
  })
  .finally(function() {
    if (aiBtn) aiBtn.disabled = false;
  });
}

function getFormattedTvSymbol(item) {
  if (!item) return "";
  if (item.has_prefix === false) {
    return item.symbol || (item.id ? item.id.split(":").pop() : "");
  }
  return item.export_symbol || item.id || item.symbol || "";
}

function showScreenerFeedback(msg) {
  var statsBadge = document.getElementById("screenerStatsBadge");
  if (statsBadge) {
    var prevText = statsBadge.textContent;
    statsBadge.textContent = msg;
    setTimeout(function() {
      if (statsBadge && statsBadge.textContent === msg) {
        statsBadge.textContent = prevText;
      }
    }, 4000);
  }
}

function exportScreenerTradingView() {
  if (!Array.isArray(lastScreenerResults) || lastScreenerResults.length === 0) {
    showScreenerFeedback("⚠️ 當前暫無入榜標的，請先執行篩選");
    return;
  }

  var seenSymbols = {};
  var powerItems = [];
  var vcpItems = [];
  var otherItems = [];

  lastScreenerResults.forEach(function(item) {
    var sym = getFormattedTvSymbol(item);
    if (!sym || seenSymbols[sym]) return;
    seenSymbols[sym] = true;

    if (item.power_trend) {
      powerItems.push(sym);
    } else if (item.is_squeeze) {
      vcpItems.push(sym);
    } else {
      otherItems.push(sym);
    }
  });

  var tokens = [];
  if (powerItems.length > 0) {
    tokens.push("###POWER TREND LEADERS");
    tokens.push.apply(tokens, powerItems);
  }
  if (vcpItems.length > 0) {
    tokens.push("###STAGE 2 VCP");
    tokens.push.apply(tokens, vcpItems);
  }
  if (otherItems.length > 0) {
    tokens.push("###STAGE 2 MOMENTUM");
    tokens.push.apply(tokens, otherItems);
  }
  if (tokens.length === 0) {
    tokens = lastScreenerResults.map(getFormattedTvSymbol).filter(Boolean);
  }

  var content = tokens.join(",");
  var blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "tradingview_watchlist.txt";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  showScreenerFeedback("✅ 已下載 TradingView 自選股清單 (.txt)");
}

function copyScreenerWatchlist() {
  if (!Array.isArray(lastScreenerResults) || lastScreenerResults.length === 0) {
    showScreenerFeedback("⚠️ 當前暫無入榜標的，請先執行篩選");
    return;
  }

  var seenSymbols = {};
  var symList = [];
  lastScreenerResults.forEach(function(item) {
    var sym = getFormattedTvSymbol(item);
    if (sym && !seenSymbols[sym]) {
      seenSymbols[sym] = true;
      symList.push(sym);
    }
  });

  if (symList.length === 0) {
    showScreenerFeedback("⚠️ 當前暫無有效標的代碼");
    return;
  }

  var textToCopy = symList.join(", ");

  if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(textToCopy)
      .then(function() {
        showScreenerFeedback("📋 已複製 " + symList.length + " 檔代碼至剪貼簿！可直接貼上 (Ctrl+V)");
      })
      .catch(function() {
        fallbackCopyText(textToCopy, symList.length);
      });
  } else {
    fallbackCopyText(textToCopy, symList.length);
  }
}

function fallbackCopyText(text, count) {
  try {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_ex) {
      ok = false;
    }
    document.body.removeChild(ta);
    if (ok) {
      showScreenerFeedback("📋 已複製 " + count + " 檔代碼至剪貼簿！可直接貼上 (Ctrl+V)");
    } else {
      showScreenerFeedback("⚠️ 複製失敗，請手動複製代碼");
    }
  } catch (e) {
    showScreenerFeedback("⚠️ 複製失敗: " + e.message);
  }
}

function escapeCsvField(val) {
  if (val === null || val === undefined) return '""';
  if (typeof val === "number") {
    return isFinite(val) ? String(val) : '""';
  }
  var str = String(val);
  if (/^[=+\-@\t\r]/.test(str)) {
    if (!/^[-+]?\d+(\.\d+)?%?$/.test(str)) {
      str = "'" + str;
    }
  }
  return '"' + str.replace(/"/g, '""') + '"';
}

function exportScreenerCsv() {
  if (!Array.isArray(lastScreenerResults) || lastScreenerResults.length === 0) {
    showScreenerFeedback("⚠️ 當前暫無入榜標的，請先執行篩選");
    return;
  }

  var headers = [
    "Ticker",
    "Name",
    "Sector",
    "Close",
    "24H%",
    "Trend Regime",
    "Weekly Stage2",
    "Mansfield RS",
    "RS Rating",
    "ATR%",
    "ATR Percentile",
    "Squeeze",
    "2xATR Stop Distance",
    "Suggested Stop Price (Close - 2xATR)",
    "Suggested Shares",
    "Position Value ($)",
    "Vol Ratio 50D",
    "YoY Rev Growth"
  ];

  var rows = [];
  rows.push(headers.map(escapeCsvField).join(","));

  lastScreenerResults.forEach(function(item) {
    var c = (item.close !== undefined && item.close !== null && !isNaN(Number(item.close))) ? Number(item.close) : null;
    var stopDist = (item.stop_distance !== undefined && item.stop_distance !== null && !isNaN(Number(item.stop_distance)))
      ? Number(item.stop_distance)
      : ((item.atr !== undefined && item.atr !== null && !isNaN(Number(item.atr)) && Number(item.atr) > 0) ? Number(item.atr) * 2.0 : null);
    var stopPrice = (c !== null && stopDist !== null) ? Math.max(0.0, c - stopDist) : null;
    var shares = (item.suggested_shares !== undefined && item.suggested_shares !== null && !isNaN(Number(item.suggested_shares)))
      ? Number(item.suggested_shares)
      : null;
    var posVal = (shares !== null && c !== null) ? (shares * c) : null;

    var row = [
      escapeCsvField(item.symbol || null),
      escapeCsvField(item.name || null),
      escapeCsvField(item.sector || null),
      escapeCsvField(c !== null ? Number(c.toFixed(2)) : null),
      escapeCsvField((item.change_pct !== undefined && item.change_pct !== null && !isNaN(Number(item.change_pct))) ? item.change_pct.toFixed(2) + "%" : null),
      escapeCsvField(item.trend_regime || null),
      escapeCsvField(item.weekly_stage2 !== undefined && item.weekly_stage2 !== null ? (item.weekly_stage2 ? "TRUE" : "FALSE") : null),
      escapeCsvField((item.mansfield_rs !== undefined && item.mansfield_rs !== null && !isNaN(Number(item.mansfield_rs))) ? Number(item.mansfield_rs.toFixed(2)) : null),
      escapeCsvField((item.rs_rating !== undefined && item.rs_rating !== null && !isNaN(Number(item.rs_rating))) ? Number(item.rs_rating) : null),
      escapeCsvField((item.atr_pct !== undefined && item.atr_pct !== null && !isNaN(Number(item.atr_pct))) ? item.atr_pct.toFixed(2) + "%" : null),
      escapeCsvField((item.atr_percentile !== undefined && item.atr_percentile !== null && !isNaN(Number(item.atr_percentile))) ? item.atr_percentile.toFixed(1) + "%" : null),
      escapeCsvField(item.is_squeeze !== undefined && item.is_squeeze !== null ? (item.is_squeeze ? "YES" : "NO") : null),
      escapeCsvField(stopDist !== null ? Number(stopDist.toFixed(2)) : null),
      escapeCsvField(stopPrice !== null ? Number(stopPrice.toFixed(2)) : null),
      escapeCsvField(shares),
      escapeCsvField(posVal !== null ? Number(posVal.toFixed(2)) : null),
      escapeCsvField((item.volume_ratio_50 !== undefined && item.volume_ratio_50 !== null && !isNaN(Number(item.volume_ratio_50))) ? item.volume_ratio_50.toFixed(2) + "x" : null),
      escapeCsvField(item.revenue_yoy || null)
    ];
    rows.push(row.join(","));
  });

  var csvContent = "\uFEFF" + rows.join("\r\n");
  var blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "institutional_screener_report.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  showScreenerFeedback("✅ 已匯出 18 欄量化 CSV 報表");
}

// ==========================================================================
// Stock Theme Heatmap Controller & Renderer
// ==========================================================================
var heatmapReqSeq = 0;

function openHeatmapModal(forceRefresh) {
  var hmModal = document.getElementById("heatmapModal");
  if (!hmModal) return;
  hmModal.style.display = "flex";

  var nowMs = Date.now();
  var isCacheFresh = heatmapDataCache &&
    heatmapDataCache.universe === activeHeatmapUniverse &&
    (nowMs - (heatmapDataCache._cachedAt || 0) < 60000);

  if (!forceRefresh && isCacheFresh) {
    renderHeatmap();
    return;
  }
  fetchHeatmapData(Boolean(forceRefresh));
}

function closeHeatmapModalHandler() {
  var hmModal = document.getElementById("heatmapModal");
  if (hmModal) {
    hmModal.style.display = "none";
  }
}

function toggleHeatmapModal() {
  var hmModal = document.getElementById("heatmapModal");
  if (!hmModal) return;
  if (hmModal.style.display === "none" || !hmModal.style.display) {
    openHeatmapModal(false);
  } else {
    closeHeatmapModalHandler();
  }
}

function getHeatmapColor(item, metric) {
  var isLight = currentTheme === "light";
  if (item && item.no_data) {
    return isLight ? "#94a3b8" : "#475569";
  }
  if (metric === "rs") {
    var rs = typeof item.rs_rating === "number" ? item.rs_rating : 50;
    if (rs >= 90) return isLight ? "#1d4ed8" : "#2563eb";
    if (rs >= 80) return isLight ? "#0d9488" : "#059669";
    if (rs >= 65) return isLight ? "#475569" : "#334155";
    if (rs >= 50) return isLight ? "#b45309" : "#d97706";
    return isLight ? "#b91c1c" : "#dc2626";
  } else {
    var chg = typeof item.change_pct === "number" ? item.change_pct : 0;
    if (chg >= 3.0) return isLight ? "#15803d" : "#007a3d";
    if (chg >= 1.5) return isLight ? "#16a34a" : "#0f9d58";
    if (chg > 0.0) return isLight ? "#22c55e" : "#1e7e34";
    if (chg === 0.0) return isLight ? "#64748b" : "#334155";
    if (chg > -1.5) return isLight ? "#f87171" : "#b91c1c";
    if (chg > -3.0) return isLight ? "#ef4444" : "#dc2626";
    return isLight ? "#b91c1c" : "#7f1d1d";
  }
}

function fetchHeatmapData(refresh) {
  var currentSeq = ++heatmapReqSeq;
  isHeatmapLoading = true;

  var loadingEl = document.getElementById("heatmapLoading");
  var gridEl = document.getElementById("heatmapGrid");
  var emptyEl = document.getElementById("heatmapEmptyState");

  if (loadingEl) loadingEl.style.display = "flex";
  if (gridEl) gridEl.style.display = "none";
  if (emptyEl) emptyEl.style.display = "none";

  var url = "/api/heatmap?universe=" + encodeURIComponent(activeHeatmapUniverse) + (refresh ? "&refresh=true" : "");
  fetch(url)
    .then(function(res) {
      if (currentSeq !== heatmapReqSeq) return null;
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      if (currentSeq !== heatmapReqSeq || !data) return;
      isHeatmapLoading = false;
      data._cachedAt = Date.now();
      heatmapDataCache = data;
      renderHeatmap();
    })
    .catch(function(err) {
      if (currentSeq !== heatmapReqSeq) return;
      isHeatmapLoading = false;
      if (loadingEl) loadingEl.style.display = "none";
      if (emptyEl) {
        emptyEl.style.display = "flex";
        var titleEl = document.getElementById("heatmapEmptyTitle");
        var dict = i18n[currentLang] || i18n.en;
        if (titleEl) titleEl.textContent = (dict.heatmapEmpty || "No theme data") + " (" + (err.message || "Network Error") + ")";
      }
    });
}

function renderHeatmap() {
  var loadingEl = document.getElementById("heatmapLoading");
  var gridEl = document.getElementById("heatmapGrid");
  var emptyEl = document.getElementById("heatmapEmptyState");

  if (loadingEl) loadingEl.style.display = "none";

  if (!heatmapDataCache || !Array.isArray(heatmapDataCache.themes) || heatmapDataCache.themes.length === 0) {
    if (gridEl) gridEl.style.display = "none";
    if (emptyEl) emptyEl.style.display = "flex";
    return;
  }

  if (emptyEl) emptyEl.style.display = "none";
  if (gridEl) {
    gridEl.style.display = "grid";
    gridEl.innerHTML = "";
  } else {
    return;
  }

  var data = heatmapDataCache;
  var benchBadge = document.getElementById("heatmapBenchBadge");
  if (benchBadge && data.benchmark) {
    var bChg = typeof data.benchmark.change_pct === "number" ? data.benchmark.change_pct : 0;
    var timeStr = "";
    if (data.updated_at) {
      try {
        var dt = new Date(data.updated_at);
        var nyParts = new Intl.DateTimeFormat("en-US", {
          timeZone: "America/New_York",
          year: "numeric", month: "2-digit", day: "2-digit",
          hour: "2-digit", minute: "2-digit", hour12: false
        }).formatToParts(dt);
        var p = {};
        for (var pi = 0; pi < nyParts.length; pi++) {
          p[nyParts[pi].type] = nyParts[pi].value;
        }
        if (p.year && p.month && p.day && p.hour && p.minute) {
          timeStr = " (" + p.year + "-" + p.month + "-" + p.day + " " + p.hour + ":" + p.minute + " ET)";
        }
      } catch (e) {
        timeStr = " (" + String(data.updated_at).slice(0, 16) + ")";
      }
    }
    benchBadge.textContent = "SPY: " + (bChg >= 0 ? "+" : "") + bChg.toFixed(2) + "%" + timeStr;
    benchBadge.style.color = bChg >= 0 ? "#10b981" : "#ef4444";
  }

  var themeGroups = data.themes.slice();
  if (activeHeatmapSort === "rs") {
    themeGroups.sort(function(a, b) {
      var rsa = typeof a.avg_rs_rating === "number" ? a.avg_rs_rating : 0;
      var rsb = typeof b.avg_rs_rating === "number" ? b.avg_rs_rating : 0;
      return rsb - rsa;
    });
  } else {
    themeGroups.sort(function(a, b) {
      var cha = typeof a.avg_change_pct === "number" ? a.avg_change_pct : 0;
      var chb = typeof b.avg_change_pct === "number" ? b.avg_change_pct : 0;
      return chb - cha;
    });
  }

  for (var ti = 0; ti < themeGroups.length; ti++) {
    var theme = themeGroups[ti];
    if (!theme || !Array.isArray(theme.items) || theme.items.length === 0) continue;

    var card = document.createElement("div");
    card.className = "heatmap-card";

    var header = document.createElement("div");
    header.className = "heatmap-card-header";

    var titleEl = document.createElement("div");
    titleEl.className = "theme-card-title";
    var themeName = currentLang === "zh" ? (theme.name_zh || theme.name_en || theme.id) : (theme.name_en || theme.name_zh || theme.id);
    titleEl.textContent = themeName + " (" + theme.items.length + ")";

    var metricsEl = document.createElement("div");
    metricsEl.className = "theme-card-metrics";

    var avgEl = document.createElement("span");
    avgEl.className = "theme-metric-avg";
    if (activeHeatmapMetric === "rs") {
      var rVal = typeof theme.avg_rs_rating === "number" ? theme.avg_rs_rating : 50;
      avgEl.textContent = "RS " + rVal.toFixed(1);
      var isLight = currentTheme === "light";
      avgEl.style.color = rVal >= 80 ? (isLight ? "#0284c7" : "#38bdf8") : (rVal >= 60 ? "var(--text-main)" : (isLight ? "#d97706" : "#f59e0b"));
    } else {
      var cVal = typeof theme.avg_change_pct === "number" ? theme.avg_change_pct : 0;
      avgEl.textContent = (cVal >= 0 ? "+" : "") + cVal.toFixed(2) + "%";
      avgEl.style.color = cVal >= 0 ? "#10b981" : "#ef4444";
    }
    metricsEl.appendChild(avgEl);

    if (theme.advancing !== undefined && theme.total !== undefined) {
      var ratioEl = document.createElement("span");
      ratioEl.className = "theme-metric-ratio";
      ratioEl.textContent = theme.advancing + "/" + theme.total + " ↗";
      metricsEl.appendChild(ratioEl);
    }

    header.appendChild(titleEl);
    header.appendChild(metricsEl);
    card.appendChild(header);

    var body = document.createElement("div");
    body.className = "theme-card-body";

    var items = theme.items.slice();
    if (activeHeatmapSort === "rs") {
      items.sort(function(a, b) {
        var ra = typeof a.rs_rating === "number" ? a.rs_rating : 0;
        var rb = typeof b.rs_rating === "number" ? b.rs_rating : 0;
        return rb - ra;
      });
    } else {
      items.sort(function(a, b) {
        var ca = typeof a.change_pct === "number" ? a.change_pct : 0;
        var cb = typeof b.change_pct === "number" ? b.change_pct : 0;
        return cb - ca;
      });
    }

    for (var ii = 0; ii < items.length; ii++) {
      var item = items[ii];
      var box = document.createElement("div");
      box.className = "heatmap-item-box";

      var volRatio = (typeof item.volume_ratio_50 === "number" && isFinite(item.volume_ratio_50)) ? item.volume_ratio_50 : 1.0;
      var flexGrow = Math.max(0.85, Math.min(2.2, volRatio));
      box.style.flexGrow = flexGrow.toFixed(2);
      box.style.backgroundColor = getHeatmapColor(item, activeHeatmapMetric);

      var symDiv = document.createElement("div");
      symDiv.className = "hb-symbol";
      symDiv.textContent = item.symbol || item.id;
      if (item.stage2) {
        var star = document.createElement("span");
        star.style.fontSize = "9px";
        star.style.color = "#facc15";
        star.style.marginLeft = "3px";
        star.title = "Stage 2 Momentum";
        star.textContent = "★";
        symDiv.appendChild(star);
      }
      box.appendChild(symDiv);

      var bottomDiv = document.createElement("div");
      bottomDiv.className = "hb-bottom";

      var valSpan = document.createElement("span");
      valSpan.className = "hb-value";
      if (item.no_data) {
        valSpan.textContent = currentLang === "zh" ? "無資料" : "No Data";
      } else if (activeHeatmapMetric === "rs") {
        valSpan.textContent = "RS " + (item.rs_rating !== undefined ? item.rs_rating : "--");
      } else {
        var cp = typeof item.change_pct === "number" ? item.change_pct : 0;
        valSpan.textContent = (cp >= 0 ? "+" : "") + cp.toFixed(2) + "%";
      }
      bottomDiv.appendChild(valSpan);

      var volSpan = document.createElement("span");
      volSpan.className = "hb-vol";
      volSpan.textContent = volRatio.toFixed(1) + "x";
      bottomDiv.appendChild(volSpan);

      box.appendChild(bottomDiv);

      var tooltipLines = [
        item.symbol + (item.name ? " (" + item.name + ")" : ""),
        "Price: $" + (typeof item.close === "number" ? item.close.toFixed(2) : "--"),
        "1D Change: " + (item.change_pct >= 0 ? "+" : "") + (item.change_pct !== undefined ? item.change_pct.toFixed(2) + "%" : "--"),
        "RS Rating: " + (item.rs_rating !== undefined ? item.rs_rating : "--"),
        "50D Vol Ratio: " + volRatio.toFixed(2) + "x",
        "Stage 2: " + (item.stage2 ? (currentLang === "zh" ? "是 (Stage 2 成長動能)" : "Yes (Stage 2 Momentum)") : (currentLang === "zh" ? "否" : "No"))
      ];
      box.setAttribute("title", tooltipLines.join("\n"));

      (function(targetItem) {
        box.onclick = function() {
          closeHeatmapModalHandler();
          if (typeof switchSymbol === "function") {
            switchSymbol(targetItem.id || targetItem.symbol);
          }
        };
      })(item);

      body.appendChild(box);
    }

    card.appendChild(body);
    gridEl.appendChild(card);
  }
}

function syncIndicatorCheckboxes() {
  var checkMap = {
    chkEma10: indicatorConfig.ema10,
    chkEma21: indicatorConfig.ema21,
    chkEma30: indicatorConfig.ema30,
    chkSma50: indicatorConfig.sma50,
    chkSma200: indicatorConfig.sma200,
    chkVolSma: indicatorConfig.volSma,
    chkBollinger: indicatorConfig.bollinger,
    chkRsLine: indicatorConfig.rsLine,
    chkVwap: indicatorConfig.vwap,
    chkAvwap: indicatorConfig.avwap,
    chkAtr: indicatorConfig.atr,
    chkShowLegend: indicatorConfig.showLegend !== false,
    chkCollapseLegend: Boolean(indicatorConfig.collapseLegend),
    chkShowDrawingToolbar: Boolean(indicatorConfig.showDrawingToolbar),
    chkShowAnalysisTools: Boolean(indicatorConfig.showAnalysisTools),
    chkTvCompareRs: Boolean(indicatorConfig.tvCompareRs)
  };
  for (var cid in checkMap) {
    var cel = document.getElementById(cid);
    if (cel) cel.checked = Boolean(checkMap[cid]);
  }
  var bm = document.getElementById("btnModeMansfield");
  var bd = document.getElementById("btnModeDeepvue");
  if (bm && bd) {
    if (indicatorConfig.rsMode === "deepvue") {
      bd.classList.add("active");
      bm.classList.remove("active");
    } else {
      bm.classList.add("active");
      bd.classList.remove("active");
    }
  }
}

function switchAvwapTab(tabName) {
  var tabs = ["inputs", "style", "visibility"];
  for (var i = 0; i < tabs.length; i++) {
    var t = tabs[i];
    var cap = t.charAt(0).toUpperCase() + t.slice(1);
    var btn = document.getElementById("tabBtn" + cap);
    var content = document.getElementById("tabContent" + cap);
    if (btn) btn.classList.toggle("active", t === tabName);
    if (content) {
      content.classList.toggle("active", t === tabName);
      content.style.display = (t === tabName) ? "block" : "none";
    }
  }
}

function openAvwapSettingsModal() {
  var modal = document.getElementById("avwapSettingsModal");
  if (!modal) return;

  switchAvwapTab("inputs");

  var selAnchor = document.getElementById("avwapInputAnchor");
  var numLookback = document.getElementById("avwapInputLookback");
  var numGapPct = document.getElementById("avwapInputGapPct");
  var numGapVol = document.getElementById("avwapInputGapVol");
  var numVolMaLen = document.getElementById("avwapInputVolMaLen");
  var inputDate = document.getElementById("avwapInputDate");
  var inputTime = document.getElementById("avwapInputTime");
  var selSource = document.getElementById("avwapInputSource");
  var chkShowBands = document.getElementById("avwapInputShowBands");
  var numBandMult = document.getElementById("avwapInputBandMult");
  var chkMarkAnchor = document.getElementById("avwapInputMarkAnchor");
  var chkShowTable = document.getElementById("avwapInputShowTable");

  if (selAnchor) selAnchor.value = avwapConfig.anchorMode || "Swing Low";
  if (numLookback) numLookback.value = avwapConfig.anchorLookback || 252;
  if (numGapPct) numGapPct.value = avwapConfig.gapPctMin !== undefined ? avwapConfig.gapPctMin : 5;
  if (numGapVol) numGapVol.value = avwapConfig.gapVolMin !== undefined ? avwapConfig.gapVolMin : 2;
  if (numVolMaLen) numVolMaLen.value = avwapConfig.volMaLen || 50;
  if (inputDate) inputDate.value = avwapConfig.manualAnchorDate || "2026-01-02";
  if (inputTime) inputTime.value = avwapConfig.manualAnchorTime || "00:00";
  if (selSource) selSource.value = avwapConfig.source || "hlc3";
  if (chkShowBands) chkShowBands.checked = Boolean(avwapConfig.showBands);
  if (numBandMult) numBandMult.value = avwapConfig.bandMult !== undefined ? avwapConfig.bandMult : 1;
  if (chkMarkAnchor) chkMarkAnchor.checked = Boolean(avwapConfig.showAnchorLbl);
  if (chkShowTable) chkShowTable.checked = Boolean(avwapConfig.showTable);

  var colAbove = document.getElementById("avwapColorAbove");
  var colBelow = document.getElementById("avwapColorBelow");
  var colBand = document.getElementById("avwapColorBand");
  var chkBandFill = document.getElementById("avwapShowBandFill");
  if (colAbove) colAbove.value = avwapConfig.colorAbove || "#26a69a";
  if (colBelow) colBelow.value = avwapConfig.colorBelow || "#ef5350";
  if (colBand) colBand.value = avwapConfig.bandColor || "#787b86";
  if (chkBandFill) chkBandFill.checked = Boolean(avwapConfig.showBandFill);

  var selDisplay = document.getElementById("avwapDisplayMode");
  if (selDisplay) selDisplay.value = avwapConfig.displayMode || "overlay";

  modal.style.display = "flex";
}

function closeAvwapSettingsModal() {
  var modal = document.getElementById("avwapSettingsModal");
  if (modal) modal.style.display = "none";
}

function resetAvwapSettingsToDefaults() {
  for (var k in defaultAvwapConfig) {
    if (defaultAvwapConfig.hasOwnProperty(k)) {
      avwapConfig[k] = defaultAvwapConfig[k];
    }
  }
  openAvwapSettingsModal();
}

function applyAvwapSettingsFromModal() {
  var selAnchor = document.getElementById("avwapInputAnchor");
  var numLookback = document.getElementById("avwapInputLookback");
  var numGapPct = document.getElementById("avwapInputGapPct");
  var numGapVol = document.getElementById("avwapInputGapVol");
  var numVolMaLen = document.getElementById("avwapInputVolMaLen");
  var inputDate = document.getElementById("avwapInputDate");
  var inputTime = document.getElementById("avwapInputTime");
  var selSource = document.getElementById("avwapInputSource");
  var chkShowBands = document.getElementById("avwapInputShowBands");
  var numBandMult = document.getElementById("avwapInputBandMult");
  var chkMarkAnchor = document.getElementById("avwapInputMarkAnchor");
  var chkShowTable = document.getElementById("avwapInputShowTable");

  var colAbove = document.getElementById("avwapColorAbove");
  var colBelow = document.getElementById("avwapColorBelow");
  var colBand = document.getElementById("avwapColorBand");
  var chkBandFill = document.getElementById("avwapShowBandFill");
  var selDisplay = document.getElementById("avwapDisplayMode");

  if (selAnchor) avwapConfig.anchorMode = selAnchor.value;
  if (numLookback) avwapConfig.anchorLookback = Math.max(5, Math.min(1000, parseInt(numLookback.value, 10) || 252));
  if (numGapPct) avwapConfig.gapPctMin = parseFloat(numGapPct.value) || 5.0;
  if (numGapVol) avwapConfig.gapVolMin = parseFloat(numGapVol.value) || 2.0;
  if (numVolMaLen) avwapConfig.volMaLen = Math.max(1, parseInt(numVolMaLen.value, 10) || 50);
  if (inputDate && inputDate.value) avwapConfig.manualAnchorDate = inputDate.value;
  if (inputTime && inputTime.value) avwapConfig.manualAnchorTime = inputTime.value;
  if (selSource) avwapConfig.source = selSource.value;
  if (chkShowBands) avwapConfig.showBands = chkShowBands.checked;
  if (numBandMult) avwapConfig.bandMult = Math.max(0.1, parseFloat(numBandMult.value) || 1.0);
  if (chkMarkAnchor) avwapConfig.showAnchorLbl = chkMarkAnchor.checked;
  if (chkShowTable) avwapConfig.showTable = chkShowTable.checked;

  if (colAbove) avwapConfig.colorAbove = colAbove.value;
  if (colBelow) avwapConfig.colorBelow = colBelow.value;
  if (colBand) avwapConfig.bandColor = colBand.value;
  if (chkBandFill) avwapConfig.showBandFill = chkBandFill.checked;
  if (selDisplay) avwapConfig.displayMode = selDisplay.value;

  saveAvwapConfig();
  closeAvwapSettingsModal();

  indicatorConfig.avwap = true;
  saveIndicatorConfig();
  applyIndicatorVisibilities();
  if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
}

function parseAndApplyPineScript() {
  var area = document.getElementById("avwapPineInputArea");
  if (!area || !area.value) return;
  if (!window.Indicators || !window.Indicators.parsePineInputs) return;

  var parsed = window.Indicators.parsePineInputs(area.value);

  for (var k in parsed) {
    if (!parsed.hasOwnProperty(k)) continue;
    var item = parsed[k];
    var nameLower = (item.varName || item.title || "").toLowerCase();
    var def = item.defval;

    if (nameLower.indexOf("anchor") !== -1 && (nameLower.indexOf("mode") !== -1 || item.type === "string")) {
      var selA = document.getElementById("avwapInputAnchor");
      if (selA && def) selA.value = String(def);
    } else if (nameLower.indexOf("lookback") !== -1) {
      var numLb = document.getElementById("avwapInputLookback");
      if (numLb && typeof def === "number") numLb.value = def;
    } else if (nameLower.indexOf("gap") !== -1 && nameLower.indexOf("%") !== -1) {
      var numGp = document.getElementById("avwapInputGapPct");
      if (numGp && typeof def === "number") numGp.value = def;
    } else if (nameLower.indexOf("gap") !== -1 && (nameLower.indexOf("ratio") !== -1 || nameLower.indexOf("vol") !== -1)) {
      var numGv = document.getElementById("avwapInputGapVol");
      if (numGv && typeof def === "number") numGv.value = def;
    } else if (nameLower.indexOf("volmalen") !== -1 || (nameLower.indexOf("volume") !== -1 && nameLower.indexOf("ma") !== -1)) {
      var numVm = document.getElementById("avwapInputVolMaLen");
      if (numVm && typeof def === "number") numVm.value = def;
    } else if (nameLower.indexOf("manual") !== -1 || item.type === "time") {
      if (def && typeof def === "string") {
        var dateParts = def.split(" ");
        if (dateParts.length >= 1) {
          var inDate = document.getElementById("avwapInputDate");
          if (inDate) inDate.value = dateParts[0];
        }
        if (dateParts.length >= 2) {
          var inTime = document.getElementById("avwapInputTime");
          if (inTime) inTime.value = dateParts[1];
        }
      }
    } else if (nameLower.indexOf("src") !== -1 || nameLower.indexOf("source") !== -1) {
      var selS = document.getElementById("avwapInputSource");
      if (selS && def) selS.value = String(def);
    } else if (nameLower.indexOf("band") !== -1 && nameLower.indexOf("mult") === -1 && item.type === "bool") {
      var chkSb = document.getElementById("avwapInputShowBands");
      if (chkSb) chkSb.checked = Boolean(def);
    } else if (nameLower.indexOf("mult") !== -1) {
      var numBm = document.getElementById("avwapInputBandMult");
      if (numBm && typeof def === "number") numBm.value = def;
    } else if (nameLower.indexOf("lbl") !== -1 || nameLower.indexOf("mark") !== -1) {
      var chkMa = document.getElementById("avwapInputMarkAnchor");
      if (chkMa) chkMa.checked = Boolean(def);
    } else if (nameLower.indexOf("table") !== -1 || nameLower.indexOf("info") !== -1) {
      var chkSt = document.getElementById("avwapInputShowTable");
      if (chkSt) chkSt.checked = Boolean(def);
    }
  }
}

function initEventListeners() {
  startDrawingCoordLoop();
  if (openSearchBtn) openSearchBtn.onclick = openSearch;
  if (addSymbolBtn) addSymbolBtn.onclick = openSearch;
  if (closeSearchModal) closeSearchModal.onclick = closeSearch;

  if (fundamentalsBtn) {
    fundamentalsBtn.onclick = function() {
      openFundamentalsModal();
    };
  }
  if (closeFundamentalsModal) {
    closeFundamentalsModal.onclick = function() {
      closeFundamentalsModalHandler();
    };
  }
  if (refreshFundamentalsBtn) {
    refreshFundamentalsBtn.onclick = function() {
      fetchFundamentals(activeSymbol, true);
    };
  }
  if (fundamentalsModal) {
    fundamentalsModal.onclick = function(e) {
      if (e.target === fundamentalsModal) {
        closeFundamentalsModalHandler();
      }
    };
  }

  if (aiLensBtn) {
    aiLensBtn.onclick = function() {
      openAiLensModal();
    };
  }
  if (closeAiLensModal) {
    closeAiLensModal.onclick = function() {
      closeAiLensModalHandler();
    };
  }
  if (closeAiLensFooterBtn) {
    closeAiLensFooterBtn.onclick = function() {
      closeAiLensModalHandler();
    };
  }
  if (refreshAiLensBtn) {
    refreshAiLensBtn.onclick = function() {
      fetchAiLens(activeSymbol, activeTimeframe, true);
    };
  }
  if (aiLensModal) {
    aiLensModal.onclick = function(e) {
      if (e.target === aiLensModal) {
        closeAiLensModalHandler();
      }
    };
  }

  // Stock Theme Heatmap Event Listeners
  if (heatmapBtn) {
    heatmapBtn.onclick = function() {
      toggleHeatmapModal();
    };
  }
  if (closeHeatmapModal) {
    closeHeatmapModal.onclick = function() {
      closeHeatmapModalHandler();
    };
  }
  if (closeHeatmapFooterBtn) {
    closeHeatmapFooterBtn.onclick = function() {
      closeHeatmapModalHandler();
    };
  }
  if (heatmapRefreshBtn) {
    heatmapRefreshBtn.onclick = function() {
      openHeatmapModal(true);
    };
  }
  if (heatmapModal) {
    heatmapModal.onclick = function(e) {
      if (e.target === heatmapModal) {
        closeHeatmapModalHandler();
      }
    };
  }

  var univBtns = document.querySelectorAll ? document.querySelectorAll("#heatmapUniverseGroup .heatmap-pill-btn") : [];
  for (var ubi = 0; ubi < univBtns.length; ubi++) {
    (function(btn) {
      btn.onclick = function() {
        var u = btn.getAttribute("data-universe");
        if (!u || u === activeHeatmapUniverse) return;
        activeHeatmapUniverse = u;
        for (var j = 0; j < univBtns.length; j++) {
          univBtns[j].classList.toggle("active", univBtns[j] === btn);
        }
        var loadingEl = document.getElementById("heatmapLoading");
        var gridEl = document.getElementById("heatmapGrid");
        var emptyEl = document.getElementById("heatmapEmptyState");
        if (gridEl) {
          gridEl.innerHTML = "";
          gridEl.style.display = "none";
        }
        if (emptyEl) emptyEl.style.display = "none";
        if (loadingEl) loadingEl.style.display = "flex";
        openHeatmapModal(false);
      };
    })(univBtns[ubi]);
  }

  var metricBtns = document.querySelectorAll ? document.querySelectorAll("#heatmapMetricGroup .heatmap-pill-btn") : [];
  for (var mbi = 0; mbi < metricBtns.length; mbi++) {
    (function(btn) {
      btn.onclick = function() {
        var m = btn.getAttribute("data-metric");
        if (!m || m === activeHeatmapMetric) return;
        activeHeatmapMetric = m;
        for (var j = 0; j < metricBtns.length; j++) {
          metricBtns[j].classList.toggle("active", metricBtns[j] === btn);
        }
        renderHeatmap();
      };
    })(metricBtns[mbi]);
  }

  var sortBtns = document.querySelectorAll ? document.querySelectorAll("#heatmapSortGroup .heatmap-pill-btn") : [];
  for (var sbi = 0; sbi < sortBtns.length; sbi++) {
    (function(btn) {
      btn.onclick = function() {
        var s = btn.getAttribute("data-sort");
        if (!s || s === activeHeatmapSort) return;
        activeHeatmapSort = s;
        for (var j = 0; j < sortBtns.length; j++) {
          sortBtns[j].classList.toggle("active", sortBtns[j] === btn);
        }
        renderHeatmap();
      };
    })(sortBtns[sbi]);
  }

  // Stock Screener Event Listeners
  var scBtn = document.getElementById("screenerBtn");
  if (scBtn) scBtn.onclick = openScreenerModal;

  var scClose = document.getElementById("closeScreenerModal");
  if (scClose) scClose.onclick = closeScreenerModal;

  var scFooterClose = document.getElementById("closeScreenerFooterBtn");
  if (scFooterClose) scFooterClose.onclick = closeScreenerModal;

  var scDockBtn = document.getElementById("screenerDockBtn");
  if (scDockBtn) scDockBtn.onclick = toggleScreenerDock;

  var runScanBtn = document.getElementById("runScreenerScanBtn");
  if (runScanBtn) runScanBtn.onclick = runStockScreen;

  var scBatchAddBtn = document.getElementById("screenerBatchAddBtn");
  if (scBatchAddBtn) scBatchAddBtn.onclick = batchAddTier12ToWatchlist;

  var scSavePresetBtn = document.getElementById("screenerSavePresetBtn");
  if (scSavePresetBtn) scSavePresetBtn.onclick = saveCurrentAsCustomPreset;

  var scDelPresetBtn = document.getElementById("screenerDeletePresetBtn");
  if (scDelPresetBtn) scDelPresetBtn.onclick = deleteCurrentCustomPreset;

  var scAiBtn = document.getElementById("screenerAiBtn");
  if (scAiBtn) scAiBtn.onclick = fetchScreenerAiSummary;

  var closeAiBox = document.getElementById("closeScreenerAiBox");
  if (closeAiBox) closeAiBox.onclick = function() {
    var b = document.getElementById("screenerAiBox");
    if (b) b.style.display = "none";
  };

  var scExportTvBtn = document.getElementById("screenerExportTvBtn");
  if (scExportTvBtn) scExportTvBtn.onclick = exportScreenerTradingView;

  var scCopyListBtn = document.getElementById("screenerCopyListBtn");
  if (scCopyListBtn) scCopyListBtn.onclick = copyScreenerWatchlist;

  var scExportCsvBtn = document.getElementById("screenerExportCsvBtn");
  if (scExportCsvBtn) scExportCsvBtn.onclick = exportScreenerCsv;

  var scRiskIn = document.getElementById("scRiskDollar");
  if (scRiskIn) {
    var savedRiskDollar = parseRiskDollar(safeGetStorage("tv_risk_dollar", ""));
    if (savedRiskDollar !== null) {
      scRiskIn.value = String(savedRiskDollar);
    }
    scRiskIn.oninput = onRiskDollarChanged;
    onRiskDollarChanged();
  }

  var uSel = document.getElementById("screenerUniverseSelect");
  if (uSel) {
    uSel.onchange = function() {
      var sWrap = document.getElementById("screenerSearchWrap");
      var cIn = document.getElementById("screenerCustomInput");
      var isCustom = (uSel.value === "custom");
      if (sWrap) sWrap.style.display = isCustom ? "flex" : "none";
      else if (cIn) cIn.style.display = isCustom ? "inline-block" : "none";
      if (isCustom && cIn) cIn.focus();
    };
  }

  var screenerAcTimer = null;
  var screenerSearchSeq = 0;
  var screenerCustomIn = document.getElementById("screenerCustomInput");
  var screenerAcDropdown = document.getElementById("screenerAutocompleteDropdown");
  var screenerClearBtn = document.getElementById("screenerSearchClearBtn");

  if (screenerClearBtn && screenerCustomIn) {
    screenerClearBtn.onclick = function() {
      screenerCustomIn.value = "";
      if (screenerAcDropdown) screenerAcDropdown.style.display = "none";
      screenerCustomIn.focus();
    };
  }

  if (screenerCustomIn && screenerAcDropdown) {
    screenerCustomIn.oninput = function() {
      var fullVal = screenerCustomIn.value;
      var tokens = fullVal.split(/[,;\s]+/);
      var currentTerm = (tokens[tokens.length - 1] || "").trim().toUpperCase();

      if (screenerAcTimer) clearTimeout(screenerAcTimer);
      if (!currentTerm || currentTerm.length < 1) {
        screenerAcDropdown.style.display = "none";
        return;
      }

      screenerAcTimer = setTimeout(function() {
        var curSeq = ++screenerSearchSeq;
        fetch("/api/symbols/search?q=" + encodeURIComponent(currentTerm))
          .then(function(res) {
            if (curSeq !== screenerSearchSeq) return null;
            if (!res.ok) throw new Error("HTTP " + res.status);
            return res.json();
          })
          .then(function(data) {
            if (curSeq !== screenerSearchSeq || !data) return;
            var results = data.results || [];
            if (results.length === 0) {
              screenerAcDropdown.style.display = "none";
              return;
            }
            screenerAcDropdown.innerHTML = "";
            results.slice(0, 8).forEach(function(item) {
              var symRow = document.createElement("div");
              symRow.className = "screener-ac-item";
              var ticker = item.pair || (item.id ? item.id.split(":")[1] : "");

              var symSpan = document.createElement("span");
              symSpan.className = "ac-sym";
              symSpan.textContent = ticker;

              var nameSpan = document.createElement("span");
              nameSpan.className = "ac-name";
              nameSpan.textContent = item.name || "";

              var secSpan = document.createElement("span");
              secSpan.className = "ac-sector";
              secSpan.textContent = item.sector || item.exchange || "";

              symRow.appendChild(symSpan);
              symRow.appendChild(nameSpan);
              symRow.appendChild(secSpan);

              symRow.onclick = function(e) {
                e.stopPropagation();
                tokens[tokens.length - 1] = ticker;
                screenerCustomIn.value = tokens.filter(Boolean).join(", ") + ", ";
                screenerAcDropdown.style.display = "none";
                screenerCustomIn.focus();
              };
              screenerAcDropdown.appendChild(symRow);
            });
            screenerAcDropdown.style.display = "block";
          })
          .catch(function(e) {
            if (curSeq !== screenerSearchSeq) return;
            screenerAcDropdown.innerHTML = "";
            var errItem = document.createElement("div");
            errItem.className = "screener-ac-item error";
            errItem.style.color = "#ef5350";
            errItem.textContent = "搜尋失敗";
            screenerAcDropdown.appendChild(errItem);
            screenerAcDropdown.style.display = "block";
          });
      }, 150);
    };

    document.addEventListener("click", function(e) {
      if (screenerAcDropdown && !screenerAcDropdown.contains(e.target) && e.target !== screenerCustomIn) {
        screenerAcDropdown.style.display = "none";
      }
    });
  }

  var pPills = document.querySelectorAll ? document.querySelectorAll(".screener-preset-pill") : [];
  pPills.forEach(function(pill) {
    pill.onclick = function() {
      var pid = pill.getAttribute("data-preset");
      if (pid && pid !== "custom") {
        applyScreenerPreset(pid);
      } else {
        activeMinRevGrowth = null;
        pPills.forEach(function(p) { p.classList.remove("active"); });
        pill.classList.add("active");
      }
    };
  });

  var sortThs = document.querySelectorAll ? document.querySelectorAll(".screener-table th.sortable") : [];
  sortThs.forEach(function(th) {
    th.onclick = function() {
      var col = th.getAttribute("data-sort");
      if (col) sortScreenerTable(col);
    };
  });

  if (searchInput) {
    searchInput.oninput = function(e) {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(function() {
        executeSearch(e.target.value);
      }, 150);
    };

    searchInput.onkeydown = function(e) {
      if (e.key === "Enter" && searchInput.value.trim()) {
        var val = searchInput.value.trim().toUpperCase();
        selectAndSubscribeSymbol({
          id: val.indexOf(":") !== -1 ? val : "NASDAQ:" + val,
          pair: val.split(":").pop(),
          name: val.split(":").pop(),
          exchange: val.indexOf(":") !== -1 ? val.split(":")[0] : "US",
          type: "stock"
        });
      }
    };
  }

  var _tfBuf = "", _tfTimer = null;
  _addQuickPriceLine = function(isAlert) {
    var lw = window.LightweightCharts || (typeof LightweightCharts !== "undefined" ? LightweightCharts : null);
    if (!candleSeries || !lw) return;
    var p = (typeof lastCrosshairPrice === "number" && isFinite(lastCrosshairPrice)) ? lastCrosshairPrice : (latestBarData ? latestBarData.close : null);
    if (p === null) return;
    var line = candleSeries.createPriceLine({
      price: p,
      color: isAlert ? "#f59e0b" : "#3b82f6",
      lineWidth: 1,
      lineStyle: isAlert ? lw.LineStyle.Dotted : lw.LineStyle.Dashed,
      axisLabelVisible: true,
      title: isAlert ? "ALERT (未啟用提醒)" : "LVL"
    });
    tvPriceLineHandles.push(line);
    if (activeSymbol) {
      try {
        var existing = safeGetStorage("tv_price_lines_" + activeSymbol, null);
        var list = existing ? JSON.parse(existing) : [];
        if (!Array.isArray(list)) list = [];
        list.push({ price: p, isAlert: Boolean(isAlert) });
        safeSetStorage("tv_price_lines_" + activeSymbol, JSON.stringify(list));
      } catch (e) {}
    }
  }
  var chartLegendEl = document.getElementById("chartLegend");
  if (chartLegendEl) {
    var legMap = { legEma10: "ema10", legEma21: "ema21", legEma30: "ema30", legSma50: "sma50", legSma200: "sma200", legVwap: "vwap", legAvwap: "avwap", legRs: "rsLine" };
    chartLegendEl.addEventListener("click", function(ev) {
      if (ev.target && ev.target.closest && ev.target.closest("button")) return;
      var pill = ev.target && ev.target.closest ? ev.target.closest(".legend-indicator") : null;
      var key = pill && legMap[pill.id];
      if (!key || !indicatorConfig) return;
      indicatorConfig[key] = !indicatorConfig[key];
      saveIndicatorConfig();
      applyIndicatorVisibilities();
    });
  }
  window.addEventListener("keydown", function(e) {
    var k = e.key.toLowerCase();
    var code = e.code || "";
    var isKeyS = (k === "s" || code === "KeyS");
    var isKeyL = (k === "l" || code === "KeyL");
    var isKeyP = (k === "p" || code === "KeyP");
    var isKeyR = (k === "r" || code === "KeyR");
    var isKeyH = (k === "h" || code === "KeyH");
    var isKeyA = (k === "a" || code === "KeyA");
    var isKeyM = (k === "m" || code === "KeyM");

    if ((e.metaKey || e.ctrlKey) && k === "k") {
      e.preventDefault(); openSearch();
    } else if (e.key === "Escape") {
      if (drawingInProgress) {
        drawingInProgress = null;
        activeDragDraw = null;
        if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
        return;
      }
      closeSearch();
      if (indicatorModal) indicatorModal.style.display = "none";
      if (fundamentalsModal) fundamentalsModal.style.display = "none";
      if (aiLensModal) aiLensModal.style.display = "none";
      closeScreenerModal();
      if (typeof closeChatModal === "function") closeChatModal();
      closeHeatmapModalHandler();
      closeRrEditModal();
      resetDrawToolToCursor();
      if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
      updateCrosshairLegend(latestBarData);
    } else if ((e.altKey && isKeyS) || ((e.metaKey || e.ctrlKey) && e.shiftKey && isKeyS)) {
      e.preventDefault(); toggleScreenerModal();
    } else if (e.altKey && isKeyL) {
      e.preventDefault(); setPriceScaleMode(currentPriceScaleMode === "log" ? "normal" : "log");
    } else if (e.altKey && isKeyP) {
      e.preventDefault(); setPriceScaleMode(currentPriceScaleMode === "percent" ? "normal" : "percent");
    } else if (e.altKey && isKeyR) {
      e.preventDefault();
      setAutoScale(true);
      resetChartView(chart, getMainTimeAxis().length);
    } else if (e.altKey && isKeyH) {
      e.preventDefault(); _addQuickPriceLine(false);
    } else if (e.altKey && isKeyA) {
      e.preventDefault(); _addQuickPriceLine(true);
    } else if ((e.altKey && isKeyM) || ((e.metaKey || e.ctrlKey) && e.shiftKey && isKeyM)) {
      e.preventDefault(); toggleHeatmapModal();
    } else if (!e.metaKey && !e.ctrlKey && !e.altKey) {
      var tag = e.target && e.target.tagName;
      var isContentEditable = Boolean(e.target && (e.target.isContentEditable || (typeof e.target.getAttribute === "function" && e.target.getAttribute("contenteditable") === "true")));
      var avwapModalEl = document.getElementById("avwapSettingsModal");
      var heatmapModalEl = document.getElementById("heatmapModal");
      var rrEditModalEl = document.getElementById("rrEditModal");
      var modalOpen = (searchModal && searchModal.style.display !== "none" && searchModal.style.display !== "") ||
                      (indicatorModal && indicatorModal.style.display !== "none" && indicatorModal.style.display !== "") ||
                      (fundamentalsModal && fundamentalsModal.style.display !== "none" && fundamentalsModal.style.display !== "") ||
                      (aiLensModal && aiLensModal.style.display !== "none" && aiLensModal.style.display !== "") ||
                      (screenerModal && screenerModal.style.display !== "none" && screenerModal.style.display !== "") ||
                      (avwapModalEl && avwapModalEl.style.display !== "none" && avwapModalEl.style.display !== "") ||
                      (heatmapModalEl && heatmapModalEl.style.display !== "none" && heatmapModalEl.style.display !== "") ||
                      (rrEditModalEl && rrEditModalEl.style.display !== "none" && rrEditModalEl.style.display !== "");
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || isContentEditable || modalOpen) return;
      if (e.key === " " || e.key === "Spacebar" || e.code === "Space" || e.key === "ArrowDown") {
        e.preventDefault();
        navigateWatchlist(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        navigateWatchlist(-1);
      } else if (e.key === "/") {
        e.preventDefault(); if (typeof syncIndicatorCheckboxes === "function") syncIndicatorCheckboxes(); if (indicatorModal) indicatorModal.style.display = "flex";
      } else if (/^[0-9]$/.test(e.key) || (_tfBuf && (k === "d" || k === "w"))) {
        _tfBuf += e.key.toUpperCase(); clearTimeout(_tfTimer); _tfTimer = setTimeout(function() { _tfBuf = ""; }, 900);
      } else if (e.key === "Enter" && _tfBuf) {
        var tfTarget = ({ "1": "1m", "5": "5m", "15": "15m", "60": "1h", "D": "1D", "W": "1W" })[_tfBuf];
        _tfBuf = "";
        if (tfTarget) { var b = document.querySelector('.tf-btn[data-tf="' + tfTarget + '"]'); if (b) b.click(); }
      } else if (/^[a-zA-Z]$/.test(e.key)) {
        e.preventDefault(); openSearch(e.key.toUpperCase());
      }
    }
  });

  // Bind pane selection clicks
  var p1El = document.getElementById("chartContainer");
  var p2El = document.getElementById("splitPane2");
  var p3El = document.getElementById("splitPane3");
  var p4El = document.getElementById("splitPane4");
  if (p1El) p1El.addEventListener("click", function() { setFocusedPane(1); });
  if (p2El) p2El.addEventListener("click", function() { setFocusedPane(2); });
  if (p3El) p3El.addEventListener("click", function() { setFocusedPane(3); });
  if (p4El) p4El.addEventListener("click", function() { setFocusedPane(4); });

  var mtfSyncBtn = document.getElementById("mtfSyncBtn");
  if (mtfSyncBtn) {
    mtfSyncBtn.onclick = function() {
      mtfSyncEnabled = !mtfSyncEnabled;
      mtfSyncBtn.classList.toggle("active", mtfSyncEnabled);
      if (mtfSyncEnabled) {
        if (activeSplitLayout === "1x1") {
          applySplitLayout("2x2");
        }
        syncMTF(activeSymbol);
      }
    };
  }

  // ==========================================================================
  // Left Vertical Drawing Toolbar & Overlay Canvas Engine
  // ==========================================================================
  currentDrawTool = "cursor";
  drawings = activeSymbol ? loadDrawingsForSymbol(activeSymbol) : [];
  drawingInProgress = null;
  isDraggingHandle = null;

  var drawingCanvas = document.getElementById("drawingCanvas");
  var drawToolBtns = document.querySelectorAll ? document.querySelectorAll(".draw-tool-btn[data-tool]:not(.draw-tool-clear):not(.draw-tool-toggle)") : [];

  for (var dtIdx = 0; dtIdx < drawToolBtns.length; dtIdx++) {
    (function(btn) {
      btn.onclick = function() {
        for (var j = 0; j < drawToolBtns.length; j++) drawToolBtns[j].classList.remove("active");
        btn.classList.add("active");
        currentDrawTool = btn.getAttribute("data-tool") || "cursor";
        activeDragDraw = null;
        if (drawingCanvas) {
          drawingCanvas.classList.toggle("drawing-active", currentDrawTool !== "cursor");
          if (currentDrawTool !== "cursor") {
            drawingCanvas.classList.remove("cursor-pointer", "cursor-move", "cursor-ns-resize");
          }
        }
      };
    })(drawToolBtns[dtIdx]);
  }

  var btnMagnet = document.getElementById("btnMagnet");
  if (btnMagnet) {
    btnMagnet.classList.toggle("active", magnetMode);
    btnMagnet.onclick = function() {
      magnetMode = !magnetMode;
      safeSetStorage("tv_magnet_mode", String(magnetMode));
      btnMagnet.classList.toggle("active", magnetMode);
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var btnPinWindow = document.getElementById("btnPinWindow");
  if (btnPinWindow) {
    btnPinWindow.classList.toggle("active", isAlwaysOnTop);
    btnPinWindow.setAttribute("title", isAlwaysOnTop ? "取消視窗置頂 (Pinned on Top)" : "視窗置頂 (Always on Top)");

    if (window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.invoke === "function") {
      window.__TAURI__.core.invoke("get_always_on_top")
        .then(function(state) {
          if (typeof state === "boolean") {
            isAlwaysOnTop = state;
            btnPinWindow.classList.toggle("active", isAlwaysOnTop);
            btnPinWindow.setAttribute("title", isAlwaysOnTop ? "取消視窗置頂 (Pinned on Top)" : "視窗置頂 (Always on Top)");
          }
        })
        .catch(function() {});
    }

    btnPinWindow.onclick = function() {
      if (window.__TAURI__ && window.__TAURI__.core && typeof window.__TAURI__.core.invoke === "function") {
        window.__TAURI__.core.invoke("toggle_always_on_top")
          .then(function(state) {
            isAlwaysOnTop = Boolean(state);
            safeSetStorage("tv_always_on_top", String(isAlwaysOnTop));
            btnPinWindow.classList.toggle("active", isAlwaysOnTop);
            btnPinWindow.setAttribute("title", isAlwaysOnTop ? "取消視窗置頂 (Pinned on Top)" : "視窗置頂 (Always on Top)");
            if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
          })
          .catch(function(err) {
            console.warn("Toggle always on top error:", err);
          });
      } else {
        isAlwaysOnTop = !isAlwaysOnTop;
        safeSetStorage("tv_always_on_top", String(isAlwaysOnTop));
        btnPinWindow.classList.toggle("active", isAlwaysOnTop);
        btnPinWindow.setAttribute("title", isAlwaysOnTop ? "取消視窗置頂 (Pinned on Top)" : "視窗置頂 (Always on Top)");
        if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
      }
    };
  }

  var btnClearDrawings = document.getElementById("btnClearDrawings");
  if (btnClearDrawings) {
    btnClearDrawings.onclick = function() {
      drawings = [];
      drawingInProgress = null;
      saveDrawingsForActiveSymbol();
      if (candleSeries && tvPriceLineHandles.length) {
        for (var pi = 0; pi < tvPriceLineHandles.length; pi++) { try { candleSeries.removePriceLine(tvPriceLineHandles[pi]); } catch (e) {} }
        tvPriceLineHandles = [];
      }
      if (drawingCanvas) drawingCanvas.classList.remove("interactive", "cursor-move", "cursor-ns-resize", "cursor-pointer");
      renderDrawingCanvas();
    };
  }

  function resizeDrawingCanvas() {
    if (!drawingCanvas) return;
    var rect = drawingCanvas.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      drawingCanvas.width = rect.width;
      drawingCanvas.height = rect.height;
      renderDrawingCanvas();
    }
  }

  window.addEventListener("resize", resizeDrawingCanvas);

  renderDrawingCanvas = function() {
    if (!Array.isArray(drawings)) drawings = [];
    if (!candlestickSeries && candleSeries) candlestickSeries = candleSeries;
    if (!drawingCanvas || !chart || !candlestickSeries) return;
    var ctx = drawingCanvas.getContext("2d");
    if (!ctx) return;

    if (drawingCanvas.width !== drawingCanvas.clientWidth || drawingCanvas.height !== drawingCanvas.clientHeight) {
      drawingCanvas.width = drawingCanvas.clientWidth;
      drawingCanvas.height = drawingCanvas.clientHeight;
    }
    ctx.clearRect(0, 0, drawingCanvas.width, drawingCanvas.height);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";

    var timeScale = chart.timeScale ? chart.timeScale() : null;
    if (!timeScale) return;
    var drawAxisTimes = getMainTimeAxis();
    var drawTfSec = getActiveTimeframeSeconds();

    // Render Anchored VWAP (AVWAP) Overlay
    if (indicatorConfig && indicatorConfig.avwap && currentAvwapData && currentAvwapData.avwap && currentAvwapData.avwap.length > 0 && avwapConfig.displayMode === "overlay") {
      var avPts = [];
      var firstOnScreenIdx = -1;
      for (var avi = 0; avi < currentAvwapData.avwap.length; avi++) {
        var aItem = currentAvwapData.avwap[avi];
        var aX = safeTimeToCoordinate(timeScale, aItem.time);
        var aYVwap = candlestickSeries.priceToCoordinate(aItem.value);
        var aYUp = aItem.upper !== undefined ? candlestickSeries.priceToCoordinate(aItem.upper) : aYVwap;
        var aYLo = aItem.lower !== undefined ? candlestickSeries.priceToCoordinate(aItem.lower) : aYVwap;
        if (aX !== null && !isNaN(aX) && aYVwap !== null && !isNaN(aYVwap)) {
          if (firstOnScreenIdx === -1) firstOnScreenIdx = avi;
          avPts.push({
            x: aX,
            yVwap: aYVwap,
            yUpper: aYUp !== null && !isNaN(aYUp) ? aYUp : aYVwap,
            yLower: aYLo !== null && !isNaN(aYLo) ? aYLo : aYVwap,
            isAbove: aItem.isAbove
          });
        }
      }

      // Extrapolate to left screen border (x = 0) if anchor/earlier points are off-screen
      if (avPts.length > 0 && firstOnScreenIdx > 0 && avPts[0].x > 0) {
        var prevItem = currentAvwapData.avwap[firstOnScreenIdx - 1];
        var prevY = candlestickSeries.priceToCoordinate(prevItem.value);
        var prevYUp = prevItem.upper !== undefined ? candlestickSeries.priceToCoordinate(prevItem.upper) : prevY;
        var prevYLo = prevItem.lower !== undefined ? candlestickSeries.priceToCoordinate(prevItem.lower) : prevY;
        avPts.unshift({
          x: 0,
          yVwap: prevY !== null && !isNaN(prevY) ? prevY : avPts[0].yVwap,
          yUpper: prevYUp !== null && !isNaN(prevYUp) ? prevYUp : avPts[0].yUpper,
          yLower: prevYLo !== null && !isNaN(prevYLo) ? prevYLo : avPts[0].yLower,
          isAbove: prevItem.isAbove !== undefined ? prevItem.isAbove : avPts[0].isAbove
        });
      }

      if (avPts.length > 0) {
        // 1. Translucent Bands Fill
        if (avwapConfig.showBands && avwapConfig.showBandFill && avPts.length > 1) {
          ctx.save();
          ctx.fillStyle = "rgba(120, 123, 134, 0.12)";
          ctx.beginPath();
          ctx.moveTo(avPts[0].x, avPts[0].yUpper);
          for (var afi = 1; afi < avPts.length; afi++) {
            ctx.lineTo(avPts[afi].x, avPts[afi].yUpper);
          }
          for (var abi = avPts.length - 1; abi >= 0; abi--) {
            ctx.lineTo(avPts[abi].x, avPts[abi].yLower);
          }
          ctx.closePath();
          ctx.fill();
          ctx.restore();
        }

        // 2. Bands Upper and Lower Lines
        if (avwapConfig.showBands && avPts.length > 1) {
          ctx.save();
          ctx.strokeStyle = avwapConfig.bandColor || "#787b86";
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);

          // Upper band line
          ctx.beginPath();
          ctx.moveTo(avPts[0].x, avPts[0].yUpper);
          for (var aui = 1; aui < avPts.length; aui++) {
            ctx.lineTo(avPts[aui].x, avPts[aui].yUpper);
          }
          ctx.stroke();

          // Lower band line
          ctx.beginPath();
          ctx.moveTo(avPts[0].x, avPts[0].yLower);
          for (var ali = 1; ali < avPts.length; ali++) {
            ctx.lineTo(avPts[ali].x, avPts[ali].yLower);
          }
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.restore();
        }

        // 3. Dual-color Main AVWAP Line (2px)
        if (avPts.length > 1) {
          ctx.save();
          ctx.lineWidth = 2;
          for (var aseg = 0; aseg < avPts.length - 1; aseg++) {
            var strokeColor = avPts[aseg + 1].isAbove ? (avwapConfig.colorAbove || "#26a69a") : (avwapConfig.colorBelow || "#ef5350");
            ctx.strokeStyle = strokeColor;
            ctx.beginPath();
            ctx.moveTo(avPts[aseg].x, avPts[aseg].yVwap);
            ctx.lineTo(avPts[aseg + 1].x, avPts[aseg + 1].yVwap);
            ctx.stroke();
          }
          ctx.restore();
        }

        // 4. Anchor Badge Marker
        if (avwapConfig.showAnchorLbl && currentAvwapData.anchorBar) {
          var rawMarkX = safeTimeToCoordinate(timeScale, currentAvwapData.anchorBar.time);
          var markY = candlestickSeries.priceToCoordinate(currentAvwapData.anchorBar.price);
          if (markY !== null && !isNaN(markY)) {
            var markX = (rawMarkX !== null && !isNaN(rawMarkX)) ? Math.max(28, rawMarkX) : 28;
            ctx.save();
            ctx.strokeStyle = "#787b86";
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(markX, markY);
            ctx.lineTo(markX, markY + 14);
            ctx.stroke();

            var bWidth = 52;
            var bHeight = 18;
            var bLeft = markX - bWidth / 2;
            var bTop = markY + 14;
            var canvasPalette = getChartThemePalette();
            ctx.fillStyle = canvasPalette.anchorBg;
            ctx.fillRect(bLeft, bTop, bWidth, bHeight);
            ctx.strokeStyle = "#787b86";
            ctx.strokeRect(bLeft, bTop, bWidth, bHeight);

            ctx.fillStyle = canvasPalette.anchorText;
            ctx.font = "bold 10px -apple-system, BlinkMacSystemFont, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText("Anchor", markX, bTop + bHeight / 2);
            ctx.restore();
          }
        }
      }
    }

    for (var i = 0; i < drawings.length; i++) {
      var d = drawings[i];
      if (!d || typeof d !== "object") continue;
      try {
        if (d.type === "hline") {
          var y = candlestickSeries.priceToCoordinate(d.price);
          if (y !== null && !isNaN(y)) {
            ctx.strokeStyle = d.color || "#3b82f6";
            ctx.lineWidth = 1.5;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(drawingCanvas.width, y);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.fillStyle = d.color || "#3b82f6";
            ctx.font = "11px Inter, system-ui, sans-serif";
            ctx.fillText(canonicalFormatPrice(d.price), drawingCanvas.width - 60, y - 4);
          }
        } else if (d.type === "trendline") {
          var x1 = safeTimeToCoordinate(timeScale, d.time1, drawAxisTimes, drawTfSec);
          var y1 = candlestickSeries.priceToCoordinate(d.price1);
          var x2 = safeTimeToCoordinate(timeScale, d.time2, drawAxisTimes, drawTfSec);
          var y2 = candlestickSeries.priceToCoordinate(d.price2);
          if (x1 !== null && y1 !== null && x2 !== null && y2 !== null) {
            ctx.strokeStyle = "#eab308";
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();

            ctx.fillStyle = "#eab308";
            ctx.beginPath();
            ctx.arc(x1, y1, 4, 0, Math.PI * 2);
            ctx.arc(x2, y2, 4, 0, Math.PI * 2);
            ctx.fill();
          }
        } else if (d.type === "risk-reward") {
          var xCenter = d.time ? safeTimeToCoordinate(timeScale, d.time, drawAxisTimes, drawTfSec) : drawingCanvas.width * 0.5;
          if (xCenter === null || isNaN(xCenter)) xCenter = drawingCanvas.width * 0.5;
          var boxWidth = Math.min(260, drawingCanvas.width * 0.45);
          var left = Math.max(10, xCenter);
          var right = Math.min(Math.max(left, drawingCanvas.width - 4), left + boxWidth);
          var drawWidth = Math.max(0, right - left);

          var yEntry = candlestickSeries.priceToCoordinate(d.entryPrice);
          var yTarget = candlestickSeries.priceToCoordinate(d.targetPrice);
          var yStop = candlestickSeries.priceToCoordinate(d.stopPrice);

          if (drawWidth > 0 && yEntry !== null && yTarget !== null && yStop !== null) {
            var targetTop = Math.min(yTarget, yEntry);
            var targetHeight = Math.max(2, Math.abs(yTarget - yEntry));
            ctx.fillStyle = "rgba(34, 197, 94, 0.08)";
            ctx.fillRect(left, targetTop, drawWidth, targetHeight);
            ctx.strokeStyle = "#22c55e";
            ctx.lineWidth = 1.5;
            ctx.strokeRect(left, targetTop, drawWidth, targetHeight);

            var stopTop = Math.min(yStop, yEntry);
            var stopHeight = Math.max(2, Math.abs(yStop - yEntry));
            ctx.fillStyle = "rgba(239, 68, 68, 0.08)";
            ctx.fillRect(left, stopTop, drawWidth, stopHeight);
            ctx.strokeStyle = "#ef4444";
            ctx.lineWidth = 1.5;
            ctx.strokeRect(left, stopTop, drawWidth, stopHeight);

            ctx.strokeStyle = "#3b82f6";
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(left - 10, yEntry);
            ctx.lineTo(Math.min(drawingCanvas.width, right + 10), yEntry);
            ctx.stroke();

            d.targetHandleY = yTarget;
            d.stopHandleY = yStop;
            d.targetTop = targetTop;
            d.targetHeight = targetHeight;
            d.stopTop = stopTop;
            d.stopHeight = stopHeight;
            d.boxLeft = left;
            d.boxRight = right;

            d.closeBtnX = Math.max(20, Math.min(drawingCanvas.width - 20, right - 14));
            d.closeBtnY = Math.max(20, Math.min(drawingCanvas.height - 20, targetTop + 14));
            ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
            ctx.beginPath();
            ctx.arc(d.closeBtnX, d.closeBtnY, 9, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = "#ffffff";
            ctx.font = "bold 10px sans-serif";
            ctx.textAlign = "left";
            ctx.textBaseline = "alphabetic";
            ctx.fillText("✕", d.closeBtnX - 4, d.closeBtnY + 3.5);

            ctx.fillStyle = "#22c55e";
            ctx.fillRect(left + drawWidth * 0.5 - 10, yTarget - 4, 20, 8);
            ctx.fillStyle = "#ef4444";
            ctx.fillRect(left + drawWidth * 0.5 - 10, yStop - 4, 20, 8);

            var currentRiskDollar = getRiskDollar();
            var rrView = computeRiskRewardView({
              entry: d.entryPrice,
              target: d.targetPrice,
              stop: d.stopPrice,
              riskDollar: currentRiskDollar
            });
            var targetPriceStr = canonicalFormatPrice(d.targetPrice);
            var stopPriceStr = canonicalFormatPrice(d.stopPrice);

            ctx.fillStyle = getChartThemePalette().rrTitle;
            ctx.font = "bold 12px Inter, system-ui, sans-serif";
            if (!rrView.error && rrView.side) {
              var isShort = rrView.side === "short";
              var targetSign = isShort ? "-" : "+";
              var stopSign = isShort ? "+" : "-";
              var actionLabel = isShort ? "建議放空" : "建議買進";

              ctx.fillText("R:R Ratio: 1 : " + rrView.rr.toFixed(2), left + 10, yEntry - 8);

              ctx.font = "11px Inter, system-ui, sans-serif";
              ctx.fillStyle = "#4ade80";
              ctx.fillText("Target: " + targetSign + rrView.rewardPct.toFixed(2) + "% (" + targetPriceStr + ")", left + 10, targetTop + 16);

              ctx.fillStyle = "#f87171";
              ctx.fillText("Stop: " + stopSign + rrView.riskPct.toFixed(2) + "% (" + stopPriceStr + ")", left + 10, stopTop + stopHeight - 8);

              ctx.fillStyle = "#93c5fd";
              ctx.fillText(actionLabel + ": " + rrView.shares + " 股 ($" + currentRiskDollar + " 風控)", left + 10, yEntry + 18);

              if (d.manualQuantity !== undefined && d.manualQuantity !== null) {
                var manualView = computeManualRiskView({
                  view: rrView,
                  quantity: d.manualQuantity,
                  riskDollar: currentRiskDollar
                });
                if (!manualView.error && manualView.riskAmount !== null) {
                  ctx.fillStyle = manualView.overRisk ? "#f87171" : "#fbbf24";
                  var manualText = "手動: " + String(d.manualQuantity) + " 股 → 風險 $" + manualView.riskAmount.toFixed(2);
                  if (manualView.overRisk) {
                    manualText += "  ⚠ 超過風控";
                  }
                  ctx.fillText(manualText, left + 10, yEntry + 32);
                }
              }
            } else {
              ctx.fillText("R:R Ratio: --", left + 10, yEntry - 8);

              ctx.font = "11px Inter, system-ui, sans-serif";
              ctx.fillStyle = "#4ade80";
              ctx.fillText("Target: (" + targetPriceStr + ")", left + 10, targetTop + 16);

              ctx.fillStyle = "#f87171";
              ctx.fillText("Stop: (" + stopPriceStr + ")", left + 10, stopTop + stopHeight - 8);

              ctx.fillStyle = "#f87171";
              ctx.fillText("停損與目標必須在進場價兩側", left + 10, yEntry + 18);
            }

            ctx.font = "10px Inter, system-ui, sans-serif";
            ctx.fillStyle = "rgba(148, 163, 184, 0.85)";
            ctx.textAlign = "right";
            ctx.fillText("雙擊編輯", right - 8, yEntry - 8);
            ctx.textAlign = "left";
          }
        } else if (d.type === "ruler") {
          var rx1 = d.time1 ? safeTimeToCoordinate(timeScale, d.time1, drawAxisTimes, drawTfSec) : d.x1;
          var ry1 = candlestickSeries ? candlestickSeries.priceToCoordinate(d.price1) : d.y1;
          var rx2 = d.time2 ? safeTimeToCoordinate(timeScale, d.time2, drawAxisTimes, drawTfSec) : d.x2;
          var ry2 = candlestickSeries ? candlestickSeries.priceToCoordinate(d.price2) : d.y2;
          if (rx1 !== null && ry1 !== null && rx2 !== null && ry2 !== null) {
            var rLeft = Math.min(rx1, rx2);
            var rTop = Math.min(ry1, ry2);
            var rW = Math.max(2, Math.abs(rx2 - rx1));
            var rH = Math.max(2, Math.abs(ry2 - ry1));
            ctx.fillStyle = "rgba(41, 98, 255, 0.08)";
            ctx.fillRect(rLeft, rTop, rW, rH);
            ctx.strokeStyle = "#2962ff";
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 2]);
            ctx.strokeRect(rLeft, rTop, rW, rH);
            ctx.setLineDash([]);
            var dp = d.price2 - d.price1;
            var dpPct = d.price1 > 0 ? (dp / d.price1) * 100 : 0;
            ctx.fillStyle = dp >= 0 ? "#26a69a" : "#ef5350";
            ctx.font = "bold 11px Inter, system-ui, sans-serif";
            ctx.fillText((dp >= 0 ? "+" : "") + dp.toFixed(2) + " (" + (dpPct >= 0 ? "+" : "") + dpPct.toFixed(2) + "%)", rLeft + 6, rTop + 16);
          }
        }
      } catch (eD) {
        console.warn("[drawings] 略過無法繪製的繪圖", i, eD);
      }
    }

    if (drawingInProgress && drawingInProgress.type === "trendline" && drawingInProgress.step === 1) {
      var tx1 = safeTimeToCoordinate(timeScale, drawingInProgress.time1, drawAxisTimes, drawTfSec);
      var ty1 = candlestickSeries.priceToCoordinate(drawingInProgress.price1);
      if (tx1 !== null && ty1 !== null) {
        ctx.strokeStyle = "rgba(234, 179, 8, 0.7)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(tx1, ty1);
        ctx.lineTo(drawingInProgress.currentX, drawingInProgress.currentY);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    } else if (drawingInProgress && drawingInProgress.type === "ruler" && drawingInProgress.step === 1) {
      var rLeft = Math.min(drawingInProgress.x1, drawingInProgress.currentX);
      var rTop = Math.min(drawingInProgress.y1, drawingInProgress.currentY);
      var rW = Math.max(2, Math.abs(drawingInProgress.currentX - drawingInProgress.x1));
      var rH = Math.max(2, Math.abs(drawingInProgress.currentY - drawingInProgress.y1));
      ctx.fillStyle = "rgba(41, 98, 255, 0.12)";
      ctx.fillRect(rLeft, rTop, rW, rH);
      ctx.strokeStyle = "#2962ff";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 2]);
      ctx.strokeRect(rLeft, rTop, rW, rH);
      ctx.setLineDash([]);
      if (candlestickSeries) {
        var pCurrent = candlestickSeries.coordinateToPrice(drawingInProgress.currentY);
        if (pCurrent && drawingInProgress.price1) {
          var dpActive = pCurrent - drawingInProgress.price1;
          var dpPctActive = (dpActive / drawingInProgress.price1) * 100;
          ctx.fillStyle = dpActive >= 0 ? "#26a69a" : "#ef5350";
          ctx.font = "bold 11px Inter, system-ui, sans-serif";
          ctx.fillText((dpActive >= 0 ? "+" : "") + dpActive.toFixed(2) + " (" + (dpPctActive >= 0 ? "+" : "") + dpPctActive.toFixed(2) + "%)", rLeft + 6, rTop + 16);
        }
      }
    }
  }

  if (drawingCanvas) {
    function resolveDrawingPointer(e) {
      var rect = drawingCanvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;
      var timeScale = chart.timeScale();
      var price = candlestickSeries.coordinateToPrice(y);
      var time = snapTimeFromCoordinate(timeScale, x, getMainTimeAxis(), getActiveTimeframeSeconds());
      if ((magnetMode || e.ctrlKey || e.metaKey) && currentRawBars && currentRawBars.length > 0 && timeScale.coordinateToLogical) {
        var lIdx = Math.round(timeScale.coordinateToLogical(x));
        if (isFinite(lIdx) && lIdx >= 0 && lIdx < currentRawBars.length) {
          var sBar = currentRawBars[lIdx];
          var ohlc = [sBar.open, sBar.high, sBar.low, sBar.close];
          var bestDist = 24, bestP = price;
          for (var oi = 0; oi < ohlc.length; oi++) {
            var cy = candlestickSeries.priceToCoordinate(ohlc[oi]);
            if (cy !== null && Math.abs(cy - y) < bestDist) { bestDist = Math.abs(cy - y); bestP = ohlc[oi]; }
          }
          price = bestP;
          time = sBar.time;
        }
      }
      return { x: x, y: y, price: price, time: time };
    }

    function completeTwoPointDrawing(pt) {
      if (!drawingInProgress || drawingInProgress.step !== 1) return false;
      if (drawingInProgress.type === "ruler") {
        drawings.push({
          type: "ruler",
          time1: drawingInProgress.time1,
          price1: drawingInProgress.price1,
          time2: pt.time,
          price2: pt.price,
          x1: drawingInProgress.x1,
          y1: drawingInProgress.y1,
          x2: pt.x,
          y2: pt.y
        });
      } else if (drawingInProgress.type === "trendline") {
        if (!isValidChartTime(pt.time)) return false;
        drawings.push({
          type: "trendline",
          time1: drawingInProgress.time1,
          price1: drawingInProgress.price1,
          time2: pt.time,
          price2: pt.price
        });
      } else {
        return false;
      }
      drawingInProgress = null;
      activeDragDraw = null;
      saveDrawingsForActiveSymbol();
      renderDrawingCanvas();
      return true;
    }

    drawingCanvas.addEventListener("mousedown", function(e) {
      if (!chart || !candlestickSeries) return;
      var pt = resolveDrawingPointer(e);
      var x = pt.x, y = pt.y, price = pt.price, time = pt.time;

      if (currentDrawTool === "cursor") {
        for (var k = drawings.length - 1; k >= 0; k--) {
          var rd = drawings[k];
          if (rd.type === "risk-reward") {
            if (rd.closeBtnX && rd.closeBtnY && Math.hypot(x - rd.closeBtnX, y - rd.closeBtnY) <= 14) {
              drawings.splice(k, 1);
              saveDrawingsForActiveSymbol();
              if (drawingCanvas) drawingCanvas.classList.remove("interactive", "cursor-move", "cursor-ns-resize", "cursor-pointer");
              renderDrawingCanvas();
              return;
            }
            if (rd.boxLeft && rd.boxRight && x >= rd.boxLeft && x <= rd.boxRight) {
              if (Math.abs(y - rd.targetHandleY) <= 10) {
                isDraggingHandle = { drawing: rd, handle: "target" };
                return;
              }
              if (Math.abs(y - rd.stopHandleY) <= 10) {
                isDraggingHandle = { drawing: rd, handle: "stop" };
                return;
              }
              var yEntCoord = candlestickSeries ? candlestickSeries.priceToCoordinate(rd.entryPrice) : null;
              if (yEntCoord !== null && Math.abs(y - yEntCoord) <= 10) {
                isDraggingHandle = { drawing: rd, handle: "entry" };
                return;
              }
              var bMinY = Math.min(rd.targetTop || 0, rd.stopTop || 0);
              var bMaxY = Math.max((rd.targetTop || 0) + (rd.targetHeight || 0), (rd.stopTop || 0) + (rd.stopHeight || 0));
              if (y >= bMinY && y <= bMaxY) {
                isDraggingHandle = {
                  drawing: rd,
                  handle: "body",
                  startX: x,
                  startY: y,
                  origPrice: price || rd.entryPrice,
                  origTime: rd.time,
                  origEntry: rd.entryPrice,
                  origTarget: rd.targetPrice,
                  origStop: rd.stopPrice
                };
                return;
              }
            }
          }
        }
        return;
      }

      if (currentDrawTool === "risk-reward") {
        if (price) {
          var targetP = price * 1.15;
          var stopP = price * 0.94;
          drawings.push({
            type: "risk-reward",
            time: time || (latestBarData ? latestBarData.time : null),
            entryPrice: price,
            targetPrice: targetP,
            stopPrice: stopP
          });
          saveDrawingsForActiveSymbol();
          renderDrawingCanvas();
        }
      } else if (currentDrawTool === "ruler") {
        if (!drawingInProgress) {
          drawingInProgress = { type: "ruler", step: 1, time1: time, price1: price, x1: x, y1: y, currentX: x, currentY: y };
          if (e.button === undefined || e.button === 0) {
            activeDragDraw = { tool: "ruler", startX: x, startY: y };
          }
        } else if (drawingInProgress.step === 1) {
          activeDragDraw = null;
          completeTwoPointDrawing(pt);
        }
      } else if (currentDrawTool === "hline") {
        if (price) {
          drawings.push({ type: "hline", price: price, color: "#3b82f6" });
          saveDrawingsForActiveSymbol();
          renderDrawingCanvas();
        }
      } else if (currentDrawTool === "trendline") {
        if (!drawingInProgress) {
          if (!isValidChartTime(time)) return;
          drawingInProgress = { type: "trendline", step: 1, time1: time, price1: price, currentX: x, currentY: y };
          if (e.button === undefined || e.button === 0) {
            activeDragDraw = { tool: "trendline", startX: x, startY: y };
          }
        } else if (drawingInProgress.step === 1) {
          activeDragDraw = null;
          completeTwoPointDrawing(pt);
        }
      } else if (currentDrawTool === "avwap") {
        if (currentRawBars && currentRawBars.length > 0 && window.Indicators && window.Indicators.calcAnchoredVWAP) {
          if (time) {
            var bSec = typeof time === "number" ? time : 0;
            if (bSec > 0) {
              var dt = new Date(bSec > 1e11 ? bSec : bSec * 1000);
              var ym = dt.toISOString().slice(0, 10);
              var hm = ("0" + dt.getUTCHours()).slice(-2) + ":" + ("0" + dt.getUTCMinutes()).slice(-2);
              avwapConfig.anchorMode = "Manual Date";
              avwapConfig.manualAnchorDate = ym;
              avwapConfig.manualAnchorTime = hm;
              saveAvwapConfig();
            }
          }
          if (indicatorConfig) indicatorConfig.avwap = true;
          saveIndicatorConfig();
          applyIndicatorVisibilities();
          calcAndRenderAvwap();
          renderDrawingCanvas();
        }
      }
    });

    drawingCanvas.addEventListener("mousemove", function(e) {
      var rect = drawingCanvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;

      if (isDraggingHandle && candlestickSeries) {
        var newPrice = candlestickSeries.coordinateToPrice(y);
        if (newPrice !== null && !isNaN(newPrice)) {
          if (isDraggingHandle.handle === "target") {
            isDraggingHandle.drawing.targetPrice = newPrice;
          } else if (isDraggingHandle.handle === "stop") {
            isDraggingHandle.drawing.stopPrice = newPrice;
          } else if (isDraggingHandle.handle === "entry") {
            var diff = newPrice - isDraggingHandle.drawing.entryPrice;
            isDraggingHandle.drawing.entryPrice = newPrice;
            isDraggingHandle.drawing.targetPrice += diff;
            isDraggingHandle.drawing.stopPrice += diff;
          } else if (isDraggingHandle.handle === "body") {
            var bDiff = newPrice - (isDraggingHandle.origPrice || isDraggingHandle.drawing.entryPrice);
            isDraggingHandle.drawing.entryPrice = isDraggingHandle.origEntry + bDiff;
            isDraggingHandle.drawing.targetPrice = isDraggingHandle.origTarget + bDiff;
            isDraggingHandle.drawing.stopPrice = isDraggingHandle.origStop + bDiff;
            var timeScale = (chart && typeof chart.timeScale === "function") ? chart.timeScale() : null;
            var dragAxisTimes = typeof getMainTimeAxis === "function" ? getMainTimeAxis() : [];
            var dragTfSec = getActiveTimeframeSeconds();
            if (timeScale && typeof timeScale.coordinateToTime === "function" && typeof timeScale.timeToCoordinate === "function") {
              var origAnchorX = safeTimeToCoordinate(timeScale, isDraggingHandle.origTime, dragAxisTimes, dragTfSec);
              if (origAnchorX !== null && !isNaN(origAnchorX)) {
                var newAnchorX = origAnchorX + (x - (isDraggingHandle.startX || x));
                var newT = snapTimeFromCoordinate(timeScale, newAnchorX, dragAxisTimes, dragTfSec);
                if (newT !== null && newT !== undefined) {
                  isDraggingHandle.drawing.time = newT;
                }
              }
            } else if (timeScale && typeof timeScale.coordinateToTime === "function") {
              var newT = snapTimeFromCoordinate(timeScale, x, dragAxisTimes, dragTfSec);
              if (newT !== null && newT !== undefined) {
                isDraggingHandle.drawing.time = newT;
              }
            }
          }
          scheduleDrawingRender();
        }
        return;
      }

      if (drawingInProgress && (drawingInProgress.type === "trendline" || drawingInProgress.type === "ruler")) {
        drawingInProgress.currentX = x;
        drawingInProgress.currentY = y;
        scheduleDrawingRender();
      }
    });

    drawingCanvas.addEventListener("dblclick", function(e) {
      var rect = drawingCanvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;
      for (var k = drawings.length - 1; k >= 0; k--) {
        var rd = drawings[k];
        if (rd.type === "risk-reward") {
          if (rd.closeBtnX && rd.closeBtnY && Math.hypot(x - rd.closeBtnX, y - rd.closeBtnY) <= 14) {
            return;
          }
          if (rd.boxLeft && rd.boxRight && x >= rd.boxLeft && x <= rd.boxRight) {
            var bMinY = Math.min(rd.targetTop || 0, rd.stopTop || 0);
            var bMaxY = Math.max((rd.targetTop || 0) + (rd.targetHeight || 0), (rd.stopTop || 0) + (rd.stopHeight || 0));
            if (y >= bMinY && y <= bMaxY) {
              e.preventDefault();
              isDraggingHandle = null;
              openRrEditModal(rd);
              return;
            }
          }
        }
      }
    });

    window.addEventListener("mouseup", function(e) {
      if (isDraggingHandle) {
        isDraggingHandle = null;
        saveDrawingsForActiveSymbol();
      }
      if (activeDragDraw && drawingInProgress && drawingInProgress.step === 1 && (currentDrawTool === "trendline" || currentDrawTool === "ruler") && chart && candlestickSeries) {
        var dragSnapshot = activeDragDraw;
        activeDragDraw = null;
        if (!e || typeof e.clientX !== "number" || typeof e.clientY !== "number") return;
        var pt = resolveDrawingPointer(e);
        if (Math.hypot(pt.x - dragSnapshot.startX, pt.y - dragSnapshot.startY) >= 6) {
          completeTwoPointDrawing(pt);
        }
      } else {
        activeDragDraw = null;
      }
    });
  }

  // Hover detection for interactive elements (handles, close button, body)
  var chartContainerEl = document.getElementById("chartContainer");
  if (chartContainerEl && drawingCanvas) {
    chartContainerEl.addEventListener("mousemove", function(e) {
      if (currentDrawTool !== "cursor") {
        if (!drawingCanvas.classList.contains("interactive")) {
          drawingCanvas.classList.add("interactive");
        }
        drawingCanvas.classList.remove("cursor-pointer", "cursor-move", "cursor-ns-resize");
        return;
      }
      if (drawings.length === 0 && !isDraggingHandle) {
        if (drawingCanvas.classList.contains("interactive")) {
          drawingCanvas.classList.remove("interactive", "cursor-move", "cursor-ns-resize", "cursor-pointer");
        }
        return;
      }
      var rect = drawingCanvas.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var my = e.clientY - rect.top;
      var hoverState = "none";

      if (isDraggingHandle) {
        hoverState = isDraggingHandle.handle === "body" ? "body" : "handle";
      } else {
        for (var k = 0; k < drawings.length; k++) {
          var d = drawings[k];
          if (d.type === "risk-reward") {
            if (d.closeBtnX && d.closeBtnY && Math.hypot(mx - d.closeBtnX, my - d.closeBtnY) <= 14) {
              hoverState = "close";
              break;
            }
            if (d.boxLeft && d.boxRight && mx >= d.boxLeft && mx <= d.boxRight) {
              if (d.targetHandleY && Math.abs(my - d.targetHandleY) <= 10) { hoverState = "handle"; break; }
              if (d.stopHandleY && Math.abs(my - d.stopHandleY) <= 10) { hoverState = "handle"; break; }
              if (candlestickSeries) {
                var yEnt = candlestickSeries.priceToCoordinate(d.entryPrice);
                if (yEnt !== null && Math.abs(my - yEnt) <= 10) { hoverState = "handle"; break; }
              }
              var bMinY = Math.min(d.targetTop || 0, d.stopTop || 0);
              var bMaxY = Math.max((d.targetTop || 0) + (d.targetHeight || 0), (d.stopTop || 0) + (d.stopHeight || 0));
              if (my >= bMinY && my <= bMaxY) {
                hoverState = "body";
                break;
              }
            }
          }
        }
      }

      var isInteractive = hoverState !== "none";
      if (drawingCanvas.classList.contains("interactive") !== isInteractive) {
        drawingCanvas.classList.toggle("interactive", isInteractive);
      }
      drawingCanvas.classList.toggle("cursor-pointer", hoverState === "close");
      drawingCanvas.classList.toggle("cursor-ns-resize", hoverState === "handle");
      drawingCanvas.classList.toggle("cursor-move", hoverState === "body");
    });
  }

  var splitBtns = document.querySelectorAll ? document.querySelectorAll(".split-btn") : [];
  for (var sIdx = 0; sIdx < splitBtns.length; sIdx++) {
    (function(sb) {
      sb.onclick = function() {
        var layout = sb.getAttribute("data-layout") || "1x1";
        applySplitLayout(layout);
      };
    })(splitBtns[sIdx]);
  }

  var tfButtons = document.querySelectorAll ? document.querySelectorAll(".tf-btn") : [];
  for (var i = 0; i < tfButtons.length; i++) {
    (function(btn) {
      btn.onclick = function() {
        userChangedTimeframe = true;
        for (var j = 0; j < tfButtons.length; j++) tfButtons[j].classList.remove("active");
        btn.classList.add("active");
        activeTimeframe = btn.getAttribute("data-tf") || "1D";
        safeSetStorage("tv_active_timeframe", activeTimeframe);
        if (currentEngine === "C") {
          initTradingViewWidget(activeSymbol);
        } else {
          loadHistory(activeSymbol);
          loadBenchmarkHistory(rsBenchmarkTag);
        }
      };
    })(tfButtons[i]);
  }

  var langToggleBtn = document.getElementById("langToggleBtn");
  if (langToggleBtn) {
    langToggleBtn.onclick = function() {
      var nextLang = currentLang === "zh" ? "en" : "zh";
      setLanguage(nextLang);
    };
  }

  var themeToggleBtn = document.getElementById("themeToggleBtn");
  if (themeToggleBtn) {
    themeToggleBtn.onclick = function() {
      var nextTheme = currentTheme === "dark" ? "light" : "dark";
      setTheme(nextTheme);
    };
  }

  var rangeButtons = document.querySelectorAll ? document.querySelectorAll(".range-btn") : [];
  for (var r = 0; r < rangeButtons.length; r++) {
    (function(rbtn) {
      rbtn.onclick = function() {
        for (var y = 0; y < rangeButtons.length; y++) rangeButtons[y].classList.remove("active");
        rbtn.classList.add("active");
        var range = rbtn.getAttribute("data-range");
        applyDateRange(range);
      };
    })(rangeButtons[r]);
  }

  var scaleAuto = document.getElementById("scaleAuto");
  if (scaleAuto) {
    scaleAuto.onclick = function() {
      setAutoScale(!isAutoScale);
    };
  }

  var scaleLog = document.getElementById("scaleLog");
  var scalePercent = document.getElementById("scalePercent");
  if (scaleLog) {
    scaleLog.onclick = function() {
      setPriceScaleMode(currentPriceScaleMode === "log" ? "normal" : "log");
    };
  }
  if (scalePercent) {
    scalePercent.onclick = function() {
      setPriceScaleMode(currentPriceScaleMode === "percent" ? "normal" : "percent");
    };
  }

  var candleStyleBtn = document.getElementById("candleStyleBtn");
  if (candleStyleBtn) {
    candleStyleBtn.onclick = function() {
      currentCandleStyle = (currentCandleStyle === "solid") ? "hollow" : "solid";
      safeSetStorage("tv_candle_style", currentCandleStyle);
      updateCandleStyleUI();
      if (currentRawBars && currentRawBars.length > 0) {
        renderChartData(currentRawBars, { keepView: true });
      }
    };
    updateCandleStyleUI();
  }

  var pContainer = (typeof priceContainer !== "undefined" && priceContainer) ? priceContainer : (document.getElementById("chart") || chartContainer);
  if (pContainer) {
    pContainer.addEventListener("dblclick", handleChartDblClick);
    (function() {
      var dragStartX = 0;
      var dragStartY = 0;
      var dragActive = false;
      var autoScaleDisabledThisDrag = false;

      pContainer.addEventListener("pointerdown", function(e) {
        if (e.button !== 0) return;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        dragActive = true;
        autoScaleDisabledThisDrag = false;
      });

      pContainer.addEventListener("pointermove", function(e) {
        if (!dragActive || e.buttons !== 1 || autoScaleDisabledThisDrag) return;
        var dx = e.clientX - dragStartX;
        var dy = e.clientY - dragStartY;
        var toolState = (typeof currentDrawTool !== "undefined" && currentDrawTool) ? currentDrawTool : "cursor";
        var handleState = (typeof isDraggingHandle !== "undefined") ? Boolean(isDraggingHandle) : false;
        var state = {
          autoScale: (typeof isAutoScale !== "undefined") ? isAutoScale : true,
          tool: toolState,
          draggingHandle: handleState
        };
        if (shouldDisableAutoScaleOnDrag(dx, dy, state)) {
          autoScaleDisabledThisDrag = true;
          if (typeof setAutoScale === "function") {
            setAutoScale(false);
          }
        }
      });

      function onPointerEnd() {
        dragActive = false;
        autoScaleDisabledThisDrag = false;
        setTimeout(syncAutoScaleFromChart, 0);
      }

      pContainer.addEventListener("pointerup", onPointerEnd);
      pContainer.addEventListener("pointercancel", onPointerEnd);
      pContainer.addEventListener("mouseup", onPointerEnd);

      pContainer.addEventListener("wheel", function(e) {
        if (!pContainer.getBoundingClientRect) return;
        var rect = pContainer.getBoundingClientRect();
        var axisWidth = 0;
        if (chart && typeof chart.priceScale === "function") {
          try {
            var ps = chart.priceScale("right");
            if (ps && typeof ps.width === "function") {
              axisWidth = ps.width() || 0;
            }
          } catch (err) {
            axisWidth = 0;
          }
        }
        if (isPointInPriceAxis(e.clientX, rect, axisWidth)) {
          setTimeout(syncAutoScaleFromChart, 0);
        }
      }, { passive: true });
    })();
  }

  if (indicatorsBtn) {
    indicatorsBtn.onclick = function() {
      syncIndicatorCheckboxes();
      if (indicatorModal) indicatorModal.style.display = "flex";
    };
  }
  if (closeIndicatorModal) closeIndicatorModal.onclick = function() { if (indicatorModal) indicatorModal.style.display = "none"; if (typeof savePreferencesImmediate === "function") savePreferencesImmediate(); };
  if (closeIndicatorModalBottom) closeIndicatorModalBottom.onclick = function() { if (indicatorModal) indicatorModal.style.display = "none"; if (typeof savePreferencesImmediate === "function") savePreferencesImmediate(); };

  if (chkEma10) chkEma10.onchange = function(e) { indicatorConfig.ema10 = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  if (chkEma21) chkEma21.onchange = function(e) { indicatorConfig.ema21 = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  var chkEma30 = document.getElementById("chkEma30");
  if (chkEma30) chkEma30.onchange = function(e) { indicatorConfig.ema30 = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  var chkVwap = document.getElementById("chkVwap");
  if (chkVwap) chkVwap.onchange = function(e) { indicatorConfig.vwap = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  var chkAvwap = document.getElementById("chkAvwap");
  if (chkAvwap) chkAvwap.onchange = function(e) {
    indicatorConfig.avwap = e.target.checked;
    saveIndicatorConfig();
    applyIndicatorVisibilities();
  };
  var chkAtr = document.getElementById("chkAtr");
  if (chkAtr) chkAtr.onchange = function(e) {
    indicatorConfig.atr = e.target.checked;
    saveIndicatorConfig();
    applyIndicatorVisibilities();
    if (currentRawBars && currentRawBars.length) updateRsPane(currentRawBars, benchmarkRawBars);
  };
  if (chkSma50) chkSma50.onchange = function(e) { indicatorConfig.sma50 = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  if (chkSma200) chkSma200.onchange = function(e) { indicatorConfig.sma200 = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  if (chkVolSma) chkVolSma.onchange = function(e) { indicatorConfig.volSma = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  if (chkBollinger) chkBollinger.onchange = function(e) { indicatorConfig.bollinger = e.target.checked; saveIndicatorConfig(); applyIndicatorVisibilities(); };
  if (chkRsLine) chkRsLine.onchange = function(e) {
    indicatorConfig.rsLine = e.target.checked;
    saveIndicatorConfig();
    applyIndicatorVisibilities();
    if (currentRawBars && currentRawBars.length) updateRsPane(currentRawBars, benchmarkRawBars);
  };

  var btnToggleLegendCollapse = document.getElementById("btnToggleLegendCollapse");
  if (btnToggleLegendCollapse) {
    btnToggleLegendCollapse.onclick = function(e) {
      e.stopPropagation();
      indicatorConfig.collapseLegend = !indicatorConfig.collapseLegend;
      saveIndicatorConfig();
      applyIndicatorVisibilities();
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var chkShowLegend = document.getElementById("chkShowLegend");
  if (chkShowLegend) {
    chkShowLegend.onchange = function(e) {
      indicatorConfig.showLegend = e.target.checked;
      saveIndicatorConfig();
      applyIndicatorVisibilities();
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var chkCollapseLegend = document.getElementById("chkCollapseLegend");
  if (chkCollapseLegend) {
    chkCollapseLegend.onchange = function(e) {
      indicatorConfig.collapseLegend = e.target.checked;
      saveIndicatorConfig();
      applyIndicatorVisibilities();
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var chkShowDrawingToolbar = document.getElementById("chkShowDrawingToolbar");
  if (chkShowDrawingToolbar) {
    chkShowDrawingToolbar.onchange = function(e) {
      indicatorConfig.showDrawingToolbar = e.target.checked;
      saveIndicatorConfig();
      applyIndicatorVisibilities();
      if (typeof resizeAllCharts === "function") resizeAllCharts();
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var chkShowAnalysisTools = document.getElementById("chkShowAnalysisTools");
  if (chkShowAnalysisTools) {
    chkShowAnalysisTools.onchange = function(e) {
      indicatorConfig.showAnalysisTools = e.target.checked;
      saveIndicatorConfig();
      applyIndicatorVisibilities();
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var chkTvCompareRs = document.getElementById("chkTvCompareRs");
  if (chkTvCompareRs) {
    chkTvCompareRs.onchange = function(e) {
      indicatorConfig.tvCompareRs = e.target.checked;
      saveIndicatorConfig();
      if (currentEngine === "C" && typeof initTradingViewWidget === "function") {
        initTradingViewWidget(activeSymbol);
      }
      if (typeof savePreferencesImmediate === "function") savePreferencesImmediate();
    };
  }

  var btnModeMansfield = document.getElementById("btnModeMansfield");
  var btnModeDeepvue = document.getElementById("btnModeDeepvue");
  if (btnModeMansfield) {
    btnModeMansfield.onclick = function() {
      indicatorConfig.rsMode = "mansfield";
      btnModeMansfield.classList.add("active");
      if (btnModeDeepvue) btnModeDeepvue.classList.remove("active");
      saveIndicatorConfig();
      if (currentRawBars && currentRawBars.length) {
        updateRsPane(currentRawBars, benchmarkRawBars);
      for (var sp = 2; sp <= 4; sp++) {
        if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
          renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
        }
      }
      }
      for (var sp = 2; sp <= 4; sp++) {
        if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
          renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
        }
      }
    };
  }
  if (btnModeDeepvue) {
    btnModeDeepvue.onclick = function() {
      indicatorConfig.rsMode = "deepvue";
      btnModeDeepvue.classList.add("active");
      if (btnModeMansfield) btnModeMansfield.classList.remove("active");
      saveIndicatorConfig();
      if (currentRawBars && currentRawBars.length) {
        updateRsPane(currentRawBars, benchmarkRawBars);
      for (var sp = 2; sp <= 4; sp++) {
        if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
          renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
        }
      }
      }
      for (var sp = 2; sp <= 4; sp++) {
        if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
          renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
        }
      }
    };
  }

  // ==========================================================================
  // Anchored VWAP (AVWAP) TradingView Settings Modal Controller
  // ==========================================================================

  // Bind settings modal controls & tab navigation
  var tabBtnInputs = document.getElementById("tabBtnInputs");
  var tabBtnStyle = document.getElementById("tabBtnStyle");
  var tabBtnVisibility = document.getElementById("tabBtnVisibility");
  if (tabBtnInputs) tabBtnInputs.onclick = function() { switchAvwapTab("inputs"); };
  if (tabBtnStyle) tabBtnStyle.onclick = function() { switchAvwapTab("style"); };
  if (tabBtnVisibility) tabBtnVisibility.onclick = function() { switchAvwapTab("visibility"); };

  var btnAvwapSettings = document.getElementById("btnAvwapSettings");
  var btnOpenAvwapFromIndModal = document.getElementById("btnOpenAvwapFromIndModal");
  if (btnAvwapSettings) btnAvwapSettings.onclick = openAvwapSettingsModal;
  if (btnOpenAvwapFromIndModal) btnOpenAvwapFromIndModal.onclick = openAvwapSettingsModal;

  var closeAvwapSettingsModalBtn = document.getElementById("closeAvwapSettingsModal");
  var btnAvwapCancel = document.getElementById("btnAvwapCancel");
  if (closeAvwapSettingsModalBtn) closeAvwapSettingsModalBtn.onclick = closeAvwapSettingsModal;
  if (btnAvwapCancel) btnAvwapCancel.onclick = closeAvwapSettingsModal;

  var closeRrEditModalBtn = document.getElementById("closeRrEditModal");
  var btnRrEditCancel = document.getElementById("btnRrEditCancel");
  var btnRrEditApply = document.getElementById("btnRrEditApply");
  if (closeRrEditModalBtn) closeRrEditModalBtn.onclick = closeRrEditModal;
  if (btnRrEditCancel) btnRrEditCancel.onclick = closeRrEditModal;
  if (btnRrEditApply) btnRrEditApply.onclick = applyRrEdit;

  var rrEditInputIds = ["rrEditEntry", "rrEditStop", "rrEditTarget", "rrEditQty"];
  for (var rri = 0; rri < rrEditInputIds.length; rri++) {
    var rrInEl = document.getElementById(rrEditInputIds[rri]);
    if (rrInEl) {
      rrInEl.oninput = updateRrEditPreview;
      rrInEl.onkeydown = function(e) {
        if (e.key === "Enter") {
          e.preventDefault();
          var applyEl = document.getElementById("btnRrEditApply");
          if (applyEl && !applyEl.disabled) {
            applyRrEdit();
          }
        }
      };
    }
  }

  var btnAvwapOk = document.getElementById("btnAvwapOk");
  var btnAvwapResetDefaults = document.getElementById("btnAvwapResetDefaults");
  var btnParsePineScript = document.getElementById("btnParsePineScript");
  if (btnAvwapOk) btnAvwapOk.onclick = applyAvwapSettingsFromModal;
  if (btnAvwapResetDefaults) btnAvwapResetDefaults.onclick = resetAvwapSettingsToDefaults;
  if (btnParsePineScript) btnParsePineScript.onclick = parseAndApplyPineScript;

  var colAboveEl = document.getElementById("avwapColorAbove");
  var colBelowEl = document.getElementById("avwapColorBelow");
  var colBandEl = document.getElementById("avwapColorBand");
  var valColorAbove = document.getElementById("valColorAbove");
  var valColorBelow = document.getElementById("valColorBelow");
  var valColorBand = document.getElementById("valColorBand");
  if (colAboveEl && valColorAbove) {
    colAboveEl.oninput = function() { valColorAbove.textContent = colAboveEl.value + " (Teal)"; };
  }
  if (colBelowEl && valColorBelow) {
    colBelowEl.oninput = function() { valColorBelow.textContent = colBelowEl.value + " (Red)"; };
  }
  if (colBandEl && valColorBand) {
    colBandEl.oninput = function() { valColorBand.textContent = colBandEl.value; };
  }

  syncIndicatorCheckboxes();

  var engineSelect = document.getElementById("engineSelect");
  if (engineSelect) {
    engineSelect.value = currentEngine;
    engineSelect.onchange = function(e) {
      switchChartEngine(e.target.value);
    };
  }

  var rsToggleBtn = document.getElementById("rsToggleBtn");
  if (rsToggleBtn) {
    rsToggleBtn.onclick = function(e) {
      if (e && e.stopPropagation) e.stopPropagation();
      indicatorConfig.rsLine = !indicatorConfig.rsLine;
      saveIndicatorConfig();
      applyIndicatorVisibilities();
      if (indicatorConfig.rsLine && currentRawBars && currentRawBars.length > 0) {
        updateRsPane(currentRawBars, benchmarkRawBars);
      for (var sp = 2; sp <= 4; sp++) {
        if (typeof renderSubPaneRs === "function" && subCharts[sp] && subCharts[sp].bars) {
          renderSubPaneRs(sp, subCharts[sp].bars, benchmarkRawBars);
        }
      }
      }
      if (typeof resizeAllCharts === "function") resizeAllCharts();
    };
  }

  if (rsActiveBadge) {
    rsActiveBadge.onclick = function(e) {
      if (e && e.stopPropagation) e.stopPropagation();
      if (indicatorModal) indicatorModal.style.display = "flex";
    };
  }

  var rsPills = document.querySelectorAll ? document.querySelectorAll(".rs-pill") : [];
  for (var k = 0; k < rsPills.length; k++) {
    (function(pill) {
      pill.onclick = function() {
        var bench = pill.getAttribute("data-bench") || "SPY";
        setRsBenchmark(bench);
      };
    })(rsPills[k]);
  }

  if (applyRsBenchmarkBtn) {
    applyRsBenchmarkBtn.onclick = function() {
      if (rsBenchmarkCustomInput) {
        var tag = rsBenchmarkCustomInput.value.trim().toUpperCase();
        if (tag && /^[A-Z0-9:._\-]{1,24}$/.test(tag)) {
          setRsBenchmark(tag);
        }
      }
    };
  }

  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("visibilitychange", handleVisibilityChange);
  }
}

function gapBackfill(symbol) {
  var targetSym = symbol || activeSymbol;
  if (!targetSym) return;
  if (!ws || ws.readyState === 3) {
    connectWebSocket();
  }
  loadHistory(targetSym, activeTimeframe, true);
}

function handleVisibilityChange() {
  if (typeof document === "undefined") return;
  if (document.hidden) {
    isAppNapSuspended = true;
    if (!appNapHiddenAt) {
      appNapHiddenAt = Date.now();
    }
    if (rafRenderId) {
      if (typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(rafRenderId);
      }
      rafRenderId = null;
    }
  } else {
    isAppNapSuspended = false;
    var elapsedMs = appNapHiddenAt > 0 ? (Date.now() - appNapHiddenAt) : 0;
    appNapHiddenAt = 0;

    if (rafRenderId) {
      if (typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(rafRenderId);
      }
      rafRenderId = null;
    }
    var scheduleRaf = typeof requestAnimationFrame === "function"
      ? requestAnimationFrame
      : function(cb) { return setTimeout(cb, 0); };

    rafRenderId = scheduleRaf(function() {
      rafRenderId = null;
      if (currentRawBars && currentRawBars.length > 0) {
        renderChartDataKeepView(currentRawBars, currentRawBars.length);
      }
      hasPendingChartUpdate = false;
    });

    if (elapsedMs > 60000 && activeSymbol) {
      gapBackfill(activeSymbol);
    }
    requestFocusRecovery();
  }
}

if (typeof window !== "undefined") {
  window.gapBackfill = gapBackfill;
  window.recoverTerminalState = recoverTerminalState;
  window.openAiLensModal = openAiLensModal;
  window.fetchAiLens = fetchAiLens;
  window.handleVisibilityChange = handleVisibilityChange;
  window.addEventListener("focus", function() {
    requestFocusRecovery();
  });
  window.getAppNapState = function() {
    return {
      isAppNapSuspended: isAppNapSuspended,
      appNapHiddenAt: appNapHiddenAt,
      hasPendingChartUpdate: hasPendingChartUpdate
    };
  };
  window.executeSearch = executeSearch;
  window.selectAndSubscribeSymbol = selectAndSubscribeSymbol;
  window.subscribeSymbol = subscribeSymbol;
  window.switchSymbol = switchSymbol;
  window.openHeatmapModal = openHeatmapModal;
  window.fetchHeatmapData = fetchHeatmapData;
  window.renderHeatmap = renderHeatmap;
  window._addQuickPriceLine = _addQuickPriceLine;
  window.resetDrawToolToCursor = resetDrawToolToCursor;
  window.checkDrawingCoordinateShift = checkDrawingCoordinateShift;
  window.restorePriceLinesForSymbol = restorePriceLinesForSymbol;
  window.readStoredJson = readStoredJson;
  window.setPriceScaleMode = setPriceScaleMode;
  window.setAutoScale = setAutoScale;
}

function bootstrap() {
  var rawSymbol = null;
  try {
    if (typeof localStorage !== "undefined") {
      rawSymbol = localStorage.getItem("tv_active_symbol");
    }
  } catch (e) {}

  var symbolRegex = /^[A-Z0-9_]{1,15}:[A-Z0-9._\-\^=]{1,24}$/;
  if (rawSymbol !== null && rawSymbol !== undefined) {
    if (typeof rawSymbol === "string" && symbolRegex.test(rawSymbol)) {
      activeSymbol = rawSymbol;
    } else {
      try {
        if (typeof localStorage !== "undefined") {
          var bakSymKey = "tv_bak_tv_active_symbol";
          if (!localStorage.getItem(bakSymKey)) {
            localStorage.setItem(bakSymKey, String(rawSymbol));
          }
        }
      } catch (eB) {}
      console.warn("[storage] 無效的儲存設定 tv_active_symbol，已備份至 tv_bak_tv_active_symbol");
      activeSymbol = "NASDAQ:NVDA";
    }
  } else {
    activeSymbol = "NASDAQ:NVDA";
  }

  var rawTf = null;
  try {
    if (typeof localStorage !== "undefined") {
      rawTf = localStorage.getItem("tv_active_timeframe");
    }
  } catch (eTf) {}

  function normalizeTimeframe(tf) {
    if (typeof tf !== "string") return null;
    var s = tf.trim();
    if (s === "1m") return "1m";
    if (s === "1M") return null; // 1M (Month) is unsupported in terminal; falls back to 1D
    var upper = s.toUpperCase();
    if (upper === "1D") return "1D";
    if (upper === "1W") return "1W";
    var lower = s.toLowerCase();
    if (lower === "5m") return "5m";
    if (lower === "15m") return "15m";
    if (lower === "1h") return "1h";
    return null;
  }

  if (rawTf !== null && rawTf !== undefined) {
    var normTf = normalizeTimeframe(rawTf);
    if (normTf) {
      activeTimeframe = normTf;
    } else {
      try {
        if (typeof localStorage !== "undefined") {
          var bakTfKey = "tv_bak_tv_active_timeframe";
          if (!localStorage.getItem(bakTfKey)) {
            localStorage.setItem(bakTfKey, String(rawTf));
          }
        }
      } catch (eBt) {}
      console.warn("[storage] 無效的儲存設定 tv_active_timeframe，已備份至 tv_bak_tv_active_timeframe");
      activeTimeframe = "1D";
    }
  } else {
    var activeTfBtn = document.querySelector ? document.querySelector(".tf-btn.active") : null;
    if (activeTfBtn && activeTfBtn.getAttribute("data-tf")) {
      var candidateTf = normalizeTimeframe(activeTfBtn.getAttribute("data-tf"));
      activeTimeframe = candidateTf || "1D";
    } else {
      activeTimeframe = "1D";
    }
  }

  var allTfBtns = document.querySelectorAll ? document.querySelectorAll(".tf-btn") : [];
  if (allTfBtns.forEach) {
    allTfBtns.forEach(function(b) {
      if (b.getAttribute("data-tf") === activeTimeframe) b.classList.add("active");
      else b.classList.remove("active");
    });
  }

  window.addEventListener("beforeunload", savePreferencesImmediate);
  window.addEventListener("pagehide", savePreferencesImmediate);

  setTheme(currentTheme, true);
  initChart();
  if (typeof restorePriceLinesForSymbol === "function" && activeSymbol) {
    try {
      restorePriceLinesForSymbol(activeSymbol);
    } catch (ePriceLines) {
      console.warn("[bootstrap] 無法載入價格線", ePriceLines);
    }
  }
  initEventListeners();
  syncIndicatorCheckboxes();
  applyIndicatorVisibilities();
  updateCandleStyleUI();
  setLanguage(currentLang, true);

  if (symbolsList.length > 0) {
    buildInitialDOM();
  }

  whenBackendReady(function() {
    syncPreferencesFromBackend();

    fetch("/api/symbols")
      .then(function(res) {
        if (res.ok) return res.json();
        return { symbols: [] };
      })
      .then(function(data) {
        var freshSymbols = data.symbols || [];
        if (freshSymbols.length > 0) {
          symbolsList = freshSymbols;
          safeSetStorage("tv_symbols_cache", JSON.stringify(freshSymbols));
          for (var i = 0; i < freshSymbols.length; i++) {
            if (freshSymbols[i].state && freshSymbols[i].id) {
              symbolStates[freshSymbols[i].id] = Object.assign({}, symbolStates[freshSymbols[i].id] || {}, freshSymbols[i].state);
            }
          }
          buildInitialDOM();
          if (activeSymbol) {
            updateSymbolMetrics(activeSymbol, symbolStates[activeSymbol] || {}, latestBarData);
          }
        }
        if (data.status && statusBadge && statusText) {
          var st = String(data.status).toLowerCase();
          if (st === "connected") {
            statusBadge.className = "status-badge connected";
            statusText.textContent = "CONNECTED";
          }
        }
        if (!activeSymbol && symbolsList.length > 0) {
          switchSymbol(symbolsList[0].id);
        }
      })
      .catch(function(e) {});

    connectWebSocket();
    if (currentEngine === "C") {
      switchChartEngine("C");
    } else {
      if (typeof loadBenchmarkHistory === "function") {
        loadBenchmarkHistory(rsBenchmarkTag);
      }
      loadHistory(activeSymbol);
    }
  });
}

if (document.addEventListener) {
  document.addEventListener("DOMContentLoaded", bootstrap);
}

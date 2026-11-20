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
  var subCharts = {};
  var activeSplitLayout = "1x1";
  var focusedPaneId = 1;
  var mtfSyncEnabled = false;
  var paneSymbols = { 1: "NASDAQ:NVDA", 2: "AMEX:SPY", 3: "NASDAQ:QQQ", 4: "NASDAQ:FAST" };
  var paneTimeframes = { 1: "1D", 2: "1D", 3: "1D", 4: "1D" };

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

    if (!subCharts[paneNum]) {
      if (typeof LightweightCharts === "undefined") return;
      var subChart = LightweightCharts.createChart(container, {
        width: container.clientWidth || 380,
        height: container.clientHeight || 240,
        layout: {
          background: { color: "#131722" },
          textColor: "#9ca3af"
        },
        grid: {
          vertLines: { color: "#1e222d" },
          horzLines: { color: "#1e222d" }
        },
        timeScale: { borderColor: "#2a2e39", timeVisible: true }
      });
      var subSeries = subChart.addCandlestickSeries({
        upColor: "#26a69a",
        downColor: "#ef5350",
        borderVisible: false,
        wickUpColor: "#26a69a",
        wickDownColor: "#ef5350"
      });
      subCharts[paneNum] = { chart: subChart, series: subSeries };
    }

    var subObj = subCharts[paneNum];
    var url = "/api/history?symbol=" + encodeURIComponent(symbol) + "&timeframe=" + encodeURIComponent(tf);
    if (sessionToken) url += "&token=" + encodeURIComponent(sessionToken);

    fetch(url)
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) {
        if (paneSymbols[paneNum] !== symbol) return;
        if (d && Array.isArray(d.bars) && d.bars.length > 0) {
          subObj.series.setData(d.bars);
          subObj.chart.timeScale().fitContent();
        }
      })
      .catch(function() {});
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
        tauriInvoke("get_session_info")
          .then(function(session) {
            if (session && session.port) {
              window.initDesktopSession(session.port, session.token);
              if (typeof loadHistory === "function" && activeSymbol) {
                loadHistory(activeSymbol);
              }
            }
          })
          .catch(function(e) {});
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
  var lastCrosshairPrice = null;
  var magnetMode = (typeof localStorage !== "undefined" && localStorage.getItem("tv_magnet_mode") === "true");
  var isAlwaysOnTop = (typeof localStorage !== "undefined" && localStorage.getItem("tv_always_on_top") === "true");
  var tvPriceLineHandles = [];
  var currentDrawTool = "cursor";
  var drawings = [];
  var drawingInProgress = null;
  var isDraggingHandle = null;

  function loadDrawingsForSymbol(sym) {
    try {
      if (typeof localStorage !== "undefined" && sym) {
        var raw = localStorage.getItem("tv_drawings_" + sym);
        return raw ? JSON.parse(raw) : [];
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
    try {
      if (typeof localStorage !== "undefined") {
        var raw = localStorage.getItem("tv_symbols_cache");
        if (raw) {
          var parsed = JSON.parse(raw);
          if (Array.isArray(parsed) && parsed.length > 0) {
            // Auto-heal any stale NYSE:PRCH entries to official NASDAQ:PRCH
            var healed = false;
            for (var k = 0; k < parsed.length; k++) {
              if (parsed[k] && parsed[k].id === "NYSE:PRCH") {
                parsed[k].id = "NASDAQ:PRCH";
                parsed[k].exchange = "NASDAQ";
                healed = true;
              }
            }
            if (healed) {
              safeSetStorage("tv_symbols_cache", JSON.stringify(parsed));
            }
            return parsed;
          }
        }
      }
    } catch (e) {}
    return DEFAULT_WATCHLIST_SEEDS.slice();
  })();

  var activeSymbol = "BINANCE:BTCUSDT";
  var activeTimeframe = "1m";
  var rsBenchmarkTag = safeGetStorage("tv_rs_benchmark", "SPY");
  var currentCandleStyle = safeGetStorage("tv_candle_style", "solid");
  var isAutoScale = true;
  var symbolStates = {};
  var currentRawBars = [];
  var benchmarkRawBars = [];
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

  var activeHistoryRequestId = 0;
  var historyAbortController = null;
  var isHistoryLoading = false;
  var lastBarTime = 0;
  var lastBarObj = null;
  var latestBarData = null;

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
    showDrawingToolbar: false
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
          currentCandleStyle: currentCandleStyle,
          alwaysOnTop: isAlwaysOnTop,
          magnetMode: magnetMode
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
        currentCandleStyle: currentCandleStyle,
        alwaysOnTop: isAlwaysOnTop,
        magnetMode: magnetMode
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
          activeTimeframe = p.activeTimeframe;
        }
        if (p.currentCandleStyle && typeof p.currentCandleStyle === "string" && p.currentCandleStyle !== currentCandleStyle) {
          currentCandleStyle = p.currentCandleStyle;
          safeSetStorage("tv_candle_style", currentCandleStyle);
          if (typeof updateCandleStyleUI === "function") updateCandleStyleUI();
        }
        if (p.currentLang && typeof p.currentLang === "string" && p.currentLang !== currentLang) {
          currentLang = p.currentLang;
          safeSetStorage("tv_terminal_lang", currentLang);
          if (typeof setLanguage === "function") setLanguage(currentLang);
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
      brandSub: "Deepvue Pro Engine",
      connecting: "CONNECTING",
      connected: "CONNECTED",
      disconnected: "DISCONNECTED",
      reconnecting: "RECONNECTING",
      equityBannerText: "Equity Symbol: Bars accumulate in real time from server launch (market hours only).",
      watchlistTitle: "WATCHLIST",
      searchPlaceholder: "Search stock, crypto or ETF (e.g. AAPL, NVDA, SPY)...",
      indicatorsTitle: "Indicators & Overlays",
      rsBenchmarkLabel: "RS Benchmark:",
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
      noResults: "No matching symbols found.",
      done: "Done",
      setTag: "Set Tag",
      customTag: "+ Tag",
      engineTag: "Engine:",
      enginePro: "Deepvue Pro (Auto Dual-Source)",
      engineTv: "TradingView Official Widget"
    },
    zh: {
      langLabel: "EN",
      brandSub: "Deepvue 專業成長股引擎",
      connecting: "連線中",
      connected: "已連線",
      disconnected: "連線中斷",
      reconnecting: "重新連線中",
      equityBannerText: "美股標的：伺服器啟動後即時累積分K，盤中持續更新。",
      watchlistTitle: "自選清單",
      searchPlaceholder: "搜尋美股、加密貨幣或 ETF (如 AAPL, NVDA, SPY)...",
      indicatorsTitle: "技術指標與均線疊加",
      rsBenchmarkLabel: "RS 相對強度基準:",
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
      noResults: "查無相符標的",
      done: "完成",
      setTag: "設定",
      customTag: "+ 標籤",
      engineTag: "引擎:",
      enginePro: "Deepvue 專業圖表 (自動高可用)",
      engineTv: "TradingView 官方組件"
    }
  };

  var currentLang = (typeof localStorage !== "undefined" && localStorage.getItem("tv_terminal_lang")) ? localStorage.getItem("tv_terminal_lang") : "zh";
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
  var watchlistItemsEl = document.getElementById("watchlistItems");
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
    els.price.textContent = formatPrice(state.price, symbol);
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

  symbolStates[symbol] = Object.assign({}, symbolStates[symbol] || {}, quoteData);
  updateTapeItem(symbol, direction);
  updateWatchlistPrice(symbol, quoteData);

  if (symbol === activeSymbol) {
    updateSymbolMetrics(symbol, quoteData, latestBarData);
    if (quoteData && quoteData.price) {
      updateActiveCandleWithPrice(quoteData.price, quoteData.volume, quoteData.time);
    }
  }
}

function updateActiveCandleWithPrice(price, volume, tickTime) {
  if (!candleSeries || isHistoryLoading || !price || isNaN(price) || price <= 0) return;
  if (!currentRawBars || currentRawBars.length === 0) return;

  var lastIdx = currentRawBars.length - 1;
  var curBar = currentRawBars[lastIdx];
  if (!curBar || typeof curBar.time !== "number") return;

  var updatedOpen = curBar.open;
  var updatedHigh = Math.max(curBar.high, price);
  var updatedLow = Math.min(curBar.low, price);
  var updatedClose = price;
  var updatedVolume = (volume !== undefined && volume !== null && !isNaN(volume)) ? volume : (curBar.volume || 0);

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
        var vw = window.Indicators.calcVWAP(currentRawBars);
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

    updateCrosshairLegend(liveBar);
    updateDeepvueHud(currentRawBars);
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

  chart = LightweightCharts.createChart(priceContainer, {
    width: initialWidth,
    height: initialHeight,
    layout: {
      background: { color: "#131722" },
      textColor: "#787b86",
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    },
    grid: {
      vertLines: { color: "#1e222d" },
      horzLines: { color: "#1e222d" }
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {
        color: "#363c4e",
        width: 1,
        style: LightweightCharts.LineStyle.Dashed,
        labelBackgroundColor: "#2a2e39"
      },
      horzLine: {
        color: "#363c4e",
        width: 1,
        style: LightweightCharts.LineStyle.Dashed,
        labelBackgroundColor: "#2a2e39"
      }
    },
    rightPriceScale: {
      borderColor: "#2a2e39",
      scaleMargins: { top: 0.20, bottom: 0.15 },
      autoScale: true
    },
    timeScale: {
      borderColor: "#2a2e39",
      timeVisible: true,
      secondsVisible: false
    }
  });

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
    scaleMargins: { top: 0.8, bottom: 0 }
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
        background: { color: "#131722" },
        textColor: "#787b86",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
      },
      grid: {
        vertLines: { color: "#1e222d" },
        horzLines: { color: "#1e222d" }
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
          color: "#363c4e",
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: "#2a2e39"
        },
        horzLine: {
          color: "#363c4e",
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: "#2a2e39"
        }
      },
      rightPriceScale: {
        borderColor: "#2a2e39",
        scaleMargins: { top: 0.1, bottom: 0.1 }
      },
      timeScale: {
        borderColor: "#2a2e39",
        timeVisible: true,
        secondsVisible: false
      }
    });

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

    // Bidirectional range synchronization
    chart.timeScale().subscribeVisibleLogicalRangeChange(function(newRange) {
      if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
      if (isSyncingRange || !rsChart || !newRange) return;
      isSyncingRange = true;
      try {
        rsChart.timeScale().setVisibleLogicalRange(newRange);
      } catch (e) {}
      isSyncingRange = false;
    });

    rsChart.timeScale().subscribeVisibleLogicalRangeChange(function(newRange) {
      if (isSyncingRange || !chart || !newRange) return;
      isSyncingRange = true;
      try {
        chart.timeScale().setVisibleLogicalRange(newRange);
      } catch (e) {}
      isSyncingRange = false;
    });
  }

  chart.subscribeCrosshairMove(function(param) {
    if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
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
  if (currentEngine === "C") return;
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
  for (var p in subCharts) {
    var cEl = document.getElementById("splitChart" + p);
    if (cEl && subCharts[p] && subCharts[p].chart) {
      var sw = cEl.clientWidth;
      var sh = cEl.clientHeight;
      if (sw > 0 && sh > 0) {
        subCharts[p].chart.applyOptions({ width: sw, height: sh });
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
}

function updateCandleStyleUI() {
  var btn = document.getElementById("candleStyleBtn");
  if (!btn) return;
  if (currentCandleStyle === "hollow") {
    btn.textContent = "🕯️ 實空心";
    btn.classList.add("hollow");
    btn.setAttribute("title", "當前為實空心蠟燭 (點擊切換為傳統實心蠟燭)");
  } else {
    btn.textContent = "🕯️ 實心";
    btn.classList.remove("hollow");
    btn.setAttribute("title", "當前為實心蠟燭 (點擊切換為實空心蠟燭)");
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

  var rsPaneEl = document.getElementById("rsPane");
  var priceContainerEl = document.getElementById("chart");
  if (rsPaneEl) {
    rsPaneEl.style.display = indicatorConfig.rsLine ? "flex" : "none";
  }
  if (priceContainerEl && priceContainerEl.parentElement) {
    priceContainerEl.parentElement.style.height = indicatorConfig.rsLine ? "72%" : "100%";
    priceContainerEl.parentElement.style.flex = indicatorConfig.rsLine ? "0 0 72%" : "0 0 100%";
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

  calcAndRenderAvwap();
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

  if (legendOpenEl) legendOpenEl.textContent = formatPrice(bar.open, activeSymbol);
  if (legendHighEl) legendHighEl.textContent = formatPrice(bar.high, activeSymbol);
  if (legendLowEl) legendLowEl.textContent = formatPrice(bar.low, activeSymbol);
  if (legendCloseEl) legendCloseEl.textContent = formatPrice(bar.close, activeSymbol);
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
  if (activeSymbol === symbol && currentRawBars.length > 0) return;
  if (activeSymbol && typeof saveDrawingsForActiveSymbol === "function") {
    saveDrawingsForActiveSymbol();
  }
  activeSymbol = symbol;
  safeSetStorage("tv_active_symbol", symbol);
  if (typeof loadDrawingsForSymbol === "function") {
    drawings = loadDrawingsForSymbol(symbol);
    if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
  }
  isHistoryLoading = true;
  currentRawBars = [];

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

  renderWatchlist();
  if ((!benchmarkRawBars || benchmarkRawBars.length === 0) && typeof loadBenchmarkHistory === "function") {
    loadBenchmarkHistory(rsBenchmarkTag);
  }
  if (currentEngine === "C") {
    initTradingViewWidget(symbol);
  } else {
    loadHistory(symbol);
  }
}

function switchChartEngine(engineMode) {
  currentEngine = engineMode;
  safeSetStorage("tv_chart_engine", engineMode);
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
    var splitGrid = document.getElementById("splitChartGrid");
    if (splitGrid) splitGrid.style.display = "none";
    var chartWrapper = document.querySelector(".chart-wrapper");
    if (chartWrapper) chartWrapper.classList.remove("layout-split-1x2", "layout-split-2x2");
    if (tvWidgetContainer) {
      tvWidgetContainer.style.display = "block";
      initTradingViewWidget(activeSymbol);
    }
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

  var pairName = getSymbolDisplayName(symbol);
  if (legendSymbolEl) legendSymbolEl.textContent = pairName;
  if (omniboxDisplay) omniboxDisplay.textContent = pairName + " • " + (getSymbolItem(symbol) ? getSymbolItem(symbol).name : symbol);

  var tvSymbol = toTradingViewRealtimeSymbol(symbol);

  var tvInterval = ({ "1m": "1", "5m": "5", "15m": "15", "1h": "60", "60m": "60", "1D": "D", "1W": "W" })[activeTimeframe] || "D";

  // User specification: Scheme C TradingView widget exclusively loads 21 EMA, 30 EMA, 200 EMA, and RS Line (Compare vs SPY)
  var tvStudies = [];
  if (indicatorConfig.ema21 !== false) tvStudies.push({ id: "MAExp@tv-basicstudies", inputs: { length: 21 } });
  if (indicatorConfig.ema30 !== false) tvStudies.push({ id: "MAExp@tv-basicstudies", inputs: { length: 30 } });
  if (indicatorConfig.sma200 !== false || indicatorConfig.ema200 !== false) tvStudies.push({ id: "MAExp@tv-basicstudies", inputs: { length: 200 } });
  if (indicatorConfig.rsLine !== false) tvStudies.push({ id: "Compare@tv-basicstudies", inputs: { symbol: toTradingViewRealtimeSymbol(rsBenchmarkTag || "SPY") } });

  if (typeof TradingView !== "undefined" && TradingView.widget) {
    try {
      tvWidgetInstance = new TradingView.widget({
        autosize: true,
        symbol: tvSymbol,
        interval: tvInterval,
        timezone: "exchange",
        theme: "dark",
        style: "1",
        locale: currentLang === "zh" ? "zh_TW" : "en",
        toolbar_bg: "#131722",
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
          "moving average exponential.plot.color": "#a855f7",
          "moving average exponential.plot.linewidth": 2,
          "moving average.plot.color": "#eab308",
          "moving average.plot.linewidth": 2,
          "vwap.plot.color": "#26a69a",
          "vwap.plot.linewidth": 2,
          "compare.plot.color": "#3b82f6",
          "compare.plot.linewidth": 2
        },
        enabled_features: ["use_localstorage_for_settings"],
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

function updateRsPane(bars, benchBars) {
  if (!bars || bars.length === 0) return;
  if (!window.Indicators) return;

  var bBars = benchBars || benchmarkRawBars || [];
  if ((!bBars || bBars.length === 0) && typeof loadBenchmarkHistory === "function") {
    loadBenchmarkHistory(rsBenchmarkTag);
  }

  var currentMode = (indicatorConfig && indicatorConfig.rsMode) || "mansfield";

  var rsPaneTitleEl = document.getElementById("rsPaneTitle");
  if (rsPaneTitleEl) {
    var modeLabel = currentMode === "mansfield" ? "Mansfield 52" : "Deepvue Ratio";
    rsPaneTitleEl.textContent = "RS Line (" + modeLabel + ") vs " + (rsBenchmarkTag || "SPY");
  }

  // Calculate ATR Toolkit (ATR%, Squeeze, and Position Sizing)
  // Always calculated from target stock bars, independent of benchmark
  var toolkit = window.Indicators.calcATRToolkit(bars, bBars, {
    riskDollar: 500,
    stopMult: 2.0,
    mansfieldLen: 52
  });

  // 1. Update ATR% badge & Squeeze indicator
  var atrPctBadge = document.getElementById("rsAtrPctBadge");
  if (atrPctBadge && toolkit.atrPct && toolkit.atrPct.length) {
    var latestAtrPct = toolkit.atrPct[toolkit.atrPct.length - 1];
    var latestNum = typeof latestAtrPct.value === "number" ? latestAtrPct.value : (latestAtrPct.atrPct || 0);
    atrPctBadge.textContent = "ATR%: " + Number(latestNum).toFixed(2) + "%";
    if (latestAtrPct.isSqueeze) {
      atrPctBadge.className = "rs-metric-badge squeeze";
      atrPctBadge.title = "波動收縮 (Volatility Squeeze)";
    } else {
      atrPctBadge.className = "rs-metric-badge";
      atrPctBadge.title = "";
    }
  }

  // 2. Update Position Sizing (建議股數)
  var suggestedBadge = document.getElementById("rsSuggestedBadge");
  if (suggestedBadge && toolkit.positionSizing) {
    var shares = toolkit.positionSizing.suggestedShares;
    var stopD = toolkit.positionSizing.stopDistance;
    if (shares > 0) {
      suggestedBadge.textContent = "建議: " + shares + " 股 ($" + stopD + " 停損)";
    } else {
      suggestedBadge.textContent = "建議: -- 股";
    }
  }

  if (currentMode === "mansfield") {
    if (rsLinePaneSeries && toolkit.mansfieldRS && toolkit.mansfieldRS.length > 0) {
      if (typeof rsLinePaneSeries.applyOptions === "function") {
        rsLinePaneSeries.applyOptions({
          color: "#26a69a",
          lineWidth: 2,
          title: "Mansfield RS (52)"
        });
      }
      rsLinePaneSeries.setData(toolkit.mansfieldRS);

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
      rsEmaPaneSeries.setData(atrLineData);
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
        rsLinePaneSeries.setData(rsData);
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
        rsEmaPaneSeries.setData(rsEma);
      }
    }
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
}

function loadBenchmarkHistory(benchTag) {
  var benchSym = "AMEX:SPY";
  if (benchTag === "QQQ") benchSym = "NASDAQ:QQQ";
  else if (benchTag === "IWM") benchSym = "AMEX:IWM";
  else if (benchTag === "DIA") benchSym = "NYSE:DIA";
  else if (benchTag === "BTCUSDT") benchSym = "BINANCE:BTCUSDT";
  else if (benchTag && benchTag.indexOf(":") !== -1) benchSym = benchTag;
  else if (benchTag) benchSym = "NASDAQ:" + benchTag;

  var benchTf = activeTimeframe || "1D";
  var fetchUrl = "/api/history?seed=true&timeframe=" + encodeURIComponent(benchTf) + "&symbol=" + encodeURIComponent(benchSym);

  return fetch(fetchUrl)
    .then(function(res) {
      if (res.ok) return res.json();
      if (benchTf !== "1D") {
        var fbUrl = "/api/history?seed=true&timeframe=1D&symbol=" + encodeURIComponent(benchSym);
        return fetch(fbUrl).then(function(r2) { return r2.ok ? r2.json() : { bars: [] }; });
      }
      return { bars: [] };
    })
    .then(function(data) {
      benchmarkRawBars = data.bars || [];
      if (rsSeries && currentRawBars && currentRawBars.length > 0 && window.Indicators && benchmarkRawBars.length > 0) {
        var rsData = window.Indicators.calcRelativeStrength(currentRawBars, benchmarkRawBars);
        rsSeries.setData(rsData);
      }
      updateRsPane(currentRawBars, benchmarkRawBars);
    })
    .catch(function(e) {
      console.debug("Failed to load benchmark history:", e);
      benchmarkRawBars = [];
      updateRsPane(currentRawBars, []);
    });
}

function loadHistory(symbol, tf, isBackfill) {
  var targetTf = tf || activeTimeframe || "1m";
  var currentRequestId = ++activeHistoryRequestId;
  isHistoryLoading = true;

  if (historyAbortController) {
    historyAbortController.abort();
  }
  historyAbortController = new AbortController();

  if (equityBanner) {
    equityBanner.style.display = symbol.indexOf("NASDAQ:") === 0 ? "flex" : "none";
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
        renderChartData(uniqueBars);
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
      }
    })
    .catch(function(err) {
      if (err.name !== "AbortError") {
        // Log gracefully
      }
    })
    .finally(function() {
      if (currentRequestId === activeHistoryRequestId) {
        isHistoryLoading = false;
      }
    });
}

function renderChartData(bars) {
  if (!candleSeries || !bars || bars.length === 0) return;

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
      var vwapData = window.Indicators.calcVWAP(bars);
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

  if (chart && chart.timeScale) {
    chart.timeScale().fitContent();
  }
  if (typeof setPriceScaleMode === "function") {
    setPriceScaleMode(currentPriceScaleMode);
  }
}

// ==========================================================================
// Real-Time Bar Processing & Invariants Enforcement
// ==========================================================================

function updateDeepvueHud(bars) {
  if (!bars || bars.length === 0) return;
  var hud52wVal = document.getElementById("hud52wVal");
  var hudVol50Val = document.getElementById("hudVol50Val");
  var hudAdrVal = document.getElementById("hudAdrVal");

  var maxHigh = 0;
  var sumVol50 = 0;
  var volCount = 0;
  var sumRangePct = 0;
  var adrCount = Math.min(bars.length, 20);

  for (var i = 0; i < bars.length; i++) {
    if (bars[i].high > maxHigh) maxHigh = bars[i].high;
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

  if (hudVol50Val && volCount > 0) {
    var avgVol50 = sumVol50 / volCount;
    var ratio = avgVol50 > 0 ? (lastBar.volume / avgVol50) : 1;
    hudVol50Val.textContent = ratio.toFixed(2) + "x";
    hudVol50Val.className = "hud-val " + (ratio >= 1.5 ? "up" : "");
  }

  if (hudAdrVal && adrCount > 0) {
    var adrPct = (sumRangePct / adrCount) * 100;
    hudAdrVal.textContent = adrPct.toFixed(1) + "%";
  }
}

function handleLiveBar(symbol, bar) {
  if (isHistoryLoading) return;
  if (symbol !== activeSymbol || !candleSeries || !bar) return;

  var barTime = normalizeBarTime(bar.time);
  var open = safeNum(bar.open);
  var high = safeNum(bar.high);
  var low = safeNum(bar.low);
  var close = safeNum(bar.close);
  var volume = safeNum(bar.volume);

  if (barTime <= 0) return;
  if (isNaN(open) || isNaN(high) || isNaN(low) || isNaN(close) || close <= 0) return;

  var tfSec = TF_SECONDS[activeTimeframe] || 60;
  var lastRawBar = (currentRawBars.length > 0) ? currentRawBars[currentRawBars.length - 1] : null;

  if (activeTimeframe !== "1m" && tfSec > 60) {
    var bucketTime = barTime - (barTime % tfSec);
    if (lastRawBar) {
      var lastBarBucket = lastRawBar.time - (lastRawBar.time % tfSec);
      if (bucketTime === lastBarBucket || Math.abs(barTime - lastRawBar.time) < tfSec) {
        bucketTime = lastRawBar.time;
      }
    }
    if (lastRawBar && bucketTime < lastRawBar.time) {
      bucketTime = lastRawBar.time;
    }

    if (lastBarObj && bucketTime === lastBarObj.time) {
      open = lastBarObj.open;
      high = Math.max(lastBarObj.high, high, close);
      low = Math.min(lastBarObj.low, low, close);
      volume = Math.max(lastBarObj.volume || 0, volume);
    } else if (lastRawBar && bucketTime === lastRawBar.time) {
      open = lastRawBar.open;
      high = Math.max(lastRawBar.high, high, close);
      low = Math.min(lastRawBar.low, low, close);
      volume = Math.max(lastRawBar.volume || 0, volume);
    } else {
      open = close;
      high = close;
      low = close;
    }
    barTime = bucketTime;
  } else {
    if (lastRawBar && barTime < lastRawBar.time) {
      return;
    }
    if (lastBarTime !== null && barTime < lastBarTime) {
      return;
    }
  }

  lastBarTime = barTime;
  lastBarObj = { time: barTime, open: open, high: high, low: low, close: close, volume: volume };
  var liveBar = { time: barTime, open: open, high: high, low: low, close: close, volume: volume };

  if (currentRawBars.length > 0) {
    var lastIdx = currentRawBars.length - 1;
    if (currentRawBars[lastIdx].time === barTime) {
      currentRawBars[lastIdx] = liveBar;
    } else if (barTime > currentRawBars[lastIdx].time) {
      currentRawBars.push(liveBar);
      if (currentRawBars.length > 500) currentRawBars.shift();
    }
  }

  latestBarData = liveBar;
  lastBarTime = barTime;

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
      var vw = window.Indicators.calcVWAP(currentRawBars);
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

function updateSymbolMetrics(symbol, quote, bar) {
  if (symbol !== activeSymbol) return;

  var price = quote.price || (bar ? bar.close : 0);
  var ch = quote.change;
  var chp = quote.changePercentage;
  var vol = quote.volume || (bar ? bar.volume : 0);

  if (activePriceEl) activePriceEl.textContent = formatPrice(price, symbol);

  if (activeChangeEl) {
    if (chp !== undefined && chp !== null) {
      var sign = chp >= 0 ? "+" : "";
      activeChangeEl.textContent = sign + chp.toFixed(2) + "%";
      activeChangeEl.className = "metric-num " + (chp >= 0 ? "up" : "down");
    } else {
      activeChangeEl.textContent = "--";
    }
  }

  if (activeVolumeEl) activeVolumeEl.textContent = formatVolume(vol);

  var symItem = getSymbolItem(symbol);
  if (activeNameEl && symItem) activeNameEl.textContent = symItem.name;
  if (activeExchangeEl && symItem) activeExchangeEl.textContent = symItem.exchange;
}

// ==========================================================================
// Watchlist Sidebar Component
// ==========================================================================

function renderWatchlist() {
  if (!watchlistItemsEl) return;
  watchlistItemsEl.innerHTML = "";
  sidebarElements = {};

  var draggedItemIndex = null;

  for (var i = 0; i < symbolsList.length; i++) {
    (function(s, idx) {
      var state = symbolStates[s.id] || {};
      var div = document.createElement("div");
      div.className = "wl-item" + (s.id === activeSymbol ? " active" : "");
      div.id = "wl_" + s.id.replace(/[^a-zA-Z0-9]/g, "_");
      div.setAttribute("draggable", "true");
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
      priceSpan.textContent = formatPrice(state.price, s.id);

      var chp = safeNum(state.changePercentage, 0);
      var sign = chp >= 0 ? "+" : "";
      var chpSpan = document.createElement("span");
      chpSpan.className = "wl-chp " + (chp >= 0 ? "up" : "down");
      chpSpan.textContent = sign + chp.toFixed(2) + "%";

      colRight.appendChild(priceSpan);
      colRight.appendChild(chpSpan);

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
      div.appendChild(removeBtn);

      // HTML5 Drag & Drop Reordering Handlers (Safe for both Browser and Headless JSC)
      var onDragStart = function(e) {
        draggedItemIndex = idx;
        div.classList.add("dragging");
        if (e && e.dataTransfer) {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", String(idx));
        }
      };

      var onDragOver = function(e) {
        if (e && e.preventDefault) e.preventDefault();
        if (e && e.dataTransfer) e.dataTransfer.dropEffect = "move";
        div.classList.add("drag-over");
      };

      var onDragLeave = function() {
        div.classList.remove("drag-over");
      };

      var onDrop = function(e) {
        if (e && e.preventDefault) e.preventDefault();
        if (e && e.stopPropagation) e.stopPropagation();
        div.classList.remove("drag-over");
        var fromIdx = draggedItemIndex;
        if (fromIdx === null && e && e.dataTransfer) {
          var raw = e.dataTransfer.getData("text/plain");
          if (raw !== "") fromIdx = parseInt(raw, 10);
        }
        var toIdx = idx;
        if (fromIdx !== null && !isNaN(fromIdx) && fromIdx !== toIdx && fromIdx >= 0 && fromIdx < symbolsList.length && toIdx >= 0 && toIdx < symbolsList.length) {
          var moved = symbolsList.splice(fromIdx, 1)[0];
          symbolsList.splice(toIdx, 0, moved);
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
      };

      var onDragEnd = function() {
        draggedItemIndex = null;
        var allWl = watchlistItemsEl.querySelectorAll ? watchlistItemsEl.querySelectorAll(".wl-item") : [];
        for (var k = 0; k < allWl.length; k++) {
          allWl[k].classList.remove("dragging");
          allWl[k].classList.remove("drag-over");
        }
      };

      if (typeof div.addEventListener === "function") {
        div.addEventListener("dragstart", onDragStart);
        div.addEventListener("dragover", onDragOver);
        div.addEventListener("dragleave", onDragLeave);
        div.addEventListener("drop", onDrop);
        div.addEventListener("dragend", onDragEnd);
      } else {
        div.ondragstart = onDragStart;
        div.ondragover = onDragOver;
        div.ondragleave = onDragLeave;
        div.ondrop = onDrop;
        div.ondragend = onDragEnd;
      }

      div.onclick = function() {
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
        chp: chpSpan
      };
    })(symbolsList[i], i);
  }
}

var activeContextSymbol = null;
function openWlContextMenu(e, symbolId) {
  activeContextSymbol = symbolId;
  var menu = document.getElementById("wlContextMenu");
  if (!menu) return;
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
    els.price.textContent = formatPrice(quote.price, symbol);
  }
  if (quote.changePercentage !== undefined) {
    var chp = Number(quote.changePercentage);
    var sign = chp >= 0 ? "+" : "";
    els.chp.textContent = sign + chp.toFixed(2) + "%";
    els.chp.className = "wl-chp " + (chp >= 0 ? "up" : "down");
  }
}

// ==========================================================================
// Omnibox Search Modal & Dynamic Subscriptions
// ==========================================================================

  var searchDebounceTimer = null;

function executeSearch(query, filter) {
  if (filter === undefined) filter = "all";
  fetch("/api/symbols/search?q=" + encodeURIComponent(query))
    .then(function(res) {
      if (!res.ok) return { results: [] };
      return res.json();
    })
    .then(function(data) {
      var items = data.results || [];
      if (filter !== "all") {
        items = items.filter(function(it) { return it.type === filter; });
      }
      renderSearchResults(items);
    })
    .catch(function(e) {});
}

function renderSearchResults(items) {
  if (!searchResultsList) return;
  searchResultsList.innerHTML = "";

  if (!items || items.length === 0) {
    var emptyDiv = document.createElement("div");
    emptyDiv.style.cssText = "padding:20px;text-align:center;color:#787b86;";
    emptyDiv.textContent = "No matching symbols found. Press Enter to add custom ticker.";
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

function selectAndSubscribeSymbol(item) {
  closeSearch();
  var payload = {
    symbol: item.id || item.pair || "",
    id: item.id || item.pair || "",
    name: item.name || item.pair || "",
    exchange: item.exchange || "US",
    pair: item.pair || item.id || "",
    type: item.type || "stock"
  };
  fetch("/api/symbols/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(function(res) {
      if (res.ok) return res.json();
      return null;
    })
    .then(function(data) {
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
      }
    })
    .catch(function(e) {});

  if (mtfSyncEnabled) {
    syncMTF(item.id);
  } else if (focusedPaneId > 1 && subCharts[focusedPaneId]) {
    loadSubChart(focusedPaneId, item.id);
  } else {
    switchSymbol(item.id);
  }
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

function connectWebSocket() {
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
    reconnectAttempts = 0;
    if (statusBadge) statusBadge.className = "status-badge connected";
    if (statusText) statusText.textContent = "CONNECTED";
  };

  ws.onmessage = function(evt) {
    try {
      var msg = JSON.parse(evt.data);

      if (msg.type === "status") {
        if (statusBadge && statusText) {
          var st = String(msg.status || "").toLowerCase();
          if (st === "connected") {
            statusBadge.className = "status-badge connected";
            statusText.textContent = "CONNECTED";
          } else if (st === "connecting" || st === "initializing") {
            statusBadge.className = "status-badge connecting";
            statusText.textContent = st.toUpperCase();
          } else {
            statusBadge.className = "status-badge disconnected";
            statusText.textContent = "DISCONNECTED";
          }
        }
        return;
      }

      tickCount++;
      if (tickCounter) tickCounter.textContent = tickCount.toLocaleString();

      if (msg.type === "quote") {
        updateQuote(msg.symbol, msg.data || {});
      } else if (msg.type === "bar") {
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
          symbolsList = symbolsList.filter(function(s) { return s.id !== remSymId; });
          safeSetStorage("tv_symbols_cache", JSON.stringify(symbolsList));
          renderWatchlist();
        }
      }
    } catch (e) {}
  };

  ws.onclose = function() {
    if (statusBadge) statusBadge.className = "status-badge disconnected";
    if (statusText) statusText.textContent = "DISCONNECTED";
    scheduleReconnect();
  };

  ws.onerror = function() {
    if (ws) ws.close();
  };
}

// ==========================================================================
// Event Listeners & Bootstrapping
// ==========================================================================

function setLanguage(lang) {
  currentLang = lang;
  safeSetStorage("tv_terminal_lang", lang);
  var dict = i18n[lang] || i18n.en;

  var langLabelEl = document.getElementById("langLabel");
  if (langLabelEl) langLabelEl.textContent = dict.langLabel;

  var brandSubEl = document.querySelector(".brand-sub");
  if (brandSubEl) brandSubEl.textContent = dict.brandSub;

  var sidebarTitleEl = document.querySelector(".sidebar-title");
  if (sidebarTitleEl) sidebarTitleEl.textContent = dict.watchlistTitle;

  var searchInputEl = document.getElementById("searchInput");
  if (searchInputEl) searchInputEl.placeholder = dict.searchPlaceholder;

  var rsLabelEl = document.querySelector(".rs-label");
  if (rsLabelEl) rsLabelEl.textContent = dict.rsBenchmarkLabel;

  var hud52wLabel = document.getElementById("hud52wLabel");
  if (hud52wLabel) hud52wLabel.textContent = dict.hud52w;

  var hudVol50Label = document.getElementById("hudVol50Label");
  if (hudVol50Label) hudVol50Label.textContent = dict.hudVol50;

  var hudAdrLabel = document.getElementById("hudAdrLabel");
  if (hudAdrLabel) hudAdrLabel.textContent = dict.hudAdr;


  var modalTitleEl = document.querySelector(".modal-title");
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

function clampAndSetVisibleRange(range) {
  if (!chart || !currentRawBars || currentRawBars.length === 0) return;
  if (range === "ALL") {
    try { chart.timeScale().fitContent(); } catch (e) {}
    return;
  }
  var lastBar = currentRawBars[currentRawBars.length - 1];
  var now = lastBar.time;
  var from = now;
  if (range === "1D") from = now - 86400;
  else if (range === "5D") from = now - 86400 * 5;
  else if (range === "1M") from = now - 86400 * 30;
  else if (range === "3M") from = now - 86400 * 90;
  else if (range === "6M") from = now - 86400 * 180;
  else if (range === "YTD") from = now - 86400 * 250;
  else if (range === "1Y") from = now - 86400 * 365;

  var earliest = currentRawBars[0].time;
  if (from < earliest) from = earliest;
  if (from >= now) {
    try { chart.timeScale().fitContent(); } catch (e) {}
    return;
  }

  try {
    chart.timeScale().setVisibleRange({ from: from, to: now });
  } catch (e) {
    try { chart.timeScale().fitContent(); } catch (err) {}
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
      renderChartData(currentRawBars);
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

function fetchFundamentals(symbol, refresh) {
  if (!symbol) return;
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
  fetch(url)
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      var metrics = data.metrics || {};
      if (statusEl) {
        var updateTimeStr = data.updated_at ? new Date(data.updated_at * 1000).toLocaleTimeString() : "";
        statusEl.textContent = (data.cached ? "📦 快取報告" : "⚡ 最新診斷") + (updateTimeStr ? " (" + updateTimeStr + ")" : "");
      }
      if (modelTagEl) {
        modelTagEl.textContent = data.model_used || "gemini-3.8-flash";
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

function fetchAiLens(symbol, timeframe, refresh) {
  if (!symbol) return;
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
  fetch(url)
    .then(function(res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function(data) {
      var rep = data.report || {};
      var lens = rep.lens || {};
      var quant = lens.quant_metrics || {};
      var decision = rep.decision || {};

      if (statusEl) {
        var updateTimeStr = rep.updated_at ? new Date(rep.updated_at * 1000).toLocaleTimeString() : "";
        statusEl.textContent = (data.cached ? "📦 快取快照" : "⚡ 最新分析") + (updateTimeStr ? " (" + updateTimeStr + ")" : "");
      }
      if (modelNoticeEl) {
        modelNoticeEl.textContent = "Model: " + (rep.model_used || "gemini-3.8-flash");
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
      if (statusEl) statusEl.textContent = "讀取失敗: " + err.message;
      if (setupEl) setupEl.textContent = "無法取得 AI Agent Lens 數據，請確認後端服務正常。";
    });
}

/**
 * Unified State & History Recovery Engine
 * Recovers terminal state and heals candle history gaps.
 */
function recoverTerminalState(options) {
  var opts = options || {};
  var sym = opts.symbol || activeSymbol || "BINANCE:BTCUSDT";
  var tf = opts.timeframe || activeTimeframe || "1D";
  var activeBars = (sym === activeSymbol && Array.isArray(currentRawBars)) ? currentRawBars : [];
  var sinceTime = opts.sinceTime || (activeBars.length ? activeBars[activeBars.length - 1].time : null);

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

      if (Array.isArray(data.watchlist) && data.watchlist.length > 0) {
        symbolsList = data.watchlist;
        renderWatchlist();
      }

      if (Array.isArray(data.bars) && data.bars.length > 0) {
        if (activeSymbol === sym) {
          currentRawBars = data.bars;
          if (currentEngine !== "C") renderChartData(currentRawBars);
        }
      } else if (Array.isArray(data.delta_bars) && data.delta_bars.length > 0 && activeSymbol === sym) {
        if (!Array.isArray(currentRawBars)) currentRawBars = [];
        for (var i = 0; i < data.delta_bars.length; i++) {
          var db = data.delta_bars[i];
          var lastIdx = currentRawBars.length - 1;
          if (lastIdx >= 0 && currentRawBars[lastIdx].time === db.time) {
            currentRawBars[lastIdx] = db;
          } else if (lastIdx < 0 || currentRawBars[lastIdx].time < db.time) {
            currentRawBars.push(db);
          }
        }
        if (currentEngine !== "C") renderChartData(currentRawBars);
      }

      var refTicker = rsBenchmarkTag || "SPY";
      var isCrypto = /^(BINANCE|COINBASE|BYBIT|OKX):/i.test(sym);
      var benchSym = refTicker.indexOf(":") !== -1 ? refTicker : (isCrypto ? "BINANCE:BTCUSDT" : "AMEX:" + refTicker);
      if (indicatorConfig && indicatorConfig.rsLine && activeSymbol === sym) {
        fetch("/api/history?symbol=" + encodeURIComponent(benchSym) + "&timeframe=" + encodeURIComponent(tf))
          .then(function(r) { return r.ok ? r.json() : null; })
          .then(function(bData) {
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

// ==========================================================================
// Institutional Stock Screener Controller (RS + ATR + CANSLIM Engine)
// ==========================================================================

var screenerModal = null;
var isScreenerDocked = false;
var lastScreenerResults = [];
var lastBenchmarkStatus = { symbol: "SPY", close: 0, regime: "NEUTRAL" };
var currentScreenerSort = { col: "rs_rating", asc: false };

var activeMinRevGrowth = null;

var SCREENER_PRESET_CONFIGS = {
  stage2_vcp: {
    min_rs_rating: 80,
    mansfield_above_zero: true,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    atr_squeeze_only: true,
    max_atr_percentile: 20.0,
    max_pct_from_52w_high: 15.0,
    min_volume_ratio: 1.1,
    min_rev_growth_yoy: null
  },
  rs_leaders: {
    min_rs_rating: 85,
    mansfield_above_zero: true,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: true,
    atr_squeeze_only: false,
    max_atr_percentile: 30.0,
    max_pct_from_52w_high: 20.0,
    min_volume_ratio: 1.2,
    min_rev_growth_yoy: null
  },
  canslim_growth: {
    min_rs_rating: 75,
    mansfield_above_zero: true,
    stage2_only: true,
    weekly_stage2_only: false,
    power_trend_only: false,
    atr_squeeze_only: false,
    max_atr_percentile: 30.0,
    max_pct_from_52w_high: 20.0,
    min_volume_ratio: 1.0,
    min_rev_growth_yoy: 20.0
  },
  low_vol_base: {
    min_rs_rating: 65,
    mansfield_above_zero: false,
    stage2_only: false,
    weekly_stage2_only: false,
    power_trend_only: false,
    atr_squeeze_only: true,
    max_atr_percentile: 15.0,
    max_pct_from_52w_high: 30.0,
    min_volume_ratio: 0.5,
    min_rev_growth_yoy: null
  }
};

function openScreenerModal() {
  screenerModal = document.getElementById("screenerModal");
  if (!screenerModal) return;
  screenerModal.style.display = "flex";
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
  var cfg = SCREENER_PRESET_CONFIGS[presetId];
  if (!cfg) return;

  var rsInput = document.getElementById("scMinRsRating");
  var mansfieldChk = document.getElementById("scMansfieldZero");
  var stage2Chk = document.getElementById("scStage2Only");
  var weeklyStage2Chk = document.getElementById("scWeeklyStage2Only");
  var powerTrendChk = document.getElementById("scPowerTrendOnly");
  var squeezeChk = document.getElementById("scAtrSqueezeOnly");
  var maxAtrPct = document.getElementById("scMaxAtrPercentile");
  var maxHighPct = document.getElementById("scMaxPctFromHigh");
  var minVol = document.getElementById("scMinVolRatio");

  if (rsInput) rsInput.value = cfg.min_rs_rating;
  if (mansfieldChk) mansfieldChk.checked = Boolean(cfg.mansfield_above_zero);
  if (stage2Chk) stage2Chk.checked = Boolean(cfg.stage2_only);
  if (weeklyStage2Chk) weeklyStage2Chk.checked = Boolean(cfg.weekly_stage2_only);
  if (powerTrendChk) powerTrendChk.checked = Boolean(cfg.power_trend_only);
  if (squeezeChk) squeezeChk.checked = Boolean(cfg.atr_squeeze_only);
  if (maxAtrPct) maxAtrPct.value = cfg.max_atr_percentile;
  if (maxHighPct) maxHighPct.value = cfg.max_pct_from_52w_high;
  if (minVol) minVol.value = cfg.min_volume_ratio;
  activeMinRevGrowth = (cfg.min_rev_growth_yoy !== undefined && cfg.min_rev_growth_yoy !== null) ? cfg.min_rev_growth_yoy : null;

  var pills = document.querySelectorAll(".screener-preset-pill");
  pills.forEach(function(p) {
    if (p.getAttribute("data-preset") === presetId) p.classList.add("active");
    else p.classList.remove("active");
  });

  if (presetId !== "custom") {
    var universeSel = document.getElementById("screenerUniverseSelect");
    if (universeSel) universeSel.value = "us_momentum";
    var sWrap = document.getElementById("screenerSearchWrap");
    if (sWrap) sWrap.style.display = "none";
    var cIn = document.getElementById("screenerCustomInput");
    if (cIn) cIn.style.display = "none";
  }

  runStockScreen();
}

function runStockScreen() {
  var scanBtn = document.getElementById("runScreenerScanBtn");
  var statsBadge = document.getElementById("screenerStatsBadge");
  var benchBadge = document.getElementById("screenerBenchBadge");
  var emptyState = document.getElementById("screenerEmptyState");
  var tableBody = document.getElementById("screenerTableBody");

  if (scanBtn) {
    scanBtn.disabled = true;
    scanBtn.textContent = "⏳ 篩選計算中...";
  }
  if (statsBadge) statsBadge.textContent = "正在量化計算 (Semaphore=8)...";

  var universeSel = document.getElementById("screenerUniverseSelect");
  var universeType = universeSel ? universeSel.value : "us_momentum";
  var customInput = document.getElementById("screenerCustomInput");
  var customSyms = (universeType === "custom" && customInput) ? customInput.value : "";

  if (universeType === "custom" && (!customSyms || !customSyms.trim())) {
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
  var mansfieldChk = document.getElementById("scMansfieldZero");
  var stage2Chk = document.getElementById("scStage2Only");
  var weeklyStage2Chk = document.getElementById("scWeeklyStage2Only");
  var powerTrendChk = document.getElementById("scPowerTrendOnly");
  var squeezeChk = document.getElementById("scAtrSqueezeOnly");
  var maxAtrPct = document.getElementById("scMaxAtrPercentile");
  var maxHighPct = document.getElementById("scMaxPctFromHigh");
  var minVol = document.getElementById("scMinVolRatio");
  var riskInput = document.getElementById("scRiskDollar");

  var riskDollar = riskInput ? (parseFloat(riskInput.value) || 500.0) : 500.0;

  var payload = {
    universe: universeType,
    custom_symbols: customSyms,
    risk_dollar: riskDollar,
    criteria: {
      min_rs_rating: rsInput ? parseInt(rsInput.value, 10) : 80,
      mansfield_above_zero: mansfieldChk ? mansfieldChk.checked : true,
      stage2_only: stage2Chk ? stage2Chk.checked : true,
      weekly_stage2_only: weeklyStage2Chk ? weeklyStage2Chk.checked : false,
      power_trend_only: powerTrendChk ? powerTrendChk.checked : false,
      atr_squeeze_only: squeezeChk ? squeezeChk.checked : true,
      max_atr_percentile: maxAtrPct ? parseFloat(maxAtrPct.value) : 20.0,
      max_pct_from_52w_high: maxHighPct ? parseFloat(maxHighPct.value) : 15.0,
      min_volume_ratio: minVol ? parseFloat(minVol.value) : 1.1,
      min_rev_growth_yoy: activeMinRevGrowth,
      risk_dollar: riskDollar,
      stop_atr_mult: 2.0,
      mansfield_length: 252
    }
  };

  fetch("/api/screener/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
  .then(function(res) {
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  })
  .then(function(data) {
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
    if (statsBadge) statsBadge.textContent = "篩選異常: " + err.message;
    if (tableBody) tableBody.innerHTML = "";
    if (emptyState) emptyState.style.display = "flex";
  })
  .finally(function() {
    if (scanBtn) {
      scanBtn.disabled = false;
      scanBtn.textContent = "🚀 開始篩選";
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
    var tr = document.createElement("tr");
    tr.className = "screener-row" + (item.id === activeSymbol ? " active-row" : "");
    tr.setAttribute("data-symbol", item.id);

    var chgClass = (item.change_pct || 0) >= 0 ? "val-green" : "val-red";
    var chgSign = (item.change_pct || 0) >= 0 ? "+" : "";
    var mRsClass = (item.mansfield_rs || 0) >= 0 ? "val-green" : "val-red";
    var mRsSign = (item.mansfield_rs || 0) >= 0 ? "+" : "";

    var regimeBadgeClass = "regime-neutral";
    if (item.trend_regime === "STAGE_2_STRONG_UPTREND") regimeBadgeClass = "regime-stage2";
    else if (item.trend_regime === "STAGE_2_UPTREND_PULLBACK") regimeBadgeClass = "regime-pullback";
    else if (item.trend_regime === "STAGE_4_DOWNTREND") regimeBadgeClass = "regime-downtrend";

    var squeezeBadge = item.is_squeeze ? '<span class="badge-squeeze-tag">SQUEEZE</span>' : '';
    var breakoutBadge = item.is_breakout ? '<span class="badge-breakout-tag">BREAKOUT</span>' : '';
    var powerTrendBadge = item.power_trend ? '<span class="badge-breakout-tag" style="background:rgba(56,189,248,0.15);border-color:#38bdf8;color:#38bdf8;">POWER</span>' : '';

    var isAlreadyInWl = symbolsList.some(function(s) { return s.id === item.id; });
    var btnText = isAlreadyInWl ? "✓ 已加入" : "+ 自選";
    var btnClass = "btn-table-add" + (isAlreadyInWl ? " added" : "");

    // 1. Symbol
    var tdSym = document.createElement("td");
    tdSym.className = "sym-col";
    tdSym.textContent = item.symbol || "";
    tr.appendChild(tdSym);

    // 2. Name
    var tdName = document.createElement("td");
    tdName.className = "name-col";
    tdName.title = item.name || "";
    tdName.textContent = item.name || "";
    tr.appendChild(tdName);

    // 3. Price
    var tdPrice = document.createElement("td");
    tdPrice.style.fontFamily = "var(--font-mono, monospace)";
    tdPrice.style.fontWeight = "600";
    tdPrice.textContent = item.close ? "$" + item.close.toFixed(2) : "--";
    tr.appendChild(tdPrice);

    // 4. 24H Change%
    var tdChg = document.createElement("td");
    tdChg.className = chgClass;
    tdChg.textContent = chgSign + (item.change_pct !== undefined ? item.change_pct.toFixed(2) : "0.00") + "%";
    tr.appendChild(tdChg);

    // 5. Trend Regime
    var tdRegime = document.createElement("td");
    var regimeSpan = document.createElement("span");
    regimeSpan.className = "badge-regime " + regimeBadgeClass;
    regimeSpan.textContent = item.trend_regime || "NEUTRAL";
    tdRegime.appendChild(regimeSpan);
    if (item.power_trend) {
      tdRegime.appendChild(document.createTextNode(" "));
      var powerSpan = document.createElement("span");
      powerSpan.className = "badge-breakout-tag";
      powerSpan.style.background = "rgba(56,189,248,0.15)";
      powerSpan.style.borderColor = "#38bdf8";
      powerSpan.style.color = "#38bdf8";
      powerSpan.textContent = "POWER";
      tdRegime.appendChild(powerSpan);
    }
    if (item.weekly_stage2) {
      tdRegime.appendChild(document.createTextNode(" "));
      var wSpan = document.createElement("span");
      wSpan.className = "badge-breakout-tag";
      wSpan.style.background = "rgba(16,185,129,0.15)";
      wSpan.style.borderColor = "#10b981";
      wSpan.style.color = "#10b981";
      wSpan.textContent = "1W S2";
      tdRegime.appendChild(wSpan);
    }
    tr.appendChild(tdRegime);

    // 6. Mansfield RS
    var tdMRs = document.createElement("td");
    tdMRs.className = mRsClass;
    tdMRs.textContent = mRsSign + (item.mansfield_rs !== undefined ? item.mansfield_rs.toFixed(2) : "--");
    tr.appendChild(tdMRs);

    // 7. RS Rating
    var tdRsRating = document.createElement("td");
    var rsRatingSpan = document.createElement("span");
    if (item.rs_rating >= 80) rsRatingSpan.className = "badge-rs-high";
    rsRatingSpan.textContent = item.rs_rating !== undefined ? String(item.rs_rating) : "--";
    tdRsRating.appendChild(rsRatingSpan);
    tr.appendChild(tdRsRating);

    // 8. ATR% & Badges
    var tdAtr = document.createElement("td");
    var atrText = (item.atr_pct !== undefined ? item.atr_pct.toFixed(2) + "%" : "--") +
      " (" + (item.atr_percentile !== undefined ? item.atr_percentile.toFixed(0) + "%" : "--") + ")";
    tdAtr.textContent = atrText;
    if (item.is_squeeze) {
      tdAtr.appendChild(document.createTextNode(" "));
      var sqSpan = document.createElement("span");
      sqSpan.className = "badge-squeeze-tag";
      sqSpan.textContent = "SQUEEZE";
      tdAtr.appendChild(sqSpan);
    }
    if (item.is_breakout) {
      tdAtr.appendChild(document.createTextNode(" "));
      var boSpan = document.createElement("span");
      boSpan.className = "badge-breakout-tag";
      boSpan.textContent = "BREAKOUT";
      tdAtr.appendChild(boSpan);
    }
    tr.appendChild(tdAtr);

    // 9. 52W High Pct
    var tdPct52w = document.createElement("td");
    tdPct52w.style.fontFamily = "var(--font-mono, monospace)";
    tdPct52w.textContent = item.pct_from_52w_high !== undefined ? item.pct_from_52w_high.toFixed(2) + "%" : "--";
    tr.appendChild(tdPct52w);

    // 10. Volume Ratio 50D
    var tdVolRatio = document.createElement("td");
    tdVolRatio.style.fontFamily = "var(--font-mono, monospace)";
    tdVolRatio.textContent = item.volume_ratio_50 !== undefined ? item.volume_ratio_50.toFixed(2) + "x" : "--";
    tr.appendChild(tdVolRatio);

    // 11. Suggested Shares
    var tdShares = document.createElement("td");
    tdShares.className = "shares-col cell-suggested-shares";
    tdShares.setAttribute("data-atr", String(item.atr || 0));
    tdShares.textContent = (item.suggested_shares || 0) + " 股";
    tr.appendChild(tdShares);

    // 12. Actions
    var tdAction = document.createElement("td");
    var addBtn = document.createElement("button");
    addBtn.className = btnClass;
    addBtn.setAttribute("data-id", item.id);
    addBtn.textContent = btnText;
    tdAction.appendChild(addBtn);
    tr.appendChild(tdAction);

    tr.onclick = function() {
      document.querySelectorAll(".screener-table tr.screener-row").forEach(function(r) {
        r.classList.remove("active-row");
      });
      tr.classList.add("active-row");
      switchSymbol(item.id);
    };

    var addBtn = tr.querySelector(".btn-table-add");
    if (addBtn && !isAlreadyInWl) {
      addBtn.onclick = function(e) {
        e.stopPropagation();
        selectAndSubscribeSymbol({
          id: item.id,
          pair: item.symbol,
          name: item.name,
          exchange: item.id.indexOf(":") !== -1 ? item.id.split(":")[0] : "NASDAQ",
          type: "stock"
        });
        addBtn.textContent = "✓ 已加入";
        addBtn.classList.add("added");
      };
    }

    tableBody.appendChild(tr);
  });
}

function onRiskDollarChanged() {
  var riskInput = document.getElementById("scRiskDollar");
  var risk = riskInput ? (parseFloat(riskInput.value) || 500.0) : 500.0;
  if (risk <= 0) risk = 500.0;

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
      var stopDist = (it.atr && it.atr > 0) ? (it.atr * 2.0) : 0;
      it.suggested_shares = stopDist > 0 ? Math.floor(risk / stopDist) : 0;
    }
  }

  var cells = document.querySelectorAll(".cell-suggested-shares");
  cells.forEach(function(td) {
    var atr = parseFloat(td.getAttribute("data-atr"));
    if (atr && atr > 0) {
      var stopDist = atr * 2.0;
      var shares = Math.floor(risk / stopDist);
      td.textContent = shares + " 股";
    } else {
      td.textContent = "0 股";
    }
  });
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
  var currentRisk = riskInput ? (parseFloat(riskInput.value) || 500.0) : 500.0;

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
  if (/^[=+\-@\t\r]/.test(str)) str = "'" + str;
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
    var c = item.close ? Number(item.close) : 0.0;
    var stopDist = (item.stop_distance !== undefined && item.stop_distance !== null)
      ? Number(item.stop_distance)
      : ((item.atr && item.atr > 0) ? Number(item.atr) * 2.0 : 0.0);
    var stopPrice = Math.max(0.0, c - stopDist);
    var shares = item.suggested_shares ? Number(item.suggested_shares) : 0;
    var posVal = shares * c;

    var row = [
      escapeCsvField(item.symbol || ""),
      escapeCsvField(item.name || ""),
      escapeCsvField(item.sector || "Equity"),
      escapeCsvField(Number(c.toFixed(2))),
      escapeCsvField((item.change_pct !== undefined ? item.change_pct.toFixed(2) : "0.00") + "%"),
      escapeCsvField(item.trend_regime || "NEUTRAL"),
      escapeCsvField(item.weekly_stage2 ? "TRUE" : "FALSE"),
      escapeCsvField(Number((item.mansfield_rs !== undefined ? item.mansfield_rs : 0.0).toFixed(2))),
      escapeCsvField(Number(item.rs_rating !== undefined ? item.rs_rating : 0)),
      escapeCsvField((item.atr_pct !== undefined ? item.atr_pct.toFixed(2) : "0.00") + "%"),
      escapeCsvField((item.atr_percentile !== undefined ? item.atr_percentile.toFixed(1) : "50.0") + "%"),
      escapeCsvField(item.is_squeeze ? "YES" : "NO"),
      escapeCsvField(Number(stopDist.toFixed(2))),
      escapeCsvField(Number(stopPrice.toFixed(2))),
      escapeCsvField(shares),
      escapeCsvField(Number(posVal.toFixed(2))),
      escapeCsvField((item.volume_ratio_50 !== undefined ? item.volume_ratio_50.toFixed(2) : "1.00") + "x"),
      escapeCsvField(item.revenue_yoy || "N/A")
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
    chkShowDrawingToolbar: Boolean(indicatorConfig.showDrawingToolbar)
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
  if (scRiskIn) scRiskIn.oninput = onRiskDollarChanged;

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
        fetch("/api/symbols/search?q=" + encodeURIComponent(currentTerm))
          .then(function(res) { return res.json(); })
          .then(function(data) {
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
          .catch(function() {
            screenerAcDropdown.style.display = "none";
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
  function _addQuickPriceLine(isAlert) {
    if (!candleSeries || !window.LightweightCharts) return;
    var p = (typeof lastCrosshairPrice === "number" && isFinite(lastCrosshairPrice)) ? lastCrosshairPrice : (latestBarData ? latestBarData.close : null);
    if (p === null) return;
    var line = candleSeries.createPriceLine({
      price: p,
      color: isAlert ? "#f59e0b" : "#3b82f6",
      lineWidth: 1,
      lineStyle: isAlert ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: isAlert ? "ALERT" : "LVL"
    });
    tvPriceLineHandles.push(line);
  }
  var btnMagnet = document.getElementById("btnMagnet");
  if (btnMagnet) {
    btnMagnet.onclick = function() {
      magnetMode = !magnetMode;
      btnMagnet.classList.toggle("active", magnetMode);
    };
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
    if ((e.metaKey || e.ctrlKey) && k === "k") {
      e.preventDefault(); openSearch();
    } else if (e.key === "Escape") {
      closeSearch();
      if (indicatorModal) indicatorModal.style.display = "none";
      if (fundamentalsModal) fundamentalsModal.style.display = "none";
      if (aiLensModal) aiLensModal.style.display = "none";
      closeScreenerModal(); closeChatModal();
      if (currentDrawTool !== "cursor" || drawingInProgress) {
        currentDrawTool = "cursor";
        drawingInProgress = null;
        var allDrawBtns = document.querySelectorAll ? document.querySelectorAll(".draw-tool-btn[data-tool]:not(.draw-tool-toggle)") : [];
        for (var dti = 0; dti < allDrawBtns.length; dti++) {
          allDrawBtns[dti].classList.toggle("active", allDrawBtns[dti].getAttribute("data-tool") === "cursor");
        }
        var dwc = document.getElementById("drawingCanvas");
        if (dwc) dwc.classList.remove("drawing-active");
        if (typeof renderDrawingCanvas === "function") renderDrawingCanvas();
      }
      updateCrosshairLegend(latestBarData);
    } else if ((e.altKey && k === "s") || ((e.metaKey || e.ctrlKey) && e.shiftKey && k === "s")) {
      e.preventDefault(); toggleScreenerModal();
    } else if (e.altKey && k === "l") {
      e.preventDefault(); setPriceScaleMode(currentPriceScaleMode === "log" ? "normal" : "log");
    } else if (e.altKey && k === "p") {
      e.preventDefault(); setPriceScaleMode(currentPriceScaleMode === "percent" ? "normal" : "percent");
    } else if (e.altKey && k === "r") {
      e.preventDefault(); setAutoScale(true); if (chart) chart.timeScale().fitContent();
    } else if (e.altKey && k === "h") {
      e.preventDefault(); _addQuickPriceLine(false);
    } else if (e.altKey && k === "a") {
      e.preventDefault(); _addQuickPriceLine(true);
    } else if (!e.metaKey && !e.ctrlKey && !e.altKey) {
      var tag = e.target && e.target.tagName;
      var modalOpen = (searchModal && searchModal.style.display === "flex") || (indicatorModal && indicatorModal.style.display === "flex");
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || modalOpen) return;
      if (e.key === "/") {
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
        if (drawingCanvas) {
          drawingCanvas.classList.toggle("drawing-active", currentDrawTool !== "cursor");
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

    // Render Anchored VWAP (AVWAP) Overlay
    if (indicatorConfig && indicatorConfig.avwap && currentAvwapData && currentAvwapData.avwap && currentAvwapData.avwap.length > 0 && avwapConfig.displayMode === "overlay") {
      var avPts = [];
      var firstOnScreenIdx = -1;
      for (var avi = 0; avi < currentAvwapData.avwap.length; avi++) {
        var aItem = currentAvwapData.avwap[avi];
        var aX = timeScale.timeToCoordinate(aItem.time);
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
          var rawMarkX = timeScale.timeToCoordinate(currentAvwapData.anchorBar.time);
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
            ctx.fillStyle = "rgba(30, 34, 45, 0.95)";
            ctx.fillRect(bLeft, bTop, bWidth, bHeight);
            ctx.strokeStyle = "#787b86";
            ctx.strokeRect(bLeft, bTop, bWidth, bHeight);

            ctx.fillStyle = "#ffffff";
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
          ctx.fillText(d.price.toFixed(2), drawingCanvas.width - 60, y - 4);
        }
      } else if (d.type === "trendline") {
        var x1 = timeScale.timeToCoordinate(d.time1);
        var y1 = candlestickSeries.priceToCoordinate(d.price1);
        var x2 = timeScale.timeToCoordinate(d.time2);
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
        var xCenter = d.time ? timeScale.timeToCoordinate(d.time) : drawingCanvas.width * 0.5;
        if (xCenter === null || isNaN(xCenter)) xCenter = drawingCanvas.width * 0.5;
        var boxWidth = Math.min(260, drawingCanvas.width * 0.45);
        var left = Math.max(10, xCenter);
        var right = left + boxWidth;

        var yEntry = candlestickSeries.priceToCoordinate(d.entryPrice);
        var yTarget = candlestickSeries.priceToCoordinate(d.targetPrice);
        var yStop = candlestickSeries.priceToCoordinate(d.stopPrice);

        if (yEntry !== null && yTarget !== null && yStop !== null) {
          var targetTop = Math.min(yTarget, yEntry);
          var targetHeight = Math.max(2, Math.abs(yTarget - yEntry));
          ctx.fillStyle = "rgba(34, 197, 94, 0.08)";
          ctx.fillRect(left, targetTop, boxWidth, targetHeight);
          ctx.strokeStyle = "#22c55e";
          ctx.lineWidth = 1.5;
          ctx.strokeRect(left, targetTop, boxWidth, targetHeight);

          var stopTop = Math.min(yStop, yEntry);
          var stopHeight = Math.max(2, Math.abs(yStop - yEntry));
          ctx.fillStyle = "rgba(239, 68, 68, 0.08)";
          ctx.fillRect(left, stopTop, boxWidth, stopHeight);
          ctx.strokeStyle = "#ef4444";
          ctx.lineWidth = 1.5;
          ctx.strokeRect(left, stopTop, boxWidth, stopHeight);

          ctx.strokeStyle = "#3b82f6";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(left - 10, yEntry);
          ctx.lineTo(right + 10, yEntry);
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
          ctx.fillRect(left + boxWidth * 0.5 - 10, yTarget - 4, 20, 8);
          ctx.fillStyle = "#ef4444";
          ctx.fillRect(left + boxWidth * 0.5 - 10, yStop - 4, 20, 8);

          var rewPct = d.entryPrice > 0 ? ((d.targetPrice - d.entryPrice) / d.entryPrice) * 100 : 0;
          var rskPct = d.entryPrice > 0 ? ((d.entryPrice - d.stopPrice) / d.entryPrice) * 100 : 0;
          var rr = rskPct > 0 ? (rewPct / rskPct).toFixed(2) : "0.00";
          var stopDist = Math.max(0.01, d.entryPrice - d.stopPrice);
          var shares = Math.floor((d.riskDollar || 500) / stopDist);

          ctx.fillStyle = "#ffffff";
          ctx.font = "bold 12px Inter, system-ui, sans-serif";
          ctx.fillText("R:R Ratio: 1 : " + rr, left + 10, yEntry - 8);

          ctx.font = "11px Inter, system-ui, sans-serif";
          ctx.fillStyle = "#4ade80";
          ctx.fillText("Target: +" + rewPct.toFixed(2) + "% (" + d.targetPrice.toFixed(2) + ")", left + 10, targetTop + 16);

          ctx.fillStyle = "#f87171";
          ctx.fillText("Stop: -" + rskPct.toFixed(2) + "% (" + d.stopPrice.toFixed(2) + ")", left + 10, stopTop + stopHeight - 8);

          ctx.fillStyle = "#93c5fd";
          ctx.fillText("建議買進: " + shares + " 股 ($" + (d.riskDollar || 500) + " 風控)", left + 10, yEntry + 18);
        }
      } else if (d.type === "ruler") {
        var rx1 = d.time1 ? timeScale.timeToCoordinate(d.time1) : d.x1;
        var ry1 = candlestickSeries ? candlestickSeries.priceToCoordinate(d.price1) : d.y1;
        var rx2 = d.time2 ? timeScale.timeToCoordinate(d.time2) : d.x2;
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
    }

    if (drawingInProgress && drawingInProgress.type === "trendline" && drawingInProgress.step === 1) {
      var tx1 = timeScale.timeToCoordinate(drawingInProgress.time1);
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
    drawingCanvas.addEventListener("mousedown", function(e) {
      if (!chart || !candlestickSeries) return;
      var rect = drawingCanvas.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var y = e.clientY - rect.top;
      var timeScale = chart.timeScale();
      var price = candlestickSeries.coordinateToPrice(y);
      var time = timeScale.coordinateToTime(x);
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

      if (currentDrawTool === "cursor") return;

      if (currentDrawTool === "risk-reward") {
        if (price) {
          var targetP = price * 1.15;
          var stopP = price * 0.94;
          drawings.push({
            type: "risk-reward",
            time: time || (latestBarData ? latestBarData.time : null),
            entryPrice: price,
            targetPrice: targetP,
            stopPrice: stopP,
            riskDollar: 500
          });
          saveDrawingsForActiveSymbol();
          renderDrawingCanvas();
        }
      } else if (currentDrawTool === "ruler") {
        if (!drawingInProgress) {
          drawingInProgress = { type: "ruler", step: 1, time1: time, price1: price, x1: x, y1: y, currentX: x, currentY: y };
        } else if (drawingInProgress.step === 1) {
          drawings.push({
            type: "ruler",
            time1: drawingInProgress.time1,
            price1: drawingInProgress.price1,
            time2: time,
            price2: price,
            x1: drawingInProgress.x1,
            y1: drawingInProgress.y1,
            x2: x,
            y2: y
          });
          drawingInProgress = null;
          saveDrawingsForActiveSymbol();
          renderDrawingCanvas();
        }
      } else if (currentDrawTool === "hline") {
        if (price) {
          drawings.push({ type: "hline", price: price, color: "#3b82f6" });
          saveDrawingsForActiveSymbol();
          renderDrawingCanvas();
        }
      } else if (currentDrawTool === "trendline") {
        if (!drawingInProgress) {
          drawingInProgress = { type: "trendline", step: 1, time1: time, price1: price, currentX: x, currentY: y };
        } else if (drawingInProgress.step === 1) {
          drawings.push({
            type: "trendline",
            time1: drawingInProgress.time1,
            price1: drawingInProgress.price1,
            time2: time,
            price2: price
          });
          drawingInProgress = null;
          saveDrawingsForActiveSymbol();
          renderDrawingCanvas();
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
            if (timeScale && typeof timeScale.coordinateToTime === "function" && typeof timeScale.timeToCoordinate === "function") {
              var origAnchorX = timeScale.timeToCoordinate(isDraggingHandle.origTime);
              if (origAnchorX !== null && !isNaN(origAnchorX)) {
                var newAnchorX = origAnchorX + (x - (isDraggingHandle.startX || x));
                var newT = timeScale.coordinateToTime(newAnchorX);
                if (newT !== null && newT !== undefined) {
                  isDraggingHandle.drawing.time = newT;
                }
              }
            } else if (timeScale && typeof timeScale.coordinateToTime === "function") {
              var newT = timeScale.coordinateToTime(x);
              if (newT !== null && newT !== undefined) {
                isDraggingHandle.drawing.time = newT;
              }
            }
          }
          renderDrawingCanvas();
        }
        return;
      }

      if (drawingInProgress && (drawingInProgress.type === "trendline" || drawingInProgress.type === "ruler")) {
        drawingInProgress.currentX = x;
        drawingInProgress.currentY = y;
        renderDrawingCanvas();
      }
    });

    window.addEventListener("mouseup", function() {
      if (isDraggingHandle) {
        isDraggingHandle = null;
        saveDrawingsForActiveSymbol();
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
        renderChartData(currentRawBars);
      }
    };
    updateCandleStyleUI();
  }

  var pContainer = (typeof priceContainer !== "undefined" && priceContainer) ? priceContainer : (document.getElementById("chart") || chartContainer);
  if (pContainer) {
    pContainer.addEventListener("dblclick", function() {
      setAutoScale(true);
      if (chart && chart.timeScale) {
        chart.timeScale().fitContent();
      }
    });
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

  if (rsActiveBadge) {
    rsActiveBadge.onclick = function() {
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
        renderChartData(currentRawBars);
      }
      hasPendingChartUpdate = false;
    });

    if (elapsedMs > 60000 && activeSymbol) {
      gapBackfill(activeSymbol);
    }
    if (activeSymbol) {
      recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe });
    }
  }
}

if (typeof window !== "undefined") {
  window.gapBackfill = gapBackfill;
  window.recoverTerminalState = recoverTerminalState;
  window.openAiLensModal = openAiLensModal;
  window.fetchAiLens = fetchAiLens;
  window.handleVisibilityChange = handleVisibilityChange;
  window.addEventListener("focus", function() {
    if (activeSymbol) {
      recoverTerminalState({ symbol: activeSymbol, timeframe: activeTimeframe });
    }
  });
  window.getAppNapState = function() {
    return {
      isAppNapSuspended: isAppNapSuspended,
      appNapHiddenAt: appNapHiddenAt,
      hasPendingChartUpdate: hasPendingChartUpdate
    };
  };
}

function bootstrap() {
  var savedSymbol = safeGetStorage("tv_active_symbol", "NASDAQ:NVDA");
  activeSymbol = savedSymbol || "NASDAQ:NVDA";

  var savedTf = safeGetStorage("tv_active_timeframe", "1D");
  if (savedTf) {
    activeTimeframe = savedTf;
    var allTfBtns = document.querySelectorAll ? document.querySelectorAll(".tf-btn") : [];
    allTfBtns.forEach(function(b) {
      if (b.getAttribute("data-tf") === savedTf) b.classList.add("active");
      else b.classList.remove("active");
    });
  } else {
    var activeTfBtn = document.querySelector ? document.querySelector(".tf-btn.active") : null;
    if (activeTfBtn && activeTfBtn.getAttribute("data-tf")) {
      activeTimeframe = activeTfBtn.getAttribute("data-tf");
    }
  }

  window.addEventListener("beforeunload", savePreferencesImmediate);
  window.addEventListener("pagehide", savePreferencesImmediate);

  initChart();
  initEventListeners();
  syncPreferencesFromBackend();
  syncIndicatorCheckboxes();
  applyIndicatorVisibilities();
  updateCandleStyleUI();
  setLanguage(currentLang);

  if (symbolsList.length > 0) {
    buildInitialDOM();
  }

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
}

if (document.addEventListener) {
  document.addEventListener("DOMContentLoaded", bootstrap);
}

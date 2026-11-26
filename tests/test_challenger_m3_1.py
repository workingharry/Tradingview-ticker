"""Empirical stress and adversarial concurrency tests for frontend terminal implementation.

Executed by Challenger M3-1 to verify:
1. Asynchronous race condition protection during rapid symbol switching.
2. Timestamp monotonicity sorting and backwards timestamp rejection.
3. Exponential backoff jitter bounds and distribution across 100 simulated runs.
4. DOM XSS sanitization against hostile symbol and quote injection.
5. Numerical safety and boundary conditions.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_APP_JS = BASE_DIR / "static" / "app.js"
STATIC_INDEX_HTML = BASE_DIR / "static" / "index.html"

# Discover JSC binary
JSC_CANDIDATES = [
    shutil.which("jsc"),
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc",
    "/System/Library/Frameworks/JavaScriptCore.framework/Resources/jsc",
]


def find_jsc() -> str:
    for candidate in JSC_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


JSC_PATH = find_jsc()


def execute_jsc_script(script: str) -> dict:
    """Executes a JavaScript test script in Apple's JavaScriptCore runtime."""
    if not JSC_PATH:
        raise RuntimeError("JavaScriptCore (jsc) binary not found on host system.")
    
    res = subprocess.run([JSC_PATH, "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"JSC execution failed (exit code {res.returncode}):\n"
            f"Stdout: {res.stdout}\n"
            f"Stderr: {res.stderr}"
        )
    try:
        return json.loads(res.stdout.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse JSC output as JSON: {res.stdout.strip()}") from e


# Standard Mock Environment for static/app.js
JS_MOCK_ENV_HEADER = f"""
var mockElements = {{}};
function MockElement(tag) {{
  this.tagName = (tag || 'div').toUpperCase();
  this.children = [];
  this.attributes = {{}};
  this.classList = {{
    classes: [],
    add: function(c) {{ if (this.classes.indexOf(c) === -1) this.classes.push(c); }},
    remove: function(c) {{
      var idx = this.classes.indexOf(c);
      if (idx !== -1) this.classes.splice(idx, 1);
    }},
    contains: function(c) {{ return this.classes.indexOf(c) !== -1; }}
  }};
  this.style = {{}};
  this._textContent = '';
  this._innerHTML = '';
}}
Object.defineProperty(MockElement.prototype, 'textContent', {{
  get: function() {{ return this._textContent; }},
  set: function(val) {{ this._textContent = String(val); }}
}});
Object.defineProperty(MockElement.prototype, 'innerHTML', {{
  get: function() {{ return this._innerHTML; }},
  set: function(val) {{
    this._innerHTML = String(val);
    if (val === '') this.children = [];
    else if (val.indexOf('<') !== -1) {{
      this.hasInjectedHtml = true;
    }}
  }}
}});
MockElement.prototype.appendChild = function(child) {{ this.children.push(child); }};
MockElement.prototype.setAttribute = function(k, v) {{ this.attributes[k] = String(v); }};
MockElement.prototype.getAttribute = function(k) {{ return this.attributes[k]; }};
MockElement.prototype.addEventListener = function() {{}};
MockElement.prototype.removeEventListener = function() {{}};
MockElement.prototype.querySelectorAll = function() {{ return []; }};

var document = {{
  getElementById: function(id) {{
    if (!mockElements[id]) mockElements[id] = new MockElement('div');
    return mockElements[id];
  }},
  createElement: function(tag) {{ return new MockElement(tag); }},
  addEventListener: function() {{}}
}};
var window = {{
  addEventListener: function() {{}},
  removeEventListener: function() {{}},
  location: {{ protocol: 'http:', host: 'localhost:8000' }}
}};
var AbortController = function() {{
  var self = this;
  this.signal = {{
    aborted: false,
    listeners: [],
    addEventListener: function(e, cb) {{ self.signal.listeners.push(cb); }}
  }};
  this.abort = function() {{
    self.signal.aborted = true;
    for (var i = 0; i < self.signal.listeners.length; i++) self.signal.listeners[i]();
  }};
}};

load('{STATIC_APP_JS.as_posix()}');
"""


@unittest.skipUnless(JSC_PATH, "JavaScriptCore (jsc) engine required for native JS execution")
class TestFrontendAsyncConcurrency(unittest.TestCase):
    """Stress tests for frontend asynchronous race conditions."""

    def test_rapid_symbol_switching_race_protection(self):
        """Simulates Symbol A fetch resolving AFTER Symbol B fetch and verifies A is discarded."""
        js_test = JS_MOCK_ENV_HEADER + """
        var candleDataSet = null;
        var volumeDataSet = null;
        candleSeries = {
          setData: function(d) { candleDataSet = d; },
          update: function(b) {}
        };
        volumeSeries = {
          setData: function(d) { volumeDataSet = d; },
          update: function(b) {}
        };
        chart = {
          timeScale: function() { return { fitContent: function(){} }; }
        };

        var deferreds = {};
        var abortedSymbols = [];

        fetch = function(url, opts) {
          var sym = decodeURIComponent(url.split('symbol=')[1]);
          return new Promise(function(resolve, reject) {
            if (opts && opts.signal) {
              opts.signal.addEventListener('abort', function() {
                abortedSymbols.push(sym);
                var err = new Error('AbortError');
                err.name = 'AbortError';
                reject(err);
              });
            }
            deferreds[sym] = {
              resolve: function(data) {
                resolve({
                  ok: true, status: 200,
                  json: function() { return Promise.resolve(data); }
                });
              },
              reject: reject
            };
          });
        };

        // 1. User clicks ETHUSDT
        switchSymbol('BINANCE:ETHUSDT');
        drainMicrotasks();

        // 2. User quickly clicks SOLUSDT before ETHUSDT resolves
        switchSymbol('BINANCE:SOLUSDT');
        drainMicrotasks();

        // 3. Fast SOLUSDT response resolves first
        var solBars = [
          { time: 1000, open: 150, high: 155, low: 148, close: 152, volume: 100 },
          { time: 1060, open: 152, high: 158, low: 151, close: 157, volume: 120 }
        ];
        deferreds['BINANCE:SOLUSDT'].resolve({ bars: solBars });
        drainMicrotasks();

        var stateAfterSol = {
          candleCount: candleDataSet.length,
          lastClose: candleDataSet[candleDataSet.length - 1].close,
          lastBarTime: lastBarTime,
          activeSymbol: activeSymbol,
          isHistoryLoading: isHistoryLoading,
          abortedSymbols: abortedSymbols
        };

        // 4. Slow ETHUSDT response resolves second (out-of-order race attempt)
        var ethBars = [
          { time: 2000, open: 3000, high: 3050, low: 2980, close: 3020, volume: 50 }
        ];
        try {
          deferreds['BINANCE:ETHUSDT'].resolve({ bars: ethBars });
          drainMicrotasks();
        } catch(e) {}

        var stateAfterStaleEth = {
          candleCount: candleDataSet.length,
          lastClose: candleDataSet[candleDataSet.length - 1].close,
          lastBarTime: lastBarTime,
          activeSymbol: activeSymbol,
          isHistoryLoading: isHistoryLoading
        };

        print(JSON.stringify({
          before: stateAfterSol,
          after: stateAfterStaleEth
        }));
        """
        res = execute_jsc_script(js_test)
        before = res["before"]
        after = res["after"]

        # AbortController triggered for ETHUSDT
        self.assertIn("BINANCE:ETHUSDT", before["abortedSymbols"])
        # SOLUSDT properly rendered
        self.assertEqual(before["activeSymbol"], "BINANCE:SOLUSDT")
        self.assertEqual(before["lastClose"], 157)
        self.assertEqual(before["lastBarTime"], 1060)
        self.assertFalse(before["isHistoryLoading"])

        # Stale ETHUSDT resolution MUST NOT overwrite SOLUSDT data
        self.assertEqual(after["activeSymbol"], "BINANCE:SOLUSDT")
        self.assertEqual(after["lastClose"], 157)
        self.assertEqual(after["lastBarTime"], 1060)
        self.assertEqual(after["candleCount"], 2)
        self.assertFalse(after["isHistoryLoading"])

    def test_in_flight_history_blocks_live_ticks(self):
        """Verifies that live ticks arriving while history is fetching are dropped until seated."""
        js_test = JS_MOCK_ENV_HEADER + """
        var candleUpdates = [];
        candleSeries = {
          setData: function(d) {},
          update: function(b) { candleUpdates.push(b); }
        };
        volumeSeries = { setData: function(d) {}, update: function(b) {} };
        chart = { timeScale: function() { return { fitContent: function(){} }; } };

        var wsHandler = null;
        var WebSocket = function() {
          this.close = function(){};
          Object.defineProperty(this, 'onmessage', {
            set: function(fn) { wsHandler = fn; },
            get: function() { return wsHandler; }
          });
        };
        connectWebSocket();

        var deferredResolve = null;
        fetch = function(url, opts) {
          return new Promise(function(resolve) {
            deferredResolve = resolve;
          });
        };

        switchSymbol('BINANCE:SOLUSDT');
        drainMicrotasks();

        var loadingDuringFetch = isHistoryLoading;

        // Dispatch live bar while history is loading
        ws.onmessage({
          data: JSON.stringify({
            type: 'bar',
            symbol: 'BINANCE:SOLUSDT',
            bar: { time: 5000, open: 150, high: 160, low: 140, close: 155, volume: 10 }
          })
        });

        var updatesWhileLoading = candleUpdates.length;

        // Resolve history
        deferredResolve({
          ok: true, status: 200,
          json: function() {
            return Promise.resolve({
              bars: [{ time: 1000, open: 150, high: 155, low: 148, close: 152, volume: 100 }]
            });
          }
        });
        drainMicrotasks();

        var loadingAfterResolve = isHistoryLoading;

        // Dispatch live bar after history finished
        ws.onmessage({
          data: JSON.stringify({
            type: 'bar',
            symbol: 'BINANCE:SOLUSDT',
            bar: { time: 5000, open: 150, high: 160, low: 140, close: 155, volume: 10 }
          })
        });

        var updatesAfterLoading = candleUpdates.length;

        print(JSON.stringify({
          loadingDuringFetch: loadingDuringFetch,
          updatesWhileLoading: updatesWhileLoading,
          loadingAfterResolve: loadingAfterResolve,
          updatesAfterLoading: updatesAfterLoading
        }));
        """
        res = execute_jsc_script(js_test)
        self.assertTrue(res["loadingDuringFetch"])
        self.assertEqual(res["updatesWhileLoading"], 0, "Live tick must be blocked while history loads")
        self.assertFalse(res["loadingAfterResolve"])
        self.assertEqual(res["updatesAfterLoading"], 1, "Live tick must be accepted after history loads")


@unittest.skipUnless(JSC_PATH, "JavaScriptCore (jsc) engine required for native JS execution")
class TestMonotonicityAndTimestampGuards(unittest.TestCase):
    """Stress tests for timestamp monotonicity, sorting, and backwards tick rejection."""

    def test_historical_bars_sorting_and_deduplication(self):
        """Verifies that unsorted bars, duplicate timestamps, and invalid records are resolved."""
        js_test = JS_MOCK_ENV_HEADER + """
        var candleDataSet = null;
        candleSeries = {
          setData: function(d) { candleDataSet = d; },
          update: function(b) {}
        };
        volumeSeries = { setData: function(d) {}, update: function(b) {} };
        chart = { timeScale: function() { return { fitContent: function(){} }; } };

        var rawInputBars = [
          { time: 1060, open: 10, high: 12, low: 9, close: 11, volume: 100 },
          { time: 1000, open: 8, high: 10, low: 7, close: 9, volume: 80 },
          { time: 1060, open: 10, high: 13, low: 9, close: 12.5, volume: 115 }, // Duplicate timestamp with updated price
          { time: 1120, open: 12, high: 15, low: 11, close: 14, volume: 150 },
          { time: 940, open: 6, high: 8, low: 5, close: 7, volume: 60 }
        ];

        fetch = function() {
          return Promise.resolve({
            ok: true, status: 200,
            json: function() { return Promise.resolve({ bars: rawInputBars }); }
          });
        };

        loadHistory('BINANCE:BTCUSDT');
        drainMicrotasks();

        var times = candleDataSet.map(function(b) { return b.time; });
        var bar1060 = candleDataSet.find(function(b) { return b.time === 1060; });

        print(JSON.stringify({
          count: candleDataSet.length,
          times: times,
          bar1060Close: bar1060 ? bar1060.close : null,
          lastBarTime: lastBarTime
        }));
        """
        res = execute_jsc_script(js_test)
        times = res["times"]
        self.assertEqual(res["count"], 4)
        # Verify strict monotonicity
        self.assertEqual(times, sorted(times))
        self.assertEqual(times, [940, 1000, 1060, 1120])
        # Verify duplicate replacement took latest values
        self.assertEqual(res["bar1060Close"], 12.5)
        self.assertEqual(res["lastBarTime"], 1120)

    def test_live_stream_backwards_timestamp_rejection(self):
        """Verifies that live stream strictly drops ticks with time < lastBarTime."""
        js_test = JS_MOCK_ENV_HEADER + """
        var candleUpdates = [];
        candleSeries = {
          setData: function(d) {},
          update: function(b) { candleUpdates.push(b); }
        };
        volumeSeries = { setData: function(d) {}, update: function(b) {} };
        chart = { timeScale: function() { return { fitContent: function(){} }; } };

        var wsHandler = null;
        var WebSocket = function() {
          this.close = function(){};
          Object.defineProperty(this, 'onmessage', {
            set: function(fn) { wsHandler = fn; },
            get: function() { return wsHandler; }
          });
        };
        connectWebSocket();

        // Seed initial history
        fetch = function() {
          return Promise.resolve({
            ok: true, status: 200,
            json: function() {
              return Promise.resolve({
                bars: [{ time: 1000, open: 100, high: 105, low: 95, close: 102, volume: 10 }]
              });
            }
          });
        };
        loadHistory('BINANCE:BTCUSDT');
        drainMicrotasks();

        var lastTime0 = lastBarTime; // 1000

        // 1. Backwards tick: time 900 < 1000 -> DROP
        ws.onmessage({
          data: JSON.stringify({
            type: 'bar',
            symbol: 'BINANCE:BTCUSDT',
            bar: { time: 900, open: 98, high: 100, low: 95, close: 99, volume: 5 }
          })
        });
        var countAfterBackwards = candleUpdates.length;

        // 2. In-place current bar tick: time 1000 === 1000 -> ACCEPT
        ws.onmessage({
          data: JSON.stringify({
            type: 'bar',
            symbol: 'BINANCE:BTCUSDT',
            bar: { time: 1000, open: 100, high: 108, low: 95, close: 107, volume: 15 }
          })
        });
        var countAfterCurrent = candleUpdates.length;
        var lastTime1 = lastBarTime;

        // 3. Forward new bar tick: time 1060 > 1000 -> ACCEPT & ADVANCE
        ws.onmessage({
          data: JSON.stringify({
            type: 'bar',
            symbol: 'BINANCE:BTCUSDT',
            bar: { time: 1060, open: 107, high: 110, low: 106, close: 109, volume: 20 }
          })
        });
        var countAfterForward = candleUpdates.length;
        var lastTime2 = lastBarTime;

        // 4. Backwards tick relative to new time: time 1050 < 1060 -> DROP
        ws.onmessage({
          data: JSON.stringify({
            type: 'bar',
            symbol: 'BINANCE:BTCUSDT',
            bar: { time: 1050, open: 108, high: 109, low: 107, close: 108, volume: 12 }
          })
        });
        var countAfterStaleRelative = candleUpdates.length;
        var lastTime3 = lastBarTime;

        print(JSON.stringify({
          lastTime0: lastTime0,
          countAfterBackwards: countAfterBackwards,
          countAfterCurrent: countAfterCurrent,
          lastTime1: lastTime1,
          countAfterForward: countAfterForward,
          lastTime2: lastTime2,
          countAfterStaleRelative: countAfterStaleRelative,
          lastTime3: lastTime3
        }));
        """
        res = execute_jsc_script(js_test)
        self.assertEqual(res["lastTime0"], 1000)
        self.assertEqual(res["countAfterBackwards"], 0, "Backwards tick (900 < 1000) must be dropped")
        self.assertEqual(res["countAfterCurrent"], 1, "Current bar tick (1000 == 1000) must be accepted")
        self.assertEqual(res["lastTime1"], 1000)
        self.assertEqual(res["countAfterForward"], 2, "Forward bar tick (1060 > 1000) must be accepted")
        self.assertEqual(res["lastTime2"], 1060)
        self.assertEqual(res["countAfterStaleRelative"], 2, "Backwards tick (1050 < 1060) must be dropped")
        self.assertEqual(res["lastTime3"], 1060)


@unittest.skipUnless(JSC_PATH, "JavaScriptCore (jsc) engine required for native JS execution")
class TestExponentialBackoffJitterBounds(unittest.TestCase):
    """Stress tests for exponential backoff formula and non-zero jitter bounds."""

    def test_reconnect_exponential_backoff_and_jitter_100_runs(self):
        """Simulates 100 reconnect attempts and validates mathematical bounds and jitter variance."""
        js_test = JS_MOCK_ENV_HEADER + """
        var scheduledDelays = [];
        setTimeout = function(fn, delay) {
          scheduledDelays.push(delay);
          return 1;
        };

        var allAttempts = [];
        for (var i = 1; i <= 100; i++) {
          reconnectAttempts = i - 1;
          reconnectTimer = null;
          scheduleReconnect();
          allAttempts.push({
            attempt: reconnectAttempts,
            delay: scheduledDelays[scheduledDelays.length - 1]
          });
        }

        // Test jitter variance across 100 independent trials for attempt #1
        var attempt1Trials = [];
        for (var t = 0; t < 100; t++) {
          reconnectAttempts = 0;
          reconnectTimer = null;
          scheduleReconnect();
          attempt1Trials.push(scheduledDelays[scheduledDelays.length - 1]);
        }

        print(JSON.stringify({
          attempts: allAttempts,
          attempt1Trials: attempt1Trials,
          baseReconnectDelay: BASE_RECONNECT_DELAY,
          maxReconnectDelay: MAX_RECONNECT_DELAY
        }));
        """
        res = execute_jsc_script(js_test)
        attempts = res["attempts"]
        attempt1_trials = res["attempt1Trials"]

        self.assertEqual(res["baseReconnectDelay"], 1000)
        self.assertEqual(res["maxReconnectDelay"], 30000)
        self.assertEqual(len(attempts), 100)

        # 1. Verify bounds for all 100 attempts
        for entry in attempts:
            att = entry["attempt"]
            delay = entry["delay"]
            base = min(30000, 1000 * (1.5 ** (att - 1)))
            # delay must be between floor(base) and ceil(base + 1000)
            self.assertGreaterEqual(delay, int(base), f"Delay {delay} < base {base} at attempt {att}")
            self.assertLessEqual(delay, int(base + 1000), f"Delay {delay} > base + 1000 at attempt {att}")
            # Global ceiling never exceeds 31000
            self.assertLessEqual(delay, 31000, f"Delay {delay} exceeded 31000ms ceiling at attempt {att}")
            # Global floor never falls below 1000
            self.assertGreaterEqual(delay, 1000, f"Delay {delay} fell below 1000ms at attempt {att}")

        # 2. Verify ceiling stabilization: for attempt >= 11, base is clamped to 30000
        for entry in attempts[10:]:
            self.assertGreaterEqual(entry["delay"], 30000)
            self.assertLessEqual(entry["delay"], 31000)

        # 3. Verify non-zero jitter variance across 100 trials
        unique_trials = set(attempt1_trials)
        self.assertGreater(
            len(unique_trials),
            80,
            f"Expected >80 unique delay values out of 100 trials, got {len(unique_trials)}"
        )

    def test_reconnect_single_timer_guard(self):
        """Verifies that scheduleReconnect does not spawn duplicate timers if one is pending."""
        js_test = JS_MOCK_ENV_HEADER + """
        var timerCallCount = 0;
        setTimeout = function(fn, delay) {
          timerCallCount++;
          return 999;
        };

        reconnectAttempts = 0;
        reconnectTimer = null;

        // Call scheduleReconnect 5 times consecutively without firing
        scheduleReconnect();
        scheduleReconnect();
        scheduleReconnect();
        scheduleReconnect();
        scheduleReconnect();

        print(JSON.stringify({
          timerCallCount: timerCallCount,
          reconnectAttempts: reconnectAttempts,
          hasTimer: reconnectTimer !== null
        }));
        """
        res = execute_jsc_script(js_test)
        self.assertEqual(res["timerCallCount"], 1, "Only a single timer should be scheduled")
        self.assertEqual(res["reconnectAttempts"], 1)
        self.assertTrue(res["hasTimer"])


@unittest.skipUnless(JSC_PATH, "JavaScriptCore (jsc) engine required for native JS execution")
class TestDomXssSanitization(unittest.TestCase):
    """Stress tests for DOM XSS protection and safe string assignment."""

    def test_hostile_symbol_injection_safe_dom(self):
        """Verifies that symbols with script tags and HTML injection do not execute or inject tags."""
        js_test = JS_MOCK_ENV_HEADER + """
        symbolsList = [
          {
            id: 'BINANCE:<script>alert("XSS")</script>',
            pair: '<script>alert("XSS")</script>',
            exchange: 'BINANCE',
            type: 'crypto'
          },
          {
            id: 'NASDAQ:<img src=x onerror=alert(1)>',
            pair: 'AAPL<img src=x onerror=alert(1)>',
            exchange: 'NASDAQ',
            type: 'stock'
          },
          {
            id: 'CRYPTO:"><svg/onload=alert(2)>',
            pair: 'BTC"><svg/onload=alert(2)>',
            exchange: 'CRYPTO',
            type: 'crypto'
          }
        ];

        buildInitialDOM();

        var watchlist = document.getElementById('watchlistItems');
        var tape = document.getElementById('tickerTape');

        var unsafeHtmlAssigned = false;
        var buttonTexts = [];
        for (var i = 0; i < watchlist.children.length; i++) {
          var item = watchlist.children[i];
          if (item.hasInjectedHtml) unsafeHtmlAssigned = true;
          for (var k = 0; k < item.children.length; k++) {
            if (item.children[k].hasInjectedHtml) unsafeHtmlAssigned = true;
            for (var m = 0; m < item.children[k].children.length; m++) {
              if (item.children[k].children[m].hasInjectedHtml) unsafeHtmlAssigned = true;
              buttonTexts.push(item.children[k].children[m].textContent);
            }
          }
        }

        var tapeTexts = [];
        for (var j = 0; j < tape.children.length; j++) {
          var item = tape.children[j];
          if (item.hasInjectedHtml) unsafeHtmlAssigned = true;
          for (var k = 0; k < item.children.length; k++) {
            if (item.children[k].hasInjectedHtml) unsafeHtmlAssigned = true;
            tapeTexts.push(item.children[k].textContent);
          }
        }

        // Test updateTapeItem with hostile symbol
        symbolStates['BINANCE:<script>alert("XSS")</script>'] = {
          price: 12345.67,
          changePercentage: 2.5
        };
        updateTapeItem('BINANCE:<script>alert("XSS")</script>', 'up');

        print(JSON.stringify({
          unsafeHtmlAssigned: unsafeHtmlAssigned,
          buttonCount: watchlist.children.length,
          tapeCount: tape.children.length,
          buttonTexts: buttonTexts,
          tapeTexts: tapeTexts
        }));
        """
        res = execute_jsc_script(js_test)
        self.assertFalse(res["unsafeHtmlAssigned"], "Unsafe HTML was assigned to innerHTML")
        self.assertEqual(res["buttonCount"], 3)
        self.assertEqual(res["tapeCount"], 3)
        # Check textContent contains literal unparsed strings
        self.assertIn('<script>alert("XSS")</script>', res["buttonTexts"])
        self.assertIn('AAPL<img src=x onerror=alert(1)>', res["buttonTexts"])

    def test_zero_dynamic_innerhtml_in_static_app_js(self):
        """Scans static/app.js to ensure innerHTML is never dynamically assigned with variable data."""
        with open(STATIC_APP_JS, "r", encoding="utf-8") as f:
            content = f.read()

        for line in content.splitlines():
            if ".innerHTML" in line and "=" in line:
                rhs = line.split(".innerHTML", 1)[1].split("=", 1)[1].strip()
                is_safe = rhs in ['"";', "'';", '""', "''"] or ("Unable to load Lightweight Charts CDN" in rhs)
                self.assertTrue(
                    is_safe,
                    f"Potentially unsafe dynamic innerHTML assignment detected: {rhs}"
                )


@unittest.skipUnless(JSC_PATH, "JavaScriptCore (jsc) engine required for native JS execution")
class TestFrontendNumericalHelpers(unittest.TestCase):
    """Stress tests for numeric sanitizers and formatters."""

    def test_safe_num_edge_cases(self):
        """Verifies safeNum handles undefined, null, NaN, Infinity, and strings safely."""
        js_test = JS_MOCK_ENV_HEADER + """
        print(JSON.stringify({
          undef: safeNum(undefined, -1),
          nullVal: safeNum(null, -1),
          emptyStr: safeNum('', -1),
          nanVal: safeNum(NaN, -1),
          infVal: safeNum(Infinity, -1),
          negInfVal: safeNum(-Infinity, -1),
          strNum: safeNum('123.45', -1),
          zeroNum: safeNum(0, -1),
          negNum: safeNum(-50.5, -1)
        }));
        """
        res = execute_jsc_script(js_test)
        self.assertEqual(res["undef"], -1)
        self.assertEqual(res["nullVal"], -1)
        self.assertEqual(res["emptyStr"], -1)
        self.assertEqual(res["nanVal"], -1)
        self.assertEqual(res["infVal"], -1)
        self.assertEqual(res["negInfVal"], -1)
        self.assertEqual(res["strNum"], 123.45)
        self.assertEqual(res["zeroNum"], 0)
        self.assertEqual(res["negNum"], -50.5)

    def test_format_price_and_volume_edge_cases(self):
        """Verifies price and volume formatting with edge cases."""
        js_test = JS_MOCK_ENV_HEADER + """
        print(JSON.stringify({
          nanPrice: formatPrice(NaN),
          nullPrice: formatPrice(null),
          zeroPrice: formatPrice(0),
          negPrice: formatPrice(-10),
          btcPrice: formatPrice(67890.123, 'BINANCE:BTCUSDT'),
          pennyPrice: formatPrice(0.00456, 'PEPE'),
          largeVol: formatVolume(5500000),
          midVol: formatVolume(12300),
          smallVol: formatVolume(45.67),
          zeroVol: formatVolume(0)
        }));
        """
        res = execute_jsc_script(js_test)
        self.assertEqual(res["nanPrice"], "--")
        self.assertEqual(res["nullPrice"], "--")
        self.assertEqual(res["zeroPrice"], "--")
        self.assertEqual(res["negPrice"], "--")
        self.assertEqual(res["btcPrice"], "67,890.12")
        self.assertEqual(res["pennyPrice"], "0.0046")
        self.assertEqual(res["largeVol"], "5.50M")
        self.assertEqual(res["midVol"], "12.3k")
        self.assertEqual(res["smallVol"], "45.67")
        self.assertEqual(res["zeroVol"], "--")


if __name__ == "__main__":
    unittest.main()

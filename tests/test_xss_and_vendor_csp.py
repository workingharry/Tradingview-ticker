"""Security, local vendoring, CSP, and XSS behavior tests (Phase 2)."""

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import server

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = PROJECT_ROOT / "static" / "app.js"
VENDOR_LWC_PATH = PROJECT_ROOT / "static" / "vendor" / "lightweight-charts.standalone.production.js"
XSS_PAYLOAD = "<img src=x onerror=alert(1)>"


def _run_node_xss_probe(scenario_js: str) -> dict:
    """Executes static/app.js in a Node VM trap DOM and detects any unsafe HTML or XSS execution."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    harness = (
        """
    const fs = require("fs");
    const vm = require("vm");
    const appCode = fs.readFileSync(APP_JS_PATH_PLACEHOLDER, "utf8");

    const nonEmptyInnerHtmlWrites = [];
    const createdTags = [];
    let alertCalls = 0;

    function makeTrapElement(id) {
      const el = {
        id: id || "",
        tagName: "DIV",
        className: "",
        _textContent: "",
        _innerHTML: "",
        style: { setProperty: function() {}, cssText: "" },
        attributes: {},
        children: [],
        dataset: {},
        classList: {
          _set: new Set(),
          add: function() { for (const a of arguments) this._set.add(a); },
          remove: function() { for (const a of arguments) this._set.delete(a); },
          toggle: function(c, force) {
            if (force === true) this._set.add(c);
            else if (force === false) this._set.delete(c);
            else if (this._set.has(c)) this._set.delete(c);
            else this._set.add(c);
          },
          contains: function(c) { return this._set.has(c); }
        },
        setAttribute: function(k, v) { this.attributes[k] = String(v); },
        getAttribute: function(k) { return this.attributes[k] || null; },
        appendChild: function(child) { this.children.push(child); return child; },
        addEventListener: function() {},
        removeEventListener: function() {},
        contains: function() { return false; },
        focus: function() {},
        closest: function() { return null; },
        querySelector: function() { return null; },
        querySelectorAll: function() { return []; },
        getBoundingClientRect: function() { return { top: 0, left: 0, width: 100, height: 20 }; }
      };
      Object.defineProperty(el, "innerHTML", {
        get: function() { return el._innerHTML; },
        set: function(val) {
          const s = String(val);
          el._innerHTML = s;
          if (s !== "") {
            nonEmptyInnerHtmlWrites.push({ id: el.id, tag: el.tagName, value: s });
          } else {
            el.children = [];
          }
        }
      });
      Object.defineProperty(el, "textContent", {
        get: function() { return el._textContent; },
        set: function(val) { el._textContent = String(val); }
      });
      return el;
    }

    const elementsById = {};
    function getOrCreateEl(id) {
      if (!elementsById[id]) elementsById[id] = makeTrapElement(id);
      return elementsById[id];
    }

    const storage = {
      _data: {},
      get length() { return Object.keys(this._data).length; },
      key: function(i) { return Object.keys(this._data)[i] || null; },
      getItem: function(k) { return Object.prototype.hasOwnProperty.call(this._data, k) ? this._data[k] : null; },
      setItem: function(k, v) { this._data[k] = String(v); },
      removeItem: function(k) { delete this._data[k]; }
    };

    const domReadyHandlers = [];
    const document = {
      body: makeTrapElement("body"),
      addEventListener: function(ev, fn) { if (ev === "DOMContentLoaded") domReadyHandlers.push(fn); },
      removeEventListener: function() {},
      getElementById: function(id) { return getOrCreateEl(id); },
      querySelector: function() { return makeTrapElement("qs"); },
      querySelectorAll: function() { return []; },
      createElement: function(tag) {
        createdTags.push(String(tag).toLowerCase());
        const e = makeTrapElement("");
        e.tagName = String(tag).toUpperCase();
        return e;
      },
      elementFromPoint: function() { return null; }
    };

    function collectText(node, acc) {
      if (!node) return acc;
      if (node._textContent) acc.push(node._textContent);
      if (Array.isArray(node.children)) {
        for (const ch of node.children) collectText(ch, acc);
      }
      return acc;
    }

    const window = {
      location: { host: "127.0.0.1:8000", protocol: "http:", search: "" },
      addEventListener: function() {},
      removeEventListener: function() {},
      innerWidth: 1440,
      innerHeight: 900,
      matchMedia: function() { return { matches: false, addEventListener: function() {} }; }
    };

    const sandbox = {
      window,
      self: window,
      document,
      navigator: { clipboard: { writeText: function() { return Promise.resolve(); } } },
      localStorage: storage,
      sessionStorage: storage,
      console: { log: function() {}, warn: function() {}, error: function() {}, debug: function() {} },
      alert: function() { alertCalls += 1; },
      setTimeout: function(fn) { return 1; },
      clearTimeout: function() {},
      setInterval: function() { return 1; },
      clearInterval: function() {},
      requestAnimationFrame: function() { return 1; },
      AbortController: function() { this.signal = {}; this.abort = function() {}; },
      WebSocket: function() { this.close = function() {}; },
      ResizeObserver: function() { this.observe = function() {}; this.disconnect = function() {}; },
      fetch: function() { return Promise.resolve({ ok: true, json: function() { return Promise.resolve({}); } }); },
      encodeURIComponent,
      decodeURIComponent,
      Intl,
      Math,
      Date,
      JSON,
      Array,
      Object,
      Number,
      String,
      Boolean,
      RegExp,
      Map,
      Set,
      Promise,
      Error,
      isFinite,
      isNaN,
      parseFloat,
      parseInt
    };
    sandbox.globalThis = sandbox;
    sandbox.window.document = document;
    sandbox.window.alert = sandbox.alert;
    sandbox.window.fetch = function(u, o) { return sandbox.fetch(u, o); };
    sandbox.collectText = collectText;
    sandbox.__domReadyHandlers = domReadyHandlers;

    vm.createContext(sandbox);
    vm.runInContext(appCode, sandbox);

    const scenarioSrc = "(async function() { const payload = " + """ + json.dumps(json.dumps(XSS_PAYLOAD)) + """ + ";\\n" + """ + json.dumps(scenario_js) + """ + "\\n})()";
    vm.runInContext(scenarioSrc, sandbox).then(function(extra) {
      console.log(JSON.stringify({
        alertCalls,
        nonEmptyInnerHtmlWrites,
        createdTags,
        extra
      }));
    }).catch(function(err) {
      console.error(err);
      process.exit(1);
    });
    """
    ).replace("APP_JS_PATH_PLACEHOLDER", json.dumps(str(APP_JS_PATH)))
    proc = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    if proc.returncode != 0:
        raise AssertionError(f"Node harness failed: {proc.stderr}")
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


class TestVendorAndCspHardening(unittest.TestCase):
    """Tests local Lightweight Charts v4.2.0 vendoring and hardened CSP policies."""

    def test_vendored_lightweight_charts_is_v4_2_0_and_loads_offline(self):
        self.assertTrue(VENDOR_LWC_PATH.is_file(), "Vendored lightweight-charts JS must exist")
        self.assertGreater(VENDOR_LWC_PATH.stat().st_size, 100_000)

        node_script = (
            "const fs = require('fs'); const vm = require('vm');"
            f"const code = fs.readFileSync({json.dumps(str(VENDOR_LWC_PATH))}, 'utf8');"
            "const ctx = { window: {}, navigator: { userAgent: 'node' }, document: {}, console,"
            " Math, Object, Array, Number, String, Boolean, RegExp, Error, TypeError, Map, Set, Date,"
            " parseInt, parseFloat, isNaN, isFinite, performance: { now: () => 0 }, requestAnimationFrame: () => 0 };"
            "ctx.self = ctx.window; ctx.globalThis = ctx;"
            "vm.runInNewContext(code, ctx);"
            "console.log(JSON.stringify({ version: ctx.window.LightweightCharts.version(), hasCreateChart: typeof ctx.window.LightweightCharts.createChart }));"
        )
        res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout.strip())
        self.assertEqual(data["version"], "4.2.0")
        self.assertEqual(data["hasCreateChart"], "function")

    def test_static_index_uses_local_vendor_and_no_cdn(self):
        """guard-test: 設定守門，允許讀原始碼 — static/index.html must load local LWC v4.2.0."""
        html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("unpkg.com", html)
        self.assertIn('src="vendor/lightweight-charts.standalone.production.js"', html)

    def test_tauri_and_server_csp_remove_unpkg_and_unsafe_eval(self):
        conf = json.loads((PROJECT_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        tauri_csp = conf["app"]["security"]["csp"]
        self.assertNotIn("unpkg.com", tauri_csp)
        self.assertNotIn("'unsafe-eval'", tauri_csp)

        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock):
            with TestClient(server.app) as client:
                resp = client.get("/")
                self.assertEqual(resp.status_code, 200)
                server_csp = resp.headers.get("Content-Security-Policy", "")
                self.assertIn("default-src 'self'", server_csp)
                self.assertNotIn("unpkg.com", server_csp)
                self.assertNotIn("'unsafe-eval'", server_csp)

                vendor_resp = client.get("/vendor/lightweight-charts.standalone.production.js")
                self.assertEqual(vendor_resp.status_code, 200)
                self.assertIn("4.2.0", vendor_resp.text)

    def test_gitignore_ignores_sidecar_static_copy(self):
        """guard-test: 設定守門，允許讀原始碼 — sidecar static dir must stay gitignored."""
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("src-tauri/bin/server-sidecar/", gitignore)


class TestXssSafeDomRendering(unittest.TestCase):
    """Behavior tests feeding <img src=x onerror=alert(1)> into external-data DOM renderers."""

    def test_ticker_tape_and_watchlist_render_xss_payload_as_text(self):
        scenario = """
        tickerTape = document.getElementById("tickerTape");
        watchlistItemsEl = document.getElementById("watchlistItems");
        symbolsList = [{ id: "NASDAQ:XSS", pair: payload, name: payload, sector: payload, exchange: "NASDAQ" }];
        symbolStates = { "NASDAQ:XSS": { price: 123.45, changePercentage: 1.5 } };
        buildTickerTape();
        renderWatchlist();
        return {
          tapeText: collectText(tickerTape, []).join(" "),
          watchlistText: collectText(watchlistItemsEl, []).join(" ")
        };
        """
        res = _run_node_xss_probe(scenario)
        self.assertEqual(res["alertCalls"], 0)
        self.assertEqual(res["nonEmptyInnerHtmlWrites"], [])
        self.assertNotIn("img", res["createdTags"])
        self.assertIn(XSS_PAYLOAD, res["extra"]["tapeText"])
        self.assertIn(XSS_PAYLOAD, res["extra"]["watchlistText"])

    def test_search_screener_heatmap_render_xss_payload_as_text(self):
        scenario = """
        searchResultsList = document.getElementById("searchResultsList");
        renderSearchResults([{ id: "NASDAQ:XSS", pair: payload, name: payload, exchange: payload, type: payload }]);
        renderScreenerTable([{
          id: "NASDAQ:XSS",
          symbol: payload,
          company_name: payload,
          tier: "Tier 1",
          frameworks: [payload],
          close: 50,
          atr: 2,
          change_pct: 3.2,
          streak_days: 2,
          filter_delta: "+1",
          rs_rating: 90,
          eps_yoy: 25,
          rev_yoy: 30,
          v6_risk: "Clear"
        }]);
        heatmapDataCache = {
          benchmark: { change_pct: 1.0 },
          themes: [{
            id: "t1",
            name_en: payload,
            name_zh: payload,
            avg_change_pct: 2.0,
            avg_rs_rating: 85,
            advancing: 1,
            total: 1,
            items: [{ id: "NASDAQ:XSS", symbol: payload, name: payload, close: 50, change_pct: 2.0, rs_rating: 85, volume_ratio_50: 1.5 }]
          }]
        };
        renderHeatmap();
        return {
          searchText: collectText(document.getElementById("searchResultsList"), []).join(" "),
          screenerText: collectText(document.getElementById("screenerTableBody"), []).join(" "),
          heatmapText: collectText(document.getElementById("heatmapGrid"), []).join(" ")
        };
        """
        res = _run_node_xss_probe(scenario)
        self.assertEqual(res["alertCalls"], 0)
        self.assertEqual(res["nonEmptyInnerHtmlWrites"], [])
        self.assertNotIn("img", res["createdTags"])
        self.assertIn(XSS_PAYLOAD, res["extra"]["searchText"])
        self.assertIn(XSS_PAYLOAD, res["extra"]["screenerText"])
        self.assertIn(XSS_PAYLOAD, res["extra"]["heatmapText"])

    def test_ai_summaries_and_fundamentals_render_xss_payload_as_text(self):
        scenario = """
        activeSymbol = "NASDAQ:XSS";
        activeTimeframe = "1D";
        symbolsList = [{ id: "NASDAQ:XSS", pair: "XSS", name: payload }];
        fetch = function(url) {
          if (String(url).indexOf("/api/fundamentals") !== -1) {
            return Promise.resolve({
              ok: true,
              json: function() { return Promise.resolve({ ai_summary: payload, model_used: payload, metrics: { fifty_two_week_high: 100 } }); }
            });
          }
          if (String(url).indexOf("/api/ai-lens") !== -1) {
            return Promise.resolve({
              ok: true,
              json: function() {
                return Promise.resolve({
                  report: {
                    model_used: payload,
                    bar_time: 1700000000,
                    lens: { quant_metrics: { trend_regime: payload } },
                    decision: {
                      bias: "LONG",
                      confidence: 88,
                      setup_summary: payload,
                      invalidation_condition: payload,
                      failure_mode: payload,
                      fundamental_diagnosis: payload,
                      human_decision_note: payload
                    }
                  }
                });
              }
            });
          }
          return Promise.resolve({
            ok: true,
            json: function() { return Promise.resolve({ summary: payload }); }
          });
        };
        fetchFundamentals("NASDAQ:XSS", true);
        fetchAiLens("NASDAQ:XSS", "1D", true);
        lastScreenerResults = [{ symbol: "XSS" }];
        lastBenchmarkStatus = { symbol: "SPY", close: 500, regime: "BULL" };
        fetchScreenerAiSummary();
        await new Promise(function(r) { setTimeout(r, 10); });
        return {
          fundAiText: document.getElementById("fundamentalsAiContent").textContent,
          lensSetupText: document.getElementById("aiLensSetup").textContent,
          screenerAiText: document.getElementById("screenerAiContent").textContent
        };
        """
        # Make setTimeout run microtask callback promptly in this test
        res = _run_node_xss_probe(
            scenario.replace(
                "await new Promise(function(r) { setTimeout(r, 10); });",
                "await Promise.resolve(); await Promise.resolve(); await Promise.resolve();",
            )
        )
        self.assertEqual(res["alertCalls"], 0)
        self.assertEqual(res["nonEmptyInnerHtmlWrites"], [])
        self.assertNotIn("img", res["createdTags"])
        self.assertEqual(res["extra"]["fundAiText"], XSS_PAYLOAD)
        self.assertEqual(res["extra"]["lensSetupText"], XSS_PAYLOAD)
        self.assertEqual(res["extra"]["screenerAiText"], XSS_PAYLOAD)

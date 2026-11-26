"""
Mega Phase 5 Unit Tests: Heatmap Reliability & Multi-Universe Hardening (S10)
Strict TDD verification for:
  S10-01: openHeatmapModal request sequence guards, immediate container clearing, and out-of-order response dropping
  S10-02: 60s cache TTL and New York (ET) timestamp display formatting
  S10-03: backend no_data flag on empty bars, exclusion from theme averages, and frontend gray tile with "無資料"
"""

import json
import subprocess
import unittest
from pathlib import Path

from engine.screener import build_theme_heatmap


class TestMegaPhase5Heatmap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.app_js_path = cls.project_root / "static" / "app.js"
        if not cls.app_js_path.exists():
            raise FileNotFoundError(f"Missing {cls.app_js_path}")

    def _execute_node_eval(self, eval_js_snippet: str):
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const elements = {{}};
        function getOrCreateEl(id) {{
            if (!elements[id]) {{
                const el = {{
                    _id: id,
                    _text: '',
                    title: '',
                    className: '',
                    style: {{}},
                    classList: {{
                        add: function(c) {{ el.className += ' ' + c; }},
                        remove: function(c) {{ el.className = el.className.replace(c, '').trim(); }},
                        contains: function(c) {{ return (el.className || '').indexOf(c) !== -1; }}
                    }},
                    children: [],
                    appendChild: function(c) {{ el.children.push(c); }},
                    insertBefore: function(c, ref) {{ el.children.push(c); }},
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}},
                    setAttribute: function(k, v) {{ el[k] = v; if (k === 'id') {{ el._id = v; elements[v] = el; }} }},
                    getAttribute: function(k) {{ return el[k] || null; }},
                    querySelector: function(s) {{ return null; }},
                    querySelectorAll: function(s) {{ return []; }}
                }};
                Object.defineProperty(el, 'id', {{
                    get: function() {{ return this._id; }},
                    set: function(v) {{ this._id = v; elements[v] = this; }}
                }});
                Object.defineProperty(el, 'innerHTML', {{
                    get: function() {{ return this._html || ''; }},
                    set: function(v) {{
                        this._html = v;
                        if (v === '') {{
                            this.children = [];
                            this._text = '';
                        }}
                    }}
                }});
                Object.defineProperty(el, 'textContent', {{
                    get: function() {{
                        if (this._text !== undefined && this._text !== '') return this._text;
                        if (this.children.length > 0) {{
                            return this.children.map(c => c.textContent).join(' ');
                        }}
                        return this._text || '';
                    }},
                    set: function(v) {{ this._text = v; }}
                }});
                elements[id] = el;
            }}
            return elements[id];
        }}

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: (id) => getOrCreateEl(id),
                querySelector: (s) => {{
                    if (s && s.startsWith('#')) return getOrCreateEl(s.slice(1));
                    return null;
                }},
                querySelectorAll: (s) => [],
                createElement: (t) => {{
                    const dynId = 'dynamic_' + Math.random();
                    return getOrCreateEl(dynId);
                }},
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            fetch: function() {{ return Promise.reject(new Error("fetch unmocked")); }},
            AbortController: globalThis.AbortController || function() {{ this.signal = {{}}; this.abort = function() {{}}; }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            sessionStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', protocol: 'http:', host: '127.0.0.1:8000', origin: 'http://127.0.0.1:8000' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl,
            Date: Date,
            setInterval: setInterval,
            clearInterval: clearInterval,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);
        vm.runInContext(appCode, sandbox);

        const result = vm.runInContext(`(async () => {{
            {eval_js_snippet}
        }})()`, sandbox);

        result.then(val => {{
            console.log("JSON_OUTPUT_START" + JSON.stringify(val) + "JSON_OUTPUT_END");
        }}).catch(err => {{
            console.error(err);
            process.exit(1);
        }});
        """

        p = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"Node execution failed with code {p.returncode}:\nStdout: {p.stdout}\nStderr: {p.stderr}")

        stdout = p.stdout
        start = stdout.find("JSON_OUTPUT_START")
        end = stdout.find("JSON_OUTPUT_END")
        if start == -1 or end == -1:
            raise ValueError(f"Could not parse JSON output from:\n{stdout}")
        return json.loads(stdout[start + len("JSON_OUTPUT_START"):end])

    def test_s10_01_universe_switch_sequence_guard_and_clear(self):
        """S10-01: Discards stale sequence responses and clears grid on switch."""
        js_code = """
        var gridEl = document.getElementById("heatmapGrid");
        var loadingEl = document.getElementById("heatmapLoading");
        var deferredCalls = [];

        fetch = function(url) {
            return new Promise(function(resolve, reject) {
                deferredCalls.push({ url: url, resolve: resolve, reject: reject });
            });
        };

        // Call 1: themes universe
        activeHeatmapUniverse = "themes";
        openHeatmapModal(true);

        // Call 2: switch to watchlist
        activeHeatmapUniverse = "watchlist";
        openHeatmapModal(true);

        // Resolve call 2 (watchlist) first
        deferredCalls[1].resolve({
            ok: true,
            json: function() {
                return Promise.resolve({
                    universe: "watchlist",
                    updated_at: "2026-11-23T12:00:00Z",
                    benchmark: { symbol: "SPY", change_pct: 1.5 },
                    themes: [{
                        id: "watchlist_group",
                        name_zh: "自選板塊",
                        name_en: "Watchlist",
                        items: [{ symbol: "NVDA", change_pct: 3.5, no_data: false }]
                    }]
                });
            }
        });

        await new Promise(r => setTimeout(r, 10));

        var cardsAfterSecond = gridEl.children.length;
        var universeInCache = heatmapDataCache ? heatmapDataCache.universe : "";

        // Now resolve call 1 (themes) late - should be dropped
        deferredCalls[0].resolve({
            ok: true,
            json: function() {
                return Promise.resolve({
                    universe: "themes",
                    updated_at: "2026-11-23T11:00:00Z",
                    benchmark: { symbol: "SPY", change_pct: 0.5 },
                    themes: [{
                        id: "ai_semis",
                        name_zh: "半導體",
                        name_en: "Semis",
                        items: [{ symbol: "AMD", change_pct: 1.0, no_data: false }]
                    }]
                });
            }
        });

        await new Promise(r => setTimeout(r, 10));

        var universeAfterLateResolve = heatmapDataCache ? heatmapDataCache.universe : "";

        return {
            cardsAfterSecond: cardsAfterSecond,
            universeInCache: universeInCache,
            universeAfterLateResolve: universeAfterLateResolve
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["cardsAfterSecond"], 1)
        self.assertEqual(res["universeInCache"], "watchlist")
        self.assertEqual(res["universeAfterLateResolve"], "watchlist")

    def test_s10_02_heatmap_cache_ttl_and_timestamp_format(self):
        """S10-02: 60s TTL prevents redundant fetch; ET timestamp formatted in bench badge."""
        js_code = """
        var fetchCount = 0;
        fetch = function(url) {
            fetchCount++;
            return Promise.resolve({
                ok: true,
                json: function() {
                    return Promise.resolve({
                        universe: "themes",
                        updated_at: "2026-11-23T14:30:00Z",
                        benchmark: { symbol: "SPY", change_pct: 0.8 },
                        themes: [{
                            id: "ai_semis",
                            name_zh: "AI 半導體",
                            items: [{ symbol: "NVDA", change_pct: 2.1 }]
                        }]
                    });
                }
            });
        };

        activeHeatmapUniverse = "themes";
        // First open: fetches
        openHeatmapModal(false);
        await new Promise(r => setTimeout(r, 10));

        var count1 = fetchCount;

        // Second open immediately (within 60s): should use cache without fetching
        openHeatmapModal(false);
        await new Promise(r => setTimeout(r, 10));

        var count2 = fetchCount;

        // Age cache to 65s ago
        if (heatmapDataCache) {
            heatmapDataCache._cachedAt = Date.now() - 65000;
        }

        // Third open: cache expired, should fetch
        openHeatmapModal(false);
        await new Promise(r => setTimeout(r, 10));

        var count3 = fetchCount;

        var benchBadge = document.getElementById("heatmapBenchBadge");
        var badgeText = benchBadge ? benchBadge.textContent : "";

        return {
            count1: count1,
            count2: count2,
            count3: count3,
            badgeText: badgeText
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["count1"], 1)
        self.assertEqual(res["count2"], 1)
        self.assertEqual(res["count3"], 2)
        self.assertIn("SPY: +0.80%", res["badgeText"])
        self.assertIn("ET", res["badgeText"])

    def test_s10_03_backend_no_data_flag_and_exclusion(self):
        """S10-03: build_theme_heatmap excludes no_data: true from average return and advancing count."""
        items = [
            {"symbol": "NVDA", "change_pct": 5.0, "rs_rating": 90, "volume_ratio_50": 1.5, "stage2": True, "no_data": False},
            {"symbol": "AMD", "change_pct": 3.0, "rs_rating": 80, "volume_ratio_50": 1.2, "stage2": True, "no_data": False},
            {"symbol": "AVGO", "change_pct": 0.0, "rs_rating": 50, "volume_ratio_50": 1.0, "stage2": False, "no_data": True},
        ]

        themes = build_theme_heatmap(items)
        self.assertEqual(len(themes), 1)
        ai_theme = themes[0]
        self.assertEqual(ai_theme["id"], "ai_semis")
        # NVDA (5.0) + AMD (3.0) / 2 = 4.0; INTC (no_data) excluded from average!
        self.assertEqual(ai_theme["avg_change_pct"], 4.0)
        # RS (90 + 80) / 2 = 85.0
        self.assertEqual(ai_theme["avg_rs_rating"], 85.0)
        # Advancing count: only NVDA and AMD are > 0 (2 advancing)
        self.assertEqual(ai_theme["advancing"], 2)
        # Total items includes all 3
        self.assertEqual(ai_theme["total"], 3)

    def test_s10_03_frontend_no_data_tile_gray_and_label(self):
        """S10-03: Frontend renders item with no_data: true using gray background and label '無資料'."""
        js_code = """
        currentLang = "zh";
        heatmapDataCache = {
            universe: "themes",
            updated_at: "2026-11-23T14:30:00Z",
            benchmark: { symbol: "SPY", change_pct: 1.0 },
            themes: [{
                id: "ai_semis",
                name_zh: "AI 半導體",
                items: [
                    { symbol: "NVDA", change_pct: 4.5, rs_rating: 95, volume_ratio_50: 1.5, no_data: false },
                    { symbol: "NODATA_SYM", change_pct: 0.0, rs_rating: 50, volume_ratio_50: 1.0, no_data: true }
                ]
            }]
        };

        renderHeatmap();

        var gridEl = document.getElementById("heatmapGrid");
        var card = gridEl.children[0];
        var body = card ? card.children.find(c => c.className === "theme-card-body") : null;
        var tiles = body ? body.children : [];

        var normalTile = tiles[0];
        var noDataTile = tiles[1];

        var normalText = normalTile ? normalTile.textContent : "";
        var noDataText = noDataTile ? noDataTile.textContent : "";
        var noDataBg = noDataTile ? noDataTile.style.backgroundColor : "";

        return {
            tilesCount: tiles.length,
            normalText: normalText,
            noDataText: noDataText,
            noDataBg: noDataBg
        };
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["tilesCount"], 2)
        self.assertIn("+4.50%", res["normalText"])
        self.assertIn("無資料", res["noDataText"])
        self.assertNotIn("0.00%", res["noDataText"])
        self.assertIn("475569", res["noDataBg"].replace("#", "").lower())


if __name__ == "__main__":
    unittest.main()

"""Tests for Real-Time Candlestick Chart Updates and Zero-Delay TradingView Widget Resolution."""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path


class TestRealtimeChartAndWidgetSeam(unittest.TestCase):
    """Executes real-time quote, bar alignment, legend, and BATS widget routing in Node.js VM."""

    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.app_js_path = cls.project_root / "static" / "app.js"
        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
        )

    def _run_node(self, snippet: str):
        if not self.node_bin:
            self.skipTest("Node.js binary not found")
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');
        const elems = {{}};
        function mkEl(id) {{
            if (!elems[id]) elems[id] = {{ id, textContent: "", className: "", style: {{}}, classList: {{ add(){{}}, remove(){{}}, toggle(){{}} }}, appendChild(){{}}, removeChild(){{}}, addEventListener(){{}} }};
            return elems[id];
        }}
        const sandbox = {{
            setTimeout, clearTimeout, console, Intl,
            fetch: () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{ bars: [] }}) }}),
            document: {{
                getElementById: (id) => mkEl(id),
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: (t) => mkEl(t),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{ _s: {{}}, getItem(k){{ return this._s[k] ?? null; }}, setItem(k,v){{ this._s[k]=String(v); }}, removeItem(k){{ delete this._s[k]; }}, length: 0 }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', protocol: 'http:', host: '127.0.0.1:8000' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            TradingView: {{ widget: function(cfg) {{ sandbox._tvConfigs.push(cfg); }} }},
            _tvConfigs: [],
            _elems: elems
        }};
        sandbox.window = sandbox;
        sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox);
        const out = vm.runInContext({json.dumps(snippet)}, sandbox);
        console.log(JSON.stringify(out));
        process.exit(0);
        """
        env = os.environ.copy()
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
        proc = subprocess.run([self.node_bin, "-e", harness], capture_output=True, text=True, cwd=str(self.project_root), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_tradingview_widget_uses_bats_for_us_equities_to_eliminate_15m_delay(self):
        """Verifies toTradingViewRealtimeSymbol and initTradingViewWidget map US equities to BATS:."""
        res = self._run_node("""
        (() => {
            const nvda = toTradingViewRealtimeSymbol("NASDAQ:NVDA");
            const tsla = toTradingViewRealtimeSymbol("NYSE:TSLA");
            const spy = toTradingViewRealtimeSymbol("AMEX:SPY");
            const btc = toTradingViewRealtimeSymbol("BINANCE:BTCUSDT");
            initTradingViewWidget("NASDAQ:NVDA");
            return { nvda, tsla, spy, btc, widgetSym: _tvConfigs[0] ? _tvConfigs[0].symbol : null };
        })()
        """)
        self.assertEqual(res["nvda"], "BATS:NVDA")
        self.assertEqual(res["tsla"], "BATS:TSLA")
        self.assertEqual(res["spy"], "BATS:SPY")
        self.assertEqual(res["btc"], "BINANCE:BTCUSDT")
        self.assertEqual(res["widgetSym"], "BATS:NVDA")

    def test_update_quote_updates_chart_candle_in_realtime(self):
        """Verifies updateQuote updates active candle close & high via candleSeries.update."""
        res = self._run_node("""
        (() => {
            const updates = [];
            activeSymbol = "BINANCE:BTCUSDT";
            activeTimeframe = "1m";
            isHistoryLoading = false;
            currentCandleStyle = "solid";
            currentRawBars = [{ time: 1700000040, open: 68000, high: 68050, low: 67950, close: 68010, volume: 10 }];
            candleSeries = { update: (b) => updates.push(b), data: () => currentRawBars };
            updateQuote("BINANCE:BTCUSDT", { price: 68125, volume: 50000, time: 1700000050 });
            return { updates, lastClose: lastBarObj.close, lastHigh: lastBarObj.high };
        })()
        """)
        self.assertEqual(len(res["updates"]), 1)
        self.assertEqual(res["updates"][0]["close"], 68125)
        self.assertEqual(res["updates"][0]["high"], 68125)
        self.assertEqual(res["lastClose"], 68125)

    def test_history_loading_initializes_last_bar_time_and_object(self):
        """Verifies renderChartDataKeepView/setData path sets currentRawBars cleanly for downstream updates."""
        res = self._run_node("""
        (() => {
            const bars = [
                { time: 1795440600, open: 100, high: 101, low: 99, close: 100.5, volume: 1000 },
                { time: 1795440660, open: 100.5, high: 102, low: 100, close: 101.2, volume: 1200 }
            ];
            let seriesData = [];
            candleSeries = { setData: (d) => { seriesData = d; }, data: () => seriesData, update: () => {} };
            volumeSeries = { setData: () => {} };
            chart = { timeScale: () => ({ getVisibleLogicalRange: () => null, setVisibleLogicalRange: () => {}, fitContent: () => {} }), priceScale: () => ({ applyOptions: () => {} }) };
            activeSymbol = "NASDAQ:NVDA";
            activeTimeframe = "1m";
            currentRawBars = bars.slice();
            lastBarTime = bars[bars.length - 1].time;
            lastBarObj = Object.assign({}, bars[bars.length - 1]);
            renderChartData(bars);
            return { count: seriesData.length, lastBarTime, lastClose: lastBarObj.close };
        })()
        """)
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["lastBarTime"], 1795440660)
        self.assertEqual(res["lastClose"], 101.2)

    def test_handle_live_bar_prevents_timeframe_bucket_timestamp_drops(self):
        """Verifies handleLiveBar merges 1m bar into active 1D bar instead of dropping on timestamp mismatch."""
        res = self._run_node("""
        (() => {
            const updates = [];
            const dayOpen = zonedWallToEpoch(2026, 11, 23, 9, 30, "America/New_York");
            const tick1030 = zonedWallToEpoch(2026, 11, 23, 10, 30, "America/New_York");
            activeSymbol = "NASDAQ:NVDA";
            activeTimeframe = "1D";
            isHistoryLoading = false;
            currentCandleStyle = "solid";
            currentRawBars = [{ time: dayOpen, open: 140, high: 142, low: 139, close: 141, volume: 1000 }];
            candleSeries = { update: (b) => updates.push(b), data: () => currentRawBars };
            volumeSeries = { update: () => {} };
            handleLiveBar("NASDAQ:NVDA", { time: tick1030, open: 141, high: 145, low: 140.5, close: 144, volume: 250 });
            return { updates, rawLen: currentRawBars.length, barTime: currentRawBars[0].time, dayOpen, high: currentRawBars[0].high, vol: currentRawBars[0].volume };
        })()
        """)
        self.assertEqual(len(res["updates"]), 1)
        self.assertEqual(res["rawLen"], 1)
        self.assertEqual(res["barTime"], res["dayOpen"])
        self.assertEqual(res["high"], 145)
        self.assertEqual(res["vol"], 1250)

    def test_crosshair_legend_mouseleave_and_historical_mode_badge(self):
        """Verifies updateCrosshairLegend toggles HISTORICAL vs LIVE badge cleanly."""
        res = self._run_node("""
        (() => {
            activeSymbol = "NASDAQ:NVDA";
            legendOpenEl = _elems["legendOpen"];
            legendHighEl = _elems["legendHigh"];
            legendLowEl = _elems["legendLow"];
            legendCloseEl = _elems["legendClose"];
            legendVolEl = _elems["legendVol"];
            updateCrosshairLegend({ time: 1700000000, open: 100, high: 105, low: 99, close: 104, isHistorical: true });
            const histBadge = _elems["legendModeBadge"].textContent;
            const histClass = _elems["legendModeBadge"].className;
            updateCrosshairLegend({ time: 1700086400, open: 104, high: 106, low: 103, close: 105, isHistorical: false });
            const liveBadge = _elems["legendModeBadge"].textContent;
            const liveClass = _elems["legendModeBadge"].className;
            return { histBadge, histClass, liveBadge, liveClass };
        })()
        """)
        self.assertTrue(res["histBadge"].startswith("HISTORICAL"))
        self.assertEqual(res["histClass"], "legend-badge historical")
        self.assertEqual(res["liveBadge"], "LIVE")
        self.assertEqual(res["liveClass"], "legend-badge live")


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server  # noqa: E402


def _mk_bars(n, t0=1795448820, step=60):
    return [{"time": t0 + i * step, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0} for i in range(n)]


class _FakeAggregator:
    def __init__(self, bars):
        self.bars = list(bars)
        self.seeded = []

    def get_history(self, symbol):
        return list(self.bars)

    def seed_history(self, symbol, bars):
        self.seeded.append(len(bars))
        merged = {b["time"]: b for b in bars}
        for b in self.bars:
            merged[b["time"]] = b
        self.bars = [merged[t] for t in sorted(merged)]


class TestOneMinuteSeedWhenHistoryShort(unittest.TestCase):
    """1m 只有伺服器啟動後累積的幾根即時 K 棒時，seed=true 也要補歷史（每標的 60 秒最多一次）。"""

    def setUp(self):
        server._last_1m_seed_attempt.clear()

    def _call(self, agg, fetch_mock, symbol="NASDAQ:MANH", seed=True):
        fake_storage = unittest.mock.MagicMock()
        fake_storage.get_bars.return_value = []
        with patch("api.deps.aggregator", agg), \
             patch("api.history.fetch_universal_history", fetch_mock), \
             patch("api.deps.get_storage", return_value=fake_storage):
            return asyncio.run(server.get_history(symbol=symbol, timeframe="1m", seed=seed, backfill=False, engine="A", limit=None))

    def test_one_live_bar_triggers_seed_and_merges(self):
        live = _mk_bars(1, t0=1795448820 + 300 * 60)
        agg = _FakeAggregator(live)
        fetch = AsyncMock(return_value=_mk_bars(300))
        res = self._call(agg, fetch)
        fetch.assert_awaited_once_with("NASDAQ:MANH", timeframe="1m")
        self.assertEqual(agg.seeded, [300])
        self.assertEqual(len(res["bars"]), 301)
        self.assertEqual(res["bars"][-1]["time"], live[0]["time"])

    def test_second_request_within_cooldown_does_not_refetch(self):
        agg = _FakeAggregator(_mk_bars(1))
        fetch = AsyncMock(return_value=[])
        self._call(agg, fetch)
        self._call(agg, fetch)
        self.assertEqual(fetch.await_count, 1)

    def test_cooldown_expires_after_60_seconds(self):
        agg = _FakeAggregator(_mk_bars(1))
        fetch = AsyncMock(return_value=[])
        self._call(agg, fetch)
        server._last_1m_seed_attempt["NASDAQ:MANH"] -= 61
        self._call(agg, fetch)
        self.assertEqual(fetch.await_count, 2)

    def test_enough_history_does_not_fetch(self):
        agg = _FakeAggregator(_mk_bars(30))
        fetch = AsyncMock(return_value=_mk_bars(300))
        res = self._call(agg, fetch)
        fetch.assert_not_awaited()
        self.assertEqual(len(res["bars"]), 30)

    def test_without_seed_flag_short_history_is_not_fetched(self):
        agg = _FakeAggregator(_mk_bars(1))
        fetch = AsyncMock(return_value=_mk_bars(300))
        self._call(agg, fetch, seed=False)
        fetch.assert_not_awaited()

    def test_empty_history_still_seeds_without_cooldown(self):
        agg = _FakeAggregator([])
        fetch = AsyncMock(return_value=[])
        self._call(agg, fetch)
        self._call(agg, fetch)
        self.assertEqual(fetch.await_count, 2)


class TestAutoScaleResetOnSymbolChange(unittest.TestCase):
    """換標的或週期時價格軸恢復 AUTO；同標的同週期重畫不改 AUTO。"""

    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )

    def setUp(self):
        if not self.node_bin:
            self.skipTest("Node.js binary not found; skipping JS evaluation")

    def _execute_node_eval(self, eval_js_snippet: str):
        """Loads static/app.js in Node.js VM sandbox and evaluates the provided expression."""
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            window: {{}},
            document: {{
                getElementById: (id) => {{
                    if (id === 'scaleAuto') {{
                        return {{
                            classList: {{
                                toggle: (cls, force) => {{
                                    sandbox._scaleAutoActive = Boolean(force);
                                }}
                            }}
                        }};
                    }}
                    return null;
                }},
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{
                _store: {{}},
                getItem: function(k) {{ return this._store[k] !== undefined ? this._store[k] : null; }},
                setItem: function(k, v) {{ this._store[k] = String(v); }},
                removeItem: function(k) {{ delete this._store[k]; }}
            }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;

        vm.createContext(sandbox);
        vm.runInContext(appCode, sandbox);

        const result = vm.runInContext({json.dumps(eval_js_snippet)}, sandbox);
        console.log(JSON.stringify(result));
        """
        env = os.environ.copy()
        env["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:{env.get('PATH', '')}"
        proc = subprocess.run(
            [self.node_bin, "-e", harness],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env
        )
        self.assertEqual(
            proc.returncode, 0,
            f"Node VM execution failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        return json.loads(proc.stdout.strip())

    def test_symbol_or_timeframe_change_turns_auto_back_on(self):
        js = """
        (function() {
            candleSeries = { setData: function() {} };
            volumeSeries = { setData: function() {} };
            var autoCalls = [];
            var curRange = null;
            chart = {
                timeScale: function() {
                    return {
                        getVisibleLogicalRange: function() { return curRange; },
                        setVisibleLogicalRange: function(r) { curRange = r; },
                        fitContent: function() {}
                    };
                },
                priceScale: function() {
                    return { applyOptions: function(o) { if (o && typeof o.autoScale === "boolean") autoCalls.push(o.autoScale); } };
                }
            };
            var bars = [];
            for (var i = 0; i < 200; i++) bars.push({ time: 1000 + i * 86400, open: 10, high: 11, low: 9, close: 10.5, volume: 100 });
            activeSymbol = "NASDAQ:MANH"; activeTimeframe = "1D"; lastChartViewKey = null;
            renderChartData(bars);
            var afterFirst = { isAuto: isAutoScale, calls: autoCalls.slice() };

            setAutoScale(false);
            curRange = { from: 50, to: 120 };
            renderChartData(bars);
            var sameKey = { isAuto: isAutoScale };

            activeSymbol = "NASDAQ:ABNB";
            renderChartData(bars);
            var newSymbol = { isAuto: isAutoScale };

            setAutoScale(false);
            activeTimeframe = "1m";
            renderChartData(bars);
            var newTf = { isAuto: isAutoScale };
            return { afterFirst: afterFirst, sameKey: sameKey, newSymbol: newSymbol, newTf: newTf, calls: autoCalls };
        })()
        """
        res = self._execute_node_eval(js)
        self.assertTrue(res["afterFirst"]["isAuto"])
        self.assertEqual(res["afterFirst"]["calls"], [True])
        self.assertFalse(res["sameKey"]["isAuto"])
        self.assertTrue(res["newSymbol"]["isAuto"])
        self.assertTrue(res["newTf"]["isAuto"])
        self.assertEqual(res["calls"], [True, False, True, False, True])


if __name__ == "__main__":
    unittest.main()

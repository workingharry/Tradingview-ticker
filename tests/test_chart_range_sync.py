import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_JS = PROJECT_ROOT / "static" / "app.js"

SIM = r"""
function MockChart(times) {
  var self = this;
  this.times = times.slice();
  this.range = { from: 0, to: times.length - 1 };
  this.logicalSubs = []; this.timeSubs = []; this.queue = [];
  var ts = {
    getVisibleLogicalRange: function() { return { from: self.range.from, to: self.range.to }; },
    setVisibleLogicalRange: function(r) { self._set({ from: r.from, to: r.to }); },
    getVisibleRange: function() { return self._timeRange(); },
    setVisibleRange: function(r) { self._set({ from: self._idx(r.from), to: self._idx(r.to) }); },
    subscribeVisibleLogicalRangeChange: function(fn) { self.logicalSubs.push(fn); },
    subscribeVisibleTimeRangeChange: function(fn) { self.timeSubs.push(fn); }
  };
  this.timeScale = function() { return ts; };
}
MockChart.prototype._idx = function(t) { var b = 0; for (var i = 0; i < this.times.length; i++) if (this.times[i] <= t) b = i; return b; };
MockChart.prototype._timeRange = function() {
  var n = this.times.length, a = Math.max(0, Math.ceil(this.range.from)), b = Math.min(n - 1, Math.floor(this.range.to));
  return a > b ? null : { from: this.times[a], to: this.times[b] };
};
MockChart.prototype._set = function(r) {
  if (Math.abs(r.from - this.range.from) < 1e-9 && Math.abs(r.to - this.range.to) < 1e-9) return;
  this.range = r; var self = this;
  this.queue.push(function() {
    self.logicalSubs.forEach(function(fn) { fn({ from: self.range.from, to: self.range.to }); });
    var tr = self._timeRange(); self.timeSubs.forEach(function(fn) { fn(tr); });
  });
};
function flush(cs) { for (var k = 0; k < 50; k++) { var any = false; cs.forEach(function(c) { var q = c.queue; c.queue = []; q.forEach(function(f) { any = true; f(); }); }); if (!any) return k; } return 50; }
var N = 300, times = []; for (var i = 0; i < N; i++) times.push(1795000000 + i * 3600);
var main = new MockChart(times), rs = new MockChart(times), redraws = 0;
bindLogicalRangeSync(main, rs, function() { redraws++; });
main.range = { from: N - 40, to: N + 5 };
var widths = [], rsMatch = true, maxRounds = 0;
for (var s = 0; s < 8; s++) {
  main.timeScale().setVisibleLogicalRange({ from: main.range.from + 4, to: main.range.to + 4 });
  maxRounds = Math.max(maxRounds, flush([main, rs]));
  widths.push(+(main.range.to - main.range.from).toFixed(6));
  if (rs.range.from !== main.range.from || rs.range.to !== main.range.to) rsMatch = false;
}
rs.timeScale().setVisibleLogicalRange({ from: rs.range.from - 10, to: rs.range.to - 10 }); flush([main, rs]);
var sc = new MockChart([1, 2, 3]); sc.range = { from: 0, to: 2 };
({
  widths: widths, rsMatch: rsMatch, maxRounds: maxRounds, redraws: redraws,
  mainFollowsRs: main.range.from === rs.range.from && main.range.to === rs.range.to,
  padBasic: padToTimeAxis([{ time: 20, value: 1 }, { time: 40, value: 2 }], [10, 20, 30, 40]),
  padExtra: padToTimeAxis([{ time: 25, value: 9 }], [10, 20, 30]),
  padEmptyAxis: padToTimeAxis([{ time: 5, value: 1 }], []),
  padNull: padToTimeAxis(null, [1, 2]),
  axisFromSeries: getSeriesTimeAxis({ data: function() { return [{ time: 1 }, { time: 2 }, { time: 2 }, { time: 3 }]; } }, null),
  axisFallback: getSeriesTimeAxis(null, [{ time: 7 }, { time: 8 }]),
  syncNoop: syncLogicalRangeTo(sc, { from: 0, to: 2 }),
  syncSet: syncLogicalRangeTo(sc, { from: 1, to: 5 }),
  syncBad: syncLogicalRangeTo(sc, { from: NaN, to: 2 })
})
"""


def _node():
    for c in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if c and Path(c).exists():
            return c
    return None


class TestChartRangeSyncNoAutoZoom(unittest.TestCase):
    """滑動圖表時自動放大到最大（主圖與 RS 副圖用時間範圍互相同步）的回歸測試。"""

    @classmethod
    def setUpClass(cls):
        cls.code = APP_JS.read_text(encoding="utf-8")
        cls.node = _node()

    def _execute_node_eval(self, eval_js_snippet: str):
        """Loads static/app.js in Node.js VM sandbox and evaluates the provided expression."""
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: () => null,
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
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

    def _execute_node_eval_async(self, eval_js_snippet: str):
        """Same as _execute_node_eval, but awaits a Promise returned by the snippet before printing."""
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const appCode = fs.readFileSync({json.dumps(str(self.app_js_path))}, 'utf8');
        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: () => null,
                querySelector: () => null,
                querySelectorAll: () => [],
                createElement: () => ({{ appendChild: () => {{}}, style: {{}}, addEventListener: () => {{}} }}),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            console: console,
            Intl: Intl,
            setTimeout: setTimeout
        }};
        sandbox.globalThis = sandbox;
        sandbox.window = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(appCode, sandbox);
        Promise.resolve(vm.runInContext({json.dumps(eval_js_snippet)}, sandbox))
            .then((result) => {{ console.log(JSON.stringify(result)); }})
            .catch((err) => {{ console.error(err && err.stack ? err.stack : String(err)); process.exit(3); }});
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

    def _run(self):
        if not self.node:
            self.skipTest("找不到 node")
        self.node_bin = self.node
        self.app_js_path = APP_JS
        return self._execute_node_eval(SIM)

    def test_static_no_time_range_sync_between_main_and_rs(self):
        self.assertNotIn("subscribeVisibleTimeRangeChange(function(range)", self.code)
        self.assertNotIn("subscribeVisibleTimeRangeChange(function(newRange)", self.code)
        self.assertIn("bindLogicalRangeSync(chart, rsChart", self.code)
        self.assertIn("bindLogicalRangeSync(subMainChart, rsChartInstance)", self.code)

    def test_static_rs_data_padded_to_main_time_axis(self):
        for s in (
            "rsLinePaneSeries.setData(padToTimeAxis(toolkit.mansfieldRS, rsAxis))",
            "rsEmaPaneSeries.setData(padToTimeAxis(atrLineData, rsAxis))",
            "rsLinePaneSeries.setData(padToTimeAxis(rsData, rsAxis))",
            "rsEmaPaneSeries.setData(padToTimeAxis(rsEma, rsAxis))",
            "subObj.rsLineSeries.setData(padToTimeAxis(toolkit.mansfieldRS, subRsAxis))",
            "subObj.rsEmaSeries.setData(padToTimeAxis(atrData, subRsAxis))",
            "subObj.rsLineSeries.setData(padToTimeAxis(rsData, subRsAxis))",
            "subObj.rsEmaSeries.setData(padToTimeAxis(rsEma, subRsAxis))",
        ):
            self.assertIn(s, self.code)

    def test_scrolling_into_right_whitespace_keeps_zoom(self):
        r = self._run()
        self.assertEqual(r["widths"], [45.0] * 8)
        self.assertTrue(r["rsMatch"])
        self.assertTrue(r["mainFollowsRs"])
        self.assertLessEqual(r["maxRounds"], 3)
        self.assertGreater(r["redraws"], 0)

    def test_pad_and_axis_helpers(self):
        r = self._run()
        self.assertEqual(r["padBasic"], [{"time": 10}, {"time": 20, "value": 1}, {"time": 30}, {"time": 40, "value": 2}])
        self.assertEqual(r["padExtra"], [{"time": 10}, {"time": 20}, {"time": 25, "value": 9}, {"time": 30}])
        self.assertEqual(r["padEmptyAxis"], [{"time": 5, "value": 1}])
        self.assertEqual(r["padNull"], [{"time": 1}, {"time": 2}])
        self.assertEqual(r["axisFromSeries"], [1, 2, 3])
        self.assertEqual(r["axisFallback"], [7, 8])
        self.assertFalse(r["syncNoop"])
        self.assertTrue(r["syncSet"])
        self.assertFalse(r["syncBad"])


if __name__ == "__main__":
    unittest.main()

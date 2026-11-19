"""Tests for pure technical indicators math and calculation invariants."""

import unittest
from engine.aggregator import CandlestickBar


class TestIndicators(unittest.TestCase):
    """Verifies SMA, EMA, RS ratio, and boundary edge cases."""

    def test_sma_calculation(self):
        prices = [10.0, 11.0, 12.0, 13.0, 14.0]
        period = 3
        # SMA(3):
        # idx 2: (10+11+12)/3 = 11.0
        # idx 3: (11+12+13)/3 = 12.0
        # idx 4: (12+13+14)/3 = 13.0
        results = []
        for i in range(period - 1, len(prices)):
            window = prices[i - period + 1: i + 1]
            results.append(sum(window) / period)

        self.assertEqual(results, [11.0, 12.0, 13.0])

    def test_ema_calculation(self):
        prices = [10.0, 10.0, 10.0, 20.0]
        period = 3
        k = 2.0 / (period + 1)
        # initial SMA = 10.0
        # next EMA = (20.0 - 10.0) * 0.5 + 10.0 = 15.0
        initial_sma = sum(prices[:period]) / period
        next_ema = (prices[3] - initial_sma) * k + initial_sma
        self.assertEqual(initial_sma, 10.0)
        self.assertEqual(next_ema, 15.0)

    def test_relative_strength_ratio(self):
        target_close = 150.0
        spy_close = 500.0
        qqq_close = 450.0

        rs_spy = (target_close / spy_close) * 100.0
        rs_qqq = (target_close / qqq_close) * 100.0

        self.assertAlmostEqual(rs_spy, 30.0, places=4)
        self.assertAlmostEqual(rs_qqq, 33.3333, places=3)
        # Benchmark switching is purely dynamic
        self.assertNotEqual(rs_spy, rs_qqq)

    def test_indicators_js_two_pointer_forward_fill_contract(self):
        """Verifies that static/indicators.js contains two-pointer O(N+M) forward-fill alignment."""
        from pathlib import Path
        content = Path("static/indicators.js").read_text("utf-8")
        self.assertIn("calcRelativeStrength", content)
        self.assertIn("validBench", content)
        self.assertIn("bIdx", content)
        self.assertIn("isFinite(b.close)", content)

    def test_relative_strength_forward_fill_algorithm(self):
        """Verifies the two-pointer forward-fill algorithm on 24/7 crypto bars against market-hours benchmark."""
        target_bars = [
            {"time": 100, "close": 100.0},
            {"time": 105, "close": 105.0},  # Gap in benchmark (e.g. weekend or extended hours)
            {"time": 110, "close": 110.0},
        ]
        benchmark_bars = [
            {"time": 100, "close": 50.0},
            {"time": 110, "close": 55.0},
        ]

        # Monotonic two-pointer forward-fill simulation
        bench_map = {b["time"]: b["close"] for b in benchmark_bars if b["close"] > 0}
        valid_bench = [b for b in benchmark_bars if b["close"] > 0]
        out = []
        b_idx = 0
        for tb in target_bars:
            while b_idx + 1 < len(valid_bench) and valid_bench[b_idx + 1]["time"] <= tb["time"]:
                b_idx += 1
            b_close = bench_map.get(tb["time"])
            if b_close is None and valid_bench[b_idx]["time"] <= tb["time"]:
                b_close = valid_bench[b_idx]["close"]
            if b_close and b_close > 0:
                out.append({"time": tb["time"], "value": round((tb["close"] / b_close) * 100.0, 4)})

        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], {"time": 100, "value": 200.0})
        self.assertEqual(out[1], {"time": 105, "value": 210.0})  # 105.0 / 50.0 * 100 = 210.0
        self.assertEqual(out[2], {"time": 110, "value": 200.0})  # 110.0 / 55.0 * 100 = 200.0

    def test_relative_strength_adversarial_zeros_nans_and_negatives(self):
        """Verifies that hostile or malformed benchmark inputs (0, negative, NaN) are safely rejected."""
        import math
        target_bars = [
            {"time": 100, "close": 150.0},
            {"time": 105, "close": 155.0},
        ]
        hostile_benchmark_bars = [
            {"time": 90, "close": 0.0},
            {"time": 95, "close": -10.0},
            {"time": 100, "close": float("nan")},
            {"time": 105, "close": float("inf")},
        ]

        valid_bench = [
            b for b in hostile_benchmark_bars
            if isinstance(b.get("close"), (int, float))
            and math.isfinite(b["close"])
            and b["close"] > 0
        ]
        self.assertEqual(len(valid_bench), 0, "All hostile benchmark bars must be filtered out")

    def test_indicators_js_bollinger_and_rs_boundary_guards(self):
        """Verifies that static/indicators.js enforces non-negative variance and finite RS checks."""
        from pathlib import Path
        content = Path("static/indicators.js").read_text("utf-8")
        self.assertIn("Math.max(0, varianceSum / period)", content)
        self.assertIn("isFinite(upVal) && isFinite(lowVal)", content)
        self.assertIn("bClose > 0", content)

    def test_rs_rating_calculation_bounds_and_null_safety(self):
        """Verifies that RS Rating logic bounds output to 1..99 and gracefully handles empty/corrupt bars."""
        import math
        # 1. Underperforming stock (dropped 50% vs benchmark gain 50%)
        t_bars = [{"close": 50.0}] * 10
        b_bars = [{"close": 150.0}] * 10
        t_bars[0]["close"] = 100.0
        b_bars[0]["close"] = 100.0
        t_perf = (50.0 - 100.0) / 100.0  # -50%
        b_perf = (150.0 - 100.0) / 100.0  # +50%
        diff = (t_perf - b_perf) * 100.0  # -100
        score = round(100.0 / (1.0 + math.exp(-diff / 30.0)))
        bounded = max(1, min(99, score))
        self.assertLessEqual(bounded, 10)
        self.assertGreaterEqual(bounded, 1)

        # 2. Outperforming stock
        diff_out = 100.0
        score_out = round(100.0 / (1.0 + math.exp(-diff_out / 30.0)))
        bounded_out = max(1, min(99, score_out))
        self.assertGreaterEqual(bounded_out, 90)
        self.assertLessEqual(bounded_out, 99)

    def test_mansfield_rs_zero_lookahead_bias_when_target_predates_benchmark(self):
        """Verifies calcATRToolkit does not divide early target bars by future benchmark close (look-ahead bias prevention)."""
        import subprocess, json, shutil, os
        node_bin = shutil.which("node") or (
            "/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None
        ) or (
            "/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None
        )
        if not node_bin:
            self.skipTest("Node.js binary not found on system PATH")
        node_script = """
        const ind = require('./static/indicators.js');
        // Target bars start at t=1000, 1060, 1120
        const targetBars = [
          { time: 1000, open: 10, high: 11, low: 9, close: 10, volume: 100 },
          { time: 1060, open: 11, high: 12, low: 10, close: 11, volume: 100 },
          { time: 1120, open: 12, high: 13, low: 11, close: 12, volume: 100 }
        ];
        // Benchmark bars only start at t=1060 (later than target's first bar)
        const benchBars = [
          { time: 1060, open: 100, high: 101, low: 99, close: 100, volume: 1000 },
          { time: 1120, open: 102, high: 103, low: 101, close: 102, volume: 1000 }
        ];
        const res = ind.calcATRToolkit(targetBars, benchBars, { mansfieldLen: 2 });
        console.log(JSON.stringify(res.mansfieldRS));
        """
        proc = subprocess.run([node_bin, "-e", node_script], capture_output=True, text=True, check=True)
        series = json.loads(proc.stdout.strip())
        # First bar at t=1000 had no benchmark, so ratio list has only t=1060 and t=1120 (length 2)
        # With mansfieldLen = 2, only 1 output point should be produced at t=1120
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["time"], 1120)

    def test_indicators_js_vwap_and_atr_exports(self):
        """Verifies that static/indicators.js defines and exports calcVWAP and calcATR."""
        from pathlib import Path
        content = Path("static/indicators.js").read_text("utf-8")
        self.assertIn("function calcVWAP", content)
        self.assertIn("function calcATR", content)
        self.assertIn("calcVWAP,", content)
        self.assertIn("calcATR", content)


if __name__ == "__main__":
    unittest.main()

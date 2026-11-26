"""Unit and integration tests for engine.storage SQLite persistence and App Nap mechanisms.

Verifies WAL mode, busy timeout, directory auto-creation, concurrent writes,
data upsert/overwrite semantics, null/empty handling, close cleanup, retention pruning,
server caching/offline fallback, and static/app.js App Nap gap backfill behavior.
"""

import asyncio
import os
import shutil
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, AsyncMock
import unittest

from engine.storage import Storage, get_storage, close_storage, DEFAULT_STORAGE_PATH
from engine.aggregator import CandlestickBar
import server


class TestStorageAndAppNap(unittest.TestCase):
    """Unit and integration tests for SQLite persistence and App Nap throttling."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp_dir)
        self.orig_db_env = os.environ.get("TERMINAL_DB_PATH")
        os.environ["TERMINAL_DB_PATH"] = str(self.tmp_path / "test.sqlite")
        close_storage()

    def tearDown(self):
        close_storage()
        if self.orig_db_env is not None:
            os.environ["TERMINAL_DB_PATH"] = self.orig_db_env
        else:
            os.environ.pop("TERMINAL_DB_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_wal_mode_and_busy_timeout(self):
        """Verifies SQLite WAL journal mode and 5000ms busy timeout are enabled on the database."""
        db_file = self.tmp_path / "test_wal.sqlite"
        with Storage(db_path=db_file) as storage:
            conn = storage._conn
            cursor = conn.cursor()

            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            cursor.execute("PRAGMA busy_timeout;")
            busy_timeout = cursor.fetchone()[0]
            self.assertGreaterEqual(busy_timeout, 5000)

    def test_auto_create_directory(self):
        """Verifies that nested non-existent directory trees are created automatically."""
        deep_path = self.tmp_path / "nested" / "app_support" / "terminal.sqlite"
        self.assertFalse(deep_path.parent.exists())

        storage = Storage(db_path=deep_path)
        try:
            self.assertTrue(deep_path.parent.exists())
            self.assertTrue(deep_path.exists())
            self.assertEqual(
                storage.save_bars("BTC", "1m", [{"time": 100, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]),
                1
            )
        finally:
            storage.close()

    def test_concurrent_writes(self):
        """Verifies thread-safe concurrent writes to a single SQLite persistent database."""
        db_file = self.tmp_path / "test_concurrent.sqlite"
        storage = Storage(db_path=db_file)

        def write_worker(worker_id: int):
            bars = [
                {
                    "time": 1000 + worker_id * 100 + i,
                    "open": 100.0 + i,
                    "high": 105.0 + i,
                    "low": 95.0 + i,
                    "close": 102.0 + i,
                    "volume": 50.0 + i,
                }
                for i in range(20)
            ]
            return storage.save_bars(f"SYM_{worker_id % 3}", "1m", bars)

        workers = 8
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(write_worker, wid) for wid in range(workers)]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), workers)
        self.assertTrue(all(r == 20 for r in results))

        for wid in range(3):
            saved = storage.get_bars(f"SYM_{wid}", "1m", limit=200)
            self.assertGreater(len(saved), 0)

        storage.close()

    def test_data_upsert_overwrite(self):
        """Verifies that inserting duplicate (symbol, timeframe, time) updates existing bar records."""
        db_file = self.tmp_path / "test_upsert.sqlite"
        with Storage(db_path=db_file) as storage:
            initial_bar = {
                "time": 1700000000,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 105.0,
                "volume": 1000.0,
            }
            res1 = storage.save_bars("BINANCE:BTCUSDT", "1m", [initial_bar])
            self.assertEqual(res1, 1)

            bars1 = storage.get_bars("BINANCE:BTCUSDT", "1m")
            self.assertEqual(len(bars1), 1)
            self.assertEqual(bars1[0]["close"], 105.0)

            updated_bar = {
                "time": 1700000000,
                "open": 100.0,
                "high": 115.0,
                "low": 88.0,
                "close": 112.5,
                "volume": 2500.0,
            }
            res2 = storage.save_bars("BINANCE:BTCUSDT", "1m", [updated_bar])
            self.assertEqual(res2, 1)

            bars2 = storage.get_bars("BINANCE:BTCUSDT", "1m")
            self.assertEqual(len(bars2), 1)
            self.assertEqual(bars2[0]["high"], 115.0)
            self.assertEqual(bars2[0]["low"], 88.0)
            self.assertEqual(bars2[0]["close"], 112.5)
            self.assertEqual(bars2[0]["volume"], 2500.0)

    def test_empty_and_null_handling(self):
        """Verifies safe handling of empty inputs, corrupt records, and missing fields."""
        db_file = self.tmp_path / "test_empty.sqlite"
        with Storage(db_path=db_file) as storage:
            self.assertEqual(storage.save_bars("BTC", "1m", []), 0)
            self.assertEqual(storage.save_bars("", "1m", [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]), 0)
            self.assertEqual(storage.save_bars("BTC", "", [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]), 0)

            mixed_records = [
                {"time": 100, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 100.0},
                {"time": None, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0},
                {"time": 101, "open": "not_a_float", "high": 12.0, "low": 9.0, "close": 11.0},
                {"invalid_key": 999},
                CandlestickBar(time=102, open=11.0, high=13.0, low=10.0, close=12.5, volume=200.0),
            ]
            saved_count = storage.save_bars("BTC", "1m", mixed_records)
            self.assertEqual(saved_count, 2)

            stored = storage.get_bars("BTC", "1m")
            self.assertEqual(len(stored), 2)
            self.assertEqual(stored[0]["time"], 100)
            self.assertEqual(stored[1]["time"], 102)

            self.assertEqual(storage.get_bars("NONEXISTENT", "1m"), [])
            self.assertEqual(storage.get_bars("BTC", "1m", limit=0), [])
            self.assertEqual(storage.get_bars("BTC", "1m", limit=-5), [])

    def test_close_and_cleanup(self):
        """Verifies idempotent closing and error raising on post-close operations."""
        db_file = self.tmp_path / "test_close.sqlite"
        storage = Storage(db_path=db_file)
        self.assertFalse(storage.is_closed)

        storage.save_bars("BTC", "1m", [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
        storage.close()
        self.assertTrue(storage.is_closed)

        storage.close()
        self.assertTrue(storage.is_closed)

        with self.assertRaises(RuntimeError):
            storage.save_bars("BTC", "1m", [{"time": 2, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])

        with self.assertRaises(RuntimeError):
            storage.get_bars("BTC", "1m")

        with self.assertRaises(RuntimeError):
            storage.prune_bars()

    def test_prune_bars(self):
        """Verifies that prune_bars retains only the most recent N bars per series."""
        db_file = self.tmp_path / "test_prune.sqlite"
        with Storage(db_path=db_file) as storage:
            btc_bars = [
                {"time": 1000 + i * 60, "open": 10, "high": 15, "low": 8, "close": 12, "volume": 100}
                for i in range(20)
            ]
            eth_bars = [
                {"time": 2000 + i * 60, "open": 20, "high": 25, "low": 18, "close": 22, "volume": 200}
                for i in range(20)
            ]
            storage.save_bars("BINANCE:BTCUSDT", "1m", btc_bars)
            storage.save_bars("BINANCE:ETHUSDT", "1m", eth_bars)

            deleted = storage.prune_bars(keep_per_series=5)
            self.assertEqual(deleted, 30)

            remaining_btc = storage.get_bars("BINANCE:BTCUSDT", "1m", limit=100)
            remaining_eth = storage.get_bars("BINANCE:ETHUSDT", "1m", limit=100)
            self.assertEqual(len(remaining_btc), 5)
            self.assertEqual(len(remaining_eth), 5)
            self.assertEqual(remaining_btc[0]["time"], 1000 + 15 * 60)
            self.assertEqual(remaining_btc[-1]["time"], 1000 + 19 * 60)

            with self.assertRaises(ValueError):
                storage.prune_bars(keep_per_series=-1)

    def test_environment_variable_override(self):
        """Verifies TERMINAL_DB_PATH overrides the default database path."""
        custom_db = self.tmp_path / "custom_env" / "market.sqlite"
        os.environ["TERMINAL_DB_PATH"] = str(custom_db)

        close_storage()
        shared_storage = get_storage()
        try:
            self.assertEqual(shared_storage.db_path, custom_db.resolve())
            self.assertTrue(custom_db.parent.exists())
        finally:
            close_storage()

    def test_server_fetch_historical_bars_caching(self):
        """Verifies server.fetch_historical_bars writes remote responses to SQLite cache."""
        test_db = self.tmp_path / "server_test.sqlite"
        os.environ["TERMINAL_DB_PATH"] = str(test_db)
        close_storage()

        mock_remote_bars = [
            {"time": 1710000000 + i * 60, "open": 100.0, "high": 105.0, "low": 98.0, "close": 102.0, "volume": 50.0}
            for i in range(5)
        ]

        async def _test():
            with patch("api.history.fetch_binance_history", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = mock_remote_bars
                bars = await server.fetch_historical_bars("BINANCE:BTCUSDT", timeframe="1m")
                self.assertEqual(len(bars), 5)
                self.assertEqual(mock_fetch.await_count, 1)

            storage = get_storage()
            cached = storage.get_bars("BINANCE:BTCUSDT", "1m")
            self.assertEqual(len(cached), 5)
            self.assertEqual(cached[0]["time"], 1710000000)

        try:
            asyncio.run(_test())
        finally:
            close_storage()

    def test_server_offline_sqlite_fallback(self):
        """Verifies server.fetch_historical_bars falls back to local SQLite cache when remote source fails."""
        test_db = self.tmp_path / "server_offline.sqlite"
        os.environ["TERMINAL_DB_PATH"] = str(test_db)
        close_storage()

        storage = get_storage()
        pre_cached = [
            {"time": 1690000000 + i * 60, "open": 50.0, "high": 55.0, "low": 48.0, "close": 52.0, "volume": 30.0}
            for i in range(3)
        ]
        storage.save_bars("NASDAQ:AAPL", "1m", pre_cached)

        async def _test():
            with patch("api.history.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo:
                mock_yahoo.return_value = []
                bars = await server.fetch_historical_bars("NASDAQ:AAPL", timeframe="1m")
                self.assertEqual(len(bars), 3)
                self.assertEqual(bars[0]["time"], 1690000000)
                self.assertEqual(bars[-1]["close"], 52.0)

            with patch("api.history.fetch_yahoo_history", new_callable=AsyncMock) as mock_yahoo_err:
                mock_yahoo_err.side_effect = RuntimeError("Network connection refused")
                bars_err = await server.fetch_historical_bars("NASDAQ:AAPL", timeframe="1m")
                self.assertEqual(len(bars_err), 3)
                self.assertEqual(bars_err[0]["time"], 1690000000)

        try:
            asyncio.run(_test())
        finally:
            close_storage()

    def test_cold_start_seeding_and_bar_completed_persistence(self):
        """Verifies cold-start pre-seeding from SQLite and live bar completion persistence."""
        test_db = self.tmp_path / "cold_start.sqlite"
        os.environ["TERMINAL_DB_PATH"] = str(test_db)
        close_storage()

        storage = get_storage()
        seed_bars = [
            {"time": 1720000000 + i * 60, "open": 60000.0, "high": 60100.0, "low": 59900.0, "close": 60050.0, "volume": 10.0}
            for i in range(5)
        ]
        storage.save_bars("BINANCE:BTCUSDT", "1m", seed_bars)

        completed_bar = CandlestickBar(
            time=1720000000 + 5 * 60,
            open=60050.0,
            high=60200.0,
            low=60000.0,
            close=60150.0,
            volume=15.0
        )
        server.on_bar_completed_handler("BINANCE:BTCUSDT", completed_bar)

        stored = storage.get_bars("BINANCE:BTCUSDT", "1m", limit=10)
        self.assertEqual(len(stored), 6)
        self.assertEqual(stored[-1]["time"], 1720000000 + 5 * 60)
        self.assertEqual(stored[-1]["close"], 60150.0)

        close_storage()

    def test_save_bars_tuple_format_and_millisecond_timestamp(self):
        """Verifies that save_bars supports tuple/list records and auto-normalizes millisecond timestamps."""
        db_file = self.tmp_path / "test_tuples.sqlite"
        with Storage(db_path=db_file) as storage:
            records = [
                (1730000000000, "100.5", "105.0", "99.0", "102.5", "500"),
                [1730000060000, 102.5, 108.0, 101.0, 107.0, 600]
            ]
            saved = storage.save_bars("BINANCE:BTCUSDT", "1m", records)
            self.assertEqual(saved, 2)

            bars = storage.get_bars("BINANCE:BTCUSDT", "1m")
            self.assertEqual(len(bars), 2)
            self.assertEqual(bars[0]["time"], 1730000000)
            self.assertEqual(bars[1]["time"], 1730000060)
            self.assertEqual(bars[0]["close"], 102.5)
            self.assertEqual(bars[1]["close"], 107.0)

    def test_get_storage_path_switching(self):
        """Verifies get_storage switches gracefully when provided a different db_path."""
        path1 = self.tmp_path / "db1.sqlite"
        path2 = self.tmp_path / "db2.sqlite"

        s1 = get_storage(path1)
        self.assertEqual(s1.db_path, path1.resolve())

        s2 = get_storage(path2)
        self.assertEqual(s2.db_path, path2.resolve())
        self.assertTrue(s1.is_closed)

        close_storage()

    def test_app_js_app_nap_and_backfill(self):
        """Executes handleVisibilityChange in Node VM to verify App Nap RAF pause and >60s gap backfill."""
        import json
        import os
        import shutil
        import subprocess
        node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )
        if not node_bin:
            self.skipTest("Node.js binary not found")
        app_js = str(Path("static/app.js").resolve())
        harness = f"""
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync({json.dumps(app_js)}, 'utf8');
        const origDateNow = Date.now;
        const doc = {{
            hidden: false,
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            createElement: () => ({{ style: {{}}, classList: {{ add(){{}}, remove(){{}}, toggle(){{}} }}, appendChild(){{}}, addEventListener(){{}} }}),
            addEventListener: () => {{}},
            removeEventListener: () => {{}}
        }};
        const sandbox = {{
            setTimeout, clearTimeout, AbortController, URLSearchParams, console, Intl,
            _nowVal: 1700000000000,
            _cancelled: 0,
            _rafScheduled: 0,
            _fetches: [],
            Date: Object.assign(Date, {{ now: () => sandbox._nowVal }}),
            requestAnimationFrame: (cb) => {{ sandbox._rafScheduled++; return sandbox._rafScheduled; }},
            cancelAnimationFrame: () => {{ sandbox._cancelled++; }},
            fetch: (url) => {{ sandbox._fetches.push(String(url)); return Promise.resolve({{ ok: true, json: () => Promise.resolve({{ bars: [] }}) }}); }},
            document: doc,
            localStorage: {{ _s: {{}}, getItem(k){{ return this._s[k] ?? null; }}, setItem(k,v){{ this._s[k]=String(v); }}, removeItem(k){{ delete this._s[k]; }} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', protocol: 'http:', host: '127.0.0.1:8000' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}}
        }};
        sandbox.window = sandbox;
        sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox);
        const res = vm.runInContext(`(() => {{
            activeSymbol = "NASDAQ:NVDA";
            activeTimeframe = "1D";
            rafRenderId = 99;
            document.hidden = true;
            handleVisibilityChange();
            const hiddenSuspended = isAppNapSuspended;
            _nowVal += 65000;
            document.hidden = false;
            handleVisibilityChange();
            return {{
                hiddenSuspended,
                resumedSuspended: isAppNapSuspended,
                cancelled: _cancelled,
                rafScheduled: _rafScheduled,
                fetches: _fetches
            }};
        }})()`, sandbox);
        Date.now = origDateNow;
        console.log(JSON.stringify(res));
        process.exit(0);
        """
        proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(data["hiddenSuspended"])
        self.assertFalse(data["resumedSuspended"])
        self.assertGreaterEqual(data["cancelled"], 1)
        self.assertGreaterEqual(data["rafScheduled"], 1)
        self.assertTrue(any("backfill=true" in u for u in data["fetches"]), f"Expected backfill=true fetch, got {data['fetches']}")

    def test_server_app_nap_backfill_heals_aggregator_gap(self):
        """Verifies GET /api/history with backfill=true fetches and merges missing bars into a pre-seeded aggregator."""
        from fastapi.testclient import TestClient
        pre_sleep = [
            {"time": 1731234000, "open": 68000.0, "high": 68050.0, "low": 67980.0, "close": 68020.0, "volume": 10.0}
        ]
        post_sleep = [
            {"time": 1731234000, "open": 68000.0, "high": 68050.0, "low": 67980.0, "close": 68020.0, "volume": 10.0},
            {"time": 1731234060, "open": 68020.0, "high": 68100.0, "low": 68010.0, "close": 68090.0, "volume": 15.0},
            {"time": 1731234120, "open": 68090.0, "high": 68200.0, "low": 68080.0, "close": 68180.0, "volume": 22.0},
        ]
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock), \
             patch("api.history.fetch_binance_history", new_callable=AsyncMock) as mock_binance:
            mock_binance.side_effect = [pre_sleep, [], [], post_sleep]
            with TestClient(server.app) as client:
                initial = client.get("/api/history?symbol=BINANCE:BTCUSDT&timeframe=1m")
                self.assertEqual(len(initial.json()["bars"]), 1)

                backfilled = client.get("/api/history?symbol=BINANCE:BTCUSDT&timeframe=1m&seed=true&backfill=true")
                bars = backfilled.json()["bars"]
                self.assertEqual(len(bars), 3)
                self.assertEqual([b["time"] for b in bars], [1731234000, 1731234060, 1731234120])
                self.assertEqual(bars[-1]["close"], 68180.0)

    def test_save_bars_filters_nan_inf_and_normalizes_ohlc(self):
        """Verifies that save_bars drops NaN/Inf/<=0 prices and enforces high >= max(o, c) and low <= min(o, c)."""
        db_file = self.tmp_path / "test_norm.sqlite"
        with Storage(db_path=db_file) as storage:
            records = [
                {"time": 1730000000, "open": 100.0, "high": 90.0, "low": 110.0, "close": 105.0, "volume": -5.0},
                {"time": 1730000060, "open": float("nan"), "high": 105.0, "low": 95.0, "close": 100.0, "volume": 10.0},
                {"time": 1730000120, "open": 100.0, "high": float("inf"), "low": 95.0, "close": 100.0, "volume": 10.0},
                {"time": 1730000180, "open": 0.0, "high": 10.0, "low": 0.0, "close": 5.0, "volume": 10.0},
            ]
            saved = storage.save_bars("TEST:NORM", "1m", records)
            self.assertEqual(saved, 1)

            bars = storage.get_bars("TEST:NORM", "1m")
            self.assertEqual(len(bars), 1)
            b = bars[0]
            self.assertEqual(b["open"], 100.0)
            self.assertEqual(b["close"], 105.0)
            self.assertEqual(b["high"], 105.0)
            self.assertEqual(b["low"], 100.0)
            self.assertEqual(b["volume"], 0.0)

    def test_watchlist_persistence_and_crud(self):
        """Verifies that watchlist table auto-seeds defaults, persists additions, and handles removals."""
        db_file = self.tmp_path / "test_wl.sqlite"
        with Storage(db_path=db_file) as storage:
            # 1. Initial read auto-seeds default symbols
            wl = storage.get_watchlist()
            self.assertEqual(len(wl), 9)
            self.assertEqual(wl[0]["id"], "NASDAQ:NVDA")
            self.assertEqual(wl[0]["sector"], "Technology")
            self.assertEqual(wl[0]["industry"], "Semiconductors")

            # 2. Add new symbol with sector and industry
            added = storage.add_watchlist_item({
                "id": "NASDAQ:TSLA",
                "name": "Tesla, Inc.",
                "exchange": "NASDAQ",
                "pair": "TSLA",
                "type": "stock",
                "sort_order": 10,
                "sector": "Consumer Cyclical",
                "industry": "Auto Manufacturers"
            })
            self.assertTrue(added)
            wl_after = storage.get_watchlist()
            self.assertEqual(len(wl_after), 10)
            tsla_item = next(w for w in wl_after if w["id"] == "NASDAQ:TSLA")
            self.assertEqual(tsla_item["sector"], "Consumer Cyclical")
            self.assertEqual(tsla_item["industry"], "Auto Manufacturers")

            # 3. Remove symbol
            deleted = storage.remove_watchlist_item("NASDAQ:TSLA")
            self.assertTrue(deleted)
            wl_final = storage.get_watchlist()
            self.assertEqual(len(wl_final), 9)
            self.assertFalse(any(w["id"] == "NASDAQ:TSLA" for w in wl_final))

    def test_fundamental_report_caching_and_persistence(self):
        """Verifies that fundamental analysis reports are persisted and retrievable."""
        db_file = self.tmp_path / "test_fund.sqlite"
        with Storage(db_path=db_file) as storage:
            # Not found initially
            self.assertIsNone(storage.get_fundamental_report("NASDAQ:NVDA"))

            # Save report
            saved = storage.save_fundamental_report(
                symbol="NASDAQ:NVDA",
                metrics={"pe": 65.5, "eps": 2.45, "revenue_yoy": "122%"},
                ai_summary="Strong AI acceleration demand with high margins.",
                model_used="gemini-3.8-flash"
            )
            self.assertTrue(saved)

            report = storage.get_fundamental_report("NASDAQ:NVDA")
            self.assertIsNotNone(report)
            self.assertEqual(report["symbol"], "NASDAQ:NVDA")
            self.assertEqual(report["metrics"]["pe"], 65.5)
            self.assertEqual(report["model_used"], "gemini-3.8-flash")
            self.assertIn("margins", report["ai_summary"])


if __name__ == "__main__":
    unittest.main()

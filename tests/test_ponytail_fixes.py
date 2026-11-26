"""Unit tests for Ponytail Review Findings Fixes.

TDD Red-Green verification for:
1. Finding 1: Default server host binding is 127.0.0.1.
2. Finding 2: Yahoo 429 negative caching cooldown (10 seconds).
3. Finding 3: Evicted watchlist symbol unsubscribes from tv_client.
4. Finding 4: Safe browser storage helper in static/app.js.
"""

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import time
from pathlib import Path
from collections import OrderedDict

import server
from engine.client import TradingViewAsyncClient


class TestPonytailReviewFixes(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Reset server caches and state before each test
        server._yahoo_cache.clear()
        server._inflight_history.clear()

    async def test_yahoo_negative_caching_on_429(self):
        """Verifies that an HTTP 429 response from Yahoo is negatively cached for 10s cooldown."""
        call_count = 0

        async def mock_fetch(url, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.json.return_value = {"error": "Too Many Requests"}
            return mock_resp

        with patch("httpx.AsyncClient.get", side_effect=mock_fetch):
            # First call: hits Yahoo API, gets 429
            bars1 = await server.fetch_yahoo_history("AAPL", timeframe="1m")
            self.assertEqual(bars1, [])
            self.assertEqual(call_count, 1)

            # Second immediate call: should be served from negative cache WITHOUT hitting Yahoo API
            bars2 = await server.fetch_yahoo_history("AAPL", timeframe="1m")
            self.assertEqual(bars2, [])
            self.assertEqual(call_count, 1, "Expected call_count to remain 1 due to 10s negative cache cooldown")

    async def test_evicted_symbol_unsubscribes_tv_client(self):
        """Verifies that when dynamic watchlist reaches 50 and evicts the oldest item, tv_client.unsubscribe is called."""
        mock_tv = MagicMock(spec=TradingViewAsyncClient)
        mock_tv.connected = True
        mock_tv.subscribe = AsyncMock()

        # Fill watchlist up to 50 items
        initial_watchlist = [dict(w) for w in server.WATCHLIST]
        initial_symbols = list(server.SYMBOLS)
        try:
            server.tv_client = mock_tv
            server.WATCHLIST.clear()
            server.SYMBOLS.clear()

            # Seed 5 core items
            for i in range(5):
                item = {"id": f"CORE:SYM{i}", "name": f"Core {i}", "exchange": "CORE", "pair": f"SYM{i}", "type": "stock"}
                server.WATCHLIST.append(item)
                server.SYMBOLS.append(item["id"])

            # Add 45 dynamic items to reach 50
            for i in range(45):
                item = {"id": f"DYN:SYM{i}", "name": f"Dynamic {i}", "exchange": "DYN", "pair": f"SYM{i}", "type": "stock"}
                server.WATCHLIST.append(item)
                server.SYMBOLS.append(item["id"])

            self.assertEqual(len(server.WATCHLIST), 50)
            oldest_dynamic_id = server.WATCHLIST[5]["id"]

            # Subscribe 51st symbol: should evict oldest dynamic symbol and call tv_client.unsubscribe
            resp = await server.subscribe_symbol({"symbol": "NEW:SYMBOL51"})
            self.assertEqual(resp["status"], "subscribed")
            self.assertEqual(len(server.WATCHLIST), 50)
            self.assertNotIn(oldest_dynamic_id, [w["id"] for w in server.WATCHLIST])
            mock_tv.unsubscribe.assert_awaited_once_with(oldest_dynamic_id)
            if server.aggregator:
                self.assertNotIn(oldest_dynamic_id, server.aggregator.bar_history)
                self.assertNotIn(oldest_dynamic_id, server.aggregator.current_bars)
                self.assertNotIn(oldest_dynamic_id, server.aggregator.last_cumulative_volume)
        finally:
            server.WATCHLIST.clear()
            server.WATCHLIST.extend(initial_watchlist)
            server.SYMBOLS.clear()
            server.SYMBOLS.extend(initial_symbols)
            server.tv_client = None

    def test_default_server_binding_is_localhost(self):
        """Verifies that server.run_server defaults to host='127.0.0.1' and passes it to uvicorn.run."""
        import inspect
        sig = inspect.signature(server.run_server)
        self.assertEqual(sig.parameters["host"].default, "127.0.0.1")

        with patch("uvicorn.run") as mock_uvicorn_run:
            server.run_server(port=8000, desktop_mode=False)
            mock_uvicorn_run.assert_called_once()
            _, kwargs = mock_uvicorn_run.call_args
            self.assertEqual(kwargs.get("host"), "127.0.0.1")
            self.assertEqual(kwargs.get("port"), 8000)

    def test_safe_set_storage_defined_in_app_js(self):
        """Verifies safeSetStorage/safeGetStorage in static/app.js handle QuotaExceededError safely in Node VM."""
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
        let stored = {{}};
        const sandbox = {{
            setTimeout, clearTimeout, console, Intl,
            fetch: () => Promise.resolve({{ ok: true, json: () => Promise.resolve({{}}) }}),
            document: {{ getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], createElement: () => ({{ style: {{}}, classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }}, appendChild: () => {{}}, addEventListener: () => {{}} }}), addEventListener: () => {{}}, removeEventListener: () => {{}} }},
            navigator: {{ userAgent: 'node-test' }},
            location: {{ search: '', protocol: 'http:', host: '127.0.0.1:8000' }},
            addEventListener: () => {{}},
            removeEventListener: () => {{}},
            localStorage: {{
                _throw: false,
                getItem: function(k) {{ if (this._throw) throw new Error('SecurityError'); return stored[k] ?? null; }},
                setItem: function(k, v) {{ if (this._throw) throw new Error('QuotaExceededError'); stored[k] = String(v); }},
                removeItem: function(k) {{ delete stored[k]; }}
            }}
        }};
        sandbox.window = sandbox;
        sandbox.globalThis = sandbox;
        vm.createContext(sandbox);
        vm.runInContext(code, sandbox);
        const res = vm.runInContext(`(() => {{
            safeSetStorage("tv_chart_engine", "B");
            const okVal = safeGetStorage("tv_chart_engine", "A");
            localStorage._throw = true;
            let threw = false;
            try {{
                safeSetStorage("tv_terminal_lang", "en");
            }} catch (e) {{
                threw = true;
            }}
            const fallbackVal = safeGetStorage("tv_terminal_lang", "zh-TW");
            return {{ okVal, threw, fallbackVal }};
        }})()`, sandbox);
        console.log(JSON.stringify(res));
        process.exit(0);
        """
        proc = subprocess.run([node_bin, "-e", harness], capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip())
        self.assertEqual(data["okVal"], "B")
        self.assertFalse(data["threw"])
        self.assertEqual(data["fallbackVal"], "zh-TW")


if __name__ == "__main__":
    unittest.main()

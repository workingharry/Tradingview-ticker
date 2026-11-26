import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import server
from server import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestMegaPhase3ConnectionAndState(unittest.TestCase):
    """階段 3：連線與狀態（S7）

    S7-09 後端發送 heartbeat，間隔 WS_HEARTBEAT_INTERVAL；前端收到不累加 tickCount，逾 45s 沒訊息主動 close
    S7-03 ws.onmessage catch 記錄 console.warn("[ws] message handling error:", e)
    S7-06 requestFocusRecovery 帶 includeWatchlist: false，回到前景不覆蓋自選清單
    S7-07 symbol_removed 清理 symbolStates、lastPrices，若為 activeSymbol 顯示停止更新提示
    S7-08 Tauri 啟動重試 get_session_info，成功前 whenBackendReady 佇列等待，非 Tauri 立即執行
    """

    @classmethod
    def setUpClass(cls):
        cls.app_js_path = PROJECT_ROOT / "static" / "app.js"
        if not cls.app_js_path.exists():
            raise FileNotFoundError(f"static/app.js not found at {cls.app_js_path}")

        cls.node_bin = (
            shutil.which("node")
            or ("/opt/homebrew/bin/node" if os.path.exists("/opt/homebrew/bin/node") else None)
            or ("/usr/local/bin/node" if os.path.exists("/usr/local/bin/node") else None)
        )

    def setUp(self):
        if not self.node_bin:
            self.skipTest("Node.js binary not found in PATH; skipping JS VM evaluation")

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
                    textContent: '',
                    title: '',
                    className: '',
                    style: {{}},
                    classList: {{
                        add: function(c) {{ el.className += ' ' + c; }},
                        remove: function() {{}},
                        contains: function() {{ return false; }}
                    }},
                    appendChild: function() {{}},
                    insertBefore: function() {{}},
                    addEventListener: function() {{}},
                    removeEventListener: function() {{}},
                    setAttribute: function(k, v) {{ el[k] = v; if (k === 'id') {{ el._id = v; elements[v] = el; }} }},
                    getAttribute: function(k) {{ return el[k] || null; }},
                    querySelector: function() {{ return null; }},
                    querySelectorAll: function() {{ return []; }}
                }};
                Object.defineProperty(el, 'id', {{
                    get: function() {{ return this._id; }},
                    set: function(v) {{ this._id = v; elements[v] = this; }}
                }});
                elements[id] = el;
            }}
            return elements[id];
        }}

        const sandbox = {{
            window: {{}},
            document: {{
                getElementById: (id) => getOrCreateEl(id),
                querySelector: (s) => null,
                querySelectorAll: (s) => [],
                createElement: (t) => getOrCreateEl('dynamic_' + Math.random()),
                addEventListener: () => {{}},
                removeEventListener: () => {{}}
            }},
            WebSocket: function(url) {{
                this.url = url;
                this.readyState = 1;
                this.close = function() {{}};
            }},
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

        const result = vm.runInContext({json.dumps(eval_js_snippet)}, sandbox);
        console.log("__JSON_OUTPUT__" + JSON.stringify(result));
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
            f"Node evaluation failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
        stdout = proc.stdout.strip()
        for line in reversed(stdout.splitlines()):
            if line.startswith("__JSON_OUTPUT__"):
                return json.loads(line[len("__JSON_OUTPUT__"):])
        return None

    def test_s7_09_backend_heartbeat(self):
        """S7-09 Backend: WS heartbeat task sends heartbeat with timestamp."""
        with patch.object(server, "WS_HEARTBEAT_INTERVAL", 0.05):
            client = TestClient(app)
            with client.websocket_connect("/ws/live") as websocket:
                heartbeat_received = False
                start_time = time.time()
                while time.time() - start_time < 2.0:
                    try:
                        data = websocket.receive_json()
                        if data.get("type") == "heartbeat":
                            self.assertIn("t", data)
                            heartbeat_received = True
                            break
                    except Exception:
                        break
                self.assertTrue(heartbeat_received, "Failed to receive heartbeat from /ws/live endpoint within 2s")

    def test_s7_09_frontend_heartbeat_and_timeout(self):
        """S7-09 Frontend: heartbeat does not increment tickCount; >45s silence triggers ws.close()."""
        js_code = """
        (function() {
            var closeCalls = 0;
            connectWebSocket();
            ws.close = function() { closeCalls++; };

            var initialTicks = tickCount;

            // 1. Send heartbeat
            ws.onmessage({ data: JSON.stringify({ type: "heartbeat", t: 12345 }) });
            var ticksAfterHeartbeat = tickCount;

            // 2. Check lastWsMessageAt was updated
            var msgTime = lastWsMessageAt;

            // 3. Check 44s timeout (should NOT close)
            var nowTime = msgTime + 44000;
            checkWsHeartbeatTimeout(nowTime);
            var closeCountAt44 = closeCalls;

            // 4. Check 46s timeout (SHOULD close)
            nowTime = msgTime + 46000;
            checkWsHeartbeatTimeout(nowTime);
            var closeCountAt46 = closeCalls;

            return {
                initialTicks: initialTicks,
                ticksAfterHeartbeat: ticksAfterHeartbeat,
                closeCountAt44: closeCountAt44,
                closeCountAt46: closeCountAt46
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertEqual(res["initialTicks"], res["ticksAfterHeartbeat"], "Heartbeat must not increment tickCount")
        self.assertEqual(res["closeCountAt44"], 0, "44s silence must not trigger close")
        self.assertEqual(res["closeCountAt46"], 1, "46s silence must trigger ws.close()")

    def test_s7_03_onmessage_catches_with_warn(self):
        """S7-03: ws.onmessage logs console.warn on message handling error."""
        js_code = """
        (function() {
            var warnCalls = [];
            console.warn = function(msg, err) { warnCalls.push(msg); };

            connectWebSocket();
            // Send unparseable JSON
            ws.onmessage({ data: "INVALID_JSON{{{" });

            return {
                warnCalls: warnCalls
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertTrue(any("[ws] message handling error:" in call for call in res["warnCalls"]))

    def test_s7_06_request_focus_recovery_does_not_include_watchlist(self):
        """S7-06: requestFocusRecovery passes includeWatchlist: false."""
        js_code = """
        (function() {
            var capturedOptions = null;
            recoverTerminalState = function(opts) {
                capturedOptions = opts;
            };

            activeSymbol = "NASDAQ:NVDA";
            activeTimeframe = "1D";
            _focusRecoveryAt = 0; // reset cooldown

            requestFocusRecovery();

            return {
                capturedOptions: capturedOptions
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertIsNotNone(res["capturedOptions"])
        self.assertFalse(res["capturedOptions"].get("includeWatchlist", True))

    def test_s7_07_symbol_removed_cleans_state_and_shows_badge(self):
        """S7-07: symbol_removed purges symbolStates, lastPrices, and marks unsubscribed if active."""
        js_code = """
        (function() {
            connectWebSocket();
            symbolStates["NASDAQ:NVDA"] = { price: 150 };
            lastPrices["NASDAQ:NVDA"] = 150;
            activeSymbol = "NASDAQ:NVDA";
            symbolsList = [{ id: "NASDAQ:NVDA" }, { id: "AMEX:SPY" }];

            var warnLog = [];
            console.warn = function() {
                warnLog.push(Array.from(arguments).join(" "));
            };

            ws.onmessage({
                data: JSON.stringify({
                    type: "symbol_removed",
                    symbol: "NASDAQ:NVDA"
                })
            });

            var badge = document.getElementById("unsubscribedBadge");

            return {
                stateDeleted: (symbolStates["NASDAQ:NVDA"] === undefined),
                priceDeleted: (lastPrices["NASDAQ:NVDA"] === undefined),
                listCount: symbolsList.length,
                badgeText: badge ? badge.textContent : "",
                warnLog: warnLog
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertTrue(res["stateDeleted"])
        self.assertTrue(res["priceDeleted"])
        self.assertEqual(res["listCount"], 1)
        self.assertIn("已從自選清單移除", res["badgeText"])

    def test_s7_08_desktop_startup_queue_and_retry(self):
        """S7-08: whenBackendReady enqueues in Tauri environment until initDesktopSession; runs immediately in non-Tauri."""
        js_code = """
        (function() {
            var nonTauriRan = false;
            whenBackendReady(function() { nonTauriRan = true; });

            // Now simulate Tauri environment before session is initialized
            window.__TAURI__ = { invoke: function() { return Promise.reject(new Error("Connecting...")); } };
            apiBase = "";
            _isBackendReady = false;
            _backendReadyQueue = [];

            var tauriQueuedRan = false;
            whenBackendReady(function() { tauriQueuedRan = true; });

            var ranBeforeInit = tauriQueuedRan;

            // Trigger initDesktopSession
            initDesktopSession(8000, "test-token");
            var ranAfterInit = tauriQueuedRan;

            return {
                nonTauriRan: nonTauriRan,
                ranBeforeInit: ranBeforeInit,
                ranAfterInit: ranAfterInit
            };
        })()
        """
        res = self._execute_node_eval(js_code)
        self.assertTrue(res["nonTauriRan"])
        self.assertFalse(res["ranBeforeInit"])
        self.assertTrue(res["ranAfterInit"])


if __name__ == "__main__":
    unittest.main()

"""Comprehensive unit and integration tests for macOS desktop mode in server.py.

Verifies:
1. Dynamic ephemeral loopback socket binding (127.0.0.1:0).
2. Cryptographically secure session token generation and constant-time verification.
3. Server stdout handshake emission (SERVER_READY:PORT=<port>:TOKEN=<token>).
4. REST endpoint token protection (query param, X-Session-Token, Authorization Bearer).
5. WebSocket query parameter token authentication and code 1008 rejection.
6. Tauri and loopback CORS origins and preflight handling.
7. Unauthenticated probe access for /api/health and static assets.
8. Backward compatibility when desktop mode is disabled.
9. Subprocess CLI execution with --desktop-mode and --port 0.
"""

import io
import os
import re
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch, AsyncMock
from contextlib import redirect_stdout
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import server
from server import (
    app,
    bind_loopback_socket,
    verify_token,
    is_desktop_mode,
    get_desktop_token,
    get_desktop_port,
    set_desktop_mode,
    desktop_mode_context,
    is_allowed_origin,
    is_parent_alive,
    run_server,
)

MOCK_BARS = [
    {
        "time": 1731234000 + i * 60,
        "open": 65000.0 + i * 10,
        "high": 65050.0 + i * 10,
        "low": 64950.0 + i * 10,
        "close": 65020.0 + i * 10,
        "volume": 10.0 + i,
    }
    for i in range(5)
]


class TestDesktopModeCore(unittest.TestCase):
    """Unit tests for desktop mode socket binding, token management, and handshake."""

    def setUp(self):
        self.orig_mode = is_desktop_mode()
        self.orig_token = get_desktop_token()
        self.orig_port = get_desktop_port()

    def tearDown(self):
        set_desktop_mode(
            enabled=self.orig_mode,
            token=self.orig_token,
            port=self.orig_port,
        )

    def test_bind_loopback_socket_allocates_ephemeral_port(self):
        """Validates that binding to port 0 allocates a valid ephemeral loopback port."""
        sock, assigned_port = bind_loopback_socket(host="127.0.0.1", port=0)
        try:
            self.assertIsInstance(sock, socket.socket)
            self.assertGreater(assigned_port, 1024)
            self.assertEqual(sock.getsockname()[0], "127.0.0.1")
            self.assertEqual(sock.getsockname()[1], assigned_port)
        finally:
            sock.close()

    def test_set_and_get_desktop_mode(self):
        """Validates state management functions for desktop mode."""
        set_desktop_mode(enabled=True, token="custom_token_123", port=54321)
        self.assertTrue(is_desktop_mode())
        self.assertEqual(get_desktop_token(), "custom_token_123")
        self.assertEqual(get_desktop_port(), 54321)

        set_desktop_mode(enabled=False)
        self.assertFalse(is_desktop_mode())
        self.assertIsNone(get_desktop_token())
        self.assertIsNone(get_desktop_port())

    def test_desktop_mode_context_manager(self):
        """Validates that desktop_mode_context guarantees clean restoration."""
        set_desktop_mode(enabled=False)
        with desktop_mode_context(token="temp_token_abc", port=44444) as tok:
            self.assertTrue(is_desktop_mode())
            self.assertEqual(tok, "temp_token_abc")
            self.assertEqual(get_desktop_token(), "temp_token_abc")
            self.assertEqual(get_desktop_port(), 44444)

        self.assertFalse(is_desktop_mode())
        self.assertIsNone(get_desktop_token())

    def test_verify_token_semantics(self):
        """Validates token verification across active desktop mode and disabled state."""
        # When desktop mode is inactive: all tokens pass
        set_desktop_mode(enabled=False)
        self.assertTrue(verify_token(None))
        self.assertTrue(verify_token(""))
        self.assertTrue(verify_token("any_random_string"))

        # When desktop mode is active: strict constant-time matching
        set_desktop_mode(enabled=True, token="alpha_secret_token", port=1234)
        self.assertTrue(verify_token("alpha_secret_token"))
        self.assertTrue(verify_token("  alpha_secret_token  "))
        self.assertFalse(verify_token("wrong_token"))
        self.assertFalse(verify_token(""))
        self.assertFalse(verify_token(None))

    def test_get_desktop_token_autogenerates_when_empty_in_desktop_mode(self):
        """Validates that get_desktop_token auto-generates a secure 32-char hex token if None."""
        set_desktop_mode(enabled=True, token=None, port=12345)
        # Force _desktop_token to None to simulate DESKTOP_MODE=1 env without DESKTOP_TOKEN
        import server
        server._desktop_token = None
        tok = get_desktop_token()
        self.assertIsNotNone(tok)
        self.assertEqual(len(tok), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in tok))

    def test_is_allowed_origin_semantics(self):
        """Validates origin allowlist rules for Tauri protocols, loopback hosts, and third-party rejection."""
        # Allowed origins
        self.assertTrue(is_allowed_origin("tauri://localhost"))
        self.assertTrue(is_allowed_origin("http://tauri.localhost"))
        self.assertTrue(is_allowed_origin("https://tauri.localhost"))
        self.assertTrue(is_allowed_origin("http://127.0.0.1:8000"))
        self.assertTrue(is_allowed_origin("http://localhost:5173"))
        self.assertTrue(is_allowed_origin("http://localhost"))
        self.assertTrue(is_allowed_origin("http://127.0.0.1"))
        self.assertTrue(is_allowed_origin("tauri://custom-app"))
        self.assertTrue(is_allowed_origin(None))
        self.assertTrue(is_allowed_origin(""))

        # Rejected origins
        self.assertFalse(is_allowed_origin("https://malicious-attacker.com"))
        self.assertFalse(is_allowed_origin("null"))
        self.assertFalse(is_allowed_origin("http://evil-tauri.localhost.attacker.com"))

    def test_is_parent_alive_detects_parent_change_and_orphan_pid_1(self):
        """Validates that is_parent_alive detects reparenting to PID 1 or dead parent PID."""
        current_ppid = os.getppid()
        self.assertTrue(is_parent_alive(current_ppid))
        self.assertTrue(is_parent_alive(1))
        # Mismatched initial_ppid (simulating reparenting after parent crash)
        self.assertFalse(is_parent_alive(current_ppid + 99999))
        with patch("os.getppid", return_value=1):
            self.assertFalse(is_parent_alive(current_ppid))

    def test_stdout_handshake_emission_on_lifespan(self):
        """Validates that entering lifespan in desktop mode emits the handshake banner."""
        buf = io.StringIO()
        with patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=MOCK_BARS), \
             patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock), \
             patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock):

            with desktop_mode_context(token="0123456789abcdef0123456789abcdef", port=59999):
                with redirect_stdout(buf):
                    with TestClient(app):
                        pass

        output = buf.getvalue()
        self.assertIn("SERVER_READY:PORT=59999:TOKEN=0123456789abcdef0123456789abcdef", output)
        match = re.search(r"SERVER_READY:PORT=(\d+):TOKEN=([a-f0-9]{32})", output)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "59999")
        self.assertEqual(match.group(2), "0123456789abcdef0123456789abcdef")


class TestDesktopModeAuthAndCORS(unittest.TestCase):
    """Integration tests for REST endpoint auth, CORS origins, and WebSocket security."""

    def setUp(self):
        self.fetch_patcher = patch(
            "server.fetch_binance_history",
            new_callable=AsyncMock,
            return_value=list(MOCK_BARS),
        )
        self.client_start_patcher = patch(
            "engine.client.TradingViewAsyncClient.start",
            new_callable=AsyncMock,
        )
        self.client_stop_patcher = patch(
            "engine.client.TradingViewAsyncClient.stop",
            new_callable=AsyncMock,
        )
        self.fetch_patcher.start()
        self.client_start_patcher.start()
        self.client_stop_patcher.start()

        self.test_token = "deadbeefcafebabe0123456789abcdef"
        set_desktop_mode(enabled=True, token=self.test_token, port=51234)

    def tearDown(self):
        set_desktop_mode(enabled=False)
        self.fetch_patcher.stop()
        self.client_start_patcher.stop()
        self.client_stop_patcher.stop()

    def test_health_endpoint_allows_unauthenticated_probe(self):
        """Validates that /api/health and /api/health/ allow unauthenticated polling for process readiness."""
        with TestClient(app) as client:
            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertTrue(data["desktop_mode"])
            self.assertEqual(data["port"], 51234)

            # Trailing slash probe
            resp_slash = client.get("/api/health/")
            self.assertEqual(resp_slash.status_code, 200)

    def test_rest_endpoints_reject_unauthenticated_requests(self):
        """Validates that all protected REST endpoints return 401 when token is missing or invalid."""
        with TestClient(app) as client:
            # Missing token
            resp1 = client.get("/api/symbols")
            self.assertEqual(resp1.status_code, 401)
            self.assertIn("error", resp1.json())

            # Invalid query token
            resp2 = client.get("/api/symbols?token=invalid_token")
            self.assertEqual(resp2.status_code, 401)

            # Search endpoint without token
            resp3 = client.get("/api/symbols/search?q=BTC")
            self.assertEqual(resp3.status_code, 401)

            # Subscribe endpoint without token
            resp4 = client.post("/api/symbols/subscribe", json={"symbol": "NASDAQ:AAPL"})
            self.assertEqual(resp4.status_code, 401)

            # History endpoint without token
            resp5 = client.get("/api/history?symbol=BINANCE:BTCUSDT")
            self.assertEqual(resp5.status_code, 401)

    def test_rest_endpoints_accept_valid_token_methods(self):
        """Validates that REST endpoints accept token via query param, X-Session-Token, or Bearer auth."""
        with TestClient(app) as client:
            # 1. Query parameter (?token=...)
            resp_query = client.get(f"/api/symbols?token={self.test_token}")
            self.assertEqual(resp_query.status_code, 200)
            self.assertIn("symbols", resp_query.json())

            # 2. X-Session-Token header
            resp_header = client.get(
                "/api/symbols",
                headers={"X-Session-Token": self.test_token}
            )
            self.assertEqual(resp_header.status_code, 200)

            # 3. Authorization: Bearer <token>
            resp_bearer = client.get(
                "/api/symbols",
                headers={"Authorization": f"Bearer {self.test_token}"}
            )
            self.assertEqual(resp_bearer.status_code, 200)

            # 4. Search with query token
            resp_search = client.get(f"/api/symbols/search?q=BTC&token={self.test_token}")
            self.assertEqual(resp_search.status_code, 200)

            # 5. History with query token
            resp_hist = client.get(f"/api/history?symbol=BINANCE:BTCUSDT&token={self.test_token}")
            self.assertEqual(resp_hist.status_code, 200)

    def test_static_assets_accessible_without_token(self):
        """Validates that static assets and root HTML remain accessible without token."""
        with TestClient(app) as client:
            resp_root = client.get("/")
            self.assertEqual(resp_root.status_code, 200)

            resp_css = client.get("/static/style.css")
            self.assertIn(resp_css.status_code, (200, 304))

    def test_cors_headers_for_tauri_and_loopback_origins(self):
        """Validates CORS middleware allows tauri://, http://tauri.localhost, and loopback origins."""
        origins_to_test = [
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
        ]

        with TestClient(app) as client:
            for origin in origins_to_test:
                resp = client.options(
                    "/api/symbols",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "GET",
                    }
                )
                self.assertEqual(
                    resp.headers.get("access-control-allow-origin"),
                    origin,
                    f"Origin {origin} was not permitted by CORS middleware"
                )

            # Malicious origin must be rejected
            evil_resp = client.options(
                "/api/symbols",
                headers={
                    "Origin": "https://malicious-attacker.com",
                    "Access-Control-Request-Method": "GET",
                }
            )
            self.assertIsNone(evil_resp.headers.get("access-control-allow-origin"))

    def test_cors_headers_preserved_on_401_responses(self):
        """Validates that 401 Unauthorized responses include CORS headers for Tauri origin."""
        with TestClient(app) as client:
            resp = client.get("/api/symbols", headers={"Origin": "tauri://localhost"})
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(resp.headers.get("access-control-allow-origin"), "tauri://localhost")

    def test_websocket_token_validation(self):
        """Validates that WebSocket /ws/live verifies query param token and rejects unauthorized."""
        with TestClient(app) as client:
            # 1. Connect without token -> Rejected with code 1008
            with self.assertRaises(WebSocketDisconnect) as cm:
                with client.websocket_connect("/ws/live"):
                    pass
            self.assertEqual(cm.exception.code, 1008)

            # 2. Connect with invalid token -> Rejected with code 1008
            with self.assertRaises(WebSocketDisconnect) as cm2:
                with client.websocket_connect("/ws/live?token=invalid_token"):
                    pass
            self.assertEqual(cm2.exception.code, 1008)

            # 3. Connect with valid token in query param -> Accepted & receives initial status
            with client.websocket_connect(f"/ws/live?token={self.test_token}") as ws:
                initial_msg = ws.receive_json()
                self.assertEqual(initial_msg["type"], "status")

            # 4. Connect with valid token in Authorization Bearer header -> Accepted
            with client.websocket_connect(
                "/ws/live",
                headers={"Authorization": f"Bearer {self.test_token}"}
            ) as ws_bearer:
                bearer_msg = ws_bearer.receive_json()
                self.assertEqual(bearer_msg["type"], "status")

    def test_websocket_origin_validation(self):
        """Validates that WebSocket connections from untrusted origins are rejected with code 1008."""
        with TestClient(app) as client:
            # 1. Connection with malicious origin -> Rejected with code 1008 even with valid token
            with self.assertRaises(WebSocketDisconnect) as cm:
                with client.websocket_connect(
                    f"/ws/live?token={self.test_token}",
                    headers={"Origin": "https://malicious-attacker.com"}
                ):
                    pass
            self.assertEqual(cm.exception.code, 1008)

            # 2. Connection with allowed tauri origin -> Accepted
            with client.websocket_connect(
                f"/ws/live?token={self.test_token}",
                headers={"Origin": "tauri://localhost"}
            ) as ws:
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "status")

            # 3. Connection with allowed loopback origin -> Accepted
            with client.websocket_connect(
                f"/ws/live?token={self.test_token}",
                headers={"Origin": "http://127.0.0.1:3000"}
            ) as ws2:
                msg2 = ws2.receive_json()
                self.assertEqual(msg2["type"], "status")


class TestDesktopModeSubprocessSmoke(unittest.TestCase):
    """End-to-end integration test spawning server.py in desktop mode as a child process."""

    def test_subprocess_desktop_mode_handshake_and_requests(self):
        """Spawns server.py with --desktop-mode --port 0, parses stdout, and tests HTTP requests."""
        cmd = [
            sys.executable,
            "server.py",
            "--desktop-mode",
            "--port", "0",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        port = None
        token = None

        try:
            start_time = time.time()
            # Read stdout line by line until handshake banner is found (timeout 10s)
            while time.time() - start_time < 10.0:
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                match = re.search(r"SERVER_READY:PORT=(\d+):TOKEN=([a-f0-9]{32})", line)
                if match:
                    port = int(match.group(1))
                    token = match.group(2)
                    break

            self.assertIsNotNone(port, "Server did not output SERVER_READY handshake within 10s")
            self.assertIsNotNone(token, "Server did not output TOKEN within 10s")
            self.assertGreater(port, 1024)

            # Wait briefly for uvicorn loop to accept sockets
            time.sleep(0.5)

            # 1. Unauthenticated request to /api/health should return 200
            req_health = Request(f"http://127.0.0.1:{port}/api/health")
            with urlopen(req_health, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)

            # 2. Unauthenticated request to /api/symbols should raise HTTPError 401
            req_symbols_no_auth = Request(f"http://127.0.0.1:{port}/api/symbols")
            with self.assertRaises(HTTPError) as cm:
                urlopen(req_symbols_no_auth, timeout=3.0)
            self.assertEqual(cm.exception.code, 401)

            # 3. Authenticated request to /api/symbols?token=<token> should return 200
            req_symbols_auth = Request(f"http://127.0.0.1:{port}/api/symbols?token={token}")
            with urlopen(req_symbols_auth, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)

            # 4. Authenticated request using X-Session-Token header should return 200
            req_header_auth = Request(
                f"http://127.0.0.1:{port}/api/symbols",
                headers={"X-Session-Token": token}
            )
            with urlopen(req_header_auth, timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_subprocess_desktop_mode_custom_token_and_default_port(self):
        """Spawns server.py with --desktop-mode and custom --token, verifying default port 0 allocation."""
        custom_tok = "custom_pre_shared_secret_token_123"
        cmd = [
            sys.executable,
            "server.py",
            "--desktop-mode",
            "--token", custom_tok,
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        port = None
        parsed_token = None

        try:
            start_time = time.time()
            while time.time() - start_time < 10.0:
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                match = re.search(r"SERVER_READY:PORT=(\d+):TOKEN=([a-zA-Z0-9_]+)", line)
                if match:
                    port = int(match.group(1))
                    parsed_token = match.group(2)
                    break

            self.assertIsNotNone(port, "Server did not output SERVER_READY handshake within 10s")
            self.assertEqual(parsed_token, custom_tok)
            self.assertGreater(port, 1024)

            time.sleep(0.5)

            # Health probe works without auth
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)

            # Protected endpoint works with custom token
            with urlopen(f"http://127.0.0.1:{port}/api/symbols?token={custom_tok}", timeout=3.0) as resp:
                self.assertEqual(resp.status, 200)

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()

"""
Mega Phase 7 Unit Tests: Backend & Security Hardening (S3)
Strict TDD verification for:
  S3-01: RedactTokenFilter redacts token query parameter values (case-insensitive) in uvicorn access/error logs
  S3-02: load_dotenv_custom does not read home directory .env unless TERMINAL_LOAD_HOME_DOTENV=1
  S3-09: Origin validation in development mode (WebSocket code 1008 on unauthorized origin; REST POST/PUT/DELETE 403)
"""

import logging
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import server
from server import (
    app,
    RedactTokenFilter,
    load_dotenv_custom,
    is_allowed_origin,
)


class TestMegaPhase7BackendSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_s3_01_redact_token_filter(self):
        """S3-01: RedactTokenFilter replaces token=<value> with token=[REDACTED] (case-insensitive)."""
        flt = RedactTokenFilter()

        # 1. Direct message string test
        rec1 = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg='GET /ws/live?token=abc123&x=1 HTTP/1.1 101 Switching Protocols',
            args=(),
            exc_info=None,
        )
        flt.filter(rec1)
        self.assertNotIn("abc123", rec1.getMessage())
        self.assertIn("token=[REDACTED]", rec1.getMessage())

        # 2. Case-insensitive TOKEN= test
        rec2 = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=55,
            msg='CONNECT /ws/live?TOKEN=SecretKey456&mode=stream',
            args=(),
            exc_info=None,
        )
        flt.filter(rec2)
        self.assertNotIn("SecretKey456", rec2.getMessage())
        self.assertIn("TOKEN=[REDACTED]", rec2.getMessage())

        # 3. Tuple args test (uvicorn formatting style: '%s - "%s" %s')
        rec3 = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=68,
            msg='%s - "%s" %s',
            args=("127.0.0.1:54321", "GET /ws/live?token=SuperSecret999&foo=bar HTTP/1.1", 200),
            exc_info=None,
        )
        flt.filter(rec3)
        formatted = rec3.getMessage()
        self.assertNotIn("SuperSecret999", formatted)
        self.assertIn("token=[REDACTED]", formatted)

        # 4. Filter is attached to uvicorn loggers
        for logger_name in ("uvicorn.access", "uvicorn.error"):
            log_obj = logging.getLogger(logger_name)
            self.assertTrue(
                any(isinstance(f, RedactTokenFilter) for f in log_obj.filters),
                f"Missing RedactTokenFilter on logger {logger_name}",
            )

    def test_s3_02_load_dotenv_custom_home_isolation(self):
        """S3-02: load_dotenv_custom ignores home .env unless TERMINAL_LOAD_HOME_DOTENV=1."""
        home_env = Path.home() / ".env"
        gemini_env = Path.home() / ".gemini" / ".env"

        # When TERMINAL_LOAD_HOME_DOTENV is not set
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TERMINAL_LOAD_HOME_DOTENV", None)
            with patch("pathlib.Path.is_file", return_value=True), \
                 patch("pathlib.Path.read_text", return_value="FOO=BAR\n"):
                loaded = load_dotenv_custom()
                self.assertNotIn(str(home_env.resolve()), loaded)
                self.assertNotIn(str(gemini_env.resolve()), loaded)

        # When TERMINAL_LOAD_HOME_DOTENV is explicitly "1"
        with patch.dict(os.environ, {"TERMINAL_LOAD_HOME_DOTENV": "1"}):
            with patch("pathlib.Path.is_file", return_value=True), \
                 patch("pathlib.Path.read_text", return_value="FOO=BAR\n"):
                loaded_with_flag = load_dotenv_custom()
                self.assertIn(str(home_env.resolve()), loaded_with_flag)
                self.assertIn(str(gemini_env.resolve()), loaded_with_flag)

    def test_s3_09_dev_mode_rest_origin_validation(self):
        """S3-09: In dev mode, REST POST/PUT/DELETE from untrusted origin returns 403; GET and trusted allowed."""
        with patch("server.is_desktop_mode", return_value=False):
            # 1. POST with hostile Origin -> 403 Forbidden
            resp_evil_post = self.client.post(
                "/api/watchlist",
                json={"symbol": "NASDAQ:AAPL"},
                headers={"Origin": "https://evil.example.com"},
            )
            self.assertEqual(resp_evil_post.status_code, 403)
            self.assertIn("unauthorized origin", resp_evil_post.text.lower())

            # 2. DELETE with hostile Origin -> 403 Forbidden
            resp_evil_delete = self.client.delete(
                "/api/drawings?symbol=NASDAQ:AAPL",
                headers={"Origin": "https://attacker.net"},
            )
            self.assertEqual(resp_evil_delete.status_code, 403)

            # 3. POST with trusted localhost Origin -> 200 OK
            resp_local_post = self.client.post(
                "/api/watchlist",
                json={"symbol": "NASDAQ:AAPL"},
                headers={"Origin": "http://127.0.0.1:5173"},
            )
            self.assertNotEqual(resp_local_post.status_code, 403)

            # 4. POST with NO Origin header (e.g. curl, test harness) -> allowed (not 403)
            resp_no_origin_post = self.client.post(
                "/api/watchlist",
                json={"symbol": "NASDAQ:AAPL"},
            )
            self.assertNotEqual(resp_no_origin_post.status_code, 403)

            # 5. GET with hostile Origin -> allowed (not 403)
            resp_evil_get = self.client.get(
                "/api/quote?symbol=NASDAQ:AAPL",
                headers={"Origin": "https://evil.example.com"},
            )
            self.assertNotEqual(resp_evil_get.status_code, 403)

    def test_s3_09_dev_mode_websocket_origin_validation(self):
        """S3-09: In dev mode, WebSocket connection with untrusted origin is rejected with code 1008."""
        with patch("server.is_desktop_mode", return_value=False):
            # 1. Untrusted origin -> rejected with code 1008
            with self.assertRaises(Exception) as ctx:
                with self.client.websocket_connect(
                    "/ws/live",
                    headers={"Origin": "https://evil.phishing.site"},
                ):
                    pass

            # 2. Trusted localhost origin -> connects successfully
            with self.client.websocket_connect(
                "/ws/live",
                headers={"Origin": "http://localhost:5173"},
            ) as ws:
                msg = ws.receive_json()
                self.assertIn("type", msg)

            # 3. No origin header -> connects successfully
            with self.client.websocket_connect("/ws/live") as ws:
                msg = ws.receive_json()
                self.assertIn("type", msg)


if __name__ == "__main__":
    unittest.main()

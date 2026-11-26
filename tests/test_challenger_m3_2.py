"""Empirical verification suite for Milestone M3: Static Asset Serving & DOM Integrity.

Executed by Challenger M3-2 to empirically verify that:
1. Static asset routes (/, /static/index.html, /static/app.js, /static/style.css, and root aliases)
   return HTTP 200 with appropriate Content-Type headers.
2. Directory traversal attacks on /static/ routes fail safely with HTTP 404 (no source code leak).
3. 100% of DOM IDs referenced via document.getElementById in static/app.js exist in static/index.html.
4. All querySelector targets and dynamic DOM container targets are present in index.html.
5. All 5 planned watchlist symbols (BINANCE:BTCUSDT, BINANCE:ETHUSDT, BINANCE:SOLUSDT, NASDAQ:AAPL,
   NASDAQ:NVDA) are delivered by the server API and integrated with frontend state.
6. Key UI elements (equityBanner, chartLegend, chartContainer, chartWatermark) exist and align.
"""

import unittest
import re
from pathlib import Path
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

import server
from server import app, STATIC_DIR, WATCHLIST


class TestFastAPIStaticAssetServing(unittest.TestCase):
    """Empirical verification of FastAPI static asset routing and Content-Type headers."""

    def setUp(self):
        self.mock_start = patch(
            "engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock
        ).start()
        self.mock_stop = patch(
            "engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock
        ).start()
        self.mock_fetch = patch(
            "server.fetch_binance_history", new_callable=AsyncMock, return_value=[]
        ).start()

    def tearDown(self):
        patch.stopall()

    def test_static_routes_return_200_and_correct_content_type(self):
        """Verifies HTTP 200 and Content-Type headers for HTML, JS, and CSS files."""
        targets = [
            ("/", 200, "text/html"),
            ("/index.html", 200, "text/html"),
            ("/static/index.html", 200, "text/html"),
            ("/app.js", 200, "javascript"),
            ("/static/app.js", 200, "javascript"),
            ("/style.css", 200, "text/css"),
            ("/static/style.css", 200, "text/css"),
        ]

        with TestClient(app) as client:
            for path, expected_status, expected_ctype in targets:
                resp = client.get(path)
                self.assertEqual(
                    resp.status_code,
                    expected_status,
                    f"Route '{path}' failed with status {resp.status_code}",
                )
                actual_ctype = resp.headers.get("content-type", "").lower()
                self.assertIn(
                    expected_ctype,
                    actual_ctype,
                    f"Route '{path}' Content-Type '{actual_ctype}' missing '{expected_ctype}'",
                )
                self.assertGreater(
                    len(resp.text),
                    0,
                    f"Route '{path}' returned empty response body",
                )

    def test_non_existent_static_files_return_404(self):
        """Verifies that requests for missing static files cleanly return HTTP 404."""
        missing_paths = [
            "/static/missing.html",
            "/static/nonexistent.js",
            "/static/unknown.css",
            "/missing_script.js",
            "/static/subdir/missing.js",
        ]
        with TestClient(app) as client:
            for path in missing_paths:
                resp = client.get(path)
                self.assertEqual(
                    resp.status_code,
                    404,
                    f"Expected 404 for '{path}', got {resp.status_code}",
                )


class TestStaticPathTraversalSecurity(unittest.TestCase):
    """Empirical verification of path traversal attack resilience on static mounts."""

    def setUp(self):
        self.mock_start = patch(
            "engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock
        ).start()
        self.mock_stop = patch(
            "engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock
        ).start()
        self.mock_fetch = patch(
            "server.fetch_binance_history", new_callable=AsyncMock, return_value=[]
        ).start()

    def tearDown(self):
        patch.stopall()

    def test_path_traversal_attempts_return_404_and_never_leak_source(self):
        """Verifies directory traversal attacks are blocked without leaking server.py or system files."""
        traversal_attempts = [
            "/static/../server.py",
            "/static/%2e%2e/server.py",
            "/static/..%2fserver.py",
            "/static/%2e%2e%2fserver.py",
            "/../server.py",
            "/%2e%2e/server.py",
            "/..%2fserver.py",
            "/static/../../etc/passwd",
            "/static/%2e%2e/%2e%2e/etc/passwd",
            "/static/....//server.py",
            "/static/..././server.py",
            "/static/..\\server.py",
        ]

        with TestClient(app) as client:
            for path in traversal_attempts:
                resp = client.get(path)
                # Traversal attacks MUST return 404 (or 400), NEVER 200
                self.assertIn(
                    resp.status_code,
                    [400, 404],
                    f"Traversal path '{path}' returned unexpected status {resp.status_code}",
                )
                # Ensure no Python source code or UNIX passwd content leaked
                self.assertNotIn(
                    "FastAPI application",
                    resp.text,
                    f"Server source code leaked via '{path}'",
                )
                self.assertNotIn(
                    "root:x:",
                    resp.text,
                    f"System passwd content leaked via '{path}'",
                )


class TestDOMIntegrityAndSelectorAlignment(unittest.TestCase):
    """Empirical parsing and verification of DOM ID and selector alignment between HTML and JS."""

    @classmethod
    def setUpClass(cls):
        cls.index_html_path = STATIC_DIR / "index.html"
        cls.app_js_path = STATIC_DIR / "app.js"
        cls.style_css_path = STATIC_DIR / "style.css"

        cls.index_html = cls.index_html_path.read_text(encoding="utf-8")
        cls.app_js = cls.app_js_path.read_text(encoding="utf-8")
        cls.style_css = cls.style_css_path.read_text(encoding="utf-8")

    def test_all_js_get_element_by_id_calls_exist_in_index_html(self):
        # guard-test: 設定守門，允許讀原始碼
        """Asserts that 100% of IDs queried via document.getElementById in app.js exist in index.html."""
        id_call_pattern = re.compile(
            r"document\.getElementById\([\"\x27]([^\"\x27]+)[\"\x27]\)"
        )
        js_ids = set(id_call_pattern.findall(self.app_js))

        # Empirical assertion: we must extract a non-trivial set of IDs from app.js
        self.assertGreaterEqual(
            len(js_ids),
            15,
            f"Expected at least 15 getElementById calls, extracted {len(js_ids)}: {js_ids}",
        )

        html_id_pattern = re.compile(r"id=[\"\x27]([^\"\x27]+)[\"\x27]")
        html_ids = set(html_id_pattern.findall(self.index_html))

        missing_ids = js_ids - html_ids
        self.assertEqual(
            missing_ids,
            set(),
            f"Zero missing DOM targets allowed! Missing IDs in index.html: {missing_ids}",
        )

    def test_query_selector_calls_and_dynamic_containers_align(self):
        """Asserts querySelector calls and dynamic element containers exist in index.html."""
        qs_pattern = re.compile(
            r"document\.querySelector(?:All)?\([\"\x27]([^\"\x27]+)[\"\x27]\)"
        )
        qs_matches = set(qs_pattern.findall(self.app_js))

        # If querySelector is used, all leading #id targets must exist in HTML
        for selector in qs_matches:
            m = re.match(r"^#([A-Za-z0-9_-]+)", selector)
            if m:
                target_id = m.group(1)
                self.assertIn(
                    f'id="{target_id}"',
                    self.index_html,
                    f"querySelector ID target '{selector}' missing from index.html",
                )

        # Dynamic DOM target containers populated by app.js MUST exist in index.html
        dynamic_targets = ["tickerTape"]
        for target in dynamic_targets:
            self.assertIn(
                f'id="{target}"',
                self.index_html,
                f"Dynamic container target '{target}' missing from index.html",
            )

    def test_critical_terminal_ui_elements_present_in_dom(self):
        """Verifies presence of mandatory TradingView terminal UI elements."""
        critical_elements = [
            "chartContainer",
            "tickerTape",
            "statusBadge",
            "statusText",
            "tickCounter",
            "activeExchange",
            "activeName",
            "activePrice",
            "activeChange",
            "activeHigh",
            "activeLow",
            "activeVolume",
            "equityBanner",
            "chartLegend",
            "legendSymbol",
            "legendOpen",
            "legendHigh",
            "legendLow",
            "legendClose",
            "legendVol",
            "chartWatermark",
        ]

        for elem_id in critical_elements:
            self.assertIn(
                f'id="{elem_id}"',
                self.index_html,
                f"Critical element #{elem_id} missing from index.html",
            )

    def test_planned_watchlist_symbols_delivery_and_integration(self):
        """Verifies that all 5 planned symbols are delivered to app.js and integrated."""
        planned_symbols = [
            "BINANCE:BTCUSDT",
            "BINANCE:ETHUSDT",
            "BINANCE:SOLUSDT",
            "NASDAQ:AAPL",
            "NASDAQ:NVDA",
        ]

        # 1. Server delivers all 5 symbols through GET /api/symbols
        with patch("engine.client.TradingViewAsyncClient.start", new_callable=AsyncMock):
            with patch("engine.client.TradingViewAsyncClient.stop", new_callable=AsyncMock):
                with patch("server.fetch_binance_history", new_callable=AsyncMock, return_value=[]):
                    with TestClient(app) as client:
                        resp = client.get("/api/symbols")
                        self.assertEqual(resp.status_code, 200)
                        delivered_ids = [s["id"] for s in resp.json().get("symbols", [])]
                        for sym in planned_symbols:
                            self.assertIn(
                                sym,
                                delivered_ids,
                                f"Symbol '{sym}' missing from /api/symbols response",
                            )

        # 2. Server WATCHLIST configuration holds all 5 symbols
        watchlist_ids = [w["id"] for w in WATCHLIST]
        for sym in planned_symbols:
            self.assertIn(sym, watchlist_ids, f"Symbol '{sym}' missing from WATCHLIST")

        # 3. Frontend app.js has active default symbol and NASDAQ equity handling
        self.assertIn(
            "BINANCE:BTCUSDT",
            self.app_js,
            "Default active symbol BINANCE:BTCUSDT missing from app.js",
        )
        self.assertIn(
            "NASDAQ:",
            self.app_js,
            "NASDAQ equity prefix detection missing from app.js",
        )

        # 4. Default watermark in index.html matches initial active symbol
        self.assertIn(
            "BTCUSDT",
            self.index_html,
            "Default symbol BTCUSDT missing from index.html",
        )

    def test_css_styling_tokens_and_keyframes_exist(self):
        """Verifies that essential institutional styling, classes, and keyframes exist in style.css."""
        required_tokens = [
            "#131722",  # Background primary
            "#1e222d",  # Card / Panel background
            "#2a2e39",  # Border color
            "#26a69a",  # Bullish teal
            "#ef5350",  # Bearish red
            "tabular-nums",  # Tabular numeral formatting
        ]
        for token in required_tokens:
            self.assertIn(
                token,
                self.style_css,
                f"Essential CSS token '{token}' missing from style.css",
            )

        required_classes = [
            ".tf-btn",
            ".tape-item",
            ".tape-symbol",
            ".tape-price",
            ".tape-change",
            ".status-badge",
            ".equity-banner",
            ".chart-legend",
            ".chart-canvas",
        ]
        for cls in required_classes:
            self.assertIn(
                cls,
                self.style_css,
                f"Required CSS selector '{cls}' missing from style.css",
            )

        # Keyframe price flash animations
        self.assertIn("@keyframes flash-up-bg", self.style_css)
        self.assertIn("@keyframes flash-down-bg", self.style_css)


if __name__ == "__main__":
    unittest.main()

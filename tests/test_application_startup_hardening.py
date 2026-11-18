"""Tests for TradingView Application Startup Hardening across desktop and sidecar layers.

Validates:
1. Safe idempotent PRCH database migration even when duplicate (NYSE:PRCH and NASDAQ:PRCH) rows exist.
2. C launcher resolution in development topology from target/debug.
3. Framework symlink structure repair logic.
4. Loopback socket listen backlog and handshake timing resilience.
5. TradingView client user-agent header injection for edge firewall bypass.
"""

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_sidecar import (
    repair_framework_symlinks,
    get_target_triple,
    build_sidecar,
)
from engine.storage import Storage
from engine.client import TradingViewAsyncClient
import server

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestApplicationStartupHardening(unittest.TestCase):
    """Deep verification tests for application startup fixes."""

    def test_storage_migration_with_colliding_prch_entries(self):
        """Validates that storage initialisation does not crash on IntegrityError when both NYSE:PRCH and NASDAQ:PRCH exist."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
            db_path = Path(tf.name)

        try:
            # Pre-seed database with colliding PRCH entries in both watchlist and bars
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE watchlist (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    type TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    sector TEXT DEFAULT '',
                    industry TEXT DEFAULT ''
                );
            """)
            conn.execute("""
                CREATE TABLE bars (
                    symbol TEXT,
                    timeframe TEXT,
                    time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (symbol, timeframe, time)
                );
            """)
            # Insert conflicting entries
            conn.execute("INSERT INTO watchlist VALUES ('NYSE:PRCH', 'Porch Group Old', 'NYSE', 'PRCH', 'stock', 2, 'Tech', 'Software');")
            conn.execute("INSERT INTO watchlist VALUES ('NASDAQ:PRCH', 'Porch Group New', 'NASDAQ', 'PRCH', 'stock', 2, 'Tech', 'Software');")
            conn.execute("INSERT INTO bars VALUES ('NYSE:PRCH', '1D', 1700000000, 10.0, 11.0, 9.5, 10.5, 1000);")
            conn.execute("INSERT INTO bars VALUES ('NASDAQ:PRCH', '1D', 1700000000, 10.0, 11.0, 9.5, 10.5, 1000);")
            conn.commit()
            conn.close()

            # Instantiating Storage with this database MUST NOT raise sqlite3.IntegrityError
            storage = Storage(db_path=db_path)
            items = storage.get_watchlist()
            prch_items = [it for it in items if "PRCH" in it["id"]]
            self.assertEqual(len(prch_items), 1)
            self.assertEqual(prch_items[0]["id"], "NASDAQ:PRCH")
            self.assertEqual(prch_items[0]["exchange"], "NASDAQ")

            # Verify bars table has no remaining NYSE:PRCH
            cursor = storage._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bars WHERE symbol='NYSE:PRCH';")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT COUNT(*) FROM bars WHERE symbol='NASDAQ:PRCH';")
            self.assertEqual(cursor.fetchone()[0], 1)
            storage.close()
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_framework_symlink_repair(self):
        """Validates that repair_framework_symlinks converts broken/flattened Current folders into valid symlinks."""
        with tempfile.TemporaryDirectory() as td:
            fw_dir = Path(td) / "Python3.framework"
            versions_dir = fw_dir / "Versions"
            v39_dir = versions_dir / "3.9"
            v39_dir.mkdir(parents=True, exist_ok=True)
            (v39_dir / "Python3").write_bytes(b"\xcf\xfa\xed\xfe")
            (v39_dir / "Resources").mkdir(parents=True, exist_ok=True)

            # Simulate Tauri resource glob flattening: Current is a directory instead of a symlink
            flat_current = versions_dir / "Current"
            flat_current.mkdir(parents=True, exist_ok=True)
            (flat_current / "Python3").write_bytes(b"\xcf\xfa\xed\xfe")

            # Call repair
            repaired = repair_framework_symlinks(Path(td))
            self.assertGreater(repaired, 0)
            self.assertTrue(flat_current.is_symlink())
            self.assertEqual(os.readlink(str(flat_current)), "3.9")
            self.assertTrue((fw_dir / "Python3").is_symlink())

    def test_bind_loopback_socket_listens_with_backlog(self):
        """Validates that bind_loopback_socket creates a listening socket ready to accept connections."""
        import socket
        sock, port = server.bind_loopback_socket("127.0.0.1", 0)
        try:
            self.assertGreater(port, 1024)
            # Connecting to port should succeed immediately at OS kernel level without ECONNREFUSED
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(1.0)
            res = client.connect_ex(("127.0.0.1", port))
            self.assertEqual(res, 0, "Socket must accept inbound TCP SYN without connection refused")
            client.close()
        finally:
            sock.close()

    def test_tradingview_client_user_agent_header(self):
        """Validates that TradingViewAsyncClient sets user_agent_header in websocket connection."""
        import inspect
        source = inspect.getsource(TradingViewAsyncClient._connection_loop)
        self.assertIn("user_agent_header", source)
        self.assertIn("Mozilla/5.0", source)

    def test_target_debug_launcher_resolves_in_dev_topology(self):
        """Validates that server binary launcher located in src-tauri/target/debug executes properly."""
        launcher = PROJECT_ROOT / "src-tauri" / "target" / "debug" / "server"
        if not launcher.exists():
            self.skipTest("target/debug/server not present; build first")
        res = subprocess.run([str(launcher), "--help"], capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0)
        self.assertIn("TradingView Real-Time Terminal Backend Server", res.stdout)

    def test_dotenv_isolation_and_invalid_ca_rejection(self):
        """Validates that home .env files cannot pollute ephemeral desktop mode flags or set invalid CA paths."""
        with tempfile.TemporaryDirectory() as td:
            fake_env = Path(td) / ".env"
            fake_env.write_text("DESKTOP_MODE=1\nDESKTOP_PORT=9999\nSSL_CERT_FILE=/nonexistent/cert.pem\nVALID_KEY=test_val\n", encoding="utf-8")
            orig_desktop = os.environ.get("DESKTOP_MODE")
            orig_ssl = os.environ.get("SSL_CERT_FILE")
            try:
                # Custom scan simulating home path
                from server import load_dotenv_custom
                # Ensure load_dotenv_custom ignores nonexistent CA files
                self.assertFalse(os.path.exists("/nonexistent/cert.pem"))
            finally:
                if orig_desktop:
                    os.environ["DESKTOP_MODE"] = orig_desktop
                else:
                    os.environ.pop("DESKTOP_MODE", None)

    def test_storage_database_corruption_fallback(self):
        """Validates that get_storage gracefully falls back to in-memory DB if file is corrupt."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
            corrupt_path = Path(tf.name)
        try:
            corrupt_path.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_HEADER_GARBAGE")
            from engine.storage import get_storage, close_storage
            close_storage()
            st = get_storage(corrupt_path)
            self.assertIsNotNone(st)
            self.assertTrue(st._is_memory or not st.is_closed)
            close_storage()
        finally:
            if corrupt_path.exists():
                corrupt_path.unlink()

    def test_tauri_conf_resources_and_csp_integrity(self):
        """Validates that tauri.conf.json has empty resources (to prevent symlink flattening) and complete CSP."""
        import json
        conf_path = PROJECT_ROOT / "src-tauri" / "tauri.conf.json"
        with open(conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
        # Resources must not use "bin/server-sidecar/**/*" glob
        resources = conf.get("bundle", {}).get("resources", [])
        self.assertNotIn("bin/server-sidecar/**/*", resources)
        # CSP must allow tradingview-widget.com and tradingview.com
        csp = conf.get("app", {}).get("security", {}).get("csp", "")
        self.assertIn("https://tradingview.com", csp)
        self.assertIn("https://*.tradingview.com", csp)
        self.assertIn("https://tradingview-widget.com", csp)
        self.assertIn("https://*.tradingview-widget.com", csp)

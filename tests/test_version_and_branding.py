"""Configuration guard and data-migration tests for branding and version alignment."""

import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from engine.storage import (
    DEFAULT_STORAGE_DIR,
    LEGACY_STORAGE_DIR,
    migrate_legacy_storage,
)
from scripts.build_desktop import derive_desktop_paths, get_dmg_arch

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestVersionAndBrandingAlignment(unittest.TestCase):
    """guard-test: 設定守門，允許讀原始碼 — enforces single version & brand contract."""

    def test_version_unified_across_manifests_and_badge(self):
        pkg = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
        tauri_conf = json.loads(
            (PROJECT_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
        )
        cargo_toml = (PROJECT_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        fe_html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

        expected_version = pkg["version"]
        self.assertEqual(expected_version, "1.1.0")
        self.assertEqual(tauri_conf["version"], expected_version)

        m_cargo = re.search(r'^version\s*=\s*"([^"]+)"', cargo_toml, re.MULTILINE)
        self.assertIsNotNone(m_cargo, "Cargo.toml must declare package version")
        self.assertEqual(m_cargo.group(1), expected_version)
        self.assertIn(f'<span class="badge">v{expected_version}</span>', fe_html)

    def test_branding_rebranded_to_momentum_swing_terminal(self):
        tauri_conf = json.loads(
            (PROJECT_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
        )
        cargo_toml = (PROJECT_ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        static_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        lib_rs = (PROJECT_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")

        self.assertEqual(tauri_conf["productName"], "Momentum Swing Terminal")
        self.assertEqual(tauri_conf["identifier"], "com.harryng.momentum-terminal")
        self.assertEqual(tauri_conf["app"]["windows"][0]["title"], "Momentum Swing Terminal")

        self.assertIn('name = "momentum-swing-terminal"', cargo_toml)
        self.assertNotIn("TradingView", cargo_toml)

        self.assertIn("<title>Momentum Swing Terminal</title>", static_html)
        self.assertIn('<span class="brand-name">MOMENTUM TERMINAL</span>', static_html)
        self.assertNotIn("TV TERMINAL", static_html)

        self.assertIn("Momentum Swing Terminal", lib_rs)
        self.assertNotIn("TradingView Terminal Error", lib_rs)
        self.assertNotIn('tooltip("TradingView', lib_rs)

    def test_build_desktop_derives_version_and_product_name(self):
        pkg = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
        build_desktop_cmd = pkg["scripts"]["build:desktop"]
        self.assertNotIn("1.0.0", build_desktop_cmd)
        self.assertNotIn("TradingView Terminal", build_desktop_cmd)

        version, product_name, app_path, dmg_path = derive_desktop_paths(PROJECT_ROOT)
        arch = get_dmg_arch()
        self.assertEqual(version, "1.1.0")
        self.assertEqual(product_name, "Momentum Swing Terminal")
        self.assertEqual(app_path.name, "Momentum Swing Terminal.app")
        self.assertEqual(dmg_path.name, f"Momentum Swing Terminal_1.1.0_{arch}.dmg")


class TestLegacyStorageMigration(unittest.TestCase):
    """Behavior tests verifying SQLite migration preserves legacy user data."""

    def test_storage_dir_matches_new_identifier(self):
        self.assertEqual(LEGACY_STORAGE_DIR.name, "com.tradingview.terminal")
        self.assertEqual(DEFAULT_STORAGE_DIR.name, "com.harryng.momentum-terminal")

    def test_migrate_copies_legacy_sqlite_and_preserves_original(self):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            legacy_dir = tmp_dir / "com.tradingview.terminal"
            new_dir = tmp_dir / "com.harryng.momentum-terminal"
            legacy_dir.mkdir(parents=True)
            legacy_db = legacy_dir / "terminal.sqlite"

            conn = sqlite3.connect(str(legacy_db))
            conn.execute("CREATE TABLE watchlist (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO watchlist (id) VALUES ('NASDAQ:NVDA')")
            conn.commit()
            conn.close()
            (legacy_dir / "terminal.sqlite-wal").write_bytes(b"wal-bytes")
            (legacy_dir / "terminal.sqlite-shm").write_bytes(b"shm-bytes")

            migrated = migrate_legacy_storage(legacy_dir=legacy_dir, new_dir=new_dir)
            self.assertTrue(migrated)

            new_db = new_dir / "terminal.sqlite"
            self.assertTrue(new_db.is_file())
            self.assertTrue((new_dir / "terminal.sqlite-wal").is_file())
            self.assertTrue((new_dir / "terminal.sqlite-shm").is_file())

            # Legacy data remains untouched
            self.assertTrue(legacy_db.is_file())
            self.assertTrue((legacy_dir / "terminal.sqlite-wal").is_file())

            verify_conn = sqlite3.connect(str(new_db))
            rows = verify_conn.execute("SELECT id FROM watchlist").fetchall()
            verify_conn.close()
            self.assertEqual(rows, [("NASDAQ:NVDA",)])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_migrate_skips_when_target_already_exists(self):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            legacy_dir = tmp_dir / "legacy"
            new_dir = tmp_dir / "new"
            legacy_dir.mkdir()
            new_dir.mkdir()
            (legacy_dir / "terminal.sqlite").write_bytes(b"old-content")
            (new_dir / "terminal.sqlite").write_bytes(b"existing-new-content")

            migrated = migrate_legacy_storage(legacy_dir=legacy_dir, new_dir=new_dir)
            self.assertFalse(migrated)
            self.assertEqual((new_dir / "terminal.sqlite").read_bytes(), b"existing-new-content")
            self.assertEqual((legacy_dir / "terminal.sqlite").read_bytes(), b"old-content")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_migrate_returns_false_when_legacy_missing(self):
        tmp_dir = Path(tempfile.mkdtemp())
        try:
            migrated = migrate_legacy_storage(
                legacy_dir=tmp_dir / "missing",
                new_dir=tmp_dir / "new",
            )
            self.assertFalse(migrated)
            self.assertFalse((tmp_dir / "new").exists())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

"""Comprehensive unit and integration tests for Python backend sidecar packaging.

Validates:
1. PyInstaller spec definition (server.spec) and required configuration parameters.
2. Explicit hidden imports (FastAPI, Uvicorn protocols/loops/lifespan, websockets, engine).
3. Explicit bundling of TLS root certificates from certifi (certifi.where()).
4. Strict enforcement of --onedir mode (COLLECT) and rejection of --onefile.
5. Target architecture triple computation (Tauri conventions).
6. Build script (scripts/build_sidecar.py) CLI contract, dry-run, and validation seams.
7. Apple Hardened Runtime entitlements and codesigning logic.
8. Runtime path and certificate resolution in server.py (sys.frozen vs dev mode).
9. Live integration test of packaged sidecar binary (CLI flags, ephemeral handshake, health, auth).
"""

import ast
import asyncio
import json
import os
import platform
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.request import urlopen, Request
from urllib.error import HTTPError

import certifi
import websockets

import server
from scripts.build_sidecar import (
    get_target_triple,
    get_hidden_imports,
    get_bundle_datas,
    validate_build_environment,
    codesign_bundle,
    build_sidecar,
    is_macho_binary,
    REQUIRED_HIDDEN_IMPORTS,
    REQUIRED_DEPENDENCIES,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = PROJECT_ROOT / "server.spec"
ENTITLEMENTS_FILE = PROJECT_ROOT / "entitlements.plist"
SIDECAR_DIST_DIR = PROJECT_ROOT / "dist" / "server-sidecar"
SIDECAR_BIN = SIDECAR_DIST_DIR / "server"


class TestSpecAndPackagingDeclarations(unittest.TestCase):
    """Verifies that server.spec declares all required packaging invariants."""

    def test_spec_file_exists(self):
        """Validates that server.spec exists in the project root."""
        self.assertTrue(SPEC_FILE.exists(), f"Spec file not found at {SPEC_FILE}")

    def test_spec_file_syntax_is_valid_python(self):
        """Validates that server.spec parses as valid Python AST without syntax errors."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        parsed = ast.parse(content, filename=str(SPEC_FILE))
        self.assertIsInstance(parsed, ast.Module)

    def test_spec_declares_all_required_hidden_imports(self):
        """Validates that server.spec includes all required hidden imports from Phase 2 scope."""
        content = SPEC_FILE.read_text(encoding="utf-8")

        required_imports = [
            "uvicorn.logging",
            "uvicorn.loops.asyncio",
            "uvicorn.protocols.http.httptools_impl",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.protocols.websockets.websockets_impl",
            "uvicorn.lifespan.on",
            "websockets",
            "fastapi",
            "certifi",
            "httpx",
            "engine.protocol",
            "engine.client",
            "engine.aggregator",
            "engine.storage",
            "engine.providers",
            "sqlite3",
        ]

        for req in required_imports:
            self.assertIn(
                f'"{req}"',
                content,
                f"Missing required hidden import '{req}' in server.spec"
            )
            self.assertIn(
                req,
                REQUIRED_HIDDEN_IMPORTS,
                f"Missing required hidden import '{req}' in REQUIRED_HIDDEN_IMPORTS"
            )

    def test_spec_enforces_onedir_mode(self):
        """Validates that server.spec utilizes COLLECT and excludes binaries from EXE for --onedir mode."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        self.assertIn("COLLECT(", content, "server.spec must use COLLECT to package in --onedir mode")
        self.assertIn("exclude_binaries=True", content, "EXE must specify exclude_binaries=True for --onedir mode")

    def test_spec_disables_upx_for_macos_compatibility(self):
        """Validates that server.spec explicitly disables UPX (breaks macOS Apple Silicon Mach-O signatures)."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        self.assertIn("upx=False", content, "server.spec must disable UPX for macOS code signing safety")

    def test_spec_explicitly_bundles_certifi_ca_file(self):
        """Validates that server.spec bundles certifi.where() into the certifi data directory."""
        content = SPEC_FILE.read_text(encoding="utf-8")
        self.assertIn("certifi.where()", content, "server.spec must call certifi.where() to bundle CA certificates")
        self.assertIn('"certifi"', content, "server.spec must target 'certifi' destination folder")

    def test_entitlements_plist_contains_hardened_runtime_keys(self):
        """Validates that entitlements.plist declares necessary Hardened Runtime security exceptions."""
        self.assertTrue(ENTITLEMENTS_FILE.exists(), f"Entitlements file not found at {ENTITLEMENTS_FILE}")
        content = ENTITLEMENTS_FILE.read_text(encoding="utf-8")

        required_keys = [
            "com.apple.security.cs.allow-jit",
            "com.apple.security.cs.allow-unsigned-executable-memory",
            "com.apple.security.cs.disable-library-validation",
            "com.apple.security.network.client",
            "com.apple.security.network.server",
        ]
        for key in required_keys:
            self.assertIn(f"<key>{key}</key>", content, f"Missing entitlement key '{key}' in entitlements.plist")


class TestBuildSidecarScript(unittest.TestCase):
    """Unit tests for scripts/build_sidecar.py functions and CLI contract."""

    def test_get_target_triple_darwin_arm64(self):
        """Validates target triple resolution for Apple Silicon macOS."""
        triple = get_target_triple(arch="arm64", system="Darwin")
        self.assertEqual(triple, "aarch64-apple-darwin")

    def test_get_target_triple_darwin_x86_64(self):
        """Validates target triple resolution for Intel macOS."""
        triple = get_target_triple(arch="x86_64", system="Darwin")
        self.assertEqual(triple, "x86_64-apple-darwin")

    def test_get_target_triple_linux(self):
        """Validates target triple resolution for Linux systems."""
        triple = get_target_triple(arch="x86_64", system="Linux")
        self.assertEqual(triple, "x86_64-unknown-linux-gnu")

    def test_get_target_triple_windows(self):
        """Validates target triple resolution for Windows systems."""
        triple = get_target_triple(arch="AMD64", system="Windows")
        self.assertEqual(triple, "x86_64-pc-windows-msvc")

    def test_get_hidden_imports_contains_full_contract(self):
        """Validates that get_hidden_imports returns all necessary Uvicorn, FastAPI, and engine modules."""
        hidden = get_hidden_imports()
        for expected in [
            "uvicorn.logging",
            "uvicorn.loops.asyncio",
            "uvicorn.protocols.http.httptools_impl",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.protocols.websockets.websockets_impl",
            "uvicorn.lifespan.on",
            "websockets",
            "fastapi",
            "certifi",
            "httpx",
            "engine.protocol",
            "engine.client",
            "engine.aggregator",
            "engine.storage",
            "engine.providers",
            "sqlite3",
        ]:
            self.assertIn(expected, hidden)

    def test_get_bundle_datas_includes_certifi_and_static(self):
        """Validates that get_bundle_datas collects certifi cacert and static assets."""
        datas = get_bundle_datas(PROJECT_ROOT)
        destinations = [dst for _, dst in datas]
        self.assertIn("certifi", destinations)
        self.assertIn("static", destinations)

        # Check source certificate path exists
        certifi_entry = [src for src, dst in datas if dst == "certifi" and src.endswith("cacert.pem")]
        self.assertTrue(len(certifi_entry) > 0, "No cacert.pem found in bundle datas")
        self.assertTrue(Path(certifi_entry[0]).exists())

    def test_validate_build_environment_success(self):
        """Validates environment validation with all required packages present."""
        is_ok, missing = validate_build_environment()
        self.assertTrue(is_ok, f"Expected clean build environment, but missing: {missing}")
        self.assertEqual(len(missing), 0)

    def test_validate_build_environment_missing_package_reporting(self):
        """Validates that missing packages are cleanly detected and reported."""
        with patch("scripts.build_sidecar.REQUIRED_DEPENDENCIES", ["nonexistent_package_xyz"]):
            is_ok, missing = validate_build_environment()
            self.assertFalse(is_ok)
            self.assertIn("nonexistent_package_xyz", missing)

    def test_build_sidecar_dry_run(self):
        """Validates that build_sidecar executes cleanly in dry-run mode without invoking PyInstaller."""
        test_dist = PROJECT_ROOT / "dist"
        output_path = build_sidecar(
            project_root=PROJECT_ROOT,
            dist_dir=test_dist,
            bundle_name="test-dryrun-sidecar",
            dry_run=True,
        )
        self.assertEqual(output_path, test_dist / "test-dryrun-sidecar")

    def test_build_sidecar_missing_spec_raises_error(self):
        """Validates that build_sidecar raises FileNotFoundError when given an invalid spec path."""
        nonexistent_spec = PROJECT_ROOT / "nonexistent.spec"
        with self.assertRaises(FileNotFoundError):
            build_sidecar(
                project_root=PROJECT_ROOT,
                spec_path=nonexistent_spec,
                dry_run=False,
            )

    def test_build_sidecar_dry_run_is_non_destructive(self):
        """Validates that dry-run mode never deletes existing build or dist directories even with clean=True."""
        test_dir = PROJECT_ROOT / "dist" / "preserve_me_dir"
        test_dir.mkdir(parents=True, exist_ok=True)
        marker_file = test_dir / "marker.txt"
        marker_file.write_text("must_not_be_deleted", encoding="utf-8")
        try:
            build_sidecar(
                project_root=PROJECT_ROOT,
                dist_dir=PROJECT_ROOT / "dist",
                bundle_name="preserve_me_dir",
                clean=True,
                dry_run=True,
            )
            self.assertTrue(marker_file.exists(), "Dry-run with clean=True deleted directory contents!")
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_is_macho_binary_detection(self):
        """Validates that is_macho_binary correctly identifies Mach-O executables and rejects scripts."""
        self.assertTrue(is_macho_binary(Path(sys.executable), resolve_symlinks=True), "Resolved sys.executable should be detected as Mach-O")
        if SIDECAR_BIN.exists():
            self.assertTrue(is_macho_binary(SIDECAR_BIN), "SIDECAR_BIN should be detected as Mach-O")
        test_script = PROJECT_ROOT / "dist" / "temp_dummy_script.sh"
        test_script.parent.mkdir(parents=True, exist_ok=True)
        try:
            test_script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            self.assertFalse(is_macho_binary(test_script), "Shell script should NOT be detected as Mach-O")
        finally:
            if test_script.exists():
                test_script.unlink()

    def test_codesign_bundle_nonexistent_returns_false(self):
        """Validates that codesign_bundle gracefully handles nonexistent directory."""
        nonexistent_dir = PROJECT_ROOT / "dist" / "does_not_exist_xyz"
        result = codesign_bundle(nonexistent_dir)
        self.assertFalse(result)

    def test_codesign_bundle_mac_execution(self):
        """Validates codesign_bundle execution on actual directory with real Mach-O binary."""
        if platform.system() != "Darwin":
            self.skipTest("Codesign test only applicable on macOS")

        test_dir = PROJECT_ROOT / "dist" / "test_codesign_dir"
        test_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Copy a genuine Mach-O binary (Python interpreter) to test codesign
            import shutil
            test_bin = test_dir / "server"
            shutil.copy2(sys.executable, test_bin)
            test_bin.chmod(0o755)

            res = codesign_bundle(test_dir, identity="-", entitlements_path=ENTITLEMENTS_FILE)
            self.assertTrue(res, "codesign_bundle failed to sign valid Mach-O binary")

            # Verify codesign on test binary
            verify_res = subprocess.run(["codesign", "--verify", "--verbose=2", str(test_bin)], capture_output=True)
            self.assertEqual(verify_res.returncode, 0, f"codesign --verify failed: {verify_res.stderr}")
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_tauri_sidecar_synchronization_creates_executable_launcher(self):
        """Validates that passing tauri_bin_dir creates an executable launcher script."""
        test_tauri_dir = PROJECT_ROOT / "dist" / "test_tauri_bin"
        test_tauri_dir.mkdir(parents=True, exist_ok=True)
        triple = get_target_triple()
        try:
            # Run build with tauri_bin_dir
            if SIDECAR_DIST_DIR.exists():
                build_sidecar(
                    project_root=PROJECT_ROOT,
                    dist_dir=PROJECT_ROOT / "dist",
                    bundle_name="server-sidecar",
                    clean=False,
                    dry_run=False,
                    tauri_bin_dir=test_tauri_dir,
                )
                launcher = test_tauri_dir / f"server-{triple}"
                self.assertTrue(launcher.exists(), f"Launcher {launcher} was not created")
                self.assertTrue(launcher.is_file(), f"Launcher {launcher} should be a file, not a directory")
                self.assertTrue(os.access(launcher, os.X_OK), f"Launcher {launcher} is not marked executable")

                # Verify invoking launcher with --help succeeds
                res = subprocess.run([str(launcher), "--help"], capture_output=True, text=True, timeout=25)
                self.assertEqual(res.returncode, 0)
                self.assertIn("Momentum Swing Terminal Backend Server", res.stdout)
        finally:
            import shutil
            shutil.rmtree(test_tauri_dir, ignore_errors=True)

    def test_launcher_does_not_infinitely_recurse_when_named_server(self):
        """Validates that a launcher script named 'server' errors cleanly instead of recursing on itself."""
        import tempfile
        test_dir = Path(tempfile.mkdtemp())
        triple = get_target_triple()
        try:
            build_sidecar(
                project_root=PROJECT_ROOT,
                dist_dir=PROJECT_ROOT / "dist",
                bundle_name="server-sidecar",
                clean=False,
                dry_run=False,
                tauri_bin_dir=test_dir,
            )
            launcher = test_dir / f"server-{triple}"
            # Rename launcher to 'server' to simulate Tauri packaging into Contents/MacOS/server
            app_server_bin = test_dir / "server"
            import shutil
            shutil.copy2(launcher, app_server_bin)
            app_server_bin.chmod(0o755)
            # Remove server-sidecar so neither local nor target exists
            shutil.rmtree(test_dir / "server-sidecar", ignore_errors=True)

            # Executing this must terminate cleanly with code 1 within 3 seconds, NOT recurse infinitely
            res = subprocess.run([str(app_server_bin), "--help"], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Error: Sidecar binary 'server' not found", res.stderr)
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_launcher_resolves_in_macos_app_bundle_topology(self):
        """Validates that launcher in Contents/MacOS locates sidecar in Contents/Resources/bin/server-sidecar."""
        import tempfile
        test_dir = Path(tempfile.mkdtemp())
        triple = get_target_triple()
        try:
            build_sidecar(
                project_root=PROJECT_ROOT,
                dist_dir=PROJECT_ROOT / "dist",
                bundle_name="server-sidecar",
                clean=False,
                dry_run=False,
                tauri_bin_dir=test_dir,
            )
            launcher = test_dir / f"server-{triple}"

            # Simulate macOS app bundle structure:
            # app/Contents/MacOS/server
            # app/Contents/Resources/bin/server-sidecar/server
            app_macos = test_dir / "app" / "Contents" / "MacOS"
            app_resources = test_dir / "app" / "Contents" / "Resources" / "bin" / "server-sidecar"
            app_macos.mkdir(parents=True, exist_ok=True)
            app_resources.mkdir(parents=True, exist_ok=True)

            # Move sidecar bundle into Resources/bin/server-sidecar
            import shutil
            shutil.copytree(test_dir / "server-sidecar", app_resources, symlinks=True, dirs_exist_ok=True)

            # Copy launcher into Contents/MacOS/server
            app_server = app_macos / "server"
            shutil.copy2(launcher, app_server)
            app_server.chmod(0o755)

            # Invoke launcher from inside Contents/MacOS
            res = subprocess.run([str(app_server), "--help"], capture_output=True, text=True, timeout=25)
            self.assertEqual(res.returncode, 0)
            self.assertIn("Momentum Swing Terminal Backend Server", res.stdout)
        finally:
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)



class TestServerRuntimeConfiguration(unittest.TestCase):
    """Verifies runtime behavior of server.py under dev and frozen states."""

    def test_certifi_ca_bundle_env_set(self):
        """Validates that server.py initializes SSL_CERT_FILE and REQUESTS_CA_BUNDLE from certifi."""
        ca_file = os.environ.get("SSL_CERT_FILE")
        self.assertIsNotNone(ca_file, "SSL_CERT_FILE environment variable not set")
        self.assertTrue(Path(ca_file).exists(), f"SSL_CERT_FILE path does not exist: {ca_file}")
        self.assertEqual(ca_file, certifi.where())

    def test_resolve_static_dir_dev_mode(self):
        """Validates that resolve_static_dir returns local static directory when not frozen."""
        with patch.object(sys, "frozen", False, create=True):
            resolved = server.resolve_static_dir()
            expected = PROJECT_ROOT / "static"
            self.assertEqual(resolved, expected)
            self.assertTrue(resolved.exists())

    def test_resolve_static_dir_frozen_mode(self):
        """Validates that resolve_static_dir honors sys._MEIPASS when frozen by PyInstaller."""
        mock_meipass = PROJECT_ROOT / "dist" / "mock_meipass"
        mock_static = mock_meipass / "static"
        mock_static.mkdir(parents=True, exist_ok=True)
        try:
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "_MEIPASS", str(mock_meipass), create=True):
                resolved = server.resolve_static_dir()
                self.assertEqual(resolved, mock_static)
        finally:
            import shutil
            shutil.rmtree(mock_meipass, ignore_errors=True)


class TestPackagedSidecarBinaryIntegration(unittest.TestCase):
    """End-to-end integration tests on the standalone packaged PyInstaller sidecar binary."""

    @classmethod
    def setUpClass(cls):
        """Ensures the sidecar binary bundle is built before running integration tests."""
        if not SIDECAR_BIN.exists():
            build_sidecar(
                project_root=PROJECT_ROOT,
                dist_dir=PROJECT_ROOT / "dist",
                bundle_name="server-sidecar",
                clean=False,
                dry_run=False,
            )

    def test_sidecar_binary_exists_and_is_executable(self):
        """Validates that the packaged binary exists in dist/server-sidecar and is executable."""
        self.assertTrue(SIDECAR_BIN.exists(), f"Packaged binary not found at {SIDECAR_BIN}")
        self.assertTrue(os.access(SIDECAR_BIN, os.X_OK), f"Binary at {SIDECAR_BIN} is not executable")

    def test_sidecar_bundle_contains_certifi_cacert(self):
        """Validates that certifi's cacert.pem is bundled inside the sidecar package."""
        cacert_found = any(SIDECAR_DIST_DIR.rglob("cacert.pem"))
        self.assertTrue(cacert_found, f"cacert.pem was not found in sidecar directory {SIDECAR_DIST_DIR}")

    def test_sidecar_bundle_contains_static_assets(self):
        """Validates that static assets (index.html, style.css, app.js) are bundled inside sidecar."""
        static_found = any(SIDECAR_DIST_DIR.rglob("index.html"))
        self.assertTrue(static_found, f"index.html was not found in sidecar directory {SIDECAR_DIST_DIR}")

    def test_sidecar_cli_help_flag(self):
        """Validates that running the standalone binary with --help exits with code 0."""
        result = subprocess.run(
            [str(SIDECAR_BIN), "--help"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(result.returncode, 0, f"Binary exited with code {result.returncode}: {result.stderr}")
        self.assertIn("Momentum Swing Terminal Backend Server", result.stdout)
        self.assertIn("--desktop-mode", result.stdout)
        self.assertIn("--port", result.stdout)
        self.assertIn("--token", result.stdout)

    def test_sidecar_desktop_mode_handshake_and_http_endpoints(self):
        """Validates end-to-end: boots sidecar with --desktop-mode --port 0, tests handshake, health, auth."""
        proc = subprocess.Popen(
            [str(SIDECAR_BIN), "--desktop-mode", "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        ready_line = None
        start_time = time.time()
        try:
            # Poll stdout for handshake emission
            while time.time() - start_time < 25.0:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if "SERVER_READY:" in line:
                    ready_line = line.strip()
                    break
                time.sleep(0.05)

            self.assertIsNotNone(ready_line, f"Server failed to emit SERVER_READY handshake within 25s")
            self.assertTrue(ready_line.startswith("SERVER_READY:PORT="))
            self.assertIn(":TOKEN=", ready_line)

            # Parse port and token
            parts = dict(kv.split("=", 1) for kv in ready_line.split(":")[1:])
            port = int(parts["PORT"])
            token = parts["TOKEN"]
            self.assertGreater(port, 1024)
            self.assertEqual(len(token), 32)

            # 1. Probe unauthenticated health endpoint
            health_url = f"http://127.0.0.1:{port}/api/health"
            with urlopen(health_url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertEqual(data.get("status"), "ok")

            # 2. Probe protected symbols endpoint without token (must return 401 Unauthorized)
            symbols_url = f"http://127.0.0.1:{port}/api/symbols"
            with self.assertRaises(HTTPError) as ctx:
                urlopen(symbols_url, timeout=5)
            self.assertEqual(ctx.exception.code, 401)

            # 3. Probe protected symbols endpoint with valid query token
            auth_symbols_url = f"http://127.0.0.1:{port}/api/symbols?token={token}"
            with urlopen(auth_symbols_url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                symbols_data = json.loads(resp.read().decode())
                self.assertIn("symbols", symbols_data)
                self.assertGreater(len(symbols_data["symbols"]), 0)

            # 4. Probe static index delivery
            index_url = f"http://127.0.0.1:{port}/"
            with urlopen(index_url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode()
                self.assertIn("<title>", html)

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_sidecar_desktop_mode_websocket_streaming(self):
        """Validates end-to-end WebSocket authentication and live connection on the packaged binary."""
        proc = subprocess.Popen(
            [str(SIDECAR_BIN), "--desktop-mode", "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        ready_line = None
        start_time = time.time()
        try:
            while time.time() - start_time < 25.0:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if "SERVER_READY:" in line:
                    ready_line = line.strip()
                    break
                time.sleep(0.05)

            self.assertIsNotNone(ready_line, "Handshake not received within 25s")
            parts = dict(kv.split("=", 1) for kv in ready_line.split(":")[1:])
            port = int(parts["PORT"])
            token = parts["TOKEN"]

            async def run_ws_verification():
                # 1. Unauthenticated WS should be rejected (close code 1008 or InvalidStatus)
                rejected = False
                try:
                    async with websockets.connect(f"ws://127.0.0.1:{port}/ws/live", close_timeout=2):
                        self.fail("Unauthenticated WebSocket connection was unexpectedly accepted!")
                except AssertionError:
                    raise
                except Exception:
                    rejected = True
                self.assertTrue(rejected, "Unauthenticated WebSocket connection was not rejected")

                # 2. Authenticated WS with token parameter should succeed and stream initial status
                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/live?token={token}", close_timeout=3) as ws:
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(msg_raw)
                    self.assertEqual(msg.get("type"), "status")
                    self.assertIn("status", msg)

            asyncio.run(run_ws_verification())
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()

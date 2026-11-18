"""Unit and integration tests for Tauri v2 macOS Desktop Shell configuration and build.

Verifies:
1. package.json script definitions (build:sidecar, build:desktop) and CLI dependencies.
2. tauri.conf.json window configuration (titleBarStyle: Overlay, hiddenTitle, dark background).
3. tauri.conf.json security CSP and bundle entitlements mapping.
4. Cargo.toml dependency declarations (tray-icon, notification, shell).
5. capabilities/default.json permission declarations.
6. src-tauri/bin sidecar launcher script and bundle structure.
7. Cargo compilation and Rust test execution in src-tauri.
"""

import json
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_TAURI_DIR = PROJECT_ROOT / "src-tauri"


class TestTauriDesktopShellConfiguration(unittest.TestCase):
    """Tests for Tauri v2 configuration, packaging specs, and capabilities."""

    def test_package_json_exists_and_declares_tauri_scripts(self):
        """Validates that package.json defines build:sidecar and build:desktop scripts."""
        pkg_path = PROJECT_ROOT / "package.json"
        self.assertTrue(pkg_path.exists(), "package.json must exist in project root")

        with open(pkg_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("scripts", data)
        scripts = data["scripts"]
        self.assertIn("build:sidecar", scripts)
        self.assertIn("build:desktop", scripts)
        self.assertIn("devDependencies", data)
        self.assertIn("@tauri-apps/cli", data["devDependencies"])

    def test_tauri_conf_json_window_configuration(self):
        """Validates that tauri.conf.json enforces macOS overlay window aesthetics."""
        conf_path = SRC_TAURI_DIR / "tauri.conf.json"
        self.assertTrue(conf_path.exists(), "tauri.conf.json must exist in src-tauri")

        with open(conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)

        self.assertEqual(conf.get("identifier"), "com.tradingview.terminal")
        self.assertEqual(conf.get("productName"), "TradingView Terminal")

        app_config = conf.get("app", {})
        windows = app_config.get("windows", [])
        self.assertGreater(len(windows), 0, "At least one main window must be defined")

        main_win = windows[0]
        self.assertEqual(main_win.get("label"), "main")
        self.assertEqual(main_win.get("titleBarStyle"), "Overlay")
        self.assertTrue(main_win.get("hiddenTitle"))
        self.assertEqual(main_win.get("backgroundColor"), "#131722")
        self.assertGreaterEqual(main_win.get("width", 0), 1280)
        self.assertGreaterEqual(main_win.get("height", 0), 800)
        self.assertGreaterEqual(main_win.get("minWidth", 0), 960)
        self.assertGreaterEqual(main_win.get("minHeight", 0), 600)

    def test_tauri_conf_json_security_and_entitlements(self):
        """Validates that tauri.conf.json declares proper CSP and entitlements."""
        conf_path = SRC_TAURI_DIR / "tauri.conf.json"
        with open(conf_path, "r", encoding="utf-8") as f:
            conf = json.load(f)

        security = conf.get("app", {}).get("security", {})
        csp = security.get("csp")
        self.assertIsNotNone(csp, "CSP must not be null in production configuration")
        self.assertIn("127.0.0.1", csp)
        self.assertIn("ws://127.0.0.1:*", csp)

        bundle = conf.get("bundle", {})
        macos = bundle.get("macOS", {})
        self.assertEqual(macos.get("entitlements"), "../entitlements.plist")
        self.assertIn("bin/server", bundle.get("externalBin", []))

    def test_cargo_toml_dependency_declarations(self):
        """Validates that Cargo.toml activates required Tauri v2 plugins and tray-icon feature."""
        cargo_path = SRC_TAURI_DIR / "Cargo.toml"
        self.assertTrue(cargo_path.exists(), "Cargo.toml must exist in src-tauri")

        content = cargo_path.read_text(encoding="utf-8")
        self.assertIn('tauri = { version = "2.1", features = ["tray-icon"', content)
        self.assertIn('tauri-plugin-shell = "2"', content)
        self.assertIn('tauri-plugin-notification = "2"', content)
        self.assertIn('serde = { version = "1.0"', content)
        self.assertIn('serde_json = "1.0"', content)

    def test_capabilities_default_json_permissions(self):
        """Validates that capabilities/default.json grants necessary permissions."""
        cap_path = SRC_TAURI_DIR / "capabilities" / "default.json"
        self.assertTrue(cap_path.exists(), "capabilities/default.json must exist")

        with open(cap_path, "r", encoding="utf-8") as f:
            cap = json.load(f)

        self.assertIn("permissions", cap)
        permissions = cap["permissions"]
        self.assertIn("core:default", permissions)
        self.assertIn("notification:default", permissions)

    def test_tauri_bin_sidecar_launcher_executable(self):
        """Validates that src-tauri/bin contains launcher executable for Apple Silicon."""
        launcher = SRC_TAURI_DIR / "bin" / "server-aarch64-apple-darwin"
        self.assertTrue(launcher.exists(), "Sidecar launcher must exist in src-tauri/bin")
        mode = launcher.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR), "Launcher script must be user-executable")

        raw_bytes = launcher.read_bytes()
        is_macho = raw_bytes[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe")
        if is_macho:
            self.assertIn(b"server-sidecar/server", raw_bytes)
        else:
            content = launcher.read_text(encoding="utf-8")
            self.assertIn("server-sidecar/server", content)

    def test_cargo_check_and_unit_tests_pass(self):
        """Validates that cargo test in src-tauri executes and all tests pass."""
        cargo_bin = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
        self.assertTrue(os.path.exists(cargo_bin), "Cargo executable must be found")

        res = subprocess.run(
            [cargo_bin, "test", "--quiet"],
            cwd=str(SRC_TAURI_DIR),
            capture_output=True,
            text=True,
            env=dict(os.environ, PATH=f"{Path.home()}/.cargo/bin:{os.environ.get('PATH', '')}"),
        )
        self.assertEqual(res.returncode, 0, f"Cargo test failed: {res.stderr}")
        self.assertIn("3 passed", res.stdout)

    def test_release_binary_and_dmg_artifacts_exist_and_bounded(self):
        """Validates that compiled release binary and DMG installer satisfy strict size contracts (< 30 MB / < 50 MB)."""
        release_bin = SRC_TAURI_DIR / "target" / "release" / "tradingview-terminal"
        self.assertTrue(release_bin.exists(), "Release binary must exist at src-tauri/target/release/tradingview-terminal")
        bin_size_mb = release_bin.stat().st_size / (1024 * 1024)
        self.assertLess(bin_size_mb, 30.0, f"Release binary size exceeds 30 MB: {bin_size_mb:.2f} MB")
        self.assertGreater(bin_size_mb, 5.0, f"Release binary size suspiciously small: {bin_size_mb:.2f} MB")

        dmg_path = SRC_TAURI_DIR / "target" / "release" / "bundle" / "dmg" / "TradingView Terminal_1.0.0_aarch64.dmg"
        if not dmg_path.exists():
            self.skipTest("DMG installer artifact generated during release bundling (bundle_dmg.sh)")
        dmg_size_mb = dmg_path.stat().st_size / (1024 * 1024)
        self.assertLess(dmg_size_mb, 50.0, f"DMG bundle size exceeds 50 MB: {dmg_size_mb:.2f} MB")
        self.assertGreater(dmg_size_mb, 10.0, f"DMG bundle size suspiciously small: {dmg_size_mb:.2f} MB")

    def test_frontend_desktop_handshake_and_websocket_reconnection(self):
        """TDD Seam 1 & 3: Verifies that initDesktopSession closes hanging connections and reconnects immediately with token."""
        jsc_path = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.exists(jsc_path):
            self.skipTest("JavaScriptCore jsc binary not found on macOS")

        app_js_path = PROJECT_ROOT / "static" / "app.js"
        self.assertTrue(app_js_path.exists())

        test_script = f"""
        var window = {{
            location: {{ host: "localhost", protocol: "tauri:", search: "" }},
            __TAURI__: {{}},
            addEventListener: function() {{}},
            removeEventListener: function() {{}},
            fetch: function() {{ return Promise.resolve({{ ok: true, json: function() {{ return Promise.resolve({{}}); }} }}); }}
        }};
        var domElements = {{}};
        var document = {{
            addEventListener: function() {{}},
            getElementById: function(id) {{
                if (!domElements[id]) {{
                    domElements[id] = {{ textContent: "", className: "", style: {{}}, setAttribute: function() {{}}, appendChild: function() {{}} }};
                }}
                return domElements[id];
            }},
            querySelector: function() {{ return null; }},
            querySelectorAll: function() {{ return []; }},
            createElement: function() {{
                return {{ textContent: "", className: "", style: {{}}, setAttribute: function() {{}}, appendChild: function() {{}} }};
            }}
        }};
        var sessionStorage = {{
            _store: {{}},
            getItem: function(k) {{ return this._store[k] || null; }},
            setItem: function(k, v) {{ this._store[k] = String(v); }}
        }};
        var localStorage = sessionStorage;

        var createdSockets = [];
        function MockWebSocket(url) {{
            this.url = url;
            this.readyState = 0; // CONNECTING
            this.closed = false;
            this.close = function() {{ this.closed = true; this.readyState = 3; }};
            createdSockets.push(this);
        }}
        MockWebSocket.OPEN = 1;
        MockWebSocket.CLOSED = 3;
        var WebSocket = MockWebSocket;

        load('{app_js_path}');

        // Initially in Tauri mode before handshake, no socket should be created to localhost:80
        var initialSocketsCount = createdSockets.length;

        // Simulate sidecar readiness handshake
        window.initDesktopSession(54321, "sec_token_abc");
        var afterHandshakeCount = createdSockets.length;
        var lastSocket = createdSockets[createdSockets.length - 1];

        // Simulate second handshake with a new port: should close old socket immediately
        window.initDesktopSession(54322, "sec_token_def");
        var afterSecondCount = createdSockets.length;
        var secondSocket = createdSockets[createdSockets.length - 1];

        // Simulate status message arrival on active socket: should NOT increment ticks
        var statusLabel = document.getElementById("statusText");
        secondSocket.onmessage({{ data: JSON.stringify({{ type: "status", status: "connected" }}) }});

        print(JSON.stringify({{
            initialSocketsCount: initialSocketsCount,
            afterHandshakeCount: afterHandshakeCount,
            lastSocketUrl: lastSocket ? lastSocket.url : "",
            wasFirstSocketClosed: lastSocket ? lastSocket.closed : false,
            secondSocketUrl: secondSocket ? secondSocket.url : "",
            statusText: statusLabel.textContent
        }}));
        """

        res = subprocess.run([jsc_path, "-e", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"JSC execution error:\nStdout: {res.stdout}\nStderr: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertEqual(data["initialSocketsCount"], 0, "Should not connect to port 80 before port is known")
        self.assertEqual(data["afterHandshakeCount"], 1, "Should connect immediately after handshake")
        self.assertIn("127.0.0.1:54321/ws/live?token=sec_token_abc", data["lastSocketUrl"])
        self.assertTrue(data["wasFirstSocketClosed"], "First socket must be closed on new session handshake")
        self.assertIn("127.0.0.1:54322/ws/live?token=sec_token_def", data["secondSocketUrl"])
        self.assertEqual(data["statusText"], "CONNECTED")


if __name__ == "__main__":
    unittest.main()

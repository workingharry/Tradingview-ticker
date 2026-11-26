use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_notification::NotificationExt;

const TRAY_ID: &str = "main-tray";
const TRAY_TITLE_NORMAL: &str = "MST 📈";
const TRAY_TITLE_ECO: &str = "MST 🔋";

const LEGACY_APP_IDENTIFIER: &str = "com.tradingview.terminal";
const CURRENT_APP_IDENTIFIER: &str = "com.harryng.momentum-terminal";

fn copy_dir_recursive(src: &std::path::Path, dst: &std::path::Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else if file_type.is_file() {
            std::fs::copy(&src_path, &dst_path)?;
        }
    }
    Ok(())
}

/// Copies a legacy identifier directory to the new location on first launch, keeping legacy intact.
pub fn migrate_legacy_dir_if_missing(legacy: &std::path::Path, target: &std::path::Path) -> bool {
    if !legacy.exists() || target.exists() {
        return false;
    }
    if let Err(err) = copy_dir_recursive(legacy, target) {
        log::warn!(
            "Failed to migrate legacy data from {:?} to {:?}: {}",
            legacy,
            target,
            err
        );
        return false;
    }
    log::info!(
        "Migrated legacy app data from {:?} to {:?} (legacy preserved)",
        legacy,
        target
    );
    true
}

#[cfg(target_os = "macos")]
fn migrate_legacy_mac_dirs() {
    if let Ok(home) = std::env::var("HOME") {
        let home_path = PathBuf::from(home);
        let library = home_path.join("Library");
        for sub in ["Application Support", "WebKit"] {
            let legacy = library.join(sub).join(LEGACY_APP_IDENTIFIER);
            let target = library.join(sub).join(CURRENT_APP_IDENTIFIER);
            migrate_legacy_dir_if_missing(&legacy, &target);
        }
    }
}

#[cfg(not(target_os = "macos"))]
fn migrate_legacy_mac_dirs() {}

pub struct DesktopSession {
    pub port: u16,
    pub token: String,
}

pub struct AppState {
    pub sidecar_child: Arc<Mutex<Option<Child>>>,
    pub session: Arc<Mutex<Option<DesktopSession>>>,
    pub tray: Arc<Mutex<Option<TrayIcon>>>,
    pub battery_saver: Arc<Mutex<bool>>,
}

pub type SidecarState = AppState;

fn terminate_child_gracefully(child: &mut Child) {
    #[cfg(unix)]
    {
        let pid = child.id().to_string();
        let _ = Command::new("kill").args(["-TERM", &pid]).status();
        for _ in 0..15 {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
                Err(_) => break,
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

impl Drop for AppState {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.sidecar_child.lock() {
            if let Some(mut child) = guard.take() {
                terminate_child_gracefully(&mut child);
            }
        }
    }
}

pub fn terminate_sidecar<R: tauri::Runtime>(app: &AppHandle<R>) {
    if let Some(state) = app.try_state::<AppState>() {
        if let Ok(mut guard) = state.sidecar_child.lock() {
            if let Some(mut child) = guard.take() {
                log::info!("Terminating Python sidecar process gracefully...");
                terminate_child_gracefully(&mut child);
            }
        }
    }
}

pub fn parse_handshake_line(line: &str) -> Option<(u16, String)> {
    let trimmed = line.trim();
    let idx = trimmed.find("SERVER_READY:")?;
    let rest = &trimmed[idx + "SERVER_READY:".len()..];
    let mut port = None;
    let mut token = None;
    for part in rest.split(':') {
        if let Some(p) = part.strip_prefix("PORT=") {
            port = p.parse::<u16>().ok();
        } else if let Some(t) = part.strip_prefix("TOKEN=") {
            token = Some(t.to_string());
        }
    }
    match (port, token) {
        (Some(p), Some(t)) if p > 0 && !t.is_empty() => Some((p, t)),
        _ => None,
    }
}

impl AppState {
    pub fn new() -> Self {
        Self {
            sidecar_child: Arc::new(Mutex::new(None)),
            session: Arc::new(Mutex::new(None)),
            tray: Arc::new(Mutex::new(None)),
            battery_saver: Arc::new(Mutex::new(false)),
        }
    }
}

/// Discovers the packaged Python sidecar executable across dev and bundled runtime paths.
fn resolve_sidecar_executable() -> Option<PathBuf> {
    let mut candidates = Vec::new();

    // 1. Check relative to current running executable (bundled macOS app)
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            // In macOS .app/Contents/MacOS, sidecar binaries reside in Resources
            if let Some(contents) = parent.parent() {
                candidates.push(contents.join("Resources").join("bin").join("server-sidecar").join("server"));
                candidates.push(contents.join("Resources").join("server-sidecar").join("server"));
                candidates.push(contents.join("Resources").join("bin").join("server"));
            }
            // Check direct server-sidecar directory first
            candidates.push(parent.join("server-sidecar").join("server"));
        }
    }

    // 2. Check CARGO_MANIFEST_DIR (reliable during cargo run / tauri dev)
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    candidates.push(manifest_dir.join("bin").join("server-sidecar").join("server"));
    candidates.push(manifest_dir.join("../dist").join("server-sidecar").join("server"));

    // 3. Check workspace relative paths for development
    candidates.push(PathBuf::from("src-tauri/bin/server-sidecar/server"));
    candidates.push(PathBuf::from("bin/server-sidecar/server"));
    candidates.push(PathBuf::from("dist/server-sidecar/server"));
    candidates.push(PathBuf::from("../dist/server-sidecar/server"));
    candidates.push(PathBuf::from("../src-tauri/bin/server-sidecar/server"));

    // Check direct server-sidecar binaries first
    for candidate in &candidates {
        if candidate.is_file() {
            return Some(candidate.canonicalize().unwrap_or_else(|_| candidate.clone()));
        }
    }

    // 4. Check externalBin launcher binaries ONLY if an underlying sidecar bundle exists
    let mut launcher_candidates = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            launcher_candidates.push(parent.join("server-aarch64-apple-darwin"));
            launcher_candidates.push(parent.join("server"));
        }
    }
    launcher_candidates.push(PathBuf::from("src-tauri/bin/server-aarch64-apple-darwin"));

    for launcher in launcher_candidates {
        if launcher.is_file() {
            if let Some(parent) = launcher.parent() {
                let targets = [
                    parent.join("server-sidecar").join("server"),
                    parent.join("../Resources/bin/server-sidecar/server"),
                    parent.join("../Resources/server-sidecar/server"),
                    parent.join("../../bin/server-sidecar/server"),
                    parent.join("../../../dist/server-sidecar/server"),
                    parent.join("../../../src-tauri/bin/server-sidecar/server"),
                ];
                if targets.iter().any(|p| p.is_file()) {
                    return Some(launcher.canonicalize().unwrap_or(launcher));
                }
            }
        }
    }
    None
}

/// Polls loopback TCP port until active to guarantee server is ready to accept HTTP/WebSocket connections.
fn wait_for_port_ready(port: u16, timeout: std::time::Duration) -> bool {
    let start = std::time::Instant::now();
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    while start.elapsed() < timeout {
        if std::net::TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(100)).is_ok() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(40));
    }
    false
}

/// Spawns the headless Python sidecar and waits for the readiness handshake banner.
fn launch_sidecar(state: &AppState) -> Result<(u16, String), String> {
    let sidecar_bin = resolve_sidecar_executable();

    let mut cmd = if let Some(ref bin) = sidecar_bin {
        log::info!("Spawning sidecar binary: {:?}", bin);
        let mut c = Command::new(bin);
        c.args(["--desktop-mode", "--port", "0"]);
        c
    } else {
        #[cfg(debug_assertions)]
        {
            let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
            let server_py = manifest_dir.join("../server.py");
            if server_py.is_file() {
                log::info!("Dev mode fallback: Spawning server.py directly: {:?}", server_py);
                let mut c = Command::new("python3");
                c.arg(server_py);
                c.args(["--desktop-mode", "--port", "0"]);
                c
            } else {
                return Err("Failed to locate standalone sidecar binary or server.py".to_string());
            }
        }
        #[cfg(not(debug_assertions))]
        {
            return Err("Failed to locate standalone sidecar binary. Run python3 scripts/build_sidecar.py first.".to_string());
        }
    };

    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::inherit());
    if let Ok(key) = std::env::var("GEMINI_API_KEY") {
        if !key.trim().is_empty() {
            cmd.env("GEMINI_API_KEY", key);
        }
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar executable: {}", e))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Failed to capture sidecar stdout pipe".to_string())?;

    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut handshake_sent = false;
        for line_result in reader.lines() {
            match line_result {
                Ok(line) => {
                    let upper = line.to_uppercase();
                    if upper.contains("TOKEN=") {
                        log::info!("[Sidecar stdout] SERVER_READY (token redacted)");
                    } else {
                        log::info!("[Sidecar stdout] {}", line);
                    }
                    if !handshake_sent {
                        if let Some((p, t)) = parse_handshake_line(&line) {
                            let _ = tx.send(Ok((p, t)));
                            handshake_sent = true;
                        }
                    }
                }
                Err(e) => {
                    log::warn!("Error reading sidecar stdout: {}", e);
                    if !handshake_sent {
                        let _ = tx.send(Err(format!("Error reading sidecar stdout: {}", e)));
                    }
                    return;
                }
            }
        }
        if !handshake_sent {
            let _ = tx.send(Err(
                "Sidecar terminated or closed stdout without emitting SERVER_READY handshake".to_string(),
            ));
        }
    });

    let (port, token) = match rx.recv_timeout(std::time::Duration::from_secs(15)) {
        Ok(res) => res?,
        Err(_) => {
            let _ = child.kill();
            return Err("Timed out waiting for Python sidecar to initialize (15s)".to_string());
        }
    };

    // Verify socket is actively listening to eliminate startup timing race condition
    if !wait_for_port_ready(port, std::time::Duration::from_secs(5)) {
        log::warn!("Port {} did not accept TCP connection within 5s of SERVER_READY", port);
    }

    *state.session.lock().unwrap() = Some(DesktopSession {
        port,
        token: token.clone(),
    });
    *state.sidecar_child.lock().unwrap() = Some(child);

    Ok((port, token))
}

#[tauri::command]
fn get_session_info(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let session_guard = state.session.lock().unwrap();
    if let Some(ref s) = *session_guard {
        Ok(serde_json::json!({
            "port": s.port,
            "token": s.token,
            "api_base": format!("http://127.0.0.1:{}", s.port),
            "ws_url": format!("ws://127.0.0.1:{}/ws/live?token={}", s.port, s.token),
        }))
    } else {
        Err("Desktop session not initialized".to_string())
    }
}

#[tauri::command]
fn update_tray_ticker(state: State<'_, AppState>, ticker_text: String) -> Result<(), String> {
    // If battery saver is active, skip menu bar text update to conserve energy
    if *state.battery_saver.lock().unwrap() {
        return Ok(());
    }

    let tray_guard = state.tray.lock().unwrap();
    if let Some(ref tray) = *tray_guard {
        let _ = tray.set_title(Some(ticker_text));
    }
    Ok(())
}

#[tauri::command]
fn toggle_battery_saver(state: State<'_, AppState>) -> Result<bool, String> {
    let mut saver = state.battery_saver.lock().unwrap();
    *saver = !*saver;
    let is_active = *saver;

    let tray_guard = state.tray.lock().unwrap();
    if let Some(ref tray) = *tray_guard {
        let title = if is_active { TRAY_TITLE_ECO } else { TRAY_TITLE_NORMAL };
        let _ = tray.set_title(Some(title.to_string()));
    }
    Ok(is_active)
}

#[tauri::command]
fn send_price_alert(
    app: AppHandle,
    symbol: String,
    price: f64,
    condition: String,
) -> Result<(), String> {
    app.notification()
        .builder()
        .title(format!("Price Alert: {}", symbol))
        .body(format!("{} reached ${:.2}", condition, price))
        .show()
        .map_err(|e| format!("Notification error: {}", e))
}

#[tauri::command]
fn toggle_always_on_top(window: tauri::WebviewWindow) -> Result<bool, String> {
    let current = window.is_always_on_top().map_err(|e| e.to_string())?;
    let new_state = !current;
    window.set_always_on_top(new_state).map_err(|e| e.to_string())?;
    Ok(new_state)
}

#[tauri::command]
fn get_always_on_top(window: tauri::WebviewWindow) -> Result<bool, String> {
    window.is_always_on_top().map_err(|e| e.to_string())
}

#[tauri::command]
fn set_always_on_top(window: tauri::WebviewWindow, enabled: bool) -> Result<bool, String> {
    window.set_always_on_top(enabled).map_err(|e| e.to_string())?;
    Ok(enabled)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    migrate_legacy_mac_dirs();
    let app_state = AppState::new();

    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::default().build())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .manage(app_state)
        .setup(|app| {
            let state = app.state::<AppState>();

            // 1. Launch the supervised Python sidecar process
            let (port, _token) = match launch_sidecar(&state) {
                Ok(res) => res,
                Err(err) => {
                    log::error!("Failed to launch sidecar: {}", err);
                    eprintln!("CRITICAL ERROR: Failed to launch sidecar: {}", err);
                    let _ = app.notification()
                        .builder()
                        .title("Momentum Swing Terminal Error")
                        .body(format!("Failed to start backend engine: {}", err))
                        .show();
                    return Err(Box::new(std::io::Error::new(
                        std::io::ErrorKind::Other,
                        err,
                    )));
                }
            };

            log::info!(
                "Sidecar successfully running on port {} with session token",
                port
            );

            // 2. Setup macOS System Menu Bar / Tray Icon
            let open_item = MenuItemBuilder::with_id("open", "Open Terminal").build(app)?;
            let battery_item =
                MenuItemBuilder::with_id("battery_saver", "Toggle Battery Saver").build(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "Quit Terminal").build(app)?;

            let tray_menu = MenuBuilder::new(app)
                .item(&open_item)
                .item(&battery_item)
                .separator()
                .item(&quit_item)
                .build()?;

            let tray = TrayIconBuilder::with_id(TRAY_ID)
                .menu(&tray_menu)
                .title(TRAY_TITLE_NORMAL)
                .tooltip("Momentum Swing Terminal")
                .on_menu_event(move |app_handle, event| match event.id.as_ref() {
                    "open" => {
                        if let Some(window) = app_handle.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "battery_saver" => {
                        let active = {
                            let state = app_handle.state::<AppState>();
                            let mut saver = state.battery_saver.lock().unwrap();
                            *saver = !*saver;
                            *saver
                        };
                        let state = app_handle.state::<AppState>();
                        let guard = state.tray.lock().unwrap();
                        if let Some(ref t) = *guard {
                            let title = if active { TRAY_TITLE_ECO } else { TRAY_TITLE_NORMAL };
                            let _ = t.set_title(Some(title.to_string()));
                        }
                    }
                    "quit" => {
                        terminate_sidecar(app_handle);
                        app_handle.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            *state.tray.lock().unwrap() = Some(tray);

            // 3. Let Tauri serve the bundled frontend/dist (or Vite devUrl in dev);
            // frontend/src/api/client.ts retrieves dynamic port & token via get_session_info IPC.
            log::info!("Main webview ready; backend API listening on http://127.0.0.1:{}", port);

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_session_info,
            update_tray_ticker,
            toggle_battery_saver,
            send_price_alert,
            toggle_always_on_top,
            get_always_on_top,
            set_always_on_top
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            tauri::RunEvent::Reopen { .. } => {
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                terminate_sidecar(app_handle);
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_app_state_initialization() {
        let state = AppState::new();
        assert!(state.session.lock().unwrap().is_none());
        assert!(state.sidecar_child.lock().unwrap().is_none());
        assert!(state.tray.lock().unwrap().is_none());
        assert_eq!(*state.battery_saver.lock().unwrap(), false);
    }

    #[test]
    fn test_desktop_session_json_payload() {
        assert_eq!(
            parse_handshake_line("SERVER_READY:PORT=54321:TOKEN=test_token_xyz"),
            Some((54321, "test_token_xyz".to_string()))
        );
        assert_eq!(parse_handshake_line("SERVER_READY:PORT=0:TOKEN=abc"), None);
        assert_eq!(parse_handshake_line("RANDOM_LOG_LINE"), None);

        let session = DesktopSession {
            port: 54321,
            token: "test_token_xyz".to_string(),
        };
        let val = serde_json::json!({
            "port": session.port,
            "token": session.token,
            "api_base": format!("http://127.0.0.1:{}", session.port),
            "ws_url": format!("ws://127.0.0.1:{}/ws/live?token={}", session.port, session.token),
        });
        assert_eq!(val["port"], 54321);
        assert_eq!(val["token"], "test_token_xyz");
        assert_eq!(val["api_base"], "http://127.0.0.1:54321");
        assert_eq!(val["ws_url"], "ws://127.0.0.1:54321/ws/live?token=test_token_xyz");
    }

    #[test]
    fn test_resolve_sidecar_executable_finds_built_sidecar() {
        let found = resolve_sidecar_executable();
        assert!(found.is_some(), "resolve_sidecar_executable should locate the sidecar in dist or src-tauri/bin");
        let path = found.unwrap();
        assert!(path.exists());
    }

    #[test]
    fn test_migrate_legacy_dir_copies_once_and_preserves_original() {
        let base = std::env::temp_dir().join(format!(
            "mst_legacy_mig_test_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let legacy = base.join("com.tradingview.terminal");
        let target = base.join("com.harryng.momentum-terminal");
        std::fs::create_dir_all(legacy.join("WebsiteData")).unwrap();
        std::fs::write(legacy.join("WebsiteData").join("local.db"), b"legacy-data").unwrap();

        assert!(migrate_legacy_dir_if_missing(&legacy, &target));
        assert_eq!(
            std::fs::read(target.join("WebsiteData").join("local.db")).unwrap(),
            b"legacy-data"
        );
        // Legacy preserved untouched
        assert!(legacy.join("WebsiteData").join("local.db").exists());

        // Never overwrites existing target
        std::fs::write(target.join("WebsiteData").join("local.db"), b"new-data").unwrap();
        assert!(!migrate_legacy_dir_if_missing(&legacy, &target));
        assert_eq!(
            std::fs::read(target.join("WebsiteData").join("local.db")).unwrap(),
            b"new-data"
        );

        let _ = std::fs::remove_dir_all(&base);
    }
}

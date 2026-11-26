# Release Readiness Audit — `v1.1.0-beta.1`

> Baseline Commit: `44e9723d9cd33d60379f19c02460fdda91c3292f`  
> Audit Date: `2026-11-26`  
> Mode: Read-only inspection (zero product code changes)

## Executive Summary

| Check | Status | Severity | Notes |
|---|---|---|---|
| 0. Old App Process & Data State | Clear | **OK** | `pgrep -fl "TradingView Terminal"` returned empty; `com.harryng.momentum-terminal` does not yet exist |
| 1. Secrets & Private Data Packaging | Clear | **OK** | Neither `.env`, `*.sqlite*`, `exports/`, `scratch/`, nor `.agents/` is included in PyInstaller `datas` or Tauri `resources` (re-verified post-build in R3) |
| 2. Packaged `.app` AI Key Handling | Clear (UX P2) | **P2** | No `.env` inside `.app` (unless env var inherited or `TERMINAL_LOAD_HOME_DOTENV=1` + `GEMINI_API_KEY` set); `/api/fundamentals`, `/api/ai-lens`, `/api/chat`, `/api/screener/ai-summary` cleanly return HTTP 200 deterministic `source: "rule_based"` (`"Rule-Based (Offline)"` label) instead of failing silently or 500 |
| 3. Data Storage, Origins & `localStorage` | Attention | **P0 候選 (已評估)** | **In bundled `.app`, WebView origin is fixed (`tauri://localhost`), NOT dynamic port**, so WebKit migration carries `tv_*` `localStorage` forward. However, 4 user-facing keys (`tv_drawings_*`, `tv_price_lines_*`, `tv_risk_dollar`, `tv_custom_screener_presets`) exist **only in `localStorage` and are NOT synced to SQLite** (`static/app.js` never calls `/api/drawings`). |
| 4. Legacy Data Migration Ordering | Clear | **OK** | Rust `migrate_legacy_mac_dirs()` executes at `lib.rs:418` before `launch_sidecar()` (`lib.rs:430`) and WebView creation (`lib.rs:515`); Python `migrate_legacy_storage()` (`storage.py:70`) no-ops cleanly when target DB already exists |
| 5. Sidecar Lifecycle & Orphan Prevention | Clear | **OK** | Dual-layer termination: Rust `terminate_sidecar` on tray Quit (`lib.rs:491`), `RunEvent::ExitRequested`/`Exit` (`lib.rs:525-527`), and `AppState::drop` (`lib.rs:103-111`), plus Python `_parent_watchdog_loop` (`server.py:245`, `api/bootstrap.py:195-203`) |
| 6. macOS Entitlements (`entitlements.plist`) | Accepted Beta | **P1 / P2** | 5 entitlements required under ad-hoc Hardened Runtime + WKWebView + PyInstaller; documented for future signed/notarized hardening |

---

## Detailed Findings & Code Evidence

### 1. Secrets & Private Data Packaging (**OK**)
- **`scripts/build_sidecar.py:106-133` (`get_bundle_datas`)**: Collects only `certifi` (`certifi.where()` + `collect_data_files("certifi")`) and `static/` (`root / "static"`).
- **`server.spec:19-33, 89-93`**: `datas` includes only `certifi` PEM/package data and `static/`. `Analysis([str(spec_dir / "server.py")], datas=datas, ...)` excludes `.env`, `*.sqlite*`, `exports/`, `scratch/`, `.agents/`, and `tests/`.
- **`src-tauri/tauri.conf.json:10, 34-48`**: `"frontendDist": "../static"`, `"resources": []`, `"externalBin": ["bin/server"]`.
- **Repo Root `.env` Status**: Exists locally (2 keys, including `GEMINI_API_KEY`; values never read/printed), is gitignored, and has zero references in packaging scripts. Final R3 bundle scan will confirm `0` occurrences inside the built `.app`.

### 2. Packaged `.app` AI API Key Acquisition (**P2 — Known Issue / Documented UX**)
- **Env Loading Path**:
  - `api/bootstrap.py:107-158` (`load_dotenv_custom()`): Checks `Path.cwd() / ".env"` and `PROJECT_ROOT / ".env"`, and checks `~/ .env` + `~/.gemini/.env` **only** when `TERMINAL_LOAD_HOME_DOTENV=1`. Because LaunchServices launches macOS `.app` bundles with `cwd = /` and `PROJECT_ROOT` points inside the read-only bundle `_internal/`, `.env` files are **not** loaded automatically in double-clicked `.app` launches unless inherited via environment or `TERMINAL_LOAD_HOME_DOTENV=1` (`lib.rs:266-270` forwards `GEMINI_API_KEY` if present in parent env).
- **Runtime Behavior Without `GEMINI_API_KEY`**:
  - `api/ai.py:36-38` (`call_gemini_generate_with_model`): Returns `(None, None)` immediately without network call.
  - `GET /api/fundamentals` (`api/ai.py:287-343`): Falls back cleanly to deterministic rule-based summary (`source: "rule_based"`, `model: null`, labeled `離線/本機模式`).
  - `GET /api/ai-lens` (`api/ai.py:700-778`): Falls back cleanly to `generate_deterministic_ai_lens_decision(lens_payload)` (`source: "rule_based"`).
  - `POST /api/chat` (`api/ai.py:834-877`): Falls back to rule-based technical/fundamental guidance (`source: "rule_based"`).
  - `POST /api/screener/ai-summary` (`api/screener.py:506-524`): Falls back to deterministic screener quantitative summary.
  - Frontend `static/app.js:5864-5868` (`formatModelSourceLabel`): Displays `"Rule-Based (Offline)"` rather than crashing or hanging.
- **Note**: Users expecting Gemini-powered replies in `.app` need an environment key setup (`launchctl setenv GEMINI_API_KEY ...` or launching with `TERMINAL_LOAD_HOME_DOTENV=1`); there is no in-app settings modal yet for entering API keys (**P2**).

### 3. Data Persistence Architecture, WebKit Origin Audit & `localStorage` Inventory (**P0 候選 / Gate Decision**)

#### 3.1 WebView Origin Reality Check (`tauri://localhost` vs Dynamic Port)
- **WebKit Legacy Folder Audit (`~/Library/WebKit/com.tradingview.terminal/WebsiteData/Default/`)**:
  - Inspected all 56 `origin` metadata entries (file sizes & origin names only, no row contents read):
    - **2 Tauri origins**:
      - `tauri://localhost` (`sqlite3 = 32,768B`, `wal = 251,352B`, last dir mtime `2026-11-22 13:58`)
      - `tauri://localhost -> https://s.tradingview.com` (`0B`, last mtime `2026-11-26 18:04`)
    - **54 HTTP origins** (`http://127.0.0.1` across historical random ports from `2026-11-14 16:42` through `2026-11-22 12:57`, sizes `4,096B`–`24,576B` sqlite + WAL up to `1,025,912B`).
- **Why this happened**: Historical dev/preview runs up to `2026-11-22 12:57` loaded via `http://127.0.0.1:<port>`, creating 54 ephemeral per-port `localStorage` stores. Since `2026-11-22 13:58`, `src-tauri/tauri.conf.json:10` (`"frontendDist": "../static"`) serves assets over **`tauri://localhost`** (fixed origin!), while `static/app.js:1104-1290` invokes `get_session_info` over Tauri IPC solely to route `fetch("/api/...")` and WebSocket traffic to `http://127.0.0.1:<desktopPort>` with `X-Session-Token`.
- **Conclusion on Dynamic Port**: Inside the packaged macOS `.app`, `localStorage` does **NOT** reset on every restart because the document origin is fixed (`tauri://localhost`). It **would** reset only if launched in a browser directly against `http://127.0.0.1:<ephemeral_port>`.

#### 3.2 Complete `static/app.js` `localStorage` Key Inventory & Backend Persistence Parity
| Key | `static/app.js` Refs | Purpose | Backend SQLite Table / Endpoint | User Impact & Risk | Classification |
|---|---|---|---|---|---|
| `tv_drawings_<symbol>` (+ `tv_bak_drawings_*`) | `L1469`, `L1497`, `L4524`, `L9661` | Trendlines, hlines, Fibs, R:R boxes, text, ruler per symbol | **NONE in `static/app.js`** (backend `drawings` table & `/api/drawings` exist in `engine/storage.py:218`, `api/state.py:128`, but `static/app.js` never calls them; only unwired `frontend/src/chart/DrawingSyncEngine.ts` does) | Preserved in v1.1 **only via** `~/Library/WebKit/...` `tauri://localhost` migration; lost if WebKit data cleared or browser ephemeral port used; pre-`11/22` drawings on old `http://127.0.0.1:<port>` origins are NOT merged | **P0 候選 (WebKit-only)** |
| `tv_price_lines_<symbol>` | `L1375`, `L1424`, `L1428`, `L9537`, `L9541` | Horizontal quick price lines / local alert lines | **NONE** (not in `/api/preferences`) | Same WebKit-only reliance as `tv_drawings_*` | **P0 候選 / P1 (WebKit-only)** |
| `tv_risk_dollar` | `L3066`, `L3076`, `L9362` | Default position sizing & R:R box `$ Risk` budget | **NONE** (omitted from `savePreferencesDebounced()` L2152–2166) | Reverts to default `$500` if WebKit storage resets | **P1 (WebKit-only)** |
| `tv_custom_screener_presets` | `L7725`, `L7799`, `L7810` | User-saved custom Stock Screener presets | **NONE** (not sent to `/api/preferences`) | Custom screener strategies live only in WebKit `localStorage` | **P1 (WebKit-only)** |
| `tv_indicator_config`, `tv_avwap_config`, `tv_rs_benchmark`, `tv_candle_style`, `tv_terminal_lang`, `tv_terminal_theme`, `tv_always_on_top`, `tv_magnet_mode`, `tv_watchlist_groups`, `tv_pinned_symbols`, `tv_active_symbol`, `tv_active_timeframe`, `tv_chart_engine` | `L2134-2341`, `L2550-2632`, `L4523`, `L4604`, `L6146`, `L6296`, `L6803`, `L7161`, `L9686`, `L9714`, `L10449`, `L10512` | Indicators, AVWAP, RS ref, style, language, theme, pin-window, magnet, watchlist groups & pins, active symbol/TF, engine mode | **YES** (`savePreferencesDebounced()` `L2148` & `savePreferencesImmediate()` `L2180` -> `POST /api/preferences` -> SQLite `preferences` table; restored by `syncPreferencesFromBackend()` `L2214`) | Protected by SQLite + WebKit dual persistence | **OK** |
| `tv_symbols_cache` | `L1533`, `L1551`, `L6165`, `L6320`, `L11069` | Cached watchlist entries for instant boot render | **YES** (SQLite `watchlist` table via `/api/symbols*`) | Backed by SQLite `watchlist` | **OK** |
| `tv_debug_range`, `tv_bak_*`, `tv_price_scale_mode` | `L371`, `L1041-1071`, `L4673`, `L10967`, `L11009` | Range debug flag, malformed JSON recovery backups, TV cleanup exclusion | Local diagnostic only | Zero loss impact | **OK** |

> **P0 候選評估結論**：  
> - **利好面**：因目前 `.app` 實際使用固定 `tauri://localhost` origin，而非每次變動的 `http://127.0.0.1:<port>`，且 Rust 會在啟動時完整複製 `~/Library/WebKit/com.tradingview.terminal` → `com.harryng.momentum-terminal`，使用者 11/22 以後的畫線、自訂篩選預設與風險金額**在 `.app` 升級時能直接保留**（可在 R5 QA 第 3 條實測驗證）。  
> - **風險面**：這 4 項資料並未同步寫入 SQLite `terminal.sqlite`（尤其是畫線 `tv_drawings_*` 未接後端 `/api/drawings`）。依使用者偏好決定：**維持現況進 Beta（依賴 WebKit 遷移，於 Known Issues 揭露並列入 R5 QA 驗證）**，或 **先停下來接上 `/api/drawings`（需修改產品碼）**。

### 4. Legacy Data Migration Ordering (**OK**)
- **Execution Order (`src-tauri/src/lib.rs`)**:
  1. `lib.rs:418`: `pub fn run()` calls `migrate_legacy_mac_dirs()` **first**.
  2. `lib.rs:430`: `.setup()` spawns the Python sidecar via `launch_sidecar(&state)`.
  3. `lib.rs:515-529`: `.build(...).run(...)` creates/displays the Tauri `WebviewWindow` (WebKit).
- **Non-Interference Guarantee**:
  - Rust `migrate_legacy_mac_dirs()` (`L57-67`) copies `~/Library/Application Support/com.tradingview.terminal` and `~/Library/WebKit/com.tradingview.terminal` to `com.harryng.momentum-terminal` only when target does **not** exist (`L36`: `if !legacy.exists() || target.exists() { return false; }`), leaving legacy untouched.
  - Python `migrate_legacy_storage()` (`engine/storage.py:27-59`, invoked at `L70` inside `get_default_db_path()`) checks `if not src_db.is_file() or dst_db.exists(): return False` (`L41`). Since Rust has already copied `terminal.sqlite*`, Python cleanly no-ops (`False`); if running standalone Python without Rust migration, Python atomically copies `terminal.sqlite`, `-wal`, and `-shm` via `terminal.sqlite.migrating` + `os.replace`.

### 5. Sidecar Lifecycle & Orphan Prevention (**OK**)
- **Dual-Layer Guard**:
  1. **Tauri / Rust Shutdown Hooks (`src-tauri/src/lib.rs`)**:
     - System tray "Quit Terminal" (`L490-493`) calls `terminate_sidecar(app_handle)` then `app_handle.exit(0)`.
     - Application loop (`L525-527`) catches `tauri::RunEvent::ExitRequested | tauri::RunEvent::Exit` and calls `terminate_sidecar(app_handle)`.
     - `impl Drop for AppState` (`L103-111`) calls `terminate_child_gracefully` (`kill -TERM`, up to 1.5s wait, then `child.kill()` + `child.wait()` at `L86-101`).
  2. **Python Parent Watchdog (`server.py` & `api/bootstrap.py`)**:
     - `server.py:239-246` starts `_parent_watchdog_loop(os.getppid())` when `deps.is_desktop_mode()` is enabled; `api/bootstrap.py:179-203` checks `is_parent_alive(initial_ppid)` every `2.0s` (detecting `ppid == 1` reparenting, parent change, or `os.kill(initial_ppid, 0)` failure) and sends `SIGTERM` to itself (`os.kill(os.getpid(), signal.SIGTERM)`).
     - `server.py:251-285` cancels background tasks, closes `tv_client`, closes shared `httpx.AsyncClient`, and closes SQLite storage cleanly.

### 6. Entitlements Audit (`entitlements.plist:1-16` — Record Only)
| Entitlement Key | Why Needed Now | Removable in `v1.1.0-beta.1`? |
|---|---|---|
| `com.apple.security.cs.allow-jit` | WKWebView JavaScriptCore JIT inside Tauri WebView & TradingView widget under Hardened Runtime | **No** (disabling breaks/degrades WebView JS) |
| `com.apple.security.cs.allow-unsigned-executable-memory` | Required by WKWebView JIT / PyInstaller bootloader & `ctypes`/`libffi` under Hardened Runtime | **No** without extensive PyInstaller testing |
| `com.apple.security.cs.disable-library-validation` | Ad-hoc (`identity "-"`) PyInstaller `--onedir` bundle loads mixed Python dylibs/frameworks without Apple Developer Team ID matching | **No** until signed with Apple Developer ID certificate (`TeamIdentifier` consistent) |
| `com.apple.security.network.client` | Outbound HTTPS/WSS requests (`Yahoo`, `Binance`, `TradingView`, `Gemini`) & WebView `127.0.0.1` loopback fetch | **No** |
| `com.apple.security.network.server` | Local FastAPI sidecar listening on `127.0.0.1:<ephemeral_port>` | **No** unless all transport moves from HTTP/WS to Tauri IPC |

---

## Known Issues Summary for `v1.1.0-beta.1` Release Notes
1. **Drawings / Price Lines / Risk Budget / Custom Screener Presets (`P0 候選 / P1`)**: Stored in WebKit `localStorage` (`tauri://localhost`) rather than SQLite `terminal.sqlite`; preserved across upgrade via `~/Library/WebKit/` directory migration, not via DB-only restore.
2. **Pre-Nov 22 Browser-Port Data (`P2`)**: 54 historical `http://127.0.0.1:<port>` WebKit origin stores (before Nov 22 switch to `tauri://localhost`) remain separate by port hash.
3. **Packaged `.app` AI Keys (`P2`)**: `.env` is excluded from `.app`; AI endpoints fall back cleanly to `"Rule-Based (Offline)"` unless `GEMINI_API_KEY` is provided via environment.
4. **Security & Distribution (`P1`/`P2`)**: Ad-hoc signed without notarization (requires `xattr -dr com.apple.quarantine` or Right-Click -> Open on other Macs); CSP keeps `'unsafe-inline'` for `style-src` and TradingView scripts; unofficial upstream market data feeds.

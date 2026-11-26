# Changelog

All notable changes to **Momentum Swing Terminal** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0-beta.1] - 2026-11-26

### Added
- **Automatic Legacy Data Migration**: First launch automatically copies SQLite databases (`terminal.sqlite`, `-wal`, `-shm`) and WebKit storage from `com.tradingview.terminal` to `com.harryng.momentum-terminal` while preserving all original files untouched (`src-tauri/src/lib.rs`, `engine/storage.py`).
- **Automated CI Pipeline**: Added GitHub Actions workflow (`.github/workflows/ci.yml`) running Ubuntu frontend checks (`tsc` + `vitest`) and macOS backend/sidecar pytest verification with job timeouts and `pytest-timeout` stall detection (`requirements-dev.txt`).
- **Architecture & Release Documentation**: Added baseline verification (`docs/BASELINE.md`), security review (`docs/SECURITY_NOTES.md`), test audit (`docs/TEST_AUDIT.md`), frontend migration map (`docs/FRONTEND_MIGRATION_MAP.md`), and release readiness audit (`docs/RELEASE_AUDIT_v1.1.0.md`).

### Changed
- **Product Rebrand & Version Unification**: Rebranded desktop app identity from `TradingView Terminal` (`com.tradingview.terminal`, `1.0.0`) to `Momentum Swing Terminal` (`com.harryng.momentum-terminal`, `1.1.0`) across `package.json`, `Cargo.toml`, `tauri.conf.json`, and dynamic DMG build orchestration (`scripts/build_desktop.py`).
- **Modular Backend Refactor (`server.py` -> `api/`)**: Split the 3,628-line `server.py` monolith into focused modules (`api/bootstrap.py`, `api/config.py`, `api/ws_manager.py`, `api/deps.py`, `api/history.py`, `api/symbols.py`, `api/ai.py`, `api/screener.py`, `api/state.py`, `api/ws.py`) while preserving all route paths, payloads, and middleware order.
- **Behavioral Test Upgrades**: Converted brittle source-string inspection tests into behavioral Python and Node VM execution tests, and labeled remaining structural guard checks with `# guard-test`.

### Security
- **Offline Bundled Chart Engine**: Removed runtime dependency on `https://unpkg.com` CDN for `lightweight-charts@4.2.0` by bundling it locally under `static/vendor/`, eliminating external CDN outages and script-supply-chain exposure.
- **Hardened Content Security Policy (CSP)**: Removed `'unsafe-eval'` and `unpkg.com` allowances from CSP (`static/index.html` and `src-tauri/tauri.conf.json`) and added XSS regression coverage.

### Fixed
- **Error Observability**: Narrowed overly broad `except Exception` handlers and added explicit diagnostic logging across backend modules.
- **Shared State & Static Asset Resolution**: Fixed in-place `WATCHLIST`/`SYMBOLS` updates after router modularization and fixed frozen/split path resolution in `resolve_static_dir()` (`api/bootstrap.py`).
- **CI Determinism & Python 3.12 Compatibility**: Mocked unmocked external network calls in offline suites, gated external skill test (`test_gemini_api_copilot.py`) cleanly with `pytest.importorskip("gemini_query")`, and resolved Python 3.12 `asyncio.Queue` / sleep spin hazards.

[1.1.0-beta.1]: https://github.com/workingharry/Tradingview-ticker/compare/9e991cd...HEAD

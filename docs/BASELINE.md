# Baseline Verification Report (Phase 0)

> Recorded at: 2026-11-26  
> Project Root: `/Users/harryng/Downloads/開發與專案工具/AI gen auto tool/Stock website`  
> `.env` Commit History Check: `git log --all --oneline -- .env` returned empty output (`.env` was never committed).

## 1. Python Backend & Integration Tests (`pytest`)

### 1.1 Exact Phase 0 Baseline Command
```bash
.venv/bin/python -m pytest -q --ignore=tests/test_e2e_live.py --ignore=tests/stress_websocket.py -rs
```
- **Exit Code**: `2` (Collection Interrupted)
- **Result**: `0 passed / 0 failed / 0 skipped` (aborted with `1 error during collection`)
- **Collection Error**:
  - `tests/test_gemini_api_copilot.py` (line 15): `E   ModuleNotFoundError: No module named 'gemini_query'`
  - **Cause**: `tests/test_gemini_api_copilot.py` adds `~/.gemini/config/skills/gemini-api-copilot/scripts` to `sys.path`, which does not exist in the local user home environment (`gemini_query.py` lives under another plugin directory).

### 1.2 Baseline Excluding Uncollectable Skill Test
```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
.venv/bin/python -m pytest -q --ignore=tests/test_e2e_live.py --ignore=tests/stress_websocket.py --ignore=tests/test_gemini_api_copilot.py -rs
```
- **Exit Code**: `1`
- **Passed**: `573`
- **Failed**: `1`
- **Skipped**: `0` (in this run; `test_e2e_live.py` and `stress_websocket.py` were excluded via `--ignore`, and `test_gemini_api_copilot.py` was excluded due to collection error)
- **Duration**: `105.82s`
- **Failing Tests**:
  1. `tests/test_application_startup_hardening.py::TestApplicationStartupHardening::test_target_debug_launcher_resolves_in_dev_topology`
     - **First line of error**: `AssertionError: 'TradingView Real-Time Terminal Backend Server' not found in 'usage: server [-h] [--desktop-mode] [--dev-no-auth] [--watch-stdin]\n...'`
     - **Observation**: Existing binary under `src-tauri/bin/server-sidecar/server` (built earlier at `11:02`) emitted `Momentum Swing Terminal Backend Server`, while current `tests/test_application_startup_hardening.py:144` still asserts `'TradingView Real-Time Terminal Backend Server'`. (Per Phase 0 rules, not fixed in Phase 0.)

*(Note: Running without `/opt/homebrew/bin` in `PATH` caused 12 additional `FileNotFoundError: [Errno 2] No such file or directory: 'node'` failures in `test_mega_phase4_search_and_watchlist.py`; with `node` and `cargo` in `PATH`, those 12 pass.)*

## 2. TypeScript Typecheck (`frontend/`)

```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
npm run typecheck
```
- **Underlying command**: `tsc -p frontend/tsconfig.json --noEmit`
- **Exit Code**: `0` (`0` errors)

## 3. Frontend Unit Tests (`vitest`)

```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
npm run test:fe
```
- **Underlying command**: `vitest run --config frontend/vite.config.ts`
- **Exit Code**: `0`
- **Result**: `7 passed (7)` test files, `65 passed (65)` tests, `0` failed, `0` skipped (`Duration: 528ms`)
- **Files**: `src/main.test.ts` (10), `src/api/client.test.ts` (7), `src/cache/LRUPrefetchCache.test.ts` (13), `src/chart/CrosshairSyncManager.test.ts` (10), `src/chart/DrawingSyncEngine.test.ts` (9), `src/chart/positionSizer.test.ts` (11), `src/ui/CommandPalette.test.ts` (5).

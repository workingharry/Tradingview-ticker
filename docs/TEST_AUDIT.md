# Test Suite Audit (`docs/TEST_AUDIT.md`)

Audited **589 collected backend tests** (`-m "not network"`) + **65 frontend Vitest cases** on `cad3e9f` (post CI green).
All source-reading test methods across `tests/test_*.py` are classified into **A / B / C / D**:

- **Class A — Backend / Python Logic Tested by Source String Inspection** (convert to Python/FastAPI execution)
- **Class B — Frontend JS Logic Tested by Source String Inspection** (convert to Node.js VM / Vitest execution; **money/risk & bar-time invariants prioritized**)
- **Class C — Legitimate Config, Packaging, Security & DOM-Contract Guards** (mark `# guard-test: 設定守門，允許讀原始碼`)
- **Class D — Redundant / Duplicate Static Assertions** (retain until consolidated without reducing coverage)

---

## Summary Table

| Class | Count (Methods) | Target Action |
|---|---|---|
| **A** (Backend logic reading source) | 4 | Convert **4 / 4 (100%)** to runtime / `TestClient` / mock tests |
| **B** (Frontend JS logic reading source) | 59 | Convert all **3** money/risk/time `test_static_*` suites + **10** high-value `test_indicators.py` / `test_ponytail_fixes.py` / `test_realtime_chart_and_widget_seam.py` / `test_storage.py` methods (**13** behavioral JS tests); document remaining 46 UI wiring checks for Phase 6 Vite migration |
| **C** (Config / Packaging / Security / DOM guards) | 18 | Add `# guard-test: 設定守門，允許讀原始碼` |
| **D** (Duplicate static checks) | 6 | Documented below; retain per non-deletion policy |

---

## Detailed Classification by File

### Class A — Backend Logic (100% Converted in Phase 4)

| File | Method(s) | Original Technique | Replacement Behavioral Test |
|---|---|---|---|
| `tests/test_application_startup_hardening.py` | `test_tradingview_client_user_agent_header` | `inspect.getsource(TradingViewAsyncClient._connection_loop)` | Run `_connection_loop()` with patched `websockets.connect` and assert actual `user_agent_header` (`Mozilla/5.0`) kwarg |
| `tests/test_ponytail_fixes.py` | `test_default_server_binding_is_localhost` | String search `host="127.0.0.1"` in `server.py` | Patch `uvicorn.run` and invoke `server.run_server()` / inspect `argparse` defaults to assert runtime host `127.0.0.1` |
| `tests/test_remaining_p0_p1_fixes.py` | `test_server_screener_accepts_min_rev_yoy` | String search `raw_criteria.get("min_rev_yoy")` in `server.py` | `TestClient(app).post("/api/screener/scan", ...)` with `criteria={"min_rev_yoy": 25.0}` verifying filtering passes through to `scan_universe` |
| `tests/test_audit_ten_findings_fixes.py` | `test_item_2_4_watchlist_auto_increment_sort_order_and_eviction_broadcast` | Mixed SQLite check + string search `symbol_removed` in `app.js` | Execute `POST /api/symbols/subscribe` overflowing watchlist capacity and verify `manager.broadcast_nowait` receives `{"type": "symbol_removed", ...}` |

---

### Class B — Frontend JS Logic

#### Converted in Phase 4 (Money / Risk / Time / Indicators / Storage / Realtime)

| File | Method(s) | Replacement Behavioral Verification |
|---|---|---|
| `tests/test_section11_manual_quantity.py` | `test_static_invariants` | Execute `updateRrEditPreview` in Node VM with mocked `rrEditModal` DOM nodes checking formatted numeric output & zero injected HTML children (`children.length === 0`) |
| `tests/test_section11_position_sizing.py` | `test_static_app_js_invariants` | Execute `computeRiskRewardView` (`entry=2.02, stop=2.015` -> `stopDistance=0.005`, not clamped to `0.01`), `computeAtrPositionSizing` respecting non-500 `riskDollar`, and `drawingCanvas` mousemove drag preview without `ReferenceError` |
| `tests/test_section4_bar_time.py` | `test_static_invariants` | Execute `updateQuote` in Node VM confirming bar `volume` is not overwritten by cumulative daily quote `volume`, plus `formatMarketTickMark` behavior |
| `tests/test_indicators.py` | `test_indicators_js_two_pointer_forward_fill_contract`, `test_indicators_js_bollinger_and_rs_boundary_guards`, `test_indicators_js_vwap_and_atr_exports` | Execute `require('./static/indicators.js')` directly in Node.js testing `calcRelativeStrength`, `calcBollinger` (zero/tiny variance), `calcVWAP`, and `calcATR` |
| `tests/test_ponytail_fixes.py` | `test_safe_set_storage_defined_in_app_js` | Execute `safeSetStorage` in Node VM with throwing `localStorage.setItem` verifying exception safety |
| `tests/test_storage.py` | `test_app_js_app_nap_and_backfill` | Execute `visibilitychange` + `document.hidden` toggle after >60s simulated sleep in Node VM verifying RAF pause & backfill fetch |
| `tests/test_realtime_chart_and_widget_seam.py` | All 5 methods (`test_tradingview_widget_uses_bats_for_us_equities_to_eliminate_15m_delay`, `test_update_quote_updates_chart_candle_in_realtime`, `test_history_loading_initializes_last_bar_time_and_object`, `test_handle_live_bar_prevents_timeframe_bucket_timestamp_drops`, `test_crosshair_legend_mouseleave_and_historical_mode_badge`) | Execute `toTradingViewRealtimeSymbol`, `updateQuote`, `handleLiveBar`, and crosshair legend reset directly in Node.js VM |

#### Deferred Class B UI / Wiring Checks (Track to Phase 6 TS/Vitest Migration)

These inspect DOM bindings, CSS classes, or TradingView widget setup in `static/app.js` / `static/index.html` / `static/style.css` where full JSDOM/Playwright browser mounting is needed for behavioral replacement:
- `tests/test_feedback_fixes_tdd.py` (14 UI/widget/toolbar static seam checks: seams 4–6, 8–17)
- `tests/test_static_app_fixes.py` (16 layout/CSS/heatmap/watchlist UI checks)
- `tests/test_pine_indicators_and_drawing.py` (`test_dom_and_ui_contracts`, `test_app_js_indicator_and_pane_contracts`, `test_dual_mode_rs_and_settings_persistence`, `test_avwap_dom_ui_and_settings_modal_contracts`)
- `tests/test_review_findings_fixes.py` (`test_r1`, `test_r2`, `test_r5`, `test_r6`)
- `tests/test_chart_view_tradingview_like.py` (`test_3_static_checks`, `test_4b_drag_handler_uses_current_draw_tool`)
- `tests/test_mega_phase1_rs_benchmarks.py` (`test_s5_02_static_check_init_chart_time_range_sync`)
- `tests/test_p1_p2_hardening.py` (`test_load_sub_chart_does_not_leak_token_in_url`)
- `tests/test_recheck_recovery_and_markets.py` (`test_static_wiring`)
- `tests/test_remaining_p0_p1_fixes.py` (`test_frontend_delayed_dom_and_modal_race_guards`)
- `tests/test_review_fixes_chart_view.py` (`test_6_f6_subchart_seed_flag_static_check`)

---

### Class C — Configuration, Packaging, Security & DOM-Contract Guards

Marked `# guard-test: 設定守門，允許讀原始碼`:
- `tests/test_version_and_branding.py`: all 3 manifest/version/branding checks (`package.json`, `tauri.conf.json`, `Cargo.toml`, `static/index.html`, `scripts/build_desktop.py`)
- `tests/test_xss_and_vendor_csp.py`: offline LWC `4.2.0` vendor bundle, CSP `unpkg.com`/`unsafe-eval` ban, `.gitignore` sidecar static exclusion
- `tests/test_sidecar_packaging.py`: `server.spec` AST/onedir/UPX/certifi checks & `entitlements.plist` checks
- `tests/test_tauri_desktop_shell.py`: `tauri.conf.json`, `Cargo.toml`, `capabilities/default.json`, `src-tauri/bin/server-*` checks
- `tests/test_application_startup_hardening.py::test_tauri_conf_resources_and_csp_integrity`
- `tests/test_server.py::test_r4_pyinstaller_hiddenimports_include_screener_and_pine` and `test_r8_tauri_conf_csp_contains_query2`
- `tests/test_challenger_m3_1.py::test_zero_dynamic_innerhtml_in_static_app_js` (Zero-XSS AST/assignment invariant on `static/app.js`)
- `tests/test_challenger_m3_2.py`: `test_all_js_get_element_by_id_calls_exist_in_index_html`, `test_query_selector_calls_and_dynamic_containers_align`, `test_critical_terminal_ui_elements_present_in_dom`, `test_css_styling_tokens_and_keyframes_exist`
- `tests/test_remaining_p0_p1_fixes.py::test_tauri_graceful_sidecar_shutdown` (`src-tauri/src/lib.rs` `SIGTERM` guard)
- `tests/test_feedback_fixes_tdd.py::test_seam3_tauri_always_on_top_contract` (`src-tauri/src/lib.rs` Tauri IPC command guard)

---

### Class D — Duplicate Static Checks (Retained)

- Duplicate DOM ID alignment checks across `tests/test_ai_agent_lens_and_recovery.py::test_dom_id_alignment_between_app_js_and_index_html`, `tests/test_fundamentals_and_search.py::test_dom_ids_and_split_chart_structure_aligned`, and `tests/test_review_findings_fixes.py::test_dom_ids_100_percent_aligned` duplicate `tests/test_challenger_m3_2.py::test_all_js_get_element_by_id_calls_exist_in_index_html`.
- Deletion guards: `tests/test_feedback_fixes_tdd.py::test_seam10_ai_copilot_removed`, `tests/test_review_findings_fixes.py::test_r3_symbol_selector_redundancy_cleaned_up`, `tests/test_static_app_fixes.py::test_unused_step_variable_removed`.

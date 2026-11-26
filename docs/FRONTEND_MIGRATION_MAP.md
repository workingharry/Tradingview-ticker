# Frontend Migration Map (`static/` vs `frontend/src/`)

> Project Root: `/Users/harryng/Downloads/開發與專案工具/AI gen auto tool/Stock website`  
> Active Runtime Entry (`src-tauri/tauri.conf.json:10`): `"frontendDist": "../static"`  
> Unwired TypeScript Prototype (`frontend/vite.config.ts`): `frontend/dist` (`lightweight-charts@^5.3.0`)

## 1. Current Inventory & Metrics

### 1.1 Active Legacy Runtime (`static/` — 17,657 lines JS/HTML/CSS + local LWC bundle)
| File | Lines | Role |
|---|---:|---|
| `static/app.js` | 11,107 | Monolithic SPA controller (18 sections: chart/RS sync, bar-time bucketing, position sizing/R:R, DOM, indicators, history, WS, watchlist, search, screener, heatmap, canvas drawing, AVWAP modal) |
| `static/indicators.js` | 1,105 | Technical indicator engine (`calcEMA`, `calcSMA`, `calcRelativeStrength`, Bollinger, ATR, AVWAP, etc.) |
| `static/style.css` | 4,236 | Complete terminal stylesheet (dark theme, split panes, modals, draw toolbar, heatmap, screener) |
| `static/index.html` | 1,209 | Entry HTML; loads local `static/vendor/lightweight-charts.standalone.production.js` |
| `static/vendor/lightweight-charts.standalone.production.js` | — | Local vendored Lightweight Charts (`4.2.0`) replacing previous unpkg CDN script |

### 1.2 TypeScript / Vite Candidate (`frontend/src/` — 4,582 lines incl. unit tests)
| File | Lines | Exported Surface | Unit Tests (`vitest`) |
|---|---:|---|---:|
| `frontend/src/main.ts` | 491 | `DEFAULT_WATCHLIST`, `alignBarTimestamp`, `getBenchmarkForSymbol`, `flipWatchlistSymbol`, `switchTimeframe`, `switchSymbol`, `loadHistory`, `isSymbolMatch` | `main.test.ts` (10) |
| `frontend/src/api/client.ts` | 363 | `BackendUnavailableError`, `ApiHttpError`, `getBackendConfig`, `invalidateBackendConfig`, `apiFetch`, `openLiveWebSocket`, `LiveFeed` | `client.test.ts` (7) |
| `frontend/src/cache/LRUPrefetchCache.ts` | 456 | `normalizeTime`, `isValidBar`, `calcRelativeStrength`, `parseBarsToChartData`, `LRUPrefetchCache` | `LRUPrefetchCache.test.ts` (13) |
| `frontend/src/chart/createMultiPaneChart.ts` | 94 | `MultiPaneChartBundle`, `createMultiPaneChart` (uses LWC v5 `addSeries` pane API) | — |
| `frontend/src/chart/CrosshairSyncManager.ts` | 189 | `normalizeToUtcSec`, `findFloorBar`, `findFloorBarTime`, `CrosshairSyncManager` | `CrosshairSyncManager.test.ts` (10) |
| `frontend/src/chart/DrawingSyncEngine.ts` | 294 | `StoredDrawing`, `DrawingSyncEngine` (REST `/api/drawings` persistence + LWC primitive attachment) | `DrawingSyncEngine.test.ts` (9) |
| `frontend/src/chart/positionSizer.ts` | 173 | `calculatePositionMetrics` | `positionSizer.test.ts` (11) |
| `frontend/src/chart/primitives/RiskRewardPrimitive.ts` | 418 | `RiskRewardPrimitive` (`ISeriesPrimitive<Time>`) | — |
| `frontend/src/ui/CommandPalette.ts` | 395 | `CommandPalette` (keyboard `Cmd+K` / `/api/symbols/search`) | `CommandPalette.test.ts` (5) |
| `frontend/src/styles/main.css` | 331 | Minimal shell layout styles for `frontend/index.html` | — |

---

## 2. Section-by-Section Mapping (`static/app.js` 18 Sections -> `frontend/src/`)

| # | `static/app.js` Section & Line Range | Core Responsibilities | `frontend/src/` Counterpart | Status |
|---:|---|---|---|---|
| 1 | Header & Engine State (`L1–L106`) | Globals, Scheme A/B/C state, `resolveBenchmarkSymbol` | `main.ts` (`getBenchmarkForSymbol`) | **Partial** (no Scheme B/C toggle or full state restore) |
| 2 | Dual-Pane X-Axis & Range Sync (`L107–L1674`) | `getSeriesTimeAxis`, `padToTimeAxis`, `bindLogicalRangeSync`, `resetChartView`, `computePreservedLogicalRange`, axis double-click & drag auto-scale | `CrosshairSyncManager.ts` + `createMultiPaneChart.ts` | **Divergent** (`frontend/` uses LWC v5 multi-pane + binary-search floor crosshair sync; lacks `padToTimeAxis` / drag-axis helpers) |
| 3 | Bar Time & Session Bucketing (`L1675–L2768`) | NY timezone US equity regular/extended buckets, crypto buckets, weekly alignment | `main.ts` (`alignBarTimestamp`, lines 30–38) | **Minimal Stub Only** (`main.ts` handles basic UTC modulo + Monday offset only; lacks `America/New_York` DST & session rules) |
| 4 | Safe Numerical Helpers (`L2769–L2823`) | `isValidPrice`, `safeNum`, `formatPrice`, `formatVolume`, `getSymbolItem` | — | **Not Migrated** |
| 5 | Common Precision & Position Math (`L2824–L3033`) | `computePositionSize`, `canonicalFormatPrice`, `computePriceFormatForPrice`, `applyPriceFormatToCandleSeries` | `positionSizer.ts` (`calculatePositionMetrics`) | **Divergent** (see Section 3) |
| 6 | Position Sizing & R:R Modal (`L3034–L3489`) | `computeRiskRewardView`, `computeAtrPositionSizing`, `computeManualRiskView`, `openRrEditModal`, `applyRrEdit` | `RiskRewardPrimitive.ts` | **Divergent** (`frontend/` renders primitive on series, lacks full edit modal & ATR daily-only calculator) |
| 7 | DOM Initializers & Ticker Tape (`L3490–L3751`) | `buildInitialDOM`, `buildTickerTape`, `updateQuote`, `updateLiveIndicatorsThrottled`, `updateActiveCandleWithPrice` | `main.ts` (`LiveFeed` quote/bar handler) | **Partial** (no ticker tape or throttled indicator overlay recompute) |
| 8 | Chart Setup & Indicators (`L3752–L4490`) | LWC v4 chart/series setup, EMA10/21, SMA50/200, BB, RS chart, Scheme C `TradingView.widget` | `createMultiPaneChart.ts` + `LRUPrefetchCache.ts` | **Partial** (RS line exists in `LRUPrefetchCache`, no SMA/EMA/BB/Scheme C widget) |
| 9 | Historical Bars & RS Alignment (`L4491–L5497`) | `/api/history` seed/backfill/recovery, benchmark fetch, abort/race guards, RS normalization | `main.ts` (`loadHistory`, `currentLoadGen`) + `LRUPrefetchCache.ts` | **Migrated Core** (`frontend/` improves with LRU cache + prefetching; lacks `/api/recovery` delta flow) |
| 10 | Real-Time Bar Processing (`L5498–L5940`) | Live tick/bar merging, sequence gap handling, OHLC invariant enforcement | `main.ts` + `LRUPrefetchCache.ts` (`isValidBar`) | **Partial** |
| 11 | Watchlist Sidebar Component (`L5941–L6364`) | Persistent watchlist groups, drag-and-drop `/api/symbols/reorder`, unsubscribe, pinned symbols | `main.ts` (`DEFAULT_WATCHLIST`, `flipWatchlistSymbol`) | **Mostly Not Migrated** |
| 12 | Omnibox Search & Subscriptions (`L6365–L6549`) | Symbol search modal, `/api/symbols/search`, `/api/symbols/subscribe` | `CommandPalette.ts` | **Partial** (`CommandPalette` queries `/api/symbols/search` via keyboard, lacks full subscribe UI integration) |
| 13 | WebSocket Lifecycle & Backoff (`L6550–L6796`) | `/ws/live` reconnect loop, desktop token attachment, status badge | `api/client.ts` (`LiveFeed`, `getBackendConfig`, `apiFetch`) | **Migrated** (`frontend/src/api/client.ts` cleanly supports Tauri desktop token & reconnection) |
| 14 | Event Listeners & Bootstrapping (`L6797–L7571`) | Preferences persistence (`/api/preferences`), AI Agent Lens modal, Gemini Chat drawer, shortcuts, i18n | `main.ts` (`initTerminal`, keyboard shortcuts) | **Mostly Not Migrated** (no AI Lens, Chat, Preferences UI, i18n) |
| 15 | Institutional Stock Screener (`L7572–L8657`) | Preset/custom criteria filters, `/api/screener/scan`, `/api/screener/ai-summary`, RS/CANSLIM table & exports | — | **Not Migrated** |
| 16 | Stock Theme Heatmap (`L8658–L9656`) | `/api/heatmap` treemap/tile layout, theme/universe switcher, advance/decline breadth | — | **Not Migrated** |
| 17 | Drawing Toolbar & Canvas (`L9657–L10735`) | Left vertical toolbar, trendlines, fib, text, R:R tool, `/api/drawings` sync | `DrawingSyncEngine.ts` + `RiskRewardPrimitive.ts` | **Partial** (persistence engine migrated, full drawing tool palette not migrated) |
| 18 | Anchored VWAP Settings Modal (`L10736–L11107`) | Multi-anchor AVWAP calculation, custom anchor picker, modal settings | — | **Not Migrated** |

---

## 3. Critical Divergences Between `static/` and `frontend/src/`

1. **Lightweight Charts API Generation (`4.2.0` vs `^5.3.0`)**
   - `static/` uses v4 API (`chart.addCandlestickSeries()`, two separate chart instances in `#chart` and `#rsChart`).
   - `frontend/src/chart/createMultiPaneChart.ts` uses v5 multi-pane API (`chart.addSeries(CandlestickSeries, ..., 0)` and pane `1` for RS). Migrating means committing fully to v5 primitives and rewriting remaining canvas overlays (`L9657–L10735`).
2. **Position Sizing Contracts (`computePositionSize` vs `calculatePositionMetrics`)**
   - `static/app.js` (`L2891`, `L3081`, `L3148`, `L3205`): dollar-risk-first model `{ entry, stop, riskDollar, side }` with decimal-scaled exact stop distance (`_s0ExactDistance`), floor-with-tolerance, plus daily-only ATR sizing (`computeAtrPositionSizing`) and manual share/R:R modal state.
   - `frontend/src/chart/positionSizer.ts` (`L27`): account-equity model `{ accountSize, riskPercent, maxAllocationPercent, entryPrice, stopPrice, targetPrice, adr20Percent }` enforcing max allocation caps and ADR multiple checks (`tight`/`ok`/`wide`). Any unification must merge both sets of invariants cleanly.
3. **Timeframe & Session Alignment (`NY_TZ` vs Naive Modulo)**
   - `static/app.js` Section 4 (`L1675–L2768`) models US equity market hours in `America/New_York` (matching `engine/market_time.py`), whereas `frontend/src/main.ts:30-38` (`alignBarTimestamp`) uses simple UTC epoch remainders that shift US intraday/daily bars across DST transitions.
4. **Test Suite Coupling (`tests/` in Python)**
   - Several backend tests evaluate extracted functions from `static/app.js` / `static/indicators.js` via `node` or inspect DOM IDs between `static/index.html` and `static/app.js`. Deleting or replacing `static/` requires updating or retiring those cross-language tests alongside Vitest coverage.

---

## 4. Migration Strategy Comparison

### Option 1: Complete `frontend/src/` Migration & Flip `frontendDist` -> `../frontend/dist`
- **Scope**: Port remaining ~70% of `static/app.js` features (Indicators, NY-TZ bar bucketing, Screener, Heatmap, AI Lens / Chat drawer, Watchlist groups/reorder, Drawing tools, AVWAP, unified Position Sizer) into `frontend/src/`, switch `src-tauri/tauri.conf.json` `frontendDist` from `"../static"` to `"../frontend/dist"`, and retire `static/` once parity is verified.
- **Pros**: Eliminates the 11,107-line global-scope monolith; enforces strict TypeScript contracts, tree-shaking, single LWC v5 codebase, and fast isolated Vitest suites.
- **Cons**: Significant engineering effort (~8–12 focused milestones); high temporary risk of subtle regressions in Canvas drawing tools, Screener tables, NY session bucketing, and AI Lens drawers unless every workflow gets E2E Playwright/Vitest parity tests.

### Option 2: Keep `static/` as Production UI & Gradually Modularize / Wrap with Bundler
- **Scope**: Treat `static/` as the single source of truth; either freeze `frontend/src/` as reference modules or incrementally bundle TypeScript/ES modules into `static/` (or migrate domain logic like `CrosshairSyncManager` / `LRUPrefetchCache` via thin build targets) while removing unused prototype dead ends when ready.
- **Pros**: Zero feature regressions on Screener, Heatmap, AI Lens, Drawing Toolbar, AVWAP, or Watchlist persistence; immediately shippable Beta desktop builds (`npm run build:sidecar && npm run tauri:build`).
- **Cons**: Dual codebase drift persists unless `frontend/src/` is either explicitly archived or incrementally wired into `static/`; keeps LWC 4.2.0 + global-variable discipline until modularized.

### Recommended Decision Path
For a **v1.1 Beta release**, ship using **`static/` (Option 2 short-term stability)** since all 588 backend/integration tests and sidecar desktop flows validate `static/`. For **v1.2+**, execute **Option 1 in vertical feature slices** behind explicit parity tests (`LRUPrefetchCache` -> `NY_TZ` session utils -> Watchlist/AI Lens -> Screener/Heatmap -> Drawing/AVWAP), flipping `frontendDist` only when feature parity reaches 100%.

### v1.2+ Backlog
- **前端繪圖/價位線/選股 preset 改由後端 `/api/drawings` 與 `/api/preferences` 持久化，含 `localStorage` 一次性匯入**：解決目前僅存在 WebKit `localStorage`（`tv_drawings_*`、`tv_price_lines_*`、`tv_risk_dollar`、`tv_custom_screener_presets`）的限制，確保重清快取或跨 origin 時使用者繪圖與風控/選股設定不遺失。

# Security & CSP Audit Notes (`v1.1-hardening`)

## 1. Local Vendoring & Supply-Chain Hardening
- `static/vendor/lightweight-charts.standalone.production.js` (v4.2.0) is vendored locally so the chart terminal works offline without CDN latency or unpkg supply-chain risk.
- `src-tauri/bin/server-sidecar/static/` is a generated build artifact copied from `static/` by `scripts/build_sidecar.py` (`shutil.copytree`) and is ignored in `.gitignore` (`src-tauri/bin/server-sidecar/`). Never edit the sidecar copy directly.

## 2. Content Security Policy (CSP) Convergence
- **Removed `https://unpkg.com`**: Eliminated from both `src-tauri/tauri.conf.json` (`app.security.csp`) and `server.py` (`DesktopAuthMiddleware`).
- **Removed `'unsafe-eval'`**: Verified across `static/` (`eval(`, `new Function(`, `setTimeout("`, `setInterval("` = `0` hits) and `https://s3.tradingview.com/tv.js` (`0` hits; external widget executes inside a cross-origin `<iframe>`). Removed `'unsafe-eval'` from `src-tauri/tauri.conf.json` (`server.py` already omitted it).
- **`'unsafe-inline'` Inventory (Not Removed in Phase 2 — Assessment Only)**:
  - `static/index.html` audit:
    - Inline `<script>` blocks (without `src=`): **0** (`4` external `<script src=...>` tags total)
    - Inline `on*=` attributes (`onclick=`, etc.): **0**
    - Inline `<style>` tags: **0**
    - Inline `style=` attributes: **56** (primarily `style="display: none;"`, modal sizing, and layout overrides)
  - `static/app.js` audit:
    - `.style.*` programmatic property updates: **186** (CSSOM style properties, unaffected by CSP `style-src`)
    - `.style.cssText` assignments: **2** (`6396`, `6409`) — subject to `style-src 'unsafe-inline'` in strict browsers when parsed as style attribute text
  - **Estimated Work to Remove `'unsafe-inline'`**:
    - `script-src 'unsafe-inline'`: **Small (S, ~0.5 day)** — verify whether external `tv.js` injects inline `<script>` on host document during Scheme C init; our own HTML/JS already uses zero inline `<script>` or `on*=` attributes.
    - `style-src 'unsafe-inline'`: **Medium (M, ~1–2 days)** — replace the 56 `style=` attributes in `static/index.html` with `.is-hidden` / utility classes in `static/style.css`, convert the 2 `cssText` assignments in `static/app.js` to classes/individual properties, and verify TradingView `tv.js` container/iframe wrapper injection does not rely on inline `<style>` tags.

## 3. `innerHTML` Audit (`static/app.js` — All 16 Occurrences)

All 16 `innerHTML` assignments in `static/app.js` are container resets (`= ""`), followed by explicit `document.createElement(...)` and `.textContent = ...` assignments. Zero occurrences interpolate external or dynamic strings into `innerHTML`, `outerHTML`, `insertAdjacentHTML`, or `document.write`.

| # | Line | Statement | Function / Context | External Data Rendered Safely Afterward |
|---|---|---|---|---|
| 1 | 78 | `if (rsContainer) rsContainer.innerHTML = "";` | `disposeSubPane()` | Static cleanup only |
| 2 | 93 | `container.innerHTML = "";` | `disposeSubPane()` | Static cleanup only |
| 3 | 3501 | `tickerTape.innerHTML = "";` | `buildTickerTape()` | `item.pair`, `price`, `changePercentage` via `.textContent` |
| 4 | 3759 | `chartContainer.innerHTML = "";` | `initChart()` | Static error fallback via `.textContent` |
| 5 | 4627 | `tvWidgetContainer.innerHTML = "";` | `applyEngineMode()` | Static cleanup only |
| 6 | 4659 | `tvWidgetContainer.innerHTML = "";` | `initTradingViewWidget()` | Cross-origin TradingView widget iframe |
| 7 | 5949 | `watchlistItemsEl.innerHTML = "";` | `renderWatchlist()` | `s.pair`, `s.name`, `s.sector`, group labels via `.textContent` |
| 8 | 6393 | `searchResultsList.innerHTML = "";` | `runSymbolSearch()` error branch | Static error string via `.textContent` |
| 9 | 6405 | `searchResultsList.innerHTML = "";` | `renderSearchResults()` | `it.type`, `it.pair`, `it.name`, `it.exchange` via `.textContent` |
| 10 | 8010 | `if (tableBody) tableBody.innerHTML = "";` | `runStockScreen()` empty input | Static cleanup only |
| 11 | 8095 | `if (tableBody) tableBody.innerHTML = "";` | `runStockScreen()` fetch catch | Error text goes to `statsBadge.textContent` |
| 12 | 8114 | `tableBody.innerHTML = "";` | `renderScreenerTable()` | `item.tier`, `frameworks`, `symbol`, `company_name`, `v6_risk` via `.textContent` |
| 13 | 8776 | `gridEl.innerHTML = "";` | `renderHeatmap()` | `theme.name_*`, `item.symbol`, `item.name` via `.textContent` / `setAttribute("title", ...)` |
| 14 | 9277 | `gridEl.innerHTML = "";` | Heatmap universe toggle click | Static loading state |
| 15 | 9423 | `screenerAcDropdown.innerHTML = "";` | Screener autocomplete search | `ticker`, `item.name`, `item.sector`, `item.exchange` via `.textContent` |
| 16 | 9458 | `screenerAcDropdown.innerHTML = "";` | Screener autocomplete catch | Static error label via `.textContent` |

XSS resilience verified by automated trap-DOM tests in `tests/test_xss_and_vendor_csp.py` using `<img src=x onerror=alert(1)>`.

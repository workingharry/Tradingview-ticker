# Vendored Frontend Dependencies (`static/vendor/`)

## `lightweight-charts.standalone.production.js`
- **Library**: TradingView Lightweight Charts
- **Version**: `4.2.0`
- **Source**: `https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js`
- **License**: Apache License 2.0 (see file header)
- **Why Vendored at v4.2.0**:
  - `static/app.js` uses Lightweight Charts v4 APIs (`addCandlestickSeries`, `addLineSeries`, `addHistogramSeries`).
  - Root `package.json` depends on `lightweight-charts@^5.3.0` for the experimental `frontend/` Vite + TypeScript client, so v4.2.0 must be vendored independently under `static/vendor/` rather than symlinked or upgraded from `node_modules/`.
  - Bundled into PyInstaller sidecars automatically by `scripts/build_sidecar.py` copying `static/`. Do not edit `src-tauri/bin/server-sidecar/static/` directly.

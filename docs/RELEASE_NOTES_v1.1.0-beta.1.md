# Momentum Swing Terminal v1.1.0-beta.1 Release Notes

**Release Date**: `2026-11-26`  
**Target Platform**: macOS Apple Silicon (`aarch64-apple-darwin`, macOS 11.0+)  
**Bundle Identifier**: `com.harryng.momentum-terminal`

---

## 本版重點 (Highlights)

1. **全新產品識別與動態打包流程**：正式更名為 **Momentum Swing Terminal**（`com.harryng.momentum-terminal`），統一 `package.json`、`Cargo.toml`、`tauri.conf.json` 版本號至 `1.1.0`，DMG 產物依版本動態命名。
2. **無痛舊版資料搬移**：首次啟動時自動從舊識別碼 `com.tradingview.terminal` 複製 SQLite 資料庫與 WebKit 瀏覽器儲存至新識別碼目錄，並**完整保留舊資料不動**。
3. **完全離線圖表引擎**：將 Lightweight Charts 4.2.0 改為本機內嵌（`static/vendor/`），移除外部 `unpkg.com` CDN 依賴，無網路環境也能啟動圖表。
4. **安全性與 CSP 收斂**：移除 CSP 中的 `'unsafe-eval'` 與 `https://unpkg.com`，保留所有既有功能的嚴格白名單連線與 Session Token 桌面端驗證。
5. **自動化 CI 與工程品質強化**：建立 GitHub Actions 雙平台 CI（macOS Pytest + Ubuntu Typecheck/Vitest），將大量原始碼字串測試升級為真實行為測試，並新增 Chrome (CDP) 與 Safari (WebKit) 實機繪圖 E2E 回歸測試（`e2e` marker，CI 預設略過）。
6. **後端模組化重構**：將原本超過 3,600 行的 `server.py` 拆分為獨立 `api/` 路由器與依賴模組（`bootstrap`、`config`、`deps`、`history`、`symbols`、`ai`、`screener`、`state`、`ws`），維持 100% API 相容。
7. **繪圖引擎與工具列體驗修正**：修正 R:R 框內畫線共存保護（非 `cursor` 模式點 `✕` 不會誤刪框）、切換工具即時清除懸停游標樣式、兩段式 `Esc`（先取消進行中線段、再退回 `cursor`）、R:R 框右邊界夾緊、右側留白區絕對時間畫線與跨週期插值渲染、`trendline` / `ruler` 按住拖曳畫線（≥ 6px 直接完成），以及工具列按鈕防折行與裁切修正。

---

## 安裝說明 (Installation)

1. 開啟 `Momentum Swing Terminal_1.1.0_aarch64.dmg`，將 **Momentum Swing Terminal.app** 拖曳至 `/Applications/`（可與舊版 `TradingView Terminal.app` 並存，不需刪除舊 App）。
2. 因本測試版採 **ad-hoc 本機簽章**（未經 Apple Developer Notarization），首次開啟若遭 macOS Gatekeeper 阻擋，請擇一操作：
   - **方法 A（GUI）**：在 Finder 的 `/Applications/` 對 **Momentum Swing Terminal.app** 按右鍵 → 點選「**打開 (Open)**」→ 再次確認「打開」。
   - **方法 B（終端機）**：移除隔離屬性後開啟：
     ```bash
     xattr -dr com.apple.quarantine "/Applications/Momentum Swing Terminal.app"
     open -a "Momentum Swing Terminal"
     ```

---

## 資料搬移說明 (Data Migration)

- **舊版資料來源**（遷移過程中完全保持唯讀與完整保留）：
  - `~/Library/Application Support/com.tradingview.terminal/`（`terminal.sqlite` 等後端資料庫）
  - `~/Library/WebKit/com.tradingview.terminal/`（內建 WebView `localStorage` 與瀏覽器狀態）
- **新版資料位置**：
  - `~/Library/Application Support/com.harryng.momentum-terminal/`
  - `~/Library/WebKit/com.harryng.momentum-terminal/`
- **遷移規則**：
  - App 主程式在建立視窗與啟動後端前，若偵測目標資料夾尚不存在，會自動遞迴複製舊版資料夾；若目標資料夾**已經存在**，會安全跳過而不覆蓋現有資料。

---

## Known Issues（已知問題與限制）

1. **WebKit `localStorage` 限定資料（預計 v1.2 改進）**：
   - 繪圖（`tv_drawings_*`）、價位線（`tv_price_lines_*`）、`$ Risk`（`tv_risk_dollar`）、自訂選股 preset（`tv_custom_screener_presets`）目前只存在 App 內建 WebView 的 `localStorage`（`tauri://localhost` origin），靠 `WebKit` 目錄複製搬移；尚未同步到 SQLite，預計 **v1.2** 處理。
   - **11/22 前以開發模式（`http://127.0.0.1:<port>`）產生的資料不會合併**（僅自動遷移 11/22 後正式 `.app` 使用 `tauri://localhost` 的 WebKit 儲存資料）。
2. **CSP `style-src 'unsafe-inline'`**：因現行圖表與嵌入式 TradingView 元件樣式需求，`style-src` 仍保留 `'unsafe-inline'`。
3. **非官方市場行情來源**：Yahoo Finance、Binance 公開介面與 TradingView WebSocket 均屬非官方或公開端點，可能因上游異動、限流或維護而隨時中斷。
4. **AI 功能金鑰設定**：AI 功能（`/api/chat`、`/api/ai-lens`、`/api/fundamentals`、`/api/screener/ai-summary`）需要 `GEMINI_API_KEY`；目前 `.app` 內未包含 `.env` 也無圖形設定介面。若未設定金鑰，系統不會閃退，會自動切換並標示為 **`Rule-Based (Offline)`** 離線規則分析；若需啟用 Gemini，可在終端機以 `launchctl setenv GEMINI_API_KEY "<你的金鑰>"` 或以環境變數啟動 App。
5. **未經 Apple Notarization**：本版使用 ad-hoc 簽章（`-`），**不適合直接公開散佈給其他一般使用者**。

---

## 退回舊版步驟 (Rollback Guide)

若測試過程中需退回 1.0.0 舊版，因舊資料全程完全未被異動，可依以下任一方式無縫回復：

1. **直接重啟 / 重裝舊版 App**：
   - 如 `/Applications/TradingView Terminal.app` 仍保留，直接關閉新版後開啟舊版即可；或從封存目錄 `~/Desktop/MST_backup_*/rollback/TradingView Terminal_1.0.0_aarch64.dmg` 重新掛載並拖回 `/Applications/`。
2. **從 R0 完整備份還原（若有需要）**：
   ```bash
   # 先確認舊版與新版 App 皆已完全退出
   BACKUP_DIR="$(ls -dt ~/Desktop/MST_backup_* | head -n 1)"
   ditto "$BACKUP_DIR/Application Support/com.tradingview.terminal" "$HOME/Library/Application Support/com.tradingview.terminal"
   ditto "$BACKUP_DIR/WebKit/com.tradingview.terminal" "$HOME/Library/WebKit/com.tradingview.terminal"
   ```

---

更多變更細節請參閱 [CHANGELOG.md](../CHANGELOG.md) 與 [RELEASE_AUDIT_v1.1.0.md](RELEASE_AUDIT_v1.1.0.md)。

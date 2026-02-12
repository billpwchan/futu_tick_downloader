# 文件風格與術語規範（zh-TW）

本文件定義 `hk-tick-collector` 文件的繁體中文寫作規範，用於確保術語一致、操作可執行、內容可維護。

## 目標

- 所有文件以繁體中文（zh-Hant / zh-TW）為主。
- 除「不得翻譯」項目外，敘述文字一律使用繁體中文。
- 命令、程式碼、路徑、設定鍵名與日誌樣例可直接複製執行，不因翻譯而失真。

## 術語對照表

| English | 繁體中文（固定用法） | 備註 |
|---|---|---|
| service | 服務 | `systemd` 服務情境優先使用「服務」 |
| daemon | 常駐程式 | |
| deployment | 部署 | |
| runbook | 操作手冊 | 可寫作「Runbook（操作手冊）」首次出現 |
| troubleshooting | 故障排除 | |
| ingestion | 資料匯入 | 若語境是寫入流程可用「資料寫入」 |
| queue | 佇列 | |
| persist / persistence | 落盤 | 全 repo 統一使用「落盤」 |
| checkpoint | 檢查點 | |
| backfill | 回補 | |
| watchdog | Watchdog（監控守護） | 首次可中英並列，後續用 `Watchdog` |
| drift | 時鐘漂移 | |
| snapshot | 快照 | |
| heartbeat | 心跳 | |
| idempotent | 冪等 | |
| failover | 容錯切換 | |

## 不翻譯清單（必須保留英文原樣）

以下內容不得翻譯、不得改大小寫、不得任意改寫：

- code block（例如 ```bash、```python、```sql）內的所有內容
- inline code（例如 `` `hk-tick-collector` ``）
- CLI 指令、CLI 輸出、日誌與 stack trace 原文
- API / 函式 / 類別 / 變數 / enum / config key 名稱
- 環境變數名稱（例如 `SQLITE_BUSY_TIMEOUT_MS`）
- 檔案路徑、檔名、副檔名（例如 `/etc/systemd/system/*.service`）
- 品牌與產品名（例如 FutuOpenD、SQLite、systemd、Telegram、GitHub Actions）
- `LICENSE` 授權全文

## 標題層級規範

- 每個文件僅一個 `H1`（`#`）。
- 主要章節使用 `H2`（`##`），子章節使用 `H3`（`###`）。
- 標題盡量以動作導向，例如「部署步驟」「如何驗證」「常見問題」。

## 操作章節模板

所有部署／維運／故障排除類章節建議使用固定骨架：

1. 目的
2. 前置條件
3. 步驟
4. 如何驗證
5. 常見問題

## 常見句型對照

- How to verify -> 如何驗證
- Prerequisites -> 前置條件
- Step-by-step -> 步驟
- Notes -> 注意事項
- Common issues -> 常見問題
- Rollback -> 回滾
- Health check -> 健康檢查
- Incident response -> 事件處置

## 日期、時間、時區寫法

- 時區代碼保留英文：`UTC`、`UTC+8`、`Asia/Hong_Kong`。
- 文件敘述以「絕對時間 + 時區」為優先，例如：`2026-02-12 09:30 UTC+8`。
- SQL/程式欄位名（例如 `ts_ms`、`recv_ts_ms`）保留原樣。

## 連結與錨點規範

- 盡量使用相對路徑連結（例如 `docs/runbook.md`）。
- 更新章節標題後，需同步檢查 TOC 與文件內部連結。
- 若需穩定錨點，優先使用顯式 HTML anchor id。

## 文風規範

- 使用台灣繁體術語：伺服器、資料庫、環境變數、佇列、監控、故障排除。
- 優先使用全形中文標點：，。；、。
- 每個步驟都應可直接執行，並附預期結果或驗證方式。
- 避免空泛敘述，盡量給出命令、路徑、輸出判讀方式。

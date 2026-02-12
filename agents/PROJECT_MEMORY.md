# 專案記憶（Agents）

## 2026-02-12：Telegram notifier 整合

### 範圍

- 新增 Telegram notifier 套件：
  - `hk_tick_collector/notifiers/telegram.py`
  - 非阻塞佇列 worker + 有界重試
  - rate limit + cooldown + 訊息長度保護
- 將 notifier 串接既有 health/watchdog/persist 訊號：
  - digest 來源 `futu_client._health_loop`
  - stall alerts 來源 `futu_client._check_watchdog`
  - sqlite busy alerts 來源 collector runtime counters
- 新增 DB 統計 helper：
  - `SQLiteTickStore.fetch_tick_stats`
- 更新執行期設定面（`Config` + `.env.example`）以支援 Telegram 與噪音調校。

### 可靠性保證

- `TELEGRAM_ENABLED=0` 時維持既有基線行為。
- 通知失敗不會阻塞匯入佇列或 SQLite 落盤。
- `429` 會依 `retry_after` 退避重試（有界）。
- alert keys 透過 cooldown 去重，避免重複刷屏。

### 影響到的文件與測試

- 新增文件：
  - `docs/deployment.md`
  - `docs/telegram.md`
  - `docs/runbook.md`
- 更新文件：
  - `README.md`、`docs/configuration.md`、`docs/getting-started.md`、`docs/deployment/systemd.md`
- 新增測試：
  - `tests/test_telegram_notifier.py`
  - `tests/test_futu_client.py` Watchdog 回歸擴充

## 2026-02-11：OSS + 發版就緒基線

### 範圍

- 公開 GitHub 文件結構對齊：
  - `README.md`
  - `docs/` 內 canonical docs + runbooks
- 新增社群治理文件與模板：
  - `LICENSE`、`CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、`SECURITY.md`、`SUPPORT.md`、`CODEOWNERS`、`MAINTAINERS.md`
  - `.github/ISSUE_TEMPLATE/*`、`.github/PULL_REQUEST_TEMPLATE.md`
- 新增封裝／工具／CI：
  - `pyproject.toml`（PEP 621 + console script）
  - `.pre-commit-config.yaml`
  - GitHub Actions CI / release workflows
  - `CHANGELOG.md`
- 新增維運範例：
  - `scripts/db_health_check.sh`、`scripts/query_examples.sql`、`scripts/export_csv.py`

### 執行期安全

- 預設執行期行為不變。
- 既有生產入口（`python -m hk_tick_collector.main`）刻意保持不變。
- 新增命令 `hk-tick-collector` 為加法。
- 時間戳語義文件化：
  - `ticks.ts_ms` 為 UTC epoch ms
  - `ticks.recv_ts_ms` 為 UTC epoch ms

## 2026-02-11：doc/runbook 基線定稿

### 交付內容

- 新增 canonical docs：
  - `docs/configuration.md`
  - `docs/deployment/systemd.md`
  - `docs/runbook/operations.md`
- 重整 `README.md`（quickstart + command cookbook）。
- 新增維運腳本：
  - `scripts/verify_db.sh`
  - `scripts/tail_logs.sh`
  - `scripts/healthcheck.sh`
  - `scripts/install_systemd.sh`
- 新增建議 unit 範本：
  - `deploy/systemd/hk-tick-collector.service`

### 最小程式修復

- Watchdog 現在會遵守 `WATCHDOG_QUEUE_THRESHOLD_ROWS`（先前未使用）。
- 可降低小 backlog／無 backlog 場景誤報，不改資料格式。

### 新增測試

- `tests/test_config.py`
- `tests/test_smoke_pipeline.py`
- `tests/test_mapping.py::test_parse_time_to_ts_ms_is_independent_from_system_tz`
- `tests/test_schema.py::test_connect_applies_sqlite_pragmas`
- `tests/test_futu_client.py` Watchdog 回歸測試

## 2026-02-11：hk-tick-collector 持續停滯 + future `ts_ms`

### 故障特徵

- （歷史階段）`WATCHDOG persistent_stall` 反覆觸發並由 systemd 重啟（目前為 `exit code 1`）。
- SQLite `MAX(ts_ms)` 比 UTC 現在時間超前約 `+8h`（`~28800s`）。
- queue backlog 持續，且 `persisted_rows_per_min=0`。

### 可靠檢查

```bash
bash scripts/verify_hk_tick_collector.sh
journalctl -u hk-tick-collector --since \"30 minutes ago\" --no-pager | grep -E \"WATCHDOG|persist_loop_heartbeat|health|persist_ticks\"
```

### 永久修復摘要

- 在 mapping 強制 HK-local -> UTC 轉換。
- 從最近 DB 檔以 `max(seq)` 做 seed（不依賴 `ts<=now`）。
- persist loop：永遠輸出 traceback，並做 connection reset + retry。
- watchdog：以 heartbeat（`last_dequeue_monotonic`、`last_commit_monotonic`）判定，先自癒，多次失敗才退出。
- 歷史 +8h 資料修復腳本：
  - `python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day <YYYYMMDD>`
- 手動回滾腳本：
  - `ROLLBACK_REF=<commit> bash scripts/rollback_hk_tick_collector.sh`

### 常見錯誤

- 把市場本地時間直接當 UTC epoch 來源。
- 以為 `PRAGMA busy_timeout` 是 DB 持久化設定（實際為連線層級）。
- 未嘗試 writer recovery 就立刻重啟服務。

# 專案記憶（Project Memory）

## 2026-02-12：Telegram 通知 v2（human-friendly + 狀態機 + 升級）

### 變更內容

- 通知模組重構為可維運結構：
  - `TelegramClient`
  - `MessageRenderer`（HTML + `<blockquote expandable>`）
  - `AlertStateMachine`（`OK/WARN/ALERT`）
  - `DedupeStore`（fingerprint 去重 + cooldown + escalation）
- HEALTH/ALERT 訊息改為兩層：
  - 第一層 6-10 行：結論、影響、是否需要處理、最小關鍵數字
  - 第二層可展開：技術細節、建議命令（2-3 條）
- 事件通知補強：
  - `DISCONNECT`（重連期）
  - `RESTART`（致命退出後重啟風險）
  - `PERSIST_STALL` / `SQLITE_BUSY` 分級文案
- 噪音控制：
  - HEALTH：狀態變化 + 交易/非交易不同 cadence
  - ALERT：fingerprint 去重，僅 cooldown / escalation / severity 升級時補發

### 配置與相容性

- 新配置主軸：
  - `TG_*`
  - `HEALTH_*`
  - `ALERT_*`
- 舊 `TELEGRAM_*` 仍可用（向後相容），既有部署不會被破壞。

### 測試與文件

- 測試：
  - `tests/test_telegram_notifier.py`（HTML expandable、截斷、狀態機、去重/升級、429）
  - `tests/test_config.py`（`TG_*` alias + backward compatibility）
- 文件：
  - `docs/telegram-notify.md`（新）
  - `docs/deployment.md`（env 變更需 restart）
  - `docs/runbook.md`（狀態定義 + SOP）
  - `README.md` Notifications 章節更新

## 2026-02-12：Telegram 群組通知（低噪音摘要 + 關鍵告警）

### 變更內容

- 新增 notifier 模組：
  - `hk_tick_collector/notifiers/telegram.py`
  - 非同步佇列 + 單一 sender worker
  - 本地共享 rate limiter（`TELEGRAM_RATE_LIMIT_PER_MIN`，預設 `18`）
  - Telegram `429 retry_after` 重試路徑（有界重試）
  - alert-key cooldown 去重（`TELEGRAM_ALERT_COOLDOWN_SEC`）
  - 訊息長度保護（`<=4096`，超出加 `...(truncated)`）
- 新增健康／告警快照物件：
  - `HealthSnapshot`、`SymbolSnapshot`、`AlertEvent`
- 沿用既有事件來源，未引入重型 event bus：
  - `futu_client._health_loop` -> digest snapshots
  - `futu_client._check_watchdog` -> `PERSIST_STALL` alerts
  - `collector` runtime busy/locked counters -> `SQLITE_BUSY` alerts
- 主流程生命週期整合：
  - `main.py` 以安全方式啟停 notifier
  - notifier 失敗不影響匯入／落盤主路徑
- 新增 DB helper：
  - `SQLiteTickStore.fetch_tick_stats(trading_day)`，供摘要顯示 `rows/max_ts`
- Watchdog 停滯判定收斂為「真停滯」：
  - 需要 backlog 或 enqueued 訊號
  - 需要 persist 安靜 + commit age 超過門檻
  - 避免 duplicate-only 假告警

### 新增設定

- Telegram 必要控制項：
  - `TELEGRAM_ENABLED`
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`
  - `TELEGRAM_THREAD_ID`
  - `TELEGRAM_DIGEST_INTERVAL_SEC`
  - `TELEGRAM_ALERT_COOLDOWN_SEC`
  - `TELEGRAM_RATE_LIMIT_PER_MIN`
  - `TELEGRAM_INCLUDE_SYSTEM_METRICS`
  - `INSTANCE_ID`
- 額外噪音調校：
  - `TELEGRAM_DIGEST_QUEUE_CHANGE_PCT`
  - `TELEGRAM_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC`
  - `TELEGRAM_DIGEST_DRIFT_THRESHOLD_SEC`
  - `TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE`
  - `TELEGRAM_SQLITE_BUSY_ALERT_THRESHOLD`

### 新增測試

- `tests/test_telegram_notifier.py`
  - formatter 行長與截斷
  - 滑動視窗 rate limiter 上限
  - alert cooldown 去重
  - `429 retry_after` 處理
- `tests/test_futu_client.py`
  - queue 未達門檻但 enqueued-window 為正時，Watchdog 仍可觸發

### 更新文件

- `README.md`（Telegram 功能 + 範例）
- `docs/deployment.md`
- `docs/telegram.md`
- `docs/runbook.md`
- `docs/configuration.md`
- `.env.example`
- `deploy/systemd/hk-tick-collector.service`

## 2026-02-11：OSS 發版強化（docs/community/packaging/CI）

### 變更內容

- 針對開源上手重整 repo 入口：
  - `README.md`
  - `docs/getting-started.md`
  - `docs/troubleshooting.md`
  - `docs/faq.md`
- 新增標準化文件與 runbook：
  - `docs/deployment/systemd.md`
  - `docs/deployment/docker.md`
  - `docs/runbook/operations.md`
  - `docs/runbook/incident-watchdog-stall.md`
  - `docs/runbook/sqlite-wal.md`
  - `docs/runbook/data-quality.md`
  - `docs/releasing.md`
- 新增操作範例與維運腳本：
  - `scripts/db_health_check.sh`
  - `scripts/query_examples.sql`
  - `scripts/export_csv.py`
- 新增社群治理文件：
  - `LICENSE`、`CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、`SECURITY.md`、`SUPPORT.md`、`CODEOWNERS`、`MAINTAINERS.md`
  - `.github/ISSUE_TEMPLATE/*`、`.github/PULL_REQUEST_TEMPLATE.md`
- 新增封裝／工具／CI：
  - `pyproject.toml`（PEP 621 metadata + `hk-tick-collector`）
  - `.pre-commit-config.yaml`
  - `.github/workflows/ci.yml`
  - `.github/workflows/release.yml`
  - `CHANGELOG.md`
- 新增入口與測試：
  - `hk_tick_collector/__main__.py`
  - `tests/test_entrypoint.py`
  - `tests/test_futu_client.py` fake-time regression

### 相容性聲明

- 預設執行期行為維持不變：
  - `systemd` `ExecStart=/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.main` 持續有效。
  - 新增 `hk-tick-collector` 只為加法別名。
- 時間戳語義維持不變且已明確文件化：
  - `ts_ms`、`recv_ts_ms` 皆為 UTC epoch 毫秒。
  - 港股本地時間仍按 `Asia/Hong_Kong` 解讀後轉 UTC。

## 2026-02-11：runbook 標準化 + Watchdog 門檻修正

### 變更內容

- 文件依關注點拆分：
  - `README.md`
  - `docs/architecture.md`
  - `docs/configuration.md`
  - `docs/deployment/systemd.md`
  - `docs/runbook/operations.md`
- 新增維運腳本：
  - `scripts/verify_db.sh`
  - `scripts/tail_logs.sh`
  - `scripts/healthcheck.sh`
  - `scripts/install_systemd.sh`
- 新增建議 unit 範本：
  - `deploy/systemd/hk-tick-collector.service`

### 根因與最小修復

- 根因：`WATCHDOG_QUEUE_THRESHOLD_ROWS` 已存在設定，但舊邏輯未使用。
- 修復：Watchdog 需滿足 `queue_size >= WATCHDOG_QUEUE_THRESHOLD_ROWS` 才進入停滯／恢復流程。
- 效果：降低小 backlog 或無 backlog 場景的誤報。

### 回歸覆蓋

- `tests/test_futu_client.py::test_watchdog_honors_queue_threshold`
- `tests/test_futu_client.py::test_watchdog_ignores_duplicate_only_window_without_backlog`
- `tests/test_config.py`
- `tests/test_schema.py::test_connect_applies_sqlite_pragmas`
- `tests/test_smoke_pipeline.py`

## 2026-02-11：Watchdog/時間戳一次性強化

### 變更內容

- 時間戳語義：
  - `ticks.ts_ms` 強制為 UTC epoch 毫秒（事件時間）
  - 新增 `ticks.recv_ts_ms`（接收時間）
  - `mapping.parse_time_to_ts_ms` 將數字 epoch 與 HK 本地文字時間統一轉換（`ZoneInfo("Asia/Hong_Kong")`）
- 落盤穩定性：
  - writer 連線 PRAGMA 增加 `temp_store=MEMORY`
  - SQLite 寫入錯誤以 `logger.exception` 記錄完整 traceback，並做 backoff + 連線重設
  - 即使 batch 全為 duplicate，`last_commit_monotonic` 仍會更新
- Watchdog：
  - 停滯條件收斂為 `queue>0 && (now_monotonic-last_commit_monotonic)>=WATCHDOG_STALL_SEC`
  - 觸發時先 dump stack，再做程序內 writer recovery
  - 連續失敗才非零退出（改為 `1`）
- Poll 降噪：
  - 只有 push/tick 停滯才啟動 poll（`FUTU_POLL_STALE_SEC`）
  - poll 去重基線改用 `last_persisted_seq`
- 維運：
  - 新增 `scripts/check_ts_semantics.py`

## 2026-02-10：HK tick pipeline 隱性停滯修復

### 事件摘要

- 症狀：`poll_stats fetched=100 enqueued=0` 重複出現，`persist_ticks` 長時間消失。
- 影響：SQLite 檔案停止成長，最大缺口約 3.27 小時。
- 臨時復原：重啟服務並清理陳舊 WAL/SHM side files。

### 根因

- 單一 `last_seq` 混合多種語義。
- poll 去重使用記憶體進度，可能超前於 DB 持久進度。
- 在 queue backpressure／flush stall 下，上游仍活躍但落盤可靜默失效。

### 永久修復

- 序列狀態拆分：
  - `last_seen_seq`：僅觀測到的上游最大 seq
  - `last_accepted_seq`：成功入佇列的最大 seq
  - `last_persisted_seq`：成功 commit 的最大 seq
- poll 去重基線改為 `max(last_accepted_seq, last_persisted_seq)`。
- enqueue 失敗不再推進 accepted/persisted seq。
- 新增 Watchdog：若上游活躍但落盤停滯超過門檻，記錄 `WATCHDOG` 並退出（`exit code 1`，由 systemd 自動重啟）。

### 可觀測性升級

- `poll_stats` 新增 queue utilization、accepted/enqueued、drop reasons 與三種 seq 狀態。
- `persist_ticks` 新增 commit latency 與 ignored 數。
- `health` 新增每分鐘 push/poll/persist/drop rollup。

### 新增測試

- enqueue 失敗不會推進 accepted/persisted seq
- push 更新 seen seq 不污染 poll 去重基線
- 上游活躍且 persist 停滯時 Watchdog 退出

## 2026-02-10：時鐘漂移與 persist-loop 強化

### 事件摘要

- `ts_ms` 曾出現與 UTC epoch 偏離約 8 小時，導致 `strftime('%s','now')` 視窗查詢失真。
- 線上出現 `WATCHDOG persistent_stall`，呈現上游活躍但 `persisted_rows_per_min=0` 且 queue 增長。

### 根因

- naive datetime 解析依賴系統時區，未顯式以 `Asia/Hong_Kong` 解讀。
- persist loop 對落盤異常僅記錄後繼續，缺少重試上限與 fatal 訊號。

### 永久修復

- `mapping.parse_time_to_ts_ms` 統一 `Asia/Hong_Kong -> UTC epoch ms`。
- `trading_day_from_ts` 改為 UTC->HK 反推，移除系統時區依賴。
- persist 增加重試與 fatal：
  - `persist_flush_failed` 日誌包含 queue/db path/last seq
  - 超過重試上限觸發 `persist_loop_exited`，主流程非零退出
- health/poll 增加 `queue_in/queue_out`、`last_commit_monotonic_age_sec`、`db_write_rate`、`ts_drift_sec`
- watchdog 停滯判定只依賴 monotonic commit 時間

### Ops 資產

- `scripts/redeploy_hk_tick_collector.sh`
- `docs/runbook/operations.md`

## 2026-02-11：Watchdog 先自癒 + future-ts 修復工具

### 事件型態

- （歷史）曾出現 `WATCHDOG persistent_stall` + `status=2/INVALIDARGUMENT` 循環重啟。
- SQL 核驗發現 `MAX(ts_ms)` 比 `now_utc` 超前約 +8h。
- `lsof` 未穩定重現 DB 鎖，顯示僅以鎖衝突解釋不足。

### 最終修復

- 時間戳：
  - `mapping.parse_time_to_ts_ms` 強制 `Asia/Hong_Kong -> UTC epoch ms`
  - 新增 compact 時間解析（`HHMMSS` / `YYYYMMDDHHMMSS`）
  - 對明顯 +8h future 值自動糾偏並告警
- seed：
  - `main` 改為掃最近交易日 DB 的 `max(seq)`，不依賴 `ts<=now`
- persist：
  - 新增 writer 自癒介面，可重建 worker/writer
  - 所有落盤異常都記錄 traceback 並重設 sqlite connection
  - heartbeat 預設 30s，新增 `wal_bytes`、`last_commit_rows`、`recovery_count`
- watchdog：
  - 基於 `last_dequeue_monotonic` / `last_commit_monotonic` + 持續 backlog 判定
  - 觸發先 dump 全 thread stack，再做 writer 自癒
  - 連續自癒失敗才非零退出交由 systemd 接手

### 新增維運腳本

- `scripts/repair_future_ts_ms.py`
  - 只修 `ts_ms > now + 2h`，預設 `-8h`，並同步更新 `trading_day`
- `scripts/verify_hk_tick_collector.sh`
  - 輸出 `now_utc/max_ts_utc/max_minus_now_sec/rows` + recent watchdog + pragma
- `scripts/redeploy_hk_tick_collector.sh`
  - stop -> deploy -> test -> repair -> start -> log acceptance -> verify ->（失敗自動回滾）
- `scripts/rollback_hk_tick_collector.sh`
  - 手動指定 `ROLLBACK_REF` 一鍵回滾並拉起服務

### 驗證命令

- `bash scripts/verify_hk_tick_collector.sh`
- `python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day $(TZ=Asia/Hong_Kong date +%Y%m%d)`
- `journalctl -u hk-tick-collector --since \"30 minutes ago\" --no-pager | grep -E \"WATCHDOG|persist_loop_heartbeat|health|persist_ticks\"`

### 常見誤區

- 把 HK 本地時間直接當 UTC 儲存，造成 `ts_ms` 偏移 +28800 秒。
- 只看 `persisted_rows_per_min` 判停寫，忽略 dequeue/commit heartbeat。
- 新連線直接查 `PRAGMA busy_timeout` 讀到 0，就誤判配置失效（該值是連線層級）。

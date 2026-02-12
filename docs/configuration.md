# 設定參考

## 目的

本文件說明 `hk-tick-collector` 的環境變數設定方式、預設值與調整時機。

## 前置條件

- 了解部署模式：本機 `.env` 或 `systemd` `EnvironmentFile=`。
- 確認可編輯目標環境檔案並具備重啟服務權限。

## 設定來源

設定以環境變數為主，來源二選一：

- repo 根目錄 `.env`（本機／開發）
- `systemd` unit 內 `EnvironmentFile=`（生產）

解析實作：`hk_tick_collector/config.py`

## 解析規則

- 整數／浮點變數：格式不合法會在啟動時拋出 `ValueError`。
- 布林變數（例如 `FUTU_POLL_ENABLED`）：接受 `1/0,true/false,yes/no,on/off`。
- CSV 變數（例如 `FUTU_SYMBOLS`）：以逗號分割並去除前後空白。

## 環境變數

| 變數 | 預設值 | 說明 | 建議範圍 | 影響 | 何時調整 |
|---|---|---|---|---|---|
| `FUTU_HOST` | `127.0.0.1` | OpenD 主機位址 | 本機或私網 IP | 設錯會導致連線失敗 | OpenD 非同機時調整 |
| `FUTU_PORT` | `11111` | OpenD API 連接埠 | 合法 TCP port | 設錯會導致 subscribe/poll 失敗 | 需與 OpenD `api_port` 一致 |
| `FUTU_SYMBOLS` | 空 | 要訂閱的 symbol 清單（逗號分隔） | 明確清單 | 空值會啟動失敗 | 依實際港股池設定 |
| `DATA_ROOT` | `/data/sqlite/HK` | 每日 SQLite 檔案根目錄 | 獨立磁碟或路徑 | 權限不足或磁碟滿會寫入失敗 | 儲存規劃改變時 |
| `BATCH_SIZE` | `500` | 每批次 DB flush 行數 | `300-1000` | 太小增加寫入開銷；太大增加 flush 延遲 | 依吞吐量與延遲目標調整 |
| `MAX_WAIT_MS` | `1000` | flush 前最長等待毫秒 | `500-1500` | 值越大端到端延遲越高 | 有延遲目標時調整 |
| `MAX_QUEUE_SIZE` | `20000` | 記憶體佇列上限 | `20k-100k` | 太小可能丟 enqueue；太大耗記憶體 | 依流量尖峰調整 |
| `BACKFILL_N` | `0` | 重新連線後回補筆數 | `0-500` | 值越大啟動/重連負載越高 | 需要回補視窗才調整 |
| `RECONNECT_MIN_DELAY` | `1` | 重連最短延遲（秒） | `1-3` | 太低可能造成連線抖動 | 網路不穩時微調 |
| `RECONNECT_MAX_DELAY` | `60` | 重連最長延遲（秒） | `30-60` | 太高會拉長恢復時間 | 調整重連曲線時 |
| `CHECK_INTERVAL_SEC` | `5` | OpenD 連線健康檢查間隔 | `3-10` | 太低會增加噪音 | 調整監控頻率時 |
| `FUTU_POLL_ENABLED` | `true` | 是否開啟 poll 備援 | `true/false` | 關閉後 push 停滯時無備援 | 生產建議保持啟用 |
| `FUTU_POLL_INTERVAL_SEC` | `3` | poll 迴圈間隔（秒） | `2-5` | 太低增加負載與重複 | 依 OpenD 承載調整 |
| `FUTU_POLL_NUM` | `100` | 每次 poll 抓取筆數 | `50-200` | 太高會增加請求與解析成本 | 依 symbol 活躍度調整 |
| `FUTU_POLL_STALE_SEC` | `10` | push 停滯多久才啟動 poll | `8-15` | 太低會造成不必要 poll | 推送穩定度改變時 |
| `WATCHDOG_STALL_SEC` | `180` | commit 停滯門檻（秒） | `120-300` | 太低易誤報；太高恢復變慢 | 依可接受故障切換時間 |
| `WATCHDOG_UPSTREAM_WINDOW_SEC` | `60` | 上游活動回看視窗 | `30-120` | 太低可能誤判上游不活躍 | 依市場尖峰特性調整 |
| `WATCHDOG_QUEUE_THRESHOLD_ROWS` | `100` | 啟用 Watchdog 停滯判定的最小 backlog | `100-1000` | 太低誤報；太高偵測延遲 | 依佇列規模調整 |
| `WATCHDOG_RECOVERY_MAX_FAILURES` | `3` | 恢復失敗上限，超過則退出 | `3-5` | 太低會造成頻繁重啟 | 依重啟策略調整 |
| `WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC` | `3.0` | recovery 時等待舊 writer thread 秒數 | `2-5` | 太短可能導致恢復失敗 | thread 收斂較慢時增加 |
| `DRIFT_WARN_SEC` | `120` | 時鐘漂移告警門檻（秒） | `60-180` | 太低會造成告警噪音 | 依監控敏感度調整 |
| `STOP_FLUSH_TIMEOUT_SEC` | `60` | 優雅關閉 flush 逾時（秒） | `60-180` | 太低可能停止前未排空 | 大佇列場景建議增加 |
| `SEED_RECENT_DB_DAYS` | `3` | 啟動時掃描 seed 的最近 DB 天數 | `3-5` | 值越高啟動越慢 | 長停機後常需回補時 |
| `PERSIST_RETRY_MAX_ATTEMPTS` | `0` | 每批次落盤重試上限（`0` 代表直到成功） | `0` 或小正整數 | 小值可能放大暫時性失敗 | 生產建議維持 `0` |
| `PERSIST_RETRY_BACKOFF_SEC` | `1.0` | 落盤重試初始 backoff（秒） | `0.1-1.0` | 太低可能持續打 SQLite | 有鎖競爭時調整 |
| `PERSIST_RETRY_BACKOFF_MAX_SEC` | `2.0` | 落盤重試最大 backoff（秒） | `1-5` | 太高會降低恢復吞吐 | 依重試節奏調整 |
| `PERSIST_HEARTBEAT_INTERVAL_SEC` | `30.0` | 落盤心跳日誌間隔 | `10-30` | 太小會增加日誌量 | 依可觀測性需求調整 |
| `SQLITE_BUSY_TIMEOUT_MS` | `5000` | 每連線 SQLite busy timeout | `3000-10000` | 太低易出現 `locked` 失敗 | 鎖競爭高時增加 |
| `SQLITE_JOURNAL_MODE` | `WAL` | SQLite journal 模式 | 建議 `WAL` | 非 WAL 會影響讀寫併發 | 特殊限制下才改 |
| `SQLITE_SYNCHRONOUS` | `NORMAL` | SQLite fsync 安全模式 | `NORMAL`/`FULL` | `OFF` 會提高崩潰毀損風險 | 高耐久需求用 `FULL` |
| `SQLITE_WAL_AUTOCHECKPOINT` | `1000` | WAL 自動 checkpoint 頁數 | `500-2000` | 太高會導致 WAL 膨脹 | 依寫入量與磁碟 IO 調整 |
| `TG_ENABLED` | `false` | 是否啟用 Telegram notifier worker | `0/1` | 關閉時不改變主流程行為 | Bot/chat 驗證完成後再開 |
| `TG_BOT_TOKEN` | 空 | Telegram bot token | secret | 缺失或錯誤會停用 notifier | 存於私密 env／secret manager |
| `TG_CHAT_ID` | 空 | 目標群組/頻道 chat id | 群組 id（常為負數） | 設錯會觸發 `400/403` | 需填真實目標 |
| `TG_MESSAGE_THREAD_ID` | 空 | 可選群組 topic id | 正整數 | 設錯會觸發 `400` | 使用 forum topics 時填寫 |
| `TG_PARSE_MODE` | `HTML` | Telegram parse mode | `HTML` | 非 HTML 會失去 expandable 區塊 | 建議保持 `HTML` |
| `HEALTH_INTERVAL_SEC` | `600` | HEALTH 固定節奏（共用基線） | `300-1800` | 太小會增加噪音 | 依值班偏好調整 |
| `HEALTH_TRADING_INTERVAL_SEC` | `600` | 交易時段 HEALTH 週期 | `300-1200` | 太小會增加噪音 | 盤中節奏調整 |
| `HEALTH_OFFHOURS_INTERVAL_SEC` | `1800` | 非交易時段 HEALTH 週期 | `900-3600` | 太小會造成夜間噪音 | 夜間建議拉長 |
| `ALERT_COOLDOWN_SEC` | `600` | 同 fingerprint 冷卻時間窗 | `300-1800` | 太小會重複刷告警 | 依事件頻率調整 |
| `ALERT_ESCALATION_STEPS` | `0,600,1800` | 告警升級補發時間點（秒） | 逗號整數 | 設太密會增加噪音 | 依值班策略調整 |
| `TG_RATE_LIMIT_PER_MIN` | `18` | 本地 sender 每分鐘上限 | `1-20` | 太高可能觸發 Telegram 限流 | 建議低於軟上限 |
| `TG_INCLUDE_SYSTEM_METRICS` | `true` | 摘要是否附 `load1/rss/disk` | `0/1` | 關閉可縮短訊息 | 需精簡訊息時關閉 |
| `TG_DIGEST_QUEUE_CHANGE_PCT` | `20` | 判定「有意義變化」的 queue 變化門檻 | `5-50` | 太低會增加摘要頻率 | 依噪音容忍度調整 |
| `TG_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC` | `60` | `last_tick_age` 變化門檻 | `30-300` | 太低會增加摘要 churn | 依 symbol 活躍度調整 |
| `TG_DIGEST_DRIFT_THRESHOLD_SEC` | `60` | `|drift_sec|` 變化門檻 | `30-300` | 太低會增加摘要 churn | 依 drift 策略調整 |
| `TG_DIGEST_SEND_ALIVE_WHEN_IDLE` | `false` | 無顯著變化時是否送精簡 alive | `0/1` | 開啟會增加背景訊息 | 低噪音模式建議關閉 |
| `TG_SQLITE_BUSY_ALERT_THRESHOLD` | `3` | 每分鐘 busy/locked 告警門檻 | `1-20` | 太低可能對暫時鎖也告警 | 依儲存競爭程度調整 |
| `INSTANCE_ID` | 空 | 訊息中的可讀 instance 標籤 | 短字串 | 空值時退回 hostname | 多節點建議填寫 |
| `LOG_LEVEL` | `INFO` | 應用程式日誌層級 | 生產建議 `INFO` | `DEBUG` 可能非常吵 | 僅短期除錯使用 |

相容性說明：

- 舊版 `TELEGRAM_*` 仍可使用（向後相容）。
- 新增部署建議改用 `TG_*` + `HEALTH_*` + `ALERT_*`。

## 生產基線範本

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700,HK.00981,HK.01810
DATA_ROOT=/data/sqlite/HK

BATCH_SIZE=500
MAX_WAIT_MS=1000
MAX_QUEUE_SIZE=50000

FUTU_POLL_ENABLED=1
FUTU_POLL_INTERVAL_SEC=3
FUTU_POLL_NUM=100
FUTU_POLL_STALE_SEC=10

WATCHDOG_STALL_SEC=180
WATCHDOG_UPSTREAM_WINDOW_SEC=60
WATCHDOG_QUEUE_THRESHOLD_ROWS=100
WATCHDOG_RECOVERY_MAX_FAILURES=3
WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC=3

PERSIST_RETRY_MAX_ATTEMPTS=0
PERSIST_RETRY_BACKOFF_SEC=0.5
PERSIST_RETRY_BACKOFF_MAX_SEC=2
PERSIST_HEARTBEAT_INTERVAL_SEC=30
STOP_FLUSH_TIMEOUT_SEC=120

SQLITE_BUSY_TIMEOUT_MS=5000
SQLITE_JOURNAL_MODE=WAL
SQLITE_SYNCHRONOUS=NORMAL
SQLITE_WAL_AUTOCHECKPOINT=1000

TG_ENABLED=1
TG_BOT_TOKEN=<secret>
TG_CHAT_ID=-1001234567890
TG_MESSAGE_THREAD_ID=
TG_PARSE_MODE=HTML
HEALTH_INTERVAL_SEC=600
HEALTH_TRADING_INTERVAL_SEC=600
HEALTH_OFFHOURS_INTERVAL_SEC=1800
ALERT_COOLDOWN_SEC=600
ALERT_ESCALATION_STEPS=0,600,1800
TG_RATE_LIMIT_PER_MIN=18
TG_INCLUDE_SYSTEM_METRICS=1
TG_DIGEST_QUEUE_CHANGE_PCT=20
TG_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC=60
TG_DIGEST_DRIFT_THRESHOLD_SEC=60
TG_DIGEST_SEND_ALIVE_WHEN_IDLE=0
TG_SQLITE_BUSY_ALERT_THRESHOLD=3
INSTANCE_ID=hk-a1

DRIFT_WARN_SEC=120
SEED_RECENT_DB_DAYS=3
LOG_LEVEL=INFO
```

## 變更控制

生產環境調整 env 後：

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
bash scripts/db_health_check.sh
```

## 如何驗證

- 服務重啟後可正常啟動，且 `journalctl` 無解析錯誤。
- `health` 與 `persist_ticks` 日誌持續輸出。
- DB `MAX(ts_ms)` 持續前進。

## 常見問題

- 啟動即 `ValueError`：通常是數值型 env 格式不合法。
- 開啟 Telegram 後沒訊息：先確認 `TG_CHAT_ID`、權限與 `429/403/400` 錯誤日誌。

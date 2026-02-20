# Telegram Actions Runbook

## 按鈕對應表

- `🔎 詳情`
  - 用途：展開/收合同一則訊息的詳細指標
  - 正常：可看到「已展開 / 已收合」切換
- `🧾 近20分鐘日誌`
  - 用途：看 ERROR/WARN/WATCHDOG/persist/sqlite_busy 摘要
  - 正常：僅回覆精簡行，不應刷出大量原始日誌
- `🗃 DB 狀態`
  - 用途：確認 rows、max_ts、drift、db path
  - 正常：rows 成長、drift 不持續擴大
- `🧯 建議/處置`
  - 用途：對應事件類型 SOP（短版）
- `🔕 靜音 1h`
  - 用途：暫停 HEALTH/WARN 心跳 1 小時
  - 正常：ALERT 仍會送
- `🔄 刷新`
  - 用途：立即重算最新 health 並更新訊息
  - 正常：受最小間隔限制，太頻繁會被拒絕

## 事件處置

## PERSIST_STALL

1. 按 `🧾 近20分鐘日誌`
2. 看是否持續出現 `WATCHDOG persistent_stall` / `write=0/min`
3. 按 `🗃 DB 狀態`，確認 `max_ts` 是否停止前進
4. 按 `🧯 建議/處置` 取得 SOP

判定正常：

- `RECOVERED` 出現
- queue 回落
- persist/min 恢復

## SQLITE_BUSY

1. 先看 `🧾` 是否連續出現 busy/backoff
2. 再看 `🗃` 的 drift 與 rows 成長是否惡化
3. 若 >10 分鐘不改善，依 `🧯` 排查並行寫入或 I/O

判定正常：

- busy 訊息下降
- rows 持續成長
- drift 回落

## DISCONNECT

1. 先按 `🧾` 確認中斷是否持續
2. 再按 `🧯` 執行短版 SOP
3. 觀察是否收到 `RECOVERED`

判定正常：

- 出現恢復訊息
- HEALTH 回到 OK

## 什麼時候該升級處理

符合任一條件就升級到值班 SRE：

1. ALERT 持續超過 10 分鐘且無恢復
2. DB `max_ts` 明顯停滯
3. queue 長時間維持高位且持續上升

## 文字命令速查（管理員）

- `/db_stats --day YYYYMMDD`：看指定交易日 DB 摘要
- `/top_symbols --limit 10 --minutes 15 --metric rows --day YYYYMMDD`：看窗口排行
- `/symbol HK.00700 --last 20 --day YYYYMMDD`：看單票明細

若命令沒反應，依序檢查：

1. `TG_CHAT_ID` 是否對應目前群組
2. `TG_ADMIN_USER_IDS` 是否包含你
3. 日誌是否出現 `telegram_send_failed`
4. 是否觸發速率限制（每分鐘次數）

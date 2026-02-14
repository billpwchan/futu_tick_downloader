# Engineering: Telegram Interactive Flow

## 架構

- 渲染層：`hk_tick_collector/notifiers/telegram_render.py`
  - `render_health_compact()` / `render_health_detail()`
  - `render_alert_compact()` / `render_alert_detail()`
  - `render_daily_digest()`
- 互動路由：`hk_tick_collector/notifiers/telegram_actions.py`
  - `parse_callback_data()`
  - `handle_callback_query()`
  - dispatch: `d/log/db/sop/mute/rf/top`
- 短期上下文：`ActionContextStore`（in-memory + TTL）
  - 儲存 `sid/eid`、compact/detail text、snapshot/event、message 綁定

## 事件流

1. `submit_health/submit_alert/resolve_alert` 產生 compact/detail
2. enqueue 後由 notifier worker 發送
3. 發送成功回寫 `message_id` 到 `ActionContextStore`
4. callback loop 收到 `callback_query`
5. 先 `answerCallbackQuery`，再進 action router
6. `detail` 用 `editMessageText`，其他 action 用 `sendMessage`

## 不阻塞原則

- callback loop 與採集主鏈路分離
- 命令查詢走 worker / thread（`asyncio.to_thread`）
- 外部命令有白名單 + timeout
- 失敗時回覆短錯誤訊息，不中斷採集

## 安全

- callback_data 限制 <=64 bytes
- chat_id 與 admin user id 雙重檢查
- 僅允許固定命令：
  - `journalctl -u hk-tick-collector --since "20 minutes ago" --no-pager`
  - `scripts/hk-tickctl db stats [--day YYYYMMDD]`

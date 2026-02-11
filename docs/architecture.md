# 架构与容量规划

## 架构概览

- 采集器运行在服务器本机，与 OpenD 同机同网段通信。
- OpenD 仅监听 `127.0.0.1:11111`，采集器通过本机回环连接。
- 数据流：OpenD push 回调 -> asyncio queue -> batch flush -> SQLite 日分库。
- 兜底轮询：定时调用 `get_rt_ticker`，并按 `seq` 去重补写。
- seq 状态拆分为 `last_seen_seq`（观测）、`last_accepted_seq`（成功入队）、`last_persisted_seq`（成功落库）。

## 数据流

1) OpenD 推送 `SubType.TICKER` 数据。
2) `TickerHandlerBase.on_recv_rsp` 解析 DataFrame，映射为 `TickRow`。
3) 写入 async queue（高吞吐，削峰）。
4) Flush 任务按 `batch_size/max_wait_ms` 触发批量写入。
5) 按交易日分库落盘到 `/data/sqlite/HK/YYYYMMDD.db`。
6) 每分钟输出 health 汇总（push/poll/persist/dropped），并在“上游活跃但持久化停滞”时触发 watchdog 自愈重启。

## 断线恢复

- 连接失败采用指数退避重连，最大间隔受 `RECONNECT_MAX_DELAY` 限制。
- 重连成功后自动重新订阅。
- 可选回补：`BACKFILL_N > 0` 时，调用 `get_rt_ticker(code, num=N)` 拉取最近 N 笔。
- SQLite 写入使用唯一索引与 `INSERT OR IGNORE`，可幂等去重。
- 轮询去重基准使用 `last_persisted_seq`（已落库进度）。
- 轮询默认启用：仅在 push 断流/`last_tick_age_sec` 超阈值时才触发，避免重复窗口与 CPU 抖动。

## 容量规划（粗略估算）

- 单条 tick 记录（含索引）估算 150~250 bytes。
- 若 100 只股票、日均 50,000 tick：
  - 记录数约 5,000,000 行/日。
  - 数据量约 0.75~1.25 GB/日（含索引）。
- 建议至少预留 30 天滚动空间，并通过定期归档/压缩控制增长。

## 可靠性建议

- 采集器已强制使用 `Asia/Hong_Kong` 解析市场时间并转换到 UTC epoch，不再依赖服务器系统时区。
- OpenD 与采集器都使用 systemd 守护，`Restart=always`。
- 建议配置 `WATCHDOG_STALL_SEC=120~300`，watchdog 会先重建 writer 自愈，连续失败才以退出码 `1` 退出交给 systemd 恢复。

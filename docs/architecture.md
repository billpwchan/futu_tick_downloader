# 架构与容量规划

## 架构概览

- 采集器运行在服务器本机，与 OpenD 同机同网段通信。
- OpenD 仅监听 `127.0.0.1:11111`，采集器通过本机回环连接。
- 数据流：OpenD push 回调 -> asyncio queue -> batch flush -> SQLite 日分库。

## 数据流

1) OpenD 推送 `SubType.TICKER` 数据。
2) `TickerHandlerBase.on_recv_rsp` 解析 DataFrame，映射为 `TickRow`。
3) 写入 async queue（高吞吐，削峰）。
4) Flush 任务按 `batch_size/max_wait_ms` 触发批量写入。
5) 按交易日分库落盘到 `/data/sqlite/HK/YYYYMMDD.db`。

## 断线恢复

- 连接失败采用指数退避重连，最大间隔受 `RECONNECT_MAX_DELAY` 限制。
- 重连成功后自动重新订阅。
- 可选回补：`BACKFILL_N > 0` 时，调用 `get_rt_ticker(code, num=N)` 拉取最近 N 笔。
- SQLite 写入使用唯一索引与 `INSERT OR IGNORE`，可幂等去重。

## 容量规划（粗略估算）

- 单条 tick 记录（含索引）估算 150~250 bytes。
- 若 100 只股票、日均 50,000 tick：
  - 记录数约 5,000,000 行/日。
  - 数据量约 0.75~1.25 GB/日（含索引）。
- 建议至少预留 30 天滚动空间，并通过定期归档/压缩控制增长。

## 可靠性建议

- 服务器时区建议设置为 `Asia/Hong_Kong`，保证 `ts_ms` 与 `trading_day` 一致性。
- OpenD 与采集器都使用 systemd 守护，`Restart=always`。

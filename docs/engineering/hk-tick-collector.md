# HK Tick Collector Engineering Notes

## 当前实现要点（代码事实）

- 入口：`hk_tick_collector/main.py`
- 配置：`hk_tick_collector/config.py`（`Config.from_env()`）
- 写入链路：`hk_tick_collector/collector.py` + `hk_tick_collector/db.py`
- 上游与 watchdog：`hk_tick_collector/futu_client.py`

## 近期关键改进（2026-02-11）

### 1) Watchdog 阈值配置生效

- Root cause：`WATCHDOG_QUEUE_THRESHOLD_ROWS` 已有配置但未参与判定，导致小 backlog 下也可能过敏触发恢复逻辑。
- 修复：`_check_watchdog()` 改为 `queue_size >= WATCHDOG_QUEUE_THRESHOLD_ROWS` 才进入 stall 检查。
- 风险：仅降低误报，不改变数据格式与落库语义。
- 回归测试：
  - `tests/test_futu_client.py::test_watchdog_honors_queue_threshold`
  - `tests/test_futu_client.py::test_watchdog_ignores_duplicate_only_window_without_backlog`

### 2) 配置与时区/PRAGMA 测试补齐

新增测试：

- `tests/test_config.py`：默认值、布尔/列表、非法数值。
- `tests/test_mapping.py::test_parse_time_to_ts_ms_is_independent_from_system_tz`：系统 TZ 变化不影响 HK->UTC 语义。
- `tests/test_schema.py::test_connect_applies_sqlite_pragmas`：WAL/busy_timeout/synchronous/wal_autocheckpoint。
- `tests/test_smoke_pipeline.py`：临时 SQLite 全链路 smoke。

## 运维标准入口

- 部署：[`docs/deployment/ubuntu-systemd.md`](../deployment/ubuntu-systemd.md)
- 运维：[`docs/operations/runbook-hk-tick-collector.md`](../operations/runbook-hk-tick-collector.md)
- 配置：[`docs/configuration.md`](../configuration.md)

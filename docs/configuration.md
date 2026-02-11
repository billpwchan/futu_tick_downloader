# Configuration Reference

配置来源：`EnvironmentFile`（systemd）或仓库根目录 `.env`。解析入口见 `hk_tick_collector/config.py`。

## 1. 解析规则

- 整数/浮点配置：非法值会在启动时抛 `ValueError` 并退出。
- 布尔配置（`FUTU_POLL_ENABLED`）：支持 `1/0,true/false,yes/no,on/off`（大小写不敏感）；非法值回退默认值。
- 列表配置（`FUTU_SYMBOLS`）：按逗号分割，自动去空白。

## 2. 全量环境变量

| Key | 默认值 | 单位 | 作用 | 风险 | 生产建议 |
|---|---:|---|---|---|---|
| `FUTU_HOST` | `127.0.0.1` | - | OpenD 地址 | 配错会连接失败 | 保持回环地址 |
| `FUTU_PORT` | `11111` | port | OpenD 端口 | 端口错误导致订阅失败 | 与 OpenD `api_port` 保持一致 |
| `FUTU_SYMBOLS` | 空 | CSV | 订阅股票列表 | 为空会启动失败 | 明确列出目标代码，如 `HK.00700,HK.00981` |
| `DATA_ROOT` | `/data/sqlite/HK` | path | SQLite 存储根目录 | 权限/磁盘不足导致写失败 | 独立磁盘，预留容量 |
| `BATCH_SIZE` | `500` | rows | 批量落库大小 | 太小写放大，太大停机 flush 慢 | `300-1000` |
| `MAX_WAIT_MS` | `1000` | ms | 最长 flush 等待时间 | 太大会增加端到端延迟 | `500-1500` |
| `MAX_QUEUE_SIZE` | `20000` | rows | 内存队列上限 | 太小会丢队列，太大会吃内存 | `20000-100000` 视吞吐定 |
| `BACKFILL_N` | `0` | rows | 重连后回补最近 N 笔 | 太大会放大重复/启动耗时 | 默认 `0`，仅必要时开启 |
| `RECONNECT_MIN_DELAY` | `1` | sec | 重连最小间隔 | 过小可能频繁打点 | `1-3` |
| `RECONNECT_MAX_DELAY` | `60` | sec | 重连最大间隔 | 过大恢复慢 | `30-60` |
| `CHECK_INTERVAL_SEC` | `5` | sec | 连接状态检查周期 | 太短增加噪音 | `3-10` |
| `FUTU_POLL_ENABLED` | `true` | bool | 启用 poll 兜底 | 关闭后 push 断流无法补 | 生产建议开启 |
| `FUTU_POLL_INTERVAL_SEC` | `3` | sec | poll 周期 | 太短会增加重复和负载 | `2-5` |
| `FUTU_POLL_NUM` | `100` | rows | 每次 poll 请求条数 | 太大增加 CPU/网络 | `50-200` |
| `FUTU_POLL_STALE_SEC` | `10` | sec | push 多久无更新才 poll | 太小会频繁 poll | `8-15` |
| `WATCHDOG_STALL_SEC` | `180` | sec | commit 停滞阈值 | 太短误报，太长恢复慢 | `120-300` |
| `WATCHDOG_UPSTREAM_WINDOW_SEC` | `60` | sec | 上游活跃判断窗口 | 太小可能漏检 | `30-120` |
| `WATCHDOG_QUEUE_THRESHOLD_ROWS` | `100` | rows | 触发 watchdog 的最小 backlog | 过低易误判 | `100-1000` |
| `WATCHDOG_RECOVERY_MAX_FAILURES` | `3` | count | 自愈失败多少次后退出 | 过小导致频繁重启 | `3-5` |
| `WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC` | `3.0` | sec | 等待旧 worker 退出时间 | 过小可能恢复失败 | `2-5` |
| `DRIFT_WARN_SEC` | `120` | sec | `ts_ms` 漂移告警阈值 | 太小告警噪音 | `60-180` |
| `STOP_FLUSH_TIMEOUT_SEC` | `60` | sec | 停机 flush 等待上限 | 太小易 timeout | `60-180` |
| `SEED_RECENT_DB_DAYS` | `3` | day | 启动时扫描最近几天 seed seq | 太大启动慢 | `3-5` |
| `PERSIST_RETRY_MAX_ATTEMPTS` | `0` | count | 单轮 retry 预算（0=持续重试） | 过低可能早失败 | 建议 `0` |
| `PERSIST_RETRY_BACKOFF_SEC` | `1.0` | sec | 持久化重试初始退避 | 太小忙等，太大恢复慢 | `0.1-1.0` |
| `PERSIST_RETRY_BACKOFF_MAX_SEC` | `2.0` | sec | 持久化最大退避 | 太小压 SQLite，太大吞吐低 | `1-5` |
| `PERSIST_HEARTBEAT_INTERVAL_SEC` | `30.0` | sec | persist heartbeat 输出周期 | 太短日志量大 | `10-30` |
| `SQLITE_BUSY_TIMEOUT_MS` | `5000` | ms | SQLite busy 等待时间 | 太小频繁 `locked` | `3000-10000` |
| `SQLITE_JOURNAL_MODE` | `WAL` | enum | journal 模式 | 非 WAL 并发读写差 | 建议 `WAL` |
| `SQLITE_SYNCHRONOUS` | `NORMAL` | enum | 同步级别 | `OFF` 有数据风险 | `NORMAL`（或高安全 `FULL`） |
| `SQLITE_WAL_AUTOCHECKPOINT` | `1000` | pages | 自动 checkpoint 页数 | 太大 WAL 膨胀 | `500-2000` |
| `LOG_LEVEL` | `INFO` | enum | 日志等级 | `DEBUG` 量大 | 生产 `INFO` |

## 3. 稳定生产配置模板

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

DRIFT_WARN_SEC=120
SEED_RECENT_DB_DAYS=3
LOG_LEVEL=INFO
```

## 4. 配置变更后验证

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
bash scripts/healthcheck.sh
bash scripts/verify_db.sh
```

相关：[`docs/deployment/ubuntu-systemd.md`](deployment/ubuntu-systemd.md)

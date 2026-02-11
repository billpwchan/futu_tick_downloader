# hk-tick-collector

[![CI](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml/badge.svg)](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

面向生产的港股 Tick 采集服务：连接 Futu OpenD，去重后持久化到 SQLite（WAL），并支持 watchdog 自愈与 systemd 托管。

English README: [README.md](README.md)

## 亮点

- Push 主链路 + Poll 兜底。
- SQLite 日分库（`DATA_ROOT/YYYYMMDD.db`）+ WAL。
- 唯一索引 + `INSERT OR IGNORE` 幂等去重。
- Watchdog 对持久化卡死自动恢复，失败后交给 systemd 重启。
- 全部配置走环境变量，易于运维。

## 5 分钟上手（无 OpenD 本地验证）

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
pytest -q
```

## 生产部署（OpenD + systemd）

```bash
cp .env.example .env
# 至少配置 FUTU_HOST/FUTU_PORT/FUTU_SYMBOLS/DATA_ROOT

. .venv/bin/activate
hk-tick-collector
# 或兼容旧入口
python -m hk_tick_collector.main
```

systemd 文档：[`docs/deployment/systemd.md`](docs/deployment/systemd.md)

## 时间语义

- `ticks.ts_ms`: 事件时间，UTC epoch 毫秒。
- `ticks.recv_ts_ms`: 采集进程接收时间，UTC epoch 毫秒。
- 对无时区的港股本地时间字符串，按 `Asia/Hong_Kong` 解释后转换为 UTC epoch。

## 文档导航

- 快速开始：[`docs/getting-started.md`](docs/getting-started.md)
- 配置：[`docs/configuration.md`](docs/configuration.md)
- 架构：[`docs/architecture.md`](docs/architecture.md)
- 运维 Runbook：[`docs/runbook/operations.md`](docs/runbook/operations.md)
- 故障处理：[`docs/troubleshooting.md`](docs/troubleshooting.md)
- 发布流程：[`docs/releasing.md`](docs/releasing.md)

## 合规说明

请确保 Futu OpenD 使用与行情数据使用符合官方条款和当地法规。本项目仅负责采集与存储，不提供任何专有数据分发授权。

# hk-tick-collector

[![CI](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml/badge.svg)](https://github.com/billpwchan/futu_tick_downloader/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/billpwchan/futu_tick_downloader)](https://github.com/billpwchan/futu_tick_downloader/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

面向生产的港股 Tick 采集服务：连接 Futu OpenD，Push 主链路 + Poll 兜底，去重后落盘 SQLite WAL，并提供 systemd 运维与故障 Runbook。

English README: [README.md](README.md)

## 为什么选择它

- 生产稳定性优先：watchdog + 健康心跳 + 可恢复写入路径。
- 数据语义清晰：`ts_ms` / `recv_ts_ms` 明确为 UTC epoch 毫秒。
- 运维成本低：按交易日分库、SQLite WAL、命令化巡检与排障。

## 核心特性

- Push + Poll 采集策略（`FUTU_POLL_*`）
- SQLite 日分库（`DATA_ROOT/YYYYMMDD.db`）
- 唯一索引 + `INSERT OR IGNORE` 幂等去重
- systemd 部署模板与完整文档

## 3 分钟上手

### 本地验证（无需 OpenD）

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
pytest -q
```

### 连接 OpenD 运行

```bash
cp .env.example .env
# 至少配置 FUTU_HOST/FUTU_PORT/FUTU_SYMBOLS/DATA_ROOT

. .venv/bin/activate
hk-tick-collector
# 兼容入口
python -m hk_tick_collector.main
```

## 生产运维入口

- systemd 部署：[`docs/deployment/systemd.md`](docs/deployment/systemd.md)
- 运维总览：[`docs/runbook/operations.md`](docs/runbook/operations.md)
- 单页生产 Runbook（推荐）：[`docs/runbook/production-onepager.md`](docs/runbook/production-onepager.md)

## 时间语义（务必统一）

- `ticks.ts_ms`: 事件时间，UTC epoch 毫秒
- `ticks.recv_ts_ms`: 采集进程接收时间，UTC epoch 毫秒
- 无时区港股本地时间按 `Asia/Hong_Kong` 解释后再转 UTC epoch

## 文档导航

- 快速开始：[`docs/getting-started.md`](docs/getting-started.md)
- 配置参考：[`docs/configuration.md`](docs/configuration.md)
- 架构：[`docs/architecture.md`](docs/architecture.md)
- 故障处理：[`docs/troubleshooting.md`](docs/troubleshooting.md)
- 发布流程：[`docs/releasing.md`](docs/releasing.md)

## 合规说明

请确保 Futu OpenD 与行情数据使用符合官方条款和当地法规。本项目仅负责采集与存储，不提供任何专有数据分发授权。

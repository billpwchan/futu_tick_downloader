# hk-tick-collector

面向服务器 24/7 的港股逐笔成交采集器（Futu OpenAPI + SQLite 分库落盘）。

- OpenD 必须使用富途官方发行包在服务器原生安装运行（禁止第三方 Docker 镜像）。
- 数据按交易日分库：`/data/sqlite/HK/YYYYMMDD.db`，表结构与索引固定一致。

## 快速开始

1) 按照 `docs/runbook.md` 完成 OpenD 安装与 systemd 守护。
2) 复制环境变量样例（任选其一）：

```
cp .env.example /etc/hk-tick-collector.env
```

或：

```
cp .env.example .env
```

3) 安装采集器并启动：

```
sudo ops/install_collector.sh
```

## 目录

- `hk_tick_collector/` 采集器代码
- `docs/architecture.md` 架构与容量规划
- `docs/runbook.md` 部署与运维步骤
- `ops/` systemd 模板与脚本
- `tests/` 单元测试

## 本地测试

```
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

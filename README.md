# hk-tick-collector

`hk-tick-collector` 是一个长期运行的港股 tick 采集服务：

- 上游：Futu OpenD (`futu-opend.service`)
- 采集：push 为主 + poll 兜底
- 存储：按交易日分库 SQLite，路径形如 `/data/sqlite/HK/YYYYMMDD.db`
- 落盘：WAL 模式 + 批量写入 + `INSERT OR IGNORE` 去重
- 运维：systemd 托管 + 健康日志 + watchdog 自愈/退出重启

## 核心数据流

```text
Futu OpenD push/poll
  -> mapping(ts_ms/seq/symbol)
  -> enqueue (AsyncTickCollector)
  -> persist worker batch flush
  -> SQLite ticks (per-day DB)
```

详细架构见：[`docs/architecture.md`](docs/architecture.md)

## 5 分钟 Quickstart（本地最小跑通）

前置：本机已运行 OpenD，且 `127.0.0.1:11111` 可访问。

```bash
cd /Users/billpwchan/Documents/futu_tick_downloader
python3.11 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
```

编辑 `.env`（至少设置）：

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700
DATA_ROOT=/tmp/hk_ticks
```

启动：

```bash
python -m hk_tick_collector.main
```

验证：

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
DB=/tmp/hk_ticks/${DAY}.db
bash scripts/verify_db.sh "$DB"
```

## 常用命令（运维）

```bash
# 启停
sudo systemctl start hk-tick-collector
sudo systemctl stop hk-tick-collector
sudo systemctl restart hk-tick-collector

# 状态与实时日志
sudo systemctl status hk-tick-collector --no-pager
bash scripts/tail_logs.sh

# 服务健康（进程 + 数据增长 + 延迟）
bash scripts/healthcheck.sh

# DB 快速核验（lag/最新记录/分布/重复）
bash scripts/verify_db.sh
```

## 文档导航

- 架构与数据语义：[`docs/architecture.md`](docs/architecture.md)
- 配置项详解：[`docs/configuration.md`](docs/configuration.md)
- Ubuntu + systemd 部署：[`docs/deployment/ubuntu-systemd.md`](docs/deployment/ubuntu-systemd.md)
- 运维 Runbook：[`docs/operations/runbook-hk-tick-collector.md`](docs/operations/runbook-hk-tick-collector.md)
- 用户数据校验指南：[`docs/user-guide/hk-data.md`](docs/user-guide/hk-data.md)

## 目录结构

```text
hk_tick_collector/           # 核心采集代码（main/config/futu_client/collector/db）
docs/                        # 架构、配置、部署、运维文档
scripts/                     # 运维脚本（healthcheck/verify_db/tail_logs/...）
deploy/systemd/              # 推荐 systemd unit 模板（单一来源）
tests/                       # 单元/回归/smoke 测试
```

## 版本与兼容性

- Python：建议 `3.11+`（部署文档以 Ubuntu Python 3.11 为基线）
- OS：Ubuntu + systemd
- SQLite：建议 `3.37+`（WAL / window function / pragma 行为更稳定）
- Futu：依赖 `futu-api>=8.0.0` 与本机 OpenD 服务

## 测试

```bash
. .venv/bin/activate
pytest -q
```

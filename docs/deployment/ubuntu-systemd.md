# Ubuntu + systemd Deployment

本文档覆盖从零部署、升级、回滚与 systemd 配置校验。默认服务名：`hk-tick-collector.service`。

## 1. 前置条件

- Ubuntu（建议 22.04+）
- 已安装并可运行 OpenD（建议 `futu-opend.service`）
- 仓库位于 `/opt/futu_tick_downloader`（如不同路径，请同步修改 unit）

安装依赖：

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv sqlite3 jq
```

## 2. 从零部署

### 2.1 创建用户与目录

```bash
sudo useradd --system --home /opt/futu_tick_downloader --shell /usr/sbin/nologin hkcollector || true
sudo mkdir -p /opt/futu_tick_downloader /data/sqlite/HK
sudo chown -R hkcollector:hkcollector /data/sqlite
sudo chmod -R 750 /data/sqlite
```

### 2.2 安装代码与 venv

```bash
cd /opt/futu_tick_downloader
python3.11 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 2.3 准备环境变量文件

```bash
sudo cp /opt/futu_tick_downloader/.env.example /etc/hk-tick-collector.env
sudo chown root:hkcollector /etc/hk-tick-collector.env
sudo chmod 640 /etc/hk-tick-collector.env
sudoedit /etc/hk-tick-collector.env
```

### 2.4 安装 systemd unit

推荐模板文件：`deploy/systemd/hk-tick-collector.service`

```bash
sudo cp /opt/futu_tick_downloader/deploy/systemd/hk-tick-collector.service /etc/systemd/system/hk-tick-collector.service
sudo systemctl daemon-reload
sudo systemctl enable futu-opend.service
sudo systemctl enable hk-tick-collector.service
```

### 2.5 启动依赖与主服务

```bash
sudo systemctl start futu-opend.service
sudo systemctl start hk-tick-collector.service
sudo systemctl status futu-opend.service --no-pager
sudo systemctl status hk-tick-collector.service --no-pager
```

## 3. unit 字段说明（推荐模板）

`deploy/systemd/hk-tick-collector.service` 关键字段：

- `After=network-online.target futu-opend.service`: 确保网络与 OpenD 先就绪。
- `Requires=futu-opend.service`: OpenD 不可用时，collector 也应停。
- `EnvironmentFile=/etc/hk-tick-collector.env`: 统一注入 `.env` 配置。
- `WorkingDirectory=/opt/futu_tick_downloader`: 固定运行目录。
- `ExecStart=/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.main`: 固定入口。
- `Restart=always` + `RestartSec=5`: 异常退出自动拉起。
- `TimeoutStopSec=180` + `KillSignal=SIGINT`: 给 flush 足够时间。
- `ProtectSystem/ProtectHome/NoNewPrivileges/ReadWritePaths`: 安全加固（仅放开写路径）。

校验命令：

```bash
sudo systemd-analyze verify /etc/systemd/system/hk-tick-collector.service
sudo systemctl cat hk-tick-collector.service
sudo systemctl show hk-tick-collector.service -p FragmentPath -p EnvironmentFiles -p ExecStart -p TimeoutStopUSec -p Restart
```

## 4. 升级流程（滚动）

```bash
cd /opt/futu_tick_downloader
sudo systemctl stop hk-tick-collector.service
sudo cp /etc/hk-tick-collector.env /etc/hk-tick-collector.env.bak.$(date +%Y%m%d%H%M%S)
git fetch --all --prune
git checkout <target_ref>
. .venv/bin/activate
pip install -r requirements.txt
sudo cp deploy/systemd/hk-tick-collector.service /etc/systemd/system/hk-tick-collector.service
sudo systemctl daemon-reload
sudo systemctl start hk-tick-collector.service
bash scripts/healthcheck.sh
bash scripts/verify_db.sh
```

## 5. 回滚流程（代码 + venv + 配置）

```bash
cd /opt/futu_tick_downloader
sudo systemctl stop hk-tick-collector.service
git checkout <rollback_ref>
. .venv/bin/activate
pip install -r requirements.txt
sudo cp /etc/hk-tick-collector.env.bak.<timestamp> /etc/hk-tick-collector.env
sudo systemctl daemon-reload
sudo systemctl start hk-tick-collector.service
bash scripts/healthcheck.sh
```

已有脚本：`scripts/rollback_hk_tick_collector.sh`

## 6. 日志与轮转

服务日志默认走 journald：

```bash
sudo journalctl -u hk-tick-collector -n 200 --no-pager
sudo journalctl -u hk-tick-collector -f
```

按时间筛选：

```bash
sudo journalctl -u hk-tick-collector --since "30 minutes ago" --no-pager
```

journald 容量维护（等价 logrotate 场景）：

```bash
sudo journalctl --vacuum-time=14d
sudo journalctl --vacuum-size=2G
```

如需永久限制，可在 `/etc/systemd/journald.conf` 设置 `SystemMaxUse=` 后重启 `systemd-journald`。

## 7. 安全加固建议

推荐保持以下项开启：

- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `ReadWritePaths=/data/sqlite/HK /opt/futu_tick_downloader`

注意：如果你修改了 `DATA_ROOT` 或应用目录，必须同步更新 `ReadWritePaths`，否则会变为只读失败。

## 8. 一键安装（可选）

本仓库提供幂等脚本：

```bash
sudo bash scripts/install_systemd.sh
```

脚本会创建用户/目录、安装 venv 依赖、部署 unit、执行 `systemd-analyze verify`。

## 9. 部署后验收

3 分钟内至少满足以下项：

- `health` 日志持续输出。
- `persist_ticks` 持续出现。
- DB lag 在阈值内。
- 断开 SSH 后服务仍是 `active`。

```bash
sudo journalctl -u hk-tick-collector --since "3 minutes ago" --no-pager | grep -E "health|persist_ticks|WATCHDOG"
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
sqlite3 "/data/sqlite/HK/${DAY}.db" "SELECT (strftime('%s','now') - MAX(ts_ms)/1000.0) AS lag_sec FROM ticks;"
systemctl is-active hk-tick-collector
```

完整排障版见：[`docs/operations/runbook-hk-tick-collector.md`](../operations/runbook-hk-tick-collector.md)

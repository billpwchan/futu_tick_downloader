# HK Tick Collector × Futu OpenD 部署与排障 Runbook (AWS Lightsail / Ubuntu)

目标：在服务器 24/7 运行 Futu OpenD + HK Tick Collector，持续订阅港股逐笔成交（TICKER）并写入 SQLite（按交易日分库），并提供可复制的部署、验证、排障流程。

## 0) 系统组件与数据结构

### 0.1 组件

- Futu OpenD（官方安装包）：本地服务进程，监听 `127.0.0.1:11111`（Quote API）和可选 `127.0.0.1:22222`（telnet 运维）。
- HK Tick Collector（本 repo）：Python 服务，通过 futu-api 连接 OpenD，订阅 TICKER 推送 + 轮询兜底，写入 SQLite。

### 0.2 SQLite 存储规范（必须保持一致）

- 目录：`/data/sqlite/HK/`
- 分片：`YYYYMMDD.db`
- 表：`ticks`（每个 db 一张表）
- 去重：
  - 有 `seq`：`(symbol, seq)` 唯一
  - 无 `seq`：`(symbol, ts_ms, price, volume, turnover)` 唯一

## 1) 服务器准备

### 1.1 基础依赖

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv sqlite3 telnet net-tools lsof jq
```

建议设置系统时区：

```bash
sudo timedatectl set-timezone Asia/Hong_Kong
```

### 1.2 目录与权限（建议）

```bash
sudo useradd --system --home /opt/futu_tick_downloader --shell /usr/sbin/nologin hkcollector || true
sudo mkdir -p /data/sqlite/HK
sudo chown -R hkcollector:hkcollector /data/sqlite
sudo chmod -R 750 /data/sqlite
```

若希望 ubuntu 用户可读（推荐用 group）：

```bash
sudo usermod -aG hkcollector ubuntu
sudo chgrp -R hkcollector /data/sqlite
sudo chmod -R g+rx /data/sqlite
sudo chmod -R g+r  /data/sqlite/HK
```

重新登录 SSH 让组权限生效。

## 2) 安装与运行 Futu OpenD（官方包）

### 2.1 安装（按官方包路径/步骤为准）

假设安装到：

- 二进制：`/opt/futu-opend/FutuOpenD`
- 配置：`/opt/futu-opend/OpenD.xml`（或 `FutuOpenD.xml`，以实际为准）
- AppData：`/opt/futu-opend/AppData.dat`

校验关键文件：

```bash
sudo ls -la /opt/futu-opend | egrep "FutuOpenD|OpenD.xml|FutuOpenD.xml|AppData.dat"
```

建议创建独立用户运行 OpenD：

```bash
sudo useradd --system --home /opt/futu-opend --shell /usr/sbin/nologin futu || true
sudo chown -R futu:futu /opt/futu-opend
```

### 2.2 OpenD 配置检查

```bash
sudo grep -nE "<ip>|<api_port>|<telnet_ip>|<telnet_port>|<Lang>" /opt/futu-opend/OpenD.xml
```

应至少包含（示例）：

```
<ip>127.0.0.1</ip>
<api_port>11111</api_port>
<telnet_ip>127.0.0.1</telnet_ip>
<telnet_port>22222</telnet_port>
```

### 2.3 systemd 服务（推荐做法：禁用 OpenD 自带 monitor）

`/etc/systemd/system/futu-opend.service` 推荐关键点：

- `-no_monitor=1`：交给 systemd 守护
- `StartLimitIntervalSec` 放在 `[Unit]`，不要放在 `[Service]`

重载与启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now futu-opend
sudo systemctl status futu-opend --no-pager
```

### 2.4 端口验证

```bash
sudo ss --tcp --listening --processes 'sport = :11111'
sudo ss --tcp --listening --processes 'sport = :22222'
```

## 3) OpenD 登录与手机验证码（必要时）

### 3.1 查看 OpenD 日志（最可靠）

```bash
sudo -u futu -H bash -lc '
LATEST=$(ls -1t ~/.com.futunn.FutuOpenD/Log/GTWLog_*.log | head -n 1)
echo "==> $LATEST <=="
tail -n 80 "$LATEST"
'
```

若看到 `NeedPhoneVerifyCode`，需要输入验证码。

### 3.2 使用 telnet 输入验证码（推荐）

```bash
telnet 127.0.0.1 22222
```

在 telnet 会话中：

```
req_phone_verify_code
input_phone_verify_code -code=123456
exit
```

完成后再看 OpenD 状态与端口：

```bash
sudo ss --tcp --listening --processes 'sport = :11111'
sudo -u futu -H bash -lc 'tail -n 40 $(ls -1t ~/.com.futunn.FutuOpenD/Log/GTWLog_*.log | head -n 1)'
```

## 4) 部署 HK Tick Collector（本 repo）

### 4.1 目录与虚拟环境

假设 repo 在 `/opt/futu_tick_downloader`（如不同路径，请同步修改 service 与脚本）：

```bash
cd /opt/futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

也可以用安装脚本（可自定义路径与 env 文件）： 

```bash
sudo APP_DIR=/opt/futu_tick_downloader ENV_FILE=/opt/futu_tick_downloader/.env ops/install_collector.sh
```

### 4.2 环境变量（两种方式任选其一）

方式 A：使用系统级环境文件（默认脚本路径）

```bash
sudo cp .env.example /etc/hk-tick-collector.env
sudo nano /etc/hk-tick-collector.env
```

方式 B：直接使用 repo 内的 `.env`

```bash
cp .env.example /opt/futu_tick_downloader/.env
```

推荐字段：

```
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700,HK.00981
DATA_ROOT=/data/sqlite/HK

FUTU_POLL_ENABLED=1
FUTU_POLL_INTERVAL_SEC=3
FUTU_POLL_NUM=100
```

### 4.3 systemd 服务（必须加载 .env）

编辑：

```bash
sudo nano /etc/systemd/system/hk-tick-collector.service
```

关键点：

- `EnvironmentFile=/etc/hk-tick-collector.env` 或 `/opt/futu_tick_downloader/.env`
- 删除 `[Service]` 下的 `StartLimitIntervalSec=...`（避免 warning）
- `WorkingDirectory` 指向 repo
- `ExecStart` 指向 `.venv/bin/python -m hk_tick_collector.main`

重载并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

## 5) 快速验证（必做）

### 5.1 OpenD 连接与 collector 连接

```bash
sudo ss -tnp | grep 11111 || true
```

应看到 python 与 FutuOpenD 之间 ESTAB。

### 5.2 检查 collector 环境变量是否注入成功

```bash
PID=$(systemctl show -p MainPID --value hk-tick-collector)
sudo tr '\0' '\n' < /proc/$PID/environ | egrep '^FUTU_(SYMBOLS|HOST|PORT)='
```

### 5.3 当天 DB 是否生成、是否增长

```bash
TODAY=$(date +%Y%m%d)
ls -lh /data/sqlite/HK/${TODAY}.db
sudo sqlite3 /data/sqlite/HK/${TODAY}.db "select count(*) from ticks;"
sudo sqlite3 /data/sqlite/HK/${TODAY}.db "select symbol, max(seq), datetime(max(ts_ms)/1000,'unixepoch') from ticks group by symbol;"
```

### 5.4 一键验证脚本

```bash
sudo ops/verify.sh
```

若使用非默认 env 文件：

```bash
sudo ENV_FILE=/opt/futu_tick_downloader/.env ops/verify.sh
```

## 6) 一次性诊断脚本（可直接复制粘贴）

用于确认：OpenD READY、市场状态、订阅状态、能否拉取逐笔。

```bash
sudo -u hkcollector -H bash -lc 'cd /opt/futu_tick_downloader && set -a && . ./.env && set +a && . .venv/bin/activate && python - <<PY
import os
from futu import OpenQuoteContext, SubType

symbols=[s.strip() for s in os.getenv("FUTU_SYMBOLS","").split(",") if s.strip()]
host=os.getenv("FUTU_HOST","127.0.0.1")
port=int(os.getenv("FUTU_PORT","11111"))

q=OpenQuoteContext(host=host, port=port)
print(q.get_global_state())
ret, ms = q.get_market_state(symbols)
print(ret)
print(ms)
ret, sub = q.query_subscription()
print(ret)
print(sub)

ret, msg = q.subscribe(symbols, [SubType.TICKER], subscribe_push=False)
print("subscribe:", ret, msg)

for code in symbols:
    ret, data = q.get_rt_ticker(code, num=20)
    if ret == 0:
        print(code, ret, len(data))
        print(data.head(3).to_string(index=False))
    else:
        print(code, ret, str(data))

q.close()
PY'
```

## 7) 常见问题排障

### 7.1 `FUTU_SYMBOLS is empty`

原因：systemd 没加载 env 或变量未设置。

修复：

- 确认 `.env` 或 `/etc/hk-tick-collector.env` 有 `FUTU_SYMBOLS=...`
- `hk-tick-collector.service` 加 `EnvironmentFile=...`

重启：

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
sudo journalctl -u hk-tick-collector -n 80 --no-pager
```

### 7.2 `telnet 127.0.0.1 22222` / `11111` Connection refused

原因：OpenD 未监听或未正常启动。

检查：

```bash
sudo systemctl status futu-opend --no-pager
sudo ss --tcp --listening --processes 'sport = :11111'
sudo ss --tcp --listening --processes 'sport = :22222'
```

若 OpenD 日志显示需要验证码：

- 用 telnet 输入验证码（见 3.2）

### 7.3 DB 只写了少量记录，之后不再增长

先判断是否真的有成交数据（交易时段）：

- 运行一次性诊断脚本，确认 `market_state` 在交易时段且 `get_rt_ticker` 能返回多行。

若 `get_rt_ticker` 报：

```
请求获取实时逐笔接口前，请先订阅Ticker数据
```

说明当前连接没有订阅，需要 subscribe 后再拉取（见诊断脚本）。

若 `get_rt_ticker` 有数据但 DB 不增长：

- 查看 collector 日志是否持续接收/写入

```bash
sudo journalctl -u hk-tick-collector -n 200 --no-pager
```

- 检查 OpenD 连接是否稳定

```bash
sudo ss -tnp | grep 11111 || true
```

- 查看 DB 最新 seq 是否前进

```bash
sudo sqlite3 /data/sqlite/HK/$(date +%Y%m%d).db "select symbol, max(seq) from ticks group by symbol;"
```

### 7.4 sqlite3 SELECT 报 `attempt to write a readonly database`

原因：当前用户对 DB/WAL/SHM 无权限（DB 属于 hkcollector）。

解决：

- 用 `sudo sqlite3 ...` 或 `sudo -u hkcollector sqlite3 ...`
- 或按 1.2 用 group 权限让 ubuntu 可读

### 7.5 systemd 提示 `StartLimitIntervalSec` Unknown key in `[Service]`

原因：该 key 放错 section。

处理：

- 从 `[Service]` 删除该行（最简单）

然后：

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
```

### 7.6 service 停止超时，被 SIGKILL

现象：

```
State 'stop-sigterm' timed out. Killing.
```

说明进程没优雅退出（线程/连接阻塞）。

处理：

- 确认代码已实现 SIGTERM/SIGINT 优雅退出（close context + flush queue）
- 临时缓解：在 service 里增加：

```
TimeoutStopSec=20
KillSignal=SIGINT
```

重载重启：

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
```

## 8) 日常运维命令合集

### 8.1 服务状态

```bash
sudo systemctl status futu-opend --no-pager
sudo systemctl status hk-tick-collector --no-pager
```

### 8.2 查看日志

```bash
sudo journalctl -u futu-opend -n 200 --no-pager
sudo journalctl -u hk-tick-collector -n 200 --no-pager
```

### 8.3 端口检查

```bash
sudo ss --tcp --listening --processes 'sport = :11111'
sudo ss --tcp --listening --processes 'sport = :22222'
sudo ss -tnp | grep 11111 || true
```

### 8.4 DB 检查

```bash
TODAY=$(date +%Y%m%d)
sudo sqlite3 /data/sqlite/HK/${TODAY}.db "select count(*) from ticks;"
sudo sqlite3 /data/sqlite/HK/${TODAY}.db "select symbol, max(seq), datetime(max(ts_ms)/1000,'unixepoch') from ticks group by symbol;"
```

### 8.5 重启

```bash
sudo systemctl restart futu-opend
sudo systemctl restart hk-tick-collector
```

## 9) 上线验收 Checklist

- futu-opend running，11111 监听正常
- hk-tick-collector running，能看到 ESTAB 到 `127.0.0.1:11111`
- `.env` 或 `/etc/hk-tick-collector.env` 中 `FUTU_SYMBOLS` 正确注入到进程环境
- 当天 `YYYYMMDD.db` 已创建且 `max(seq)` 在交易时段持续前进
- systemd 无 `StartLimitIntervalSec` 警告
- stop/restart 不会超时 SIGKILL（优雅退出 OK）

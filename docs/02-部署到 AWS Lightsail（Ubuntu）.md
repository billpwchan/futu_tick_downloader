# 02-部署到 AWS Lightsail（Ubuntu）

本文件是雲端實盤部署的完整步驟（含 OpenD 登錄驗證、行情權限優先、Telegram 互動、常見坑位）。

## 1. 主機規格與網路

- OS：Ubuntu 22.04/24.04 LTS
- 規格：至少 2 vCPU / 2GB RAM（symbol 較多建議 4GB+）
- 防火牆：
  - `22/tcp`（SSH）僅允許固定來源 IP
  - OpenD 與 collector 同機時，不需對外開 `11111/22222`
- 建議把資料磁碟掛到 `/data`，避免 WAL 撐爆系統盤

## 2. 安裝基礎套件

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv sqlite3 jq zstd ripgrep
sudo timedatectl set-timezone Asia/Hong_Kong
```

## 3. 拉專案 + 建立 env

```bash
cd /opt
sudo git clone https://github.com/billpwchan/futu_tick_downloader.git
cd /opt/futu_tick_downloader
sudo cp deploy/env/.env.example /etc/hk-tick-collector.env
sudo chown root:root /etc/hk-tick-collector.env
sudo chmod 640 /etc/hk-tick-collector.env
```

先填這些關鍵值：

- `FUTU_HOST=127.0.0.1`
- `FUTU_PORT=11111`
- `FUTU_SYMBOLS=HK.00700,HK.00981`（按需）
- `DATA_ROOT=/data/sqlite/HK`
- `TG_ENABLED/TG_TOKEN/TG_CHAT_ID`（若啟用 Telegram）

## 4. 安裝服務（systemd）

```bash
cd /opt/futu_tick_downloader
sudo bash deploy/scripts/install.sh
```

這會建立：

- `hkcollector`（collector 執行帳號）
- `.venv`（服務實際使用）
- `hk-tick-collector.service`
- `futu-opend.service`（若檔案存在）

## 5. OpenD 服務與行情最高權限

### 5.1 服務參數（必核對）

`futu-opend.service` 的 `ExecStart` 必須包含：

- `-no_monitor=1`
- `-auto_hold_quote_right=1`

本專案預設為：

```bash
/opt/futu-opend/FutuOpenD -c /opt/futu-opend/OpenD.xml -no_monitor=1 -auto_hold_quote_right=1
```

> 說明：交易所限制多端同時在線只有一端可拿最高行情權限。`auto_hold_quote_right=1` 會在被搶後自動搶回；若 10 秒內再次被搶，會讓其他端持有。

### 5.2 OpenD 用戶/目錄權限

```bash
sudo getent group futu >/dev/null || sudo groupadd --system futu
id -u futu >/dev/null 2>&1 || sudo useradd --system --gid futu --home-dir /opt/futu-opend --shell /usr/sbin/nologin futu

sudo chown -R futu:futu /opt/futu-opend
sudo chmod 750 /opt/futu-opend
sudo chmod 600 /opt/futu-opend/FutuOpenD.xml
sudo ln -sf /opt/futu-opend/FutuOpenD.xml /opt/futu-opend/OpenD.xml
```

> 若你看到 `chmod: cannot access /opt/futu-opend/OpenD.xml`，通常是實際檔名是 `FutuOpenD.xml`，先建 symlink 再重啟。

### 5.3 啟動並檢查是否監聽

```bash
sudo systemctl daemon-reload
sudo systemctl restart futu-opend
sudo systemctl status futu-opend --no-pager -l
sudo ss -lntp | grep -E 'FutuOpenD|:11111|:22222'
```

若 `ECONNREFUSED`，先檢查：

```bash
sudo grep -nEi 'ip|api_port|telnet_ip|telnet_port' /opt/futu-opend/FutuOpenD.xml
```

## 6. OpenD 首次登錄與短信驗證

當 OpenD 首次登入需要短信驗證時，使用 telnet 介面（22222）：

```bash
python3 - <<'PY'
from telnetlib import Telnet
with Telnet("127.0.0.1", 22222, timeout=5) as tn:
    tn.write(b"req_phone_verify_code\r\n")
    print(tn.read_until(b"\n", timeout=3).decode(errors="ignore"))
PY
```

收到短信後送驗證碼：

```bash
python3 - <<'PY'
from telnetlib import Telnet
code = "123456"  # 改成你的短信碼
with Telnet("127.0.0.1", 22222, timeout=5) as tn:
    tn.write(f"input_phone_verify_code -code={code}\\r\\n".encode())
    print(tn.read_until(b"\n", timeout=3).decode(errors="ignore"))
PY
```

再測 API 連線：

```bash
/opt/futu_tick_downloader/.venv/bin/python - <<'PY'
from futu import OpenQuoteContext
ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret, data = ctx.get_global_state()
print("ret =", ret)
print(data)
ctx.close()
PY
```

## 7. Collector 權限（避免常見 PermissionError / readonly）

```bash
# 讓 hkcollector 可讀寫專案執行態與資料庫
sudo chown -R hkcollector:hkcollector /opt/futu_tick_downloader
sudo chown -R hkcollector:hkcollector /data/sqlite/HK
sudo find /data/sqlite/HK -type d -exec chmod 750 {} \;
sudo find /data/sqlite/HK -type f -exec chmod 640 {} \;
```

若曾出現這些錯誤，幾乎都是權限：

- `PermissionError: ... /opt/futu_tick_downloader/.com.futunn.FutuOpenD`
- `sqlite3.OperationalError: attempt to write a readonly database`

## 8. 啟動採集服務 + 驗證落庫

```bash
sudo systemctl reset-failed hk-tick-collector
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager -l
```

建議用 `.venv` 或在 repo 根目錄執行 CLI，避免 `ModuleNotFoundError`：

```bash
cd /opt/futu_tick_downloader
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.cli.main db stats --day "$DAY"
sleep 10
/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.cli.main db stats --day "$DAY"
```

## 9. Telegram 互動驗收

啟用後重啟：

```bash
sudo systemctl restart hk-tick-collector
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager -l | grep -E "telegram_notifier_started|telegram_send_ok|telegram_send_failed|COMMAND"
```

群內測試：

- `/help`
- `/db_stats --day 20260220`
- `/top_symbols --limit 10 --minutes 15 --metric rows --day 20260220`
- `/symbol HK.00700 --last 20 --day 20260220`

## 10. 快速升級流程

```bash
cd /opt/futu_tick_downloader
sudo bash deploy/scripts/upgrade.sh
sudo systemctl restart futu-opend hk-tick-collector
sudo systemctl status futu-opend hk-tick-collector --no-pager
```

## 11. 安全建議

- SSH 僅金鑰登入，關閉密碼登入
- `/etc/hk-tick-collector.env` 維持 `640`
- 定期巡檢磁碟與 journald 大小
- 僅在必要時開放對外端口

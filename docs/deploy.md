# 部署與升級（systemd）

本文件提供 `hk-tick-collector` 的標準生產部署流程，目標是「可重複、可回滾、可快速驗證」。

## 1. 目錄與帳號

```bash
sudo useradd --system --home /opt/futu_tick_downloader --shell /usr/sbin/nologin hkcollector || true
sudo mkdir -p /opt/futu_tick_downloader /data/sqlite/HK
sudo chown -R hkcollector:hkcollector /opt/futu_tick_downloader /data/sqlite/HK
```

## 2. 安裝程式

```bash
sudo rsync -a --delete ./ /opt/futu_tick_downloader/
sudo -u hkcollector python3 -m venv /opt/futu_tick_downloader/.venv
sudo -u hkcollector /opt/futu_tick_downloader/.venv/bin/pip install -U pip
sudo -u hkcollector /opt/futu_tick_downloader/.venv/bin/pip install -e /opt/futu_tick_downloader
```

## 3. systemd Unit + EnvironmentFile

- Unit 範本：`/opt/futu_tick_downloader/deploy/systemd/hk-tick-collector.service`
- 建議 EnvironmentFile：`/etc/hk-tick-collector.env`

```bash
sudo cp /opt/futu_tick_downloader/deploy/systemd/hk-tick-collector.service /etc/systemd/system/hk-tick-collector.service
sudo cp /opt/futu_tick_downloader/.env.example /etc/hk-tick-collector.env
sudo chown root:hkcollector /etc/hk-tick-collector.env
sudo chmod 640 /etc/hk-tick-collector.env
```

編輯 `/etc/hk-tick-collector.env`（最少需填）：

```dotenv
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_SYMBOLS=HK.00700,HK.00981
DATA_ROOT=/data/sqlite/HK

TG_ENABLED=1
TG_BOT_TOKEN=<secret>
TG_CHAT_ID=-100xxxxxxxxxx
INSTANCE_ID=hk-prod-a1
```

## 4. 啟用與驗證

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

建議驗證：

```bash
scripts/hk-tickctl logs --since "10 minutes ago"
scripts/hk-tickctl db stats
```

## 5. 日常重啟

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

## 6. 升級流程（最小風險）

1. 先做 DB 快照（可回滾資料）

```bash
DAY=$(TZ=Asia/Hong_Kong date +%Y%m%d)
sqlite3 "/data/sqlite/HK/${DAY}.db" ".backup '/data/sqlite/HK/${DAY}.snapshot.db'"
```

2. 更新程式碼並重裝套件

```bash
sudo rsync -a --delete ./ /opt/futu_tick_downloader/
sudo -u hkcollector /opt/futu_tick_downloader/.venv/bin/pip install -e /opt/futu_tick_downloader
```

3. 重載 + 重啟 + 驗證

```bash
sudo systemctl daemon-reload
sudo systemctl restart hk-tick-collector
scripts/hk-tickctl logs --ops --since "15 minutes ago"
scripts/hk-tickctl db symbols --minutes 10
```

## 7. 快速回滾

1. 切回上一版程式碼
2. 重新安裝套件
3. `sudo systemctl restart hk-tick-collector`
4. 若資料異常，使用 `.snapshot.db` 先做只讀比對，不要直接覆蓋線上 DB


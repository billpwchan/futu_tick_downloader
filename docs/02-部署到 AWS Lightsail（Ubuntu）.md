# 02-部署到 AWS Lightsail（Ubuntu）

本文件提供從乾淨 Ubuntu 到 `systemctl status` healthy 的最短路徑。

## 1. 建立主機與安全組

- OS：Ubuntu 22.04 LTS
- 規格：至少 2 vCPU / 2GB RAM（symbol 多時建議 4GB）
- 金鑰（key pair）：建立新的 Lightsail SSH key，僅保存於你的本機安全位置
- 防火牆建議：
- `22/tcp`（SSH）僅允許你的固定來源 IP
- OpenD 與 collector 同機可不開 `11111` 對外

若資料量大，建議在這一步就掛載獨立磁碟到 `/data`（例如 ext4），避免系統磁碟被 WAL 檔案擠滿。

## 2. 初次登入與基礎套件

```bash
sudo apt-get update
sudo apt-get install -y git python3.11 python3.11-venv sqlite3 jq zstd
sudo timedatectl set-timezone Asia/Hong_Kong
```

驗證：

```bash
timedatectl status | grep 'Time zone'
python3.11 --version
sqlite3 --version
zstd --version
```

## 3. 拉專案與環境檔

```bash
cd /opt
sudo git clone https://github.com/billpwchan/futu_tick_downloader.git
cd futu_tick_downloader
sudo cp deploy/env/.env.example /etc/hk-tick-collector.env
sudo chown root:root /etc/hk-tick-collector.env
sudo chmod 640 /etc/hk-tick-collector.env
```

編輯必要變數：

- `FUTU_HOST`, `FUTU_PORT`, `FUTU_SYMBOLS`
- `DATA_ROOT`
- `TG_ENABLED`, `TG_TOKEN`, `TG_CHAT_ID`（可選）

## 4. 安裝 systemd 服務

```bash
cd /opt/futu_tick_downloader
sudo bash deploy/scripts/install.sh
```

驗證：

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo systemctl status futu-opend --no-pager || true
```

## 5. 部署後健康檢查

```bash
sudo bash deploy/scripts/status.sh
scripts/hk-tickctl status
scripts/hk-tickctl db stats
```

## 6. 升級流程

```bash
cd /opt/futu_tick_downloader
sudo bash deploy/scripts/upgrade.sh
```

## 7. （可選）設定每日 18:00 盤後歸檔

```bash
cd /opt/futu_tick_downloader
sudo cp examples/systemd/hk-tick-archive.service /etc/systemd/system/
sudo cp examples/systemd/hk-tick-archive.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hk-tick-archive.timer
sudo systemctl status hk-tick-archive.timer --no-pager
```

## 安全建議

- 金鑰登入 + 關閉密碼登入。
- `/etc/hk-tick-collector.env` 權限固定 `640`。
- DB 目錄獨立磁碟時，開機自動掛載並監控剩餘空間。

# 04-運維 Runbook

## 0) 先看整體狀態（3 分鐘版）

```bash
sudo systemctl status futu-opend hk-tick-collector --no-pager -l
sudo ss -lntp | grep -E 'FutuOpenD|:11111|:22222' || echo "NO_LISTEN"
DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
cd /opt/futu_tick_downloader
/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.cli.main db stats --day "${DAY}"
```

判斷順序：

1. OpenD 是否存活且有監聽 `11111/22222`
2. collector 是否持續跑（非 `start-limit-hit`）
3. DB rows 是否持續增加
4. Telegram 是否有 `telegram_send_ok`

## 1) 常用命令

```bash
make setup
make lint
make test
make run
make logs
make db-stats
scripts/hk-tickctl status --data-root /data/sqlite/HK --day 20260216
scripts/hk-tickctl validate --data-root /data/sqlite/HK --day 20260216 --regen-report 1
scripts/hk-tickctl archive --data-root /data/sqlite/HK --day 20260216 --verify 1
```

## 2) 健康巡檢

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo journalctl -u hk-tick-collector --since "15 minutes ago" --no-pager | tail -n 80
```

## 3) 重啟

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

若遇到 `start-limit-hit`：

```bash
sudo systemctl reset-failed hk-tick-collector
sudo systemctl restart hk-tick-collector
```

## 4) 盤後資料驗收（SOP）

```bash
DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
scripts/hk-tickctl validate --data-root /data/sqlite/HK --day "${DAY}" --regen-report 1 --strict 1
scripts/hk-tickctl export --data-root /data/sqlite/HK report --day "${DAY}" --out "/tmp/quality_${DAY}.json"
```

## 5) 盤後歸檔（SOP）

```bash
DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
scripts/hk-tickctl archive --data-root /data/sqlite/HK \
  --day "${DAY}" \
  --archive-dir /data/sqlite/HK/_archive \
  --keep-days 14 \
  --delete-original 1 \
  --verify 1
```

## 6) 每日自動化（systemd + 本地拉取）

若要啟用「16:30（UTC+8）服務器歸檔 + 17:10（UTC+8）本地拉取轉 zip」，請看：

- [`/docs/09-收盤後自動化（歸檔與本地拉取）.md`](09-%E6%94%B6%E7%9B%A4%E5%BE%8C%E8%87%AA%E5%8B%95%E5%8C%96%EF%BC%88%E6%AD%B8%E6%AA%94%E8%88%87%E6%9C%AC%E5%9C%B0%E6%8B%89%E5%8F%96%EF%BC%89.md)

## 7) 匯出資料給策略/研究

```bash
DAY=20260216
scripts/hk-tickctl export --data-root /data/sqlite/HK db --day "${DAY}" --out "/tmp/${DAY}.backup.db"
scripts/hk-tickctl export --data-root /data/sqlite/HK gaps --day "${DAY}" --out "/tmp/gaps_${DAY}.csv"
scripts/hk-tickctl export --data-root /data/sqlite/HK report --day "${DAY}" --out "/tmp/quality_${DAY}.json"
```

建議最少保留：

- 壓縮檔：`YYYYMMDD.db.zst`
- 校驗檔：`YYYYMMDD.db.zst.sha256`
- 品質報告：`quality YYYYMMDD.json`

## 8) 告警處理順序

1. 先看 `status` 判斷資料新鮮度與 gaps
2. 跑 `validate --strict 1` 看是否可用
3. 查 DB 是否仍成長
4. 再決定重啟或升級

Telegram 互動按鈕對應 SOP 請看：[`/docs/runbook/telegram-actions.md`](runbook/telegram-actions.md)

## 9) 常見故障對照表（實戰）

### A. `futu-opend.service status=217/USER`

原因：`User=futu` 不存在或憑證失效。  
處理：

```bash
sudo getent group futu >/dev/null || sudo groupadd --system futu
id -u futu >/dev/null 2>&1 || sudo useradd --system --gid futu --home-dir /opt/futu-opend --shell /usr/sbin/nologin futu
sudo systemctl daemon-reload
sudo systemctl restart futu-opend
```

### B. OpenD 存活但 `ECONNREFUSED` / `NO_LISTEN`

原因：未監聽、配置檔路徑錯、未完成登入驗證。  
處理：

```bash
sudo ss -lntp | grep -E 'FutuOpenD|:11111|:22222' || echo "NO_LISTEN"
sudo grep -nEi 'ip|api_port|telnet_ip|telnet_port' /opt/futu-opend/FutuOpenD.xml
sudo systemctl cat futu-opend
```

並確認 `ExecStart` 使用 `-c /opt/futu-opend/OpenD.xml -auto_hold_quote_right=1`。

### C. `PermissionError: /opt/futu_tick_downloader/.com.futunn.FutuOpenD`

原因：collector 執行帳號無法寫入專案目錄。  
處理：

```bash
sudo chown -R hkcollector:hkcollector /opt/futu_tick_downloader
```

### D. `sqlite3.OperationalError: attempt to write a readonly database`

原因：`DATA_ROOT` 或 `.db/.db-wal/.db-shm` 所有權錯。  
處理：

```bash
sudo chown -R hkcollector:hkcollector /data/sqlite/HK
sudo find /data/sqlite/HK -type d -exec chmod 750 {} \;
sudo find /data/sqlite/HK -type f -exec chmod 640 {} \;
```

### E. `scripts/hk-tickctl ... ModuleNotFoundError: hk_tick_collector`

原因：使用系統 `python3` 執行但未在 repo 根目錄或未裝可編輯套件。  
處理（推薦）：

```bash
cd /opt/futu_tick_downloader
/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.cli.main db stats --day "$(TZ=Asia/Hong_Kong date +%Y%m%d)"
```

### F. Telegram `400 can't parse entities: Unsupported start tag`

原因：HTML parse_mode 下未跳脫 `<...>`。  
處理：

- 升到包含跳脫修復的版本
- 自訂訊息請使用 HTML escape（如 `&lt;SYMBOL&gt;`）

### G. `/db_stats` 常逾時

處理：

```bash
sudo sed -n '1,200p' /etc/hk-tick-collector.env | grep -E '^TG_ACTION_COMMAND_TIMEOUT_SEC='
# 沒設就加大，例如 12 秒
echo 'TG_ACTION_COMMAND_TIMEOUT_SEC=12.0' | sudo tee -a /etc/hk-tick-collector.env
sudo systemctl restart hk-tick-collector
```

### H. `systemctl stop hk-tick-collector` 卡住

先看是否在等待 flush / I/O：

```bash
sudo systemctl status hk-tick-collector --no-pager -l
sudo journalctl -u hk-tick-collector -n 120 --no-pager -l
```

必要時可用：

```bash
sudo systemctl kill -s SIGINT hk-tick-collector
sudo systemctl stop hk-tick-collector
```

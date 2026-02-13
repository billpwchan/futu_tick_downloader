# 04-運維 Runbook

## 常用命令

```bash
make setup
make lint
make test
make run
make logs
make db-stats
scripts/hk-tickctl export --day 20260213 --out /tmp/hk-20260213.tar.gz
scripts/hk-tickctl tg test
```

## 健康巡檢

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo journalctl -u hk-tick-collector --since "15 minutes ago" --no-pager | tail -n 80
```

## 重啟

```bash
sudo systemctl restart hk-tick-collector
sudo systemctl status hk-tick-collector --no-pager
```

## 抓 DB 與空間清理

```bash
scripts/hk-tickctl export --day $(TZ=Asia/Hong_Kong date +%Y%m%d) --out /tmp/hk-latest.tar.gz
df -h
```

## 備份建議

- 每日收盤後匯出 `YYYYMMDD.db` + `sha256`
- 備份到 S3/物件儲存並保留至少 7~30 天

## 告警處理順序

1. 先看 `hk-tickctl status` 判斷是否停滯
2. 看 `logs --ops` 抓 `WATCHDOG` / `sqlite_busy`
3. 查 DB 是否仍成長
4. 再決定重啟或升級

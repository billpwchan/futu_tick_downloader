# 04-運維 Runbook

## 常用命令

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

## 盤後資料驗收（SOP）

```bash
DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
scripts/hk-tickctl validate --data-root /data/sqlite/HK --day "${DAY}" --regen-report 1 --strict 1
scripts/hk-tickctl export --data-root /data/sqlite/HK report --day "${DAY}" --out "/tmp/quality_${DAY}.json"
```

## 盤後歸檔（SOP）

```bash
DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
scripts/hk-tickctl archive --data-root /data/sqlite/HK \
  --day "${DAY}" \
  --archive-dir /data/sqlite/HK/_archive \
  --keep-days 14 \
  --delete-original 1 \
  --verify 1
```

## 每日自動化（systemd + 本地拉取）

若要啟用「16:30（UTC+8）服務器歸檔 + 17:10（UTC+8）本地拉取轉 zip」，請看：

- [`/docs/09-收盤後自動化（歸檔與本地拉取）.md`](09-%E6%94%B6%E7%9B%A4%E5%BE%8C%E8%87%AA%E5%8B%95%E5%8C%96%EF%BC%88%E6%AD%B8%E6%AA%94%E8%88%87%E6%9C%AC%E5%9C%B0%E6%8B%89%E5%8F%96%EF%BC%89.md)

## 匯出資料給策略/研究

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

## 告警處理順序

1. 先看 `status` 判斷資料新鮮度與 gaps
2. 跑 `validate --strict 1` 看是否可用
3. 查 DB 是否仍成長
4. 再決定重啟或升級

Telegram 互動按鈕對應 SOP 請看：[`/docs/runbook/telegram-actions.md`](runbook/telegram-actions.md)

# hk-tickctl 使用手冊

`hk-tickctl` 是給交易者 / 使用者快速判斷資料可用性的 CLI。

> 建議在專案根目錄執行（`/opt/futu_tick_downloader`），或直接用：
> `/opt/futu_tick_downloader/.venv/bin/python -m hk_tick_collector.cli.main ...`
> 以避免 `ModuleNotFoundError: hk_tick_collector`。

## 1) status

```bash
scripts/hk-tickctl status --data-root /data/sqlite/HK --day 20260216
```

輸出重點：

- DB 路徑與大小（含 WAL）
- ticks 總量
- 每個 symbol 最新時間與 `last_tick_age_sec`
- 當日 gaps 總數與最大 gap
- `hk-tick-collector` / `futu-opend` 服務狀態（可取得時）

## 2) validate

```bash
scripts/hk-tickctl validate --data-root /data/sqlite/HK \
  --day 20260216 \
  --regen-report 1 \
  --strict 1
```

驗證項目：

- DB 是否可開啟
- `ticks` 表是否存在且有資料
- `MAX(ts_ms)` 是否合理（不可明顯超前現在）
- `gaps` 摘要（若表存在）
- 產生/更新 `quality_report`

結果：

- `VALIDATE PASS` / `WARN` / `FAIL`
- 同步寫入 `quality_report` 的 `validate` 區塊

## 3) export

### export db（一致性 backup）

```bash
scripts/hk-tickctl export --data-root /data/sqlite/HK db --day 20260216 --out /tmp/20260216.backup.db
```

### export gaps（CSV）

```bash
scripts/hk-tickctl export --data-root /data/sqlite/HK gaps --day 20260216 --out /tmp/gaps_20260216.csv
```

### export report（JSON）

```bash
scripts/hk-tickctl export --data-root /data/sqlite/HK report --day 20260216 --out /tmp/quality_20260216.json
```

## 4) archive

```bash
scripts/hk-tickctl archive --data-root /data/sqlite/HK \
  --day 20260216 \
  --archive-dir /data/sqlite/HK/_archive \
  --keep-days 14 \
  --delete-original 1 \
  --verify 1
```

## 5) 舊版相容命令

- `scripts/hk-tickctl db stats`
- `scripts/hk-tickctl db symbols`
- `scripts/hk-tickctl db symbol HK.00700`
- `scripts/hk-tickctl db top-symbols --limit 10 --minutes 15 --metric rows`
- `scripts/hk-tickctl db top-symbols --day 20260216 --limit 10 --minutes 15 --metric rows`

# 10-手動批次拉回與轉 ZIP（低 CPU）

適用情境：

1. 不用定時器（systemd / launchd）
2. 每日收盤後手動批次處理
3. 避免壓縮打滿 CPU（不走 `archive` 的 `zstd -T0 -19` 路徑）

以下流程以交易日 `20260226`（2026-02-26）示例。

---

## A. 關閉自動化（只需做一次）

### 服務器

```bash
sudo systemctl disable --now hk-tick-eod-archive.timer 2>/dev/null || true
```

### 本地 macOS

```bash
launchctl unload ~/Library/LaunchAgents/com.billpwchan.hk-tick-pull.plist 2>/dev/null || true
```

---

## B. 服務器手動批次導出（低 CPU）

這一步只做 SQLite 一致性 backup，不做 zstd 壓縮。

```bash
set -euo pipefail

REPO="/opt/futu_tick_downloader"
DATA_ROOT="/data/sqlite/HK"
OUT="/home/ubuntu/hk_batch_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

for DAY in $(sudo find "$DATA_ROOT" -maxdepth 1 -type f -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db' -printf '%f\n' | sed 's/\.db$//' | sort); do
  echo "export $DAY ..."
  sudo env PYTHONPATH="$REPO" nice -n 10 "$REPO/.venv/bin/python" -m hk_tick_collector.cli.main \
    export --data-root "$DATA_ROOT" db --day "$DAY" --out "$OUT/${DAY}.backup.db"
done

sudo chown -R ubuntu:ubuntu "$OUT"
( cd "$OUT" && shasum -a 256 *.backup.db > SHA256SUMS )
echo "OUT=$OUT"
ls -lh "$OUT" | sed -n '1,20p'

```

---

## C. 本地一次性拉回（不打包 tar）

```bash
SERVER="ubuntu@<server-ip>"
OUT="/home/ubuntu/hk_batch_20260310_160440"    # 換成上一步輸出的 OUT
LOCAL_OUT="$HOME/Downloads/$(basename "$OUT")"
mkdir -p "$LOCAL_OUT"

scp -P 22 "$SERVER:$OUT/"'*.backup.db' "$SERVER:$OUT/SHA256SUMS" "$LOCAL_OUT/"
```

---

## D. 本地校驗

```bash
OUT="/home/ubuntu/hk_batch_20260310_160440"    # 換成你拉回的那批
cd "$HOME/Downloads/$(basename "$OUT")"
shasum -a 256 -c SHA256SUMS
```

---

## E. 批量轉成 Futu zip（YYYYMMDD.zip）

使用腳本：

- `/Users/billpwchan/Documents/futu_tick_downloader/scripts/convert_all_backup_to_futu_zip.command`

執行：

```bash
/Users/billpwchan/Documents/futu_tick_downloader/scripts/convert_all_backup_to_futu_zip.command \
  --input-dir ~/Downloads/hk_batch_20260310_160440 \
  --out-dir ~/Downloads/hk_batch_20260310_160440/zip_out \
  --compress-level 1
```

結果範例：

- `zip_out/20260213.zip`
- `zip_out/20260216.zip`
- `zip_out/20260226.zip`

---

## F. 服務器清理（確認本地已校驗與轉檔後）

先預覽：

```bash
sudo find /data/sqlite/HK -maxdepth 1 -type f \
  \( -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-wal' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-shm' \) -print
ls -ld /home/ubuntu/hk_batch_* 2>/dev/null || true
```

刪除批次導出檔：

```bash
rm -rf /home/ubuntu/hk_batch_*
```

刪除原始日庫：

```bash
sudo find /data/sqlite/HK -maxdepth 1 -type f \
  \( -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-wal' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-shm' \) -delete
```

---

## G. 每日最短操作清單

1. 跑 B（服務器批次導出）
2. 跑 C（直接 scp 全部 `.backup.db` + `SHA256SUMS`）
3. 跑 D（checksum）
4. 跑 E（批量轉 ZIP）
5. 跑 F（確認後清理）

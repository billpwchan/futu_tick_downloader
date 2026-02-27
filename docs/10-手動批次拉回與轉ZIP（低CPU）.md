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
  sudo nice -n 10 "$REPO/.venv/bin/python" -m hk_tick_collector.cli.main \
    export --data-root "$DATA_ROOT" db --day "$DAY" --out "$OUT/${DAY}.backup.db"
done

sudo chown -R ubuntu:ubuntu "$OUT"
( cd "$OUT" && shasum -a 256 *.backup.db > SHA256SUMS )
echo "OUT=$OUT"
ls -lh "$OUT" | sed -n '1,20p'
```

---

## C. 打包成單一檔（可選，方便一次 scp）

```bash
OUT="/home/ubuntu/hk_batch_YYYYMMDD_HHMMSS"   # 換成上一步輸出的 OUT
PACK="/home/ubuntu/$(basename "$OUT").tar"
tar -C /home/ubuntu -cf "$PACK" "$(basename "$OUT")"
ls -lh "$PACK"
```

---

## D. 本地一次性拉回

```bash
SERVER="ubuntu@<server-ip>"
scp -P 22 "$SERVER:/home/ubuntu/hk_batch_YYYYMMDD_HHMMSS.tar" ~/Downloads/
```

解包：

```bash
cd ~/Downloads
tar -xf hk_batch_YYYYMMDD_HHMMSS.tar
```

---

## E. 本地校驗

```bash
cd ~/Downloads/hk_batch_YYYYMMDD_HHMMSS
shasum -a 256 -c SHA256SUMS
```

---

## F. 批量轉成 Futu zip（YYYYMMDD.zip）

使用腳本：

- `/Users/billpwchan/Documents/futu_tick_downloader/scripts/convert_all_backup_to_futu_zip.command`

執行：

```bash
/Users/billpwchan/Documents/futu_tick_downloader/scripts/convert_all_backup_to_futu_zip.command \
  --input-dir ~/Downloads/hk_batch_YYYYMMDD_HHMMSS \
  --out-dir ~/Downloads/hk_batch_YYYYMMDD_HHMMSS/zip_out \
  --compress-level 1
```

結果範例：

- `zip_out/20260213.zip`
- `zip_out/20260216.zip`
- `zip_out/20260226.zip`

---

## G. 服務器清理（確認本地已校驗與轉檔後）

先預覽：

```bash
sudo find /data/sqlite/HK -maxdepth 1 -type f \
  \( -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-wal' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-shm' \) -print
ls -ld /home/ubuntu/hk_batch_* /home/ubuntu/hk_batch_*.tar 2>/dev/null || true
```

刪除批次導出檔：

```bash
rm -rf /home/ubuntu/hk_batch_*
rm -f /home/ubuntu/hk_batch_*.tar
```

刪除原始日庫：

```bash
sudo find /data/sqlite/HK -maxdepth 1 -type f \
  \( -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-wal' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-shm' \) -delete
```

---

## H. 每日最短操作清單

1. 跑 B（服務器批次導出）
2. 跑 C + D（一次 scp）
3. 跑 E（checksum）
4. 跑 F（批量轉 ZIP）
5. 跑 G（確認後清理）

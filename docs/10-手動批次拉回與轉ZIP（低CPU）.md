# 10-手動從服務器導出 backup 並同步到本地（低 CPU）

此流程供補救或歷史資料遷移使用。日常發布與本地鏡像請依[收盤後自動化](09-%E6%94%B6%E7%9B%A4%E5%BE%8C%E8%87%AA%E5%8B%95%E5%8C%96%EF%BC%88%E6%AD%B8%E6%AA%94%E8%88%87%E6%9C%AC%E5%9C%B0%E6%8B%89%E5%8F%96%EF%BC%89.md)。

適用情境：

1. 不用定時器（systemd / launchd）
2. 收盤後手動批次導出多個交易日
3. 不想額外打 tar，只想直接把整個 backup 目錄同步回本地
4. 避免壓縮打滿 CPU（不走 `archive` 的 `zstd -T0 -19` 路徑）

以下流程假設：

- 服務器 repo：`/opt/futu_tick_downloader`
- 服務器 SQLite 根目錄：`/data/sqlite/HK`
- 服務器登入帳號：`ubuntu`
- 本地下載目錄：`~/Downloads`

---

## A. 可選：一次性 SSH 設定（建議）

如果你有 Lightsail key，建議先放到 `~/.ssh`，後面 `scp` 會簡單很多。

```bash
mkdir -p ~/.ssh
mv ~/Downloads/LightsailDefaultKey-ap-northeast-1.pem ~/.ssh/lightsail-apne1.pem
chmod 600 ~/.ssh/lightsail-apne1.pem
```

可選：加入 `~/.ssh/config`

```sshconfig
Host lightsail-hk
  HostName <server-ip>
  User ubuntu
  IdentityFile ~/.ssh/lightsail-apne1.pem
  IdentitiesOnly yes
  Port 22
```

之後可直接用 `lightsail-hk` 當主機別名。

---

## B. 可選：先確認服務正常

導出前可先確認採集服務與 OpenD 都還活著。

```bash
sudo systemctl is-active --quiet hk-tick-collector futu-opend && \
  echo "OK: hk-tick-collector + futu-opend 都在運行" || \
  (echo "NOT OK"; sudo systemctl --no-pager -l status hk-tick-collector futu-opend | sed -n '1,80p')
```

---

## C. 服務器批次導出 backup（低 CPU）

這一步只做 SQLite 一致性 backup，不做 tar，不做 zstd。

重點：

1. 要用 `sudo env PYTHONPATH="$REPO"`，否則容易遇到 `ModuleNotFoundError: No module named 'hk_tick_collector'`
2. 輸出到 `/home/ubuntu/hk_batch_YYYYMMDD_HHMMSS`
3. 最後生成 `SHA256SUMS` 供本地校驗

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

成功後會看到類似：

```text
OUT=/home/ubuntu/hk_batch_20260310_160440
```

把這個 `OUT` 記下來，後面同步會用到。

---

## D. 同步到本地（推薦：整個目錄直接 scp）

### 方案 1：一次性帶 key 的 `scp -r`

`scp` 複製目錄一定要帶 `-r`，否則會報：

```text
scp: download ...: not a regular file
```

```bash
scp -i ~/.ssh/lightsail-apne1.pem -r \
  ubuntu@<server-ip>:/home/ubuntu/hk_batch_20260310_160440 \
  ~/Downloads/
```

### 方案 2：已配置 `~/.ssh/config`

```bash
scp -r lightsail-hk:/home/ubuntu/hk_batch_20260310_160440 ~/Downloads/
```

同步後，本地會得到：

```text
~/Downloads/hk_batch_20260310_160440
```

裡面包含：

- `*.backup.db`
- `SHA256SUMS`

---

## E. 本地校驗

```bash
cd ~/Downloads/hk_batch_20260310_160440
shasum -a 256 -c SHA256SUMS
```

理想結果是每個 `.backup.db` 都顯示 `OK`。

---

## F. 批量轉成 Futu zip（YYYYMMDD.zip）

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

## G. 服務器清理（確認本地已校驗與轉檔後）

先預覽：

```bash
sudo find /data/sqlite/HK -maxdepth 1 -type f \
  \( -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-wal' -o \
     -name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].db-shm' \) -print
ls -ld /home/ubuntu/hk_batch_* 2>/dev/null || true
df -h
```

只刪除本次批次導出目錄，將路徑替換為 C 步實際輸出的 `OUT`：

```bash
OUT=/home/ubuntu/hk_batch_20260310_160440
test -d "$OUT" && rm -r -- "$OUT"
```

原始日庫由 collector 的歸檔保留策略清理。手動批次導出與本地 ZIP 校驗並不代替 collector
歸檔校驗；不要直接刪除 `*.db`、`*.db-wal`、`*.db-shm`。

若要檢查歸檔與保留策略：

```bash
sudo systemctl status hk-tick-eod-archive.timer --no-pager
sudo journalctl -u hk-tick-eod-archive.service --since today --no-pager
```

如果歸檔清理後空間仍未回來，檢查 `df -h` 與 `sudo lsof +L1`。若有已刪除但仍被進程占用的檔案，
先確認進程和資料日期，再安排服務重啟。

---

## H. 每次最短操作清單

1. 在服務器跑 C，導出整批 `.backup.db`
2. 記下輸出的 `OUT=/home/ubuntu/hk_batch_YYYYMMDD_HHMMSS`
3. 在本地跑 D，用 `scp -r` 把整個目錄拉回來
4. 跑 E，確認 `SHA256SUMS` 全部通過
5. 跑 F，批量轉成 Futu zip
6. 確認本地結果無誤後，只清理本次批次導出目錄；原始日庫交由已驗證的歸檔策略處理

---

## I. 常見坑

### 1. `ModuleNotFoundError: No module named 'hk_tick_collector'`

原因：從 `~` 直接跑 `python -m hk_tick_collector.cli.main`，但 repo 沒有在 `PYTHONPATH`。

處理方式：照本文件的導出命令，使用：

```bash
sudo env PYTHONPATH="$REPO" "$REPO/.venv/bin/python" -m hk_tick_collector.cli.main ...
```

### 2. `scp: ... not a regular file`

原因：你在複製目錄，但沒加 `-r`。

正確方式：

```bash
scp -r ubuntu@<server-ip>:/home/ubuntu/hk_batch_20260310_160440 ~/Downloads/
```

### 3. 刪掉 `/home/ubuntu/hk_batch_*` 後空間沒有明顯下降

常見原因：

1. 真正佔空間的是別的目錄，例如 `/opt/futu_tick_downloader/.com.futunn.FutuOpenD/Log` 或 `/var/log/journal`
2. 檔案已刪除，但仍被進程占用

排查：

```bash
sudo du -xhd1 /home /data /var /opt 2>/dev/null | sort -h
sudo lsof +L1
```

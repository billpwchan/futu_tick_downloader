# 部署：Docker（選用）

## 目的

提供容器化環境的最小可行部署範例。

## 前置條件

- 你的環境明確需要容器化。
- 已有可用的 `hk-tick-collector:latest` 映像。

## 說明

Linux 主機生產環境仍以 `systemd` 為主要目標。只有在平台限制或既有平台規範要求時，才建議使用 Docker。

注意事項：

- OpenD 與 collector 可分開容器，但需驗證延遲與網路連通。
- `DATA_ROOT` 必須 bind mount 到持久儲存。
- 分析層請明確處理時區；collector 儲存的是 UTC epoch 毫秒。

## 步驟

最小範例：

```bash
docker run --rm \
  --network host \
  -e FUTU_HOST=127.0.0.1 \
  -e FUTU_PORT=11111 \
  -e FUTU_SYMBOLS=HK.00700 \
  -e DATA_ROOT=/data/sqlite/HK \
  -v /data/sqlite/HK:/data/sqlite/HK \
  hk-tick-collector:latest
```

## 如何驗證

- 容器內日誌可看到 `health` 與 `persist_summary`。
- 主機掛載目錄出現當日 `YYYYMMDD.db`。

## 常見問題

- 容器有跑但無資料：檢查 `FUTU_HOST/FUTU_PORT` 與 host 網路模式。
- DB 未落盤：檢查 volume mount 是否正確。

若為長期生產運行，除非平台已全面容器化，否則優先採 `systemd`。

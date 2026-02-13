# 支援說明（Support）

## 問題提問順序

1. 先看 `README.md` FAQ 與 `docs/_index.md`
2. 再看 `docs/04-運維 Runbook.md`
3. 仍未解決再發 Discussion / Issue

## 你該發在哪裡

- 使用與部署問題：GitHub Discussions
- 可重現程式缺陷：GitHub Issues（請用表單）
- 安全議題：依 `SECURITY.md` 私下通報

## 開 Issue 前請準備

- 部署型態（Docker / systemd）
- OS 與 Python 版本
- 專案版本（tag 或 commit SHA）
- 去敏後 `.env` 關鍵欄位
- 最近 10~30 分鐘日誌片段
- 最小重現步驟（可貼命令）

## 常用診斷命令

```bash
scripts/hk-tickctl status
scripts/hk-tickctl logs --ops --since "20 minutes ago"
scripts/hk-tickctl db stats
```

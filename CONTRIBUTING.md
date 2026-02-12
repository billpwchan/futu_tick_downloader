# 貢獻指南

感謝你參與 `hk-tick-collector`。

## 範圍

本專案是面向生產環境的採集服務，優先考量穩定性與執行期安全。

## 開發環境

```bash
git clone <YOUR_FORK_OR_REPO_URL>
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
```

## 程式碼風格與品質

送出 PR 前請執行：

```bash
pre-commit run -a
pytest -q
```

工具鏈：

- ruff（`ruff check`、`ruff format`）
- pytest（含 `pyproject.toml` 的 coverage 設定）

## 測試要求

- 任何會影響行為的變更，都要新增或更新測試。
- CI 測試不得依賴即時 Futu OpenD。
- 優先使用可重現測試與暫存 SQLite 檔案。

## 向後相容

- 預設情境下不得破壞既有執行期行為。
- 必須維持 `python -m hk_tick_collector.main` 生產入口可用。
- 新入口需採加法設計（例如 console script 別名）。

## Pull Request 流程

1. 使用小而可審閱的 commit。
2. 必要時更新文件與變更記錄（`CHANGELOG.md`）。
3. 依 PR 範本填寫測試證據與部署注意事項。
4. 確認 CI 全綠。

## Commit / 版本策略

- 發版遵循 SemVer。
- `CHANGELOG.md` 採 Keep a Changelog 格式。

## 回報問題

請使用 issue template，並盡量提供：

- 作業系統與 Python 版本
- 已去敏的環境設定
- 近期日誌（`journalctl` 節錄）
- DB 大小與 PRAGMA 資訊
- 可重現步驟

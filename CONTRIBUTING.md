# 貢獻指南（繁體中文主版）

感謝你願意改進 `HK Tick Collector`。

## 專案原則

- 不破壞現有功能：預設執行行為需維持相容。
- 文件同步：行為、部署、配置變更必須同步更新 docs。
- 可驗證：每個關鍵變更都要有可執行驗證命令。
- 不提交 secrets：僅維護 `.env.example` / `deploy/env/.env.example`。

## 開發環境

```bash
git clone https://github.com/billpwchan/futu_tick_downloader.git
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
```

## 本機工作流

```bash
make setup
make lint
make test
```

## 程式碼與測試要求

- 行為變更必須補測試（pytest）。
- `ruff check` 與 `ruff format --check` 必須通過。
- 新增命令或運維流程時，請同步更新：
- `README.md` 的「常用命令」
- `docs/04-運維 Runbook.md`

## Commit 與 PR 規範

1. 單一 PR 聚焦單一目的，避免混雜重構。
2. PR 描述需包含：動機、風險、測試證據、回滾方式。
3. 若有配置變更，請附 `.env` 升級說明。
4. 若有 schema / 索引變更，請附資料相容性說明。

## 版本與發版

- 採用 SemVer。
- 變更記錄採 Keep a Changelog（見 `CHANGELOG.md`）。
- 發版流程：`docs/08-發版流程.md`。

## 建議先讀

- 文件入口：`docs/_index.md`
- 開發者導覽：`docs/07-貢獻指南（開發者）.md`
- Runbook：`docs/04-運維 Runbook.md`

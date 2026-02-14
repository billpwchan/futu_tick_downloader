# 變更記錄

本檔案記錄本專案所有重要變更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

## [Unreleased]

### Added

- 建立標準 OSS 文件結構（`docs/00-總覽.md` 到 `docs/08-發版流程.md`）。
- 新增社群治理文件（`LICENSE`、`CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、`SECURITY.md`、`SUPPORT.md`、`CODEOWNERS`、`MAINTAINERS.md`）。
- 新增 GitHub templates 與 workflows。
- 透過 `pyproject.toml` 新增封裝 metadata 與 console script `hk-tick-collector`。
- 新增 pre-commit 與 Ruff 設定。
- 新增維運輔助腳本（`scripts/hk-tickctl`、`scripts/mock_tick_replay.py`）。
- 新增 entrypoint 測試與 watchdog fake-time regression 測試。
- 新增產品化文件入口與 00~08 文件體系（繁中主版）。
- 新增 `compose.yaml`、`Dockerfile`、`Makefile` 與 `scripts/mock_tick_replay.py` 本機一鍵體驗路徑。
- 新增 `deploy/env/.env.example` 與 `deploy/scripts/{install,upgrade,status}.sh` 伺服器部署工具鏈。
- 新增 `scripts/hk-tickctl` 子命令：`export`、`tg test`。
- 新增 `CITATION.cff` 與 `.github/FUNDING.yml`。
- 新增市場狀態判定模組（`hk_tick_collector/market_state.py`），支援交易時段與可配置休市日（`FUTU_HOLIDAYS` / `FUTU_HOLIDAY_FILE`）。
- 新增 poll 控制參數：`FUTU_POLL_TRADING_ONLY`、`FUTU_POLL_PREOPEN_ENABLED`、`FUTU_POLL_OFFHOURS_PROBE_INTERVAL_SEC`、`FUTU_POLL_OFFHOURS_PROBE_NUM`。

### Changed

- `README.md` 改寫為生產導向的開源上手與維運說明。
- 社群健康文件與 Issue/PR 模板改為繁中產品化格式。
- SQLite schema 初始化流程改為精簡路徑（僅建立必要表與索引，不再包含舊版自動 migration）。
- 服務啟動流程不再預先建立當日 SQLite DB，改為首筆資料落盤時建立（避免非交易日空 DB）。
- poll 迴圈改為「交易時段常規輪詢、非交易時段可選低頻 probe」。
- DB 讀取統計路徑改為唯讀查詢，不再在 health/stat 查詢時觸發 schema 確保流程。

### Removed

- 移除 legacy `README.zh-CN.md`，改為單一繁體中文入口（`README.md`）。
- 移除舊版文件路徑（`docs/deploy.md`、`docs/getting-started.md`、`docs/runbook.md` 等）與重複模板（`.github/PULL_REQUEST_TEMPLATE.md`）。
- 移除過時維運腳本（`scripts/install_systemd.sh`、`scripts/redeploy_hk_tick_collector.sh`、`scripts/verify_hk_tick_collector.sh` 等）。
- 移除未使用腳本（`scripts/repair_future_ts_ms.py`）與重複維護者檔（`MAINTAINERS.md`）。

### Compatibility

- 現有生產啟動路徑維持有效：`python -m hk_tick_collector.main`。
- 執行期資料語義不變（`ts_ms`、`recv_ts_ms` 仍為 UTC epoch ms）。

## [0.1.0] - 2026-02-11

### Added

- 首次公開釋出版基線，包含文件、CI、封裝與貢獻流程。

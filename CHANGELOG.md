# 變更記錄

本檔案記錄本專案所有重要變更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

## [Unreleased]

### Added

- 建立標準 OSS 文件結構（`docs/getting-started.md`、`docs/runbook/*`、`docs/troubleshooting.md`、`docs/faq.md`）。
- 新增社群治理文件（`LICENSE`、`CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、`SECURITY.md`、`SUPPORT.md`、`CODEOWNERS`、`MAINTAINERS.md`）。
- 新增 GitHub templates 與 workflows。
- 透過 `pyproject.toml` 新增封裝 metadata 與 console script `hk-tick-collector`。
- 新增 pre-commit 與 Ruff 設定。
- 新增維運輔助腳本（`scripts/db_health_check.sh`、`scripts/query_examples.sql`、`scripts/export_csv.py`）。
- 新增 entrypoint 測試與 watchdog fake-time regression 測試。

### Changed

- `README.md` 改寫為生產導向的開源上手與維運說明。

### Removed

- 移除 legacy `README.zh-CN.md`，改為單一繁體中文入口（`README.md`）。

### Compatibility

- 現有生產啟動路徑維持有效：`python -m hk_tick_collector.main`。
- 執行期資料語義不變（`ts_ms`、`recv_ts_ms` 仍為 UTC epoch ms）。

## [0.1.0] - 2026-02-11

### Added

- 首次公開釋出版基線，包含文件、CI、封裝與貢獻流程。

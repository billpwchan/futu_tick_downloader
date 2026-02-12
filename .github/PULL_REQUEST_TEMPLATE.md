## 摘要

-

## 變更內容

-

## 向後相容

- [ ] 預設執行期行為無破壞性變更
- [ ] 既有 systemd ExecStart 路徑仍可用（`python -m hk_tick_collector.main`）

## 測試計畫

- [ ] `pytest -q`
- [ ] `pre-commit run -a`
- [ ] （如適用）手動 `systemd` 驗證

## 維運檢查清單

- [ ] 若行為或可觀測性變更，已更新 docs/runbook
- [ ] 若設定變更，已更新 `.env.example`
- [ ] 若 schema 變更，已補 migration 說明

## 部署備註

-

# 發版流程

本專案遵循 SemVer 與 Keep a Changelog。

## 版本策略

- Patch（`x.y.Z`）：向後相容的修正／文件／工具更新
- Minor（`x.Y.z`）：向後相容的新功能或改進
- Major（`X.y.z`）：破壞性變更

## 發版步驟

1. 確認 `main` 在 CI 為綠燈。
2. 更新 `CHANGELOG.md` 的 `[Unreleased]`，準備下一版區塊。
3. 更新 `pyproject.toml` 與 `hk_tick_collector/__init__.py` 的版本號。
4. 本地執行檢查：

```bash
pre-commit run -a
pytest -q
```

5. 建立 commit 與 tag：

```bash
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

6. 依 changelog 在 GitHub 建立 release notes。

## 回滾

- 重新部署上一個 tag／commit
- 重啟服務
- 使用 `scripts/db_health_check.sh` 驗證 DB 新鮮度

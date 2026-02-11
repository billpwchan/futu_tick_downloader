# Releasing

This project follows SemVer and Keep a Changelog.

## Versioning Policy

- Patch (`x.y.Z`): backward-compatible fixes/docs/tooling updates
- Minor (`x.Y.z`): backward-compatible features/improvements
- Major (`X.y.z`): breaking changes

## Release Steps

1. ensure `main` is green in CI.
2. update `CHANGELOG.md` under `[Unreleased]` and prepare next version section.
3. bump version in `pyproject.toml` and `hk_tick_collector/__init__.py`.
4. run checks locally:

```bash
pre-commit run -a
pytest -q
```

5. commit and tag:

```bash
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

6. create GitHub release notes from changelog.

## Rollback

- redeploy previous tag/commit
- restart service
- verify DB freshness using `scripts/db_health_check.sh`

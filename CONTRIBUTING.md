# Contributing

Thanks for contributing to `hk-tick-collector`.

## Scope

This is a production-oriented collector service. Stability and runtime safety are prioritized.

## Development Setup

```bash
git clone <YOUR_FORK_OR_REPO_URL>
cd futu_tick_downloader
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .[dev]
```

## Code Style and Quality

Run before opening a PR:

```bash
pre-commit run -a
pytest -q
```

Tooling:

- ruff (`ruff check`, `ruff format`)
- pytest (+ coverage config in `pyproject.toml`)

## Testing Expectations

- Add or update tests for any behavior-impacting change.
- Do not require live Futu OpenD in CI tests.
- Prefer deterministic tests with temporary SQLite files.

## Backward Compatibility

- Do not break existing runtime behavior by default.
- Keep `python -m hk_tick_collector.main` production path working.
- New entrypoints should be additive (e.g., console script alias).

## Pull Request Process

1. Create small, reviewable commits.
2. Update docs and changelog (`CHANGELOG.md`) when needed.
3. Fill out PR template with test evidence and rollout notes.
4. Ensure CI is green.

## Commit/Versioning Policy

- SemVer is used for releases.
- Keep a Changelog format is used in `CHANGELOG.md`.

## Reporting Issues

Use issue templates and include:

- OS + Python version
- sanitized env config
- recent logs (`journalctl` snippet)
- DB size/PRAGMA details
- reproduction steps

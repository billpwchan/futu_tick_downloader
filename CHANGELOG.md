# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Canonical OSS docs structure (`docs/getting-started.md`, `docs/runbook/*`, `docs/troubleshooting.md`, `docs/faq.md`).
- Chinese README (`README.zh-CN.md`).
- Community files (`LICENSE`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `CODEOWNERS`, `MAINTAINERS.md`).
- GitHub templates and workflows.
- Packaging metadata via `pyproject.toml` with console script `hk-tick-collector`.
- Pre-commit and Ruff configuration.
- Operational helper scripts (`scripts/db_health_check.sh`, `scripts/query_examples.sql`, `scripts/export_csv.py`).
- Entrypoint test and watchdog fake-time regression test.

### Changed

- `README.md` rewritten for English-first open-source onboarding and production operations guidance.

### Compatibility

- Existing production run path remains valid: `python -m hk_tick_collector.main`.
- Runtime data semantics unchanged (`ts_ms` and `recv_ts_ms` stay UTC epoch ms).

## [0.1.0] - 2026-02-11

### Added

- Initial public release baseline with docs, CI, packaging, and contributor workflows.

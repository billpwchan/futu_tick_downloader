# Project Memory (Agents)

## 2026-02-11: OSS + release readiness baseline

### Scope

- Documentation structure aligned for public GitHub use:
  - `README.md`, `README.zh-CN.md`
  - canonical docs + runbooks under `docs/`
- Community health files and templates added:
  - `LICENSE`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `CODEOWNERS`, `MAINTAINERS.md`
  - `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`
- Packaging/tooling/CI added:
  - `pyproject.toml` (PEP 621 + console script)
  - `.pre-commit-config.yaml`
  - GitHub Actions CI and release workflows
  - `CHANGELOG.md`
- Ops examples added:
  - `scripts/db_health_check.sh`, `scripts/query_examples.sql`, `scripts/export_csv.py`

### Runtime safety

- No default runtime behavior change.
- Existing production entrypoint (`python -m hk_tick_collector.main`) intentionally unchanged.
- New command `hk-tick-collector` is additive.
- Timestamp semantics documented as:
  - `ticks.ts_ms` UTC epoch ms
  - `ticks.recv_ts_ms` UTC epoch ms

## 2026-02-11: doc/runbook baseline finalized

### Deliverables

- Added canonical docs:
  - `docs/configuration.md`
  - `docs/deployment/ubuntu-systemd.md`
  - `docs/operations/runbook-hk-tick-collector.md`
- Reworked `README.md` with quickstart + command cookbook + compatibility.
- Added ops helper scripts:
  - `scripts/verify_db.sh`
  - `scripts/tail_logs.sh`
  - `scripts/healthcheck.sh`
  - `scripts/install_systemd.sh`
- Added recommended unit template:
  - `deploy/systemd/hk-tick-collector.service`

### Minimal code fix

- Watchdog now honors `WATCHDOG_QUEUE_THRESHOLD_ROWS` (was previously unused).
- This reduces false positives on tiny/no backlog without changing data format.

### Tests added

- `tests/test_config.py`
- `tests/test_smoke_pipeline.py`
- `tests/test_mapping.py::test_parse_time_to_ts_ms_is_independent_from_system_tz`
- `tests/test_schema.py::test_connect_applies_sqlite_pragmas`
- `tests/test_futu_client.py` watchdog regression tests

## 2026-02-11: hk-tick-collector persistent stall + future ts_ms

### Failure signature

- （历史阶段）`WATCHDOG persistent_stall` recurring with systemd restart（旧版本曾为 `exit code 2`；当前实现为 `exit code 1`）。
- SQLite `MAX(ts_ms)` ahead of UTC now by about `+8h` (`~28800s`).
- Queue backlog persists while `persisted_rows_per_min=0`.

### Reliable checks

```bash
bash scripts/verify_hk_tick_collector.sh
journalctl -u hk-tick-collector --since \"30 minutes ago\" --no-pager | grep -E \"WATCHDOG|persist_loop_heartbeat|health|persist_ticks\"
```

### Permanent remediation summary

- Enforce HK-local-to-UTC conversion in mapping.
- Seed sequence from recent DB files by `max(seq)` (not `ts<=now`).
- Persist loop: exception traceback always on, connection reset + retry.
- Watchdog: heartbeat-based (`last_dequeue_monotonic`, `last_commit_monotonic`), self-heal first, exit only after repeated heal failures.
- Repair script for historical +8h rows:
  - `python3 scripts/repair_future_ts_ms.py --data-root /data/sqlite/HK --day <YYYYMMDD>`
- Manual rollback script:
  - `ROLLBACK_REF=<commit> bash scripts/rollback_hk_tick_collector.sh`

### Common mistakes

- Treating market-local tick time as UTC epoch source.
- Assuming `PRAGMA busy_timeout` is DB-persisted (it is connection-level).
- Triggering restart immediately without attempting writer recovery.

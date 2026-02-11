#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME=${SERVICE_NAME:-hk-tick-collector}
SINCE=${SINCE:-"10 minutes ago"}
FOLLOW=${FOLLOW:-1}
PATTERN=${PATTERN:-"health|persist_ticks|persist_loop_heartbeat|poll_stats|WATCHDOG|sqlite_busy|persist_flush_failed|ts_drift_warn|collector_stop_timeout"}

if ! command -v journalctl >/dev/null 2>&1; then
  echo "[FAIL] journalctl not found" >&2
  exit 1
fi

if [[ "${FOLLOW}" == "1" ]]; then
  journalctl -u "${SERVICE_NAME}" --since "${SINCE}" -f --no-pager | grep -E --line-buffered "${PATTERN}" || true
else
  journalctl -u "${SERVICE_NAME}" --since "${SINCE}" --no-pager | grep -E "${PATTERN}" || true
fi

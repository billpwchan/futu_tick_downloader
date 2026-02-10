#!/usr/bin/env bash
set -euo pipefail

log_step() {
  echo
  echo "[STEP] $*"
}

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

if [[ "${EUID}" -ne 0 ]]; then
  fail "please run as root"
fi

require_cmd git
require_cmd python3
require_cmd systemctl
require_cmd journalctl
require_cmd sqlite3

TARGET_DIR="${TARGET_DIR:-/opt/futu_tick_downloader}"
REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${ENV_FILE:-/etc/hk-tick-collector.env}"
LOG_SCAN_SECONDS="${LOG_SCAN_SECONDS:-120}"
DRIFT_MIN_SEC="${DRIFT_MIN_SEC:--5}"
DRIFT_MAX_SEC="${DRIFT_MAX_SEC:-30}"
RECENT_WINDOW_SEC="${RECENT_WINDOW_SEC:-600}"
RECENT_MAX_LAG_SEC="${RECENT_MAX_LAG_SEC:-120}"

log_step "Load environment"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck source=/dev/null
  set -a
  . "${ENV_FILE}"
  set +a
fi
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"

log_step "Sync repository at ${TARGET_DIR}"
if [[ -d "${TARGET_DIR}/.git" ]]; then
  git -C "${TARGET_DIR}" fetch --all --prune
  git -C "${TARGET_DIR}" checkout "${BRANCH}"
  git -C "${TARGET_DIR}" pull --ff-only origin "${BRANCH}"
else
  [[ -n "${REPO_URL}" ]] || fail "REPO_URL is required when ${TARGET_DIR} is not a git repo"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
fi

log_step "Install Python dependencies"
if [[ -f "${TARGET_DIR}/pyproject.toml" ]] && grep -q "^\[tool.poetry\]" "${TARGET_DIR}/pyproject.toml"; then
  require_cmd poetry
  (cd "${TARGET_DIR}" && poetry install --no-interaction --only main)
elif [[ -f "${TARGET_DIR}/requirements.txt" ]]; then
  python3 -m venv "${TARGET_DIR}/.venv"
  "${TARGET_DIR}/.venv/bin/pip" install --upgrade pip
  "${TARGET_DIR}/.venv/bin/pip" install -r "${TARGET_DIR}/requirements.txt"
else
  fail "no supported dependency manifest found (requirements.txt or poetry project)"
fi

log_step "Install systemd unit"
[[ -f "${TARGET_DIR}/ops/${SERVICE_NAME}.service" ]] || fail "service template missing: ${TARGET_DIR}/ops/${SERVICE_NAME}.service"
install -m 0644 "${TARGET_DIR}/ops/${SERVICE_NAME}.service" "${SERVICE_FILE}"
systemctl daemon-reload

log_step "Restart ${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'

log_step "Acceptance: drift / recent-10-min / top-gap"
TODAY_HK="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
DB_PATH="${DATA_ROOT}/${TODAY_HK}.db"
[[ -f "${DB_PATH}" ]] || fail "db not found: ${DB_PATH}"

python3 - <<PY
import sqlite3
import time
from datetime import datetime, timezone

db_path = "${DB_PATH}"
drift_min = float("${DRIFT_MIN_SEC}")
drift_max = float("${DRIFT_MAX_SEC}")
recent_window_sec = int("${RECENT_WINDOW_SEC}")
recent_max_lag_sec = int("${RECENT_MAX_LAG_SEC}")
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
try:
    now_ms = int(time.time() * 1000)
    max_ts = conn.execute("SELECT MAX(ts_ms) FROM ticks").fetchone()[0]
    if max_ts is None:
        raise SystemExit("no rows in ticks table")
    drift_sec = (now_ms - int(max_ts)) / 1000.0
    max_ts_utc = datetime.fromtimestamp(max_ts / 1000.0, tz=timezone.utc).isoformat()
    now_utc = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).isoformat()
    print(f"now_utc={now_utc}")
    print(f"max_ts_utc={max_ts_utc}")
    print(f"drift_sec={drift_sec:.3f}")
    if drift_sec < drift_min or drift_sec > drift_max:
        raise SystemExit(
            f"drift check failed: drift_sec={drift_sec:.3f}, expected in [{drift_min}, {drift_max}]"
        )

    row = conn.execute(
        "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) "
        f"FROM ticks WHERE ts_ms >= (strftime('%s','now') - {recent_window_sec}) * 1000"
    ).fetchone()
    n, min_ts, max_recent = row
    def fmt(ts):
        if ts is None:
            return "none"
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
    print(f"recent10m_count={n} min_utc={fmt(min_ts)} max_utc={fmt(max_recent)}")
    if n > 0:
        lower_bound_ms = now_ms - (recent_window_sec + recent_max_lag_sec) * 1000
        if min_ts < lower_bound_ms:
            raise SystemExit(
                "recent-window check failed: min_ts is older than allowed window "
                f"(min_ts={fmt(min_ts)} lower_bound={fmt(lower_bound_ms)})"
            )
        if max_recent > now_ms + 5000:
            raise SystemExit(
                "recent-window check failed: max_ts appears ahead of now "
                f"(max_ts={fmt(max_recent)} now={fmt(now_ms)})"
            )

    gap_rows = conn.execute(
        """
        WITH x AS (
          SELECT ts_ms, LAG(ts_ms) OVER (ORDER BY ts_ms) AS prev
          FROM ticks
          WHERE ts_ms >= (strftime('%s','now') - 86400) * 1000
        )
        SELECT ts_ms, prev, (ts_ms - prev) / 1000.0 AS gap_sec
        FROM x
        WHERE prev IS NOT NULL
        ORDER BY gap_sec DESC
        LIMIT 5
        """
    ).fetchall()
    print("top_gap_sec:")
    for ts_ms, prev, gap_sec in gap_rows:
        print(
            f"  gap_sec={gap_sec:.3f} "
            f"prev_utc={datetime.fromtimestamp(prev / 1000.0, tz=timezone.utc).isoformat()} "
            f"curr_utc={datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()}"
        )
finally:
    conn.close()
PY

log_step "Acceptance: monitor logs for ${LOG_SCAN_SECONDS}s"
SINCE_UTC="$(date -u +'%Y-%m-%d %H:%M:%S')"
sleep "${LOG_SCAN_SECONDS}"
LOG_OUTPUT="$(journalctl -u "${SERVICE_NAME}" --since "${SINCE_UTC}" --no-pager || true)"
echo "${LOG_OUTPUT}" | tail -n 80

if echo "${LOG_OUTPUT}" | grep -E "WATCHDOG persistent_stall|sqlite3\\.OperationalError|OperationalError" >/dev/null 2>&1; then
  fail "log acceptance failed: detected persistent_stall or OperationalError"
fi

echo
echo "[OK] redeploy and acceptance finished"

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
DEPLOY_REF="${DEPLOY_REF:-}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="${ENV_FILE:-/etc/hk-tick-collector.env}"
RUN_TESTS="${RUN_TESTS:-1}"
LOG_SCAN_SECONDS="${LOG_SCAN_SECONDS:-180}"
ROLLBACK_ON_FAIL="${ROLLBACK_ON_FAIL:-1}"
STRICT_ROWS_GROWTH="${STRICT_ROWS_GROWTH:-0}"
REPAIR_SCOPE="${REPAIR_SCOPE:-today}"   # today|all|db|off
REPAIR_DB="${REPAIR_DB:-}"
REPAIR_DAY="${REPAIR_DAY:-}"
REPAIR_FUTURE_THRESHOLD_HOURS="${REPAIR_FUTURE_THRESHOLD_HOURS:-2}"
REPAIR_SHIFT_HOURS="${REPAIR_SHIFT_HOURS:-8}"

PRE_DEPLOY_REF=""
ROLLED_BACK=0

rollback_if_needed() {
  local err_line="$1"
  if [[ "${ROLLBACK_ON_FAIL}" != "1" ]]; then
    echo "[ERROR] failed at line ${err_line}, rollback disabled"
    return
  fi
  if [[ "${ROLLED_BACK}" == "1" ]]; then
    return
  fi
  if [[ -z "${PRE_DEPLOY_REF}" ]]; then
    echo "[ERROR] failed at line ${err_line}, no previous git ref captured"
    return
  fi

  echo "[ROLLBACK] line=${err_line} ref=${PRE_DEPLOY_REF}"
  set +e
  systemctl stop "${SERVICE_NAME}"
  git -C "${TARGET_DIR}" checkout "${PRE_DEPLOY_REF}"
  if [[ -f "${TARGET_DIR}/requirements.txt" ]]; then
    python3 -m venv "${TARGET_DIR}/.venv"
    "${TARGET_DIR}/.venv/bin/pip" install --upgrade pip
    "${TARGET_DIR}/.venv/bin/pip" install -r "${TARGET_DIR}/requirements.txt"
  fi
  systemctl start "${SERVICE_NAME}"
  systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'
  set -e
  ROLLED_BACK=1
}

trap 'rollback_if_needed ${LINENO}' ERR

log_step "Load environment"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck source=/dev/null
  set -a
  . "${ENV_FILE}"
  set +a
fi
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
TODAY_HK="${TODAY_HK:-$(TZ=Asia/Hong_Kong date +%Y%m%d)}"
VERIFY_DB_PATH="${VERIFY_DB_PATH:-${DATA_ROOT}/${TODAY_HK}.db}"

log_step "Sync repository at ${TARGET_DIR}"
if [[ -d "${TARGET_DIR}/.git" ]]; then
  PRE_DEPLOY_REF="$(git -C "${TARGET_DIR}" rev-parse HEAD)"
  git -C "${TARGET_DIR}" fetch --all --prune
  if [[ -n "${DEPLOY_REF}" ]]; then
    git -C "${TARGET_DIR}" checkout "${DEPLOY_REF}"
  else
    git -C "${TARGET_DIR}" checkout "${BRANCH}"
    git -C "${TARGET_DIR}" pull --ff-only origin "${BRANCH}"
  fi
else
  [[ -n "${REPO_URL}" ]] || fail "REPO_URL is required when ${TARGET_DIR} is not a git repo"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${TARGET_DIR}"
  PRE_DEPLOY_REF=""
fi

deploy_ref_now="$(git -C "${TARGET_DIR}" rev-parse --short HEAD)"
echo "[INFO] deploy_ref=${deploy_ref_now}"

log_step "Stop ${SERVICE_NAME}"
systemctl stop "${SERVICE_NAME}" || true

log_step "Install Python dependencies"
if [[ -f "${TARGET_DIR}/pyproject.toml" ]] && grep -q "^\[tool.poetry\]" "${TARGET_DIR}/pyproject.toml"; then
  require_cmd poetry
  (cd "${TARGET_DIR}" && poetry install --no-interaction --only main)
elif [[ -f "${TARGET_DIR}/requirements.txt" ]]; then
  python3 -m venv "${TARGET_DIR}/.venv"
  "${TARGET_DIR}/.venv/bin/pip" install --upgrade pip
  "${TARGET_DIR}/.venv/bin/pip" install -r "${TARGET_DIR}/requirements.txt"
  if [[ -f "${TARGET_DIR}/requirements-dev.txt" ]]; then
    "${TARGET_DIR}/.venv/bin/pip" install -r "${TARGET_DIR}/requirements-dev.txt"
  fi
else
  fail "no supported dependency manifest found (requirements.txt or poetry project)"
fi

log_step "Install systemd unit"
UNIT_SOURCE="${TARGET_DIR}/deploy/systemd/${SERVICE_NAME}.service"
[[ -f "${UNIT_SOURCE}" ]] || fail "service template missing: ${UNIT_SOURCE}"
install -m 0644 "${UNIT_SOURCE}" "${SERVICE_FILE}"
systemctl daemon-reload

if [[ "${RUN_TESTS}" == "1" ]]; then
  log_step "Run tests"
  if [[ -x "${TARGET_DIR}/.venv/bin/pytest" ]]; then
    (cd "${TARGET_DIR}" && PYTHONPYCACHEPREFIX=/tmp/pycache "${TARGET_DIR}/.venv/bin/pytest" -q)
  else
    (cd "${TARGET_DIR}" && PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m pytest -q)
  fi
else
  echo "[INFO] skip tests (RUN_TESTS=${RUN_TESTS})"
fi

if [[ "${REPAIR_SCOPE}" != "off" ]]; then
  log_step "Repair future ts_ms"
  repair_args=(--data-root "${DATA_ROOT}" --future-threshold-hours "${REPAIR_FUTURE_THRESHOLD_HOURS}" --shift-hours "${REPAIR_SHIFT_HOURS}")
  case "${REPAIR_SCOPE}" in
    today)
      repair_day="${REPAIR_DAY:-${TODAY_HK}}"
      repair_args+=(--day "${repair_day}")
      ;;
    all)
      repair_args+=(--all-days)
      ;;
    db)
      [[ -n "${REPAIR_DB}" ]] || fail "REPAIR_DB is required when REPAIR_SCOPE=db"
      repair_args+=(--db "${REPAIR_DB}")
      ;;
    *)
      fail "unsupported REPAIR_SCOPE=${REPAIR_SCOPE} (expected: today|all|db|off)"
      ;;
  esac
  PYTHONPYCACHEPREFIX=/tmp/pycache python3 "${TARGET_DIR}/scripts/repair_future_ts_ms.py" "${repair_args[@]}"
else
  echo "[INFO] skip repair (REPAIR_SCOPE=off)"
fi

rows_before=0
if [[ -f "${VERIFY_DB_PATH}" ]]; then
  rows_before="$(sqlite3 "${VERIFY_DB_PATH}" "SELECT COUNT(*) FROM ticks;" 2>/dev/null || echo 0)"
fi
echo "[INFO] rows_before=${rows_before} db=${VERIFY_DB_PATH}"

log_step "Start ${SERVICE_NAME}"
systemctl start "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'

log_step "Collect health logs (${LOG_SCAN_SECONDS}s)"
SINCE_UTC="$(date -u +'%Y-%m-%d %H:%M:%S')"
sleep "${LOG_SCAN_SECONDS}"
LOG_OUTPUT="$(journalctl -u "${SERVICE_NAME}" --since "${SINCE_UTC}" --no-pager || true)"
echo "${LOG_OUTPUT}" | grep -E "health|persist_summary|persist_loop_heartbeat|WATCHDOG" | tail -n 120 || true

if echo "${LOG_OUTPUT}" | grep -q "WATCHDOG persistent_stall"; then
  fail "acceptance failed: detected WATCHDOG persistent_stall"
fi
if ! echo "${LOG_OUTPUT}" | grep -q "persist_loop_heartbeat"; then
  fail "acceptance failed: missing persist_loop_heartbeat logs"
fi

log_step "Run verify script"
SERVICE_NAME="${SERVICE_NAME}" DATA_ROOT="${DATA_ROOT}" DAY="${TODAY_HK}" DB_PATH="${VERIFY_DB_PATH}" \
  bash "${TARGET_DIR}/scripts/verify_hk_tick_collector.sh"

rows_after=0
if [[ -f "${VERIFY_DB_PATH}" ]]; then
  rows_after="$(sqlite3 "${VERIFY_DB_PATH}" "SELECT COUNT(*) FROM ticks;" 2>/dev/null || echo 0)"
fi
echo "[INFO] rows_after=${rows_after} db=${VERIFY_DB_PATH}"

if [[ "${STRICT_ROWS_GROWTH}" == "1" ]] && [[ "${rows_after}" -le "${rows_before}" ]]; then
  fail "acceptance failed: rows did not grow (rows_before=${rows_before}, rows_after=${rows_after})"
fi

echo
echo "[OK] redeploy finished deploy_ref=${deploy_ref_now} rows_before=${rows_before} rows_after=${rows_after}"
if [[ -n "${PRE_DEPLOY_REF}" ]]; then
  echo "[INFO] rollback_ref=${PRE_DEPLOY_REF}"
fi

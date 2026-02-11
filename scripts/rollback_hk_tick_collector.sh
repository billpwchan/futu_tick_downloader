#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${TARGET_DIR:-/opt/futu_tick_downloader}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
ROLLBACK_REF="${ROLLBACK_REF:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[FAIL] please run as root" >&2
  exit 1
fi

if [[ -z "${ROLLBACK_REF}" ]]; then
  echo "[FAIL] ROLLBACK_REF is required" >&2
  exit 1
fi

if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  echo "[FAIL] not a git repo: ${TARGET_DIR}" >&2
  exit 1
fi

echo "[STEP] stop ${SERVICE_NAME}"
systemctl stop "${SERVICE_NAME}" || true

echo "[STEP] checkout rollback ref ${ROLLBACK_REF}"
git -C "${TARGET_DIR}" checkout "${ROLLBACK_REF}"

if [[ -f "${TARGET_DIR}/requirements.txt" ]]; then
  echo "[STEP] reinstall runtime dependencies"
  python3 -m venv "${TARGET_DIR}/.venv"
  "${TARGET_DIR}/.venv/bin/pip" install --upgrade pip
  "${TARGET_DIR}/.venv/bin/pip" install -r "${TARGET_DIR}/requirements.txt"
fi

echo "[STEP] start ${SERVICE_NAME}"
systemctl start "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'

echo "[OK] rollback finished ref=${ROLLBACK_REF}"

#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "[FAIL] please run as root" >&2
  exit 1
fi

APP_DIR=${APP_DIR:-/opt/futu_tick_downloader}
RUN_USER=${RUN_USER:-hkcollector}
RUN_GROUP=${RUN_GROUP:-hkcollector}
DATA_ROOT=${DATA_ROOT:-/data/sqlite/HK}
ENV_FILE=${ENV_FILE:-/etc/hk-tick-collector.env}
SERVICE_NAME=${SERVICE_NAME:-hk-tick-collector}
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
UNIT_SRC=${UNIT_SRC:-${APP_DIR}/deploy/systemd/hk-tick-collector.service}
PYTHON_BIN=${PYTHON_BIN:-python3.11}
INSTALL_DEPS=${INSTALL_DEPS:-1}

if [[ ! -d "${APP_DIR}" ]]; then
  echo "[FAIL] app dir not found: ${APP_DIR}" >&2
  exit 1
fi
if [[ ! -f "${UNIT_SRC}" ]]; then
  echo "[FAIL] unit template not found: ${UNIT_SRC}" >&2
  exit 1
fi

if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${RUN_USER}"
fi

install -d -m 0750 -o "${RUN_USER}" -g "${RUN_GROUP}" "${DATA_ROOT}"

if [[ "${INSTALL_DEPS}" == "1" ]]; then
  "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
  "${APP_DIR}/.venv/bin/pip" install --upgrade pip
  "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0640 -o root -g "${RUN_GROUP}" "${APP_DIR}/.env.example" "${ENV_FILE}"
fi

install -m 0644 "${UNIT_SRC}" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "${SERVICE_FILE}"
fi

echo "[OK] installed ${SERVICE_NAME}.service"
echo "[INFO] env_file=${ENV_FILE}"
echo "[INFO] start with: sudo systemctl start ${SERVICE_NAME}.service"

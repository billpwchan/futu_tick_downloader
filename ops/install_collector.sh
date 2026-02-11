#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "[FAIL] please run as root" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${APP_DIR:-/opt/futu_tick_downloader}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
ENV_FILE="${ENV_FILE:-/etc/hk-tick-collector.env}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
RUN_USER="${RUN_USER:-hkcollector}"
RUN_GROUP="${RUN_GROUP:-hkcollector}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"

INSTALL_SCRIPT="${ROOT_DIR}/scripts/install_systemd.sh"
if [[ ! -f "${INSTALL_SCRIPT}" ]]; then
  echo "[FAIL] missing installer: ${INSTALL_SCRIPT}" >&2
  exit 1
fi

echo "[INFO] ops/install_collector.sh is a compatibility wrapper."
echo "[INFO] canonical installer: scripts/install_systemd.sh"

APP_DIR="${APP_DIR}" \
PYTHON_BIN="${PYTHON_BIN}" \
DATA_ROOT="${DATA_ROOT}" \
ENV_FILE="${ENV_FILE}" \
SERVICE_NAME="${SERVICE_NAME}" \
RUN_USER="${RUN_USER}" \
RUN_GROUP="${RUN_GROUP}" \
INSTALL_DEPS="${INSTALL_DEPS}" \
bash "${INSTALL_SCRIPT}"

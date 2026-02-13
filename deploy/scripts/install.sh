#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/futu_tick_downloader}"
RUN_USER="${RUN_USER:-hkcollector}"
RUN_GROUP="${RUN_GROUP:-hkcollector}"
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
ENV_FILE="${ENV_FILE:-/etc/hk-tick-collector.env}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
FUTU_SERVICE_NAME="${FUTU_SERVICE_NAME:-futu-opend}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[失敗] 請用 root 執行 install.sh" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
  echo "[失敗] 找不到 APP_DIR=${APP_DIR}" >&2
  exit 1
fi

if ! getent group "${RUN_GROUP}" >/dev/null 2>&1; then
  groupadd --system "${RUN_GROUP}"
fi

if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${RUN_GROUP}" --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${RUN_USER}"
fi

install -d -m 0750 -o "${RUN_USER}" -g "${RUN_GROUP}" "${DATA_ROOT}"

if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0640 -o root -g "${RUN_GROUP}" "${APP_DIR}/deploy/env/.env.example" "${ENV_FILE}"
  echo "[資訊] 已建立 ${ENV_FILE}（請先填入 FUTU/TG 設定）"
fi

"${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"

install -m 0644 "${APP_DIR}/deploy/systemd/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
if [[ -f "${APP_DIR}/deploy/systemd/${FUTU_SERVICE_NAME}.service" ]]; then
  install -m 0644 "${APP_DIR}/deploy/systemd/${FUTU_SERVICE_NAME}.service" "/etc/systemd/system/${FUTU_SERVICE_NAME}.service"
fi

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"
if systemctl list-unit-files | grep -q "^${FUTU_SERVICE_NAME}.service"; then
  systemctl enable --now "${FUTU_SERVICE_NAME}.service" || true
fi

echo "[完成] 安裝完成"
echo "[資訊] 查看狀態：systemctl status ${SERVICE_NAME} --no-pager"
echo "[資訊] 巡檢腳本：bash ${APP_DIR}/deploy/scripts/status.sh"

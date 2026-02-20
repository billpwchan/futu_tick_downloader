#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/futu_tick_downloader}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-eod-archive}"
ENV_FILE="${ENV_FILE:-/etc/hk-tick-eod-archive.env}"
RUN_USER="${RUN_USER:-hkcollector}"
RUN_GROUP="${RUN_GROUP:-hkcollector}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[失敗] 請用 root 執行 install_eod_archive.sh" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
  echo "[失敗] 找不到 APP_DIR=${APP_DIR}" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/deploy/systemd/${SERVICE_NAME}.service" ]]; then
  echo "[失敗] 找不到 systemd service template: ${SERVICE_NAME}.service" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/deploy/systemd/${SERVICE_NAME}.timer" ]]; then
  echo "[失敗] 找不到 systemd timer template: ${SERVICE_NAME}.timer" >&2
  exit 1
fi

if [[ ! -f "${APP_DIR}/deploy/scripts/eod_archive.sh" ]]; then
  echo "[失敗] 找不到腳本: deploy/scripts/eod_archive.sh" >&2
  exit 1
fi

if ! getent group "${RUN_GROUP}" >/dev/null 2>&1; then
  groupadd --system "${RUN_GROUP}"
fi

if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${RUN_GROUP}" --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${RUN_USER}"
fi

chmod 0755 "${APP_DIR}/deploy/scripts/eod_archive.sh"
install -m 0644 \
  "${APP_DIR}/deploy/systemd/${SERVICE_NAME}.service" \
  "/etc/systemd/system/${SERVICE_NAME}.service"
install -m 0644 \
  "${APP_DIR}/deploy/systemd/${SERVICE_NAME}.timer" \
  "/etc/systemd/system/${SERVICE_NAME}.timer"

if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0640 -o root -g "${RUN_GROUP}" \
    "${APP_DIR}/deploy/env/hk-tick-eod-archive.env.example" \
    "${ENV_FILE}"
  echo "[資訊] 已建立 ${ENV_FILE}，請先確認路徑設定"
fi

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.timer"

echo "[完成] 已啟用 ${SERVICE_NAME}.timer"
systemctl --no-pager --full status "${SERVICE_NAME}.timer" | sed -n '1,20p'

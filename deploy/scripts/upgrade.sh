#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/futu_tick_downloader}"
SERVICE_NAME="${SERVICE_NAME:-hk-tick-collector}"
BRANCH="${BRANCH:-main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[失敗] 請用 root 執行 upgrade.sh" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "[失敗] ${APP_DIR} 不是 git repository" >&2
  exit 1
fi

cd "${APP_DIR}"
BEFORE_REF="$(git rev-parse --short HEAD)"

echo "[步驟] 更新程式碼 (${BRANCH})"
git fetch --all --prune
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"
AFTER_REF="$(git rev-parse --short HEAD)"

echo "[步驟] 更新 Python 依賴"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"

echo "[步驟] 重啟服務"
systemctl restart "${SERVICE_NAME}.service"
systemctl --no-pager --full status "${SERVICE_NAME}.service" | sed -n '1,20p'

echo "[完成] ${SERVICE_NAME} 升級完成"
echo "[資訊] before=${BEFORE_REF} after=${AFTER_REF}"

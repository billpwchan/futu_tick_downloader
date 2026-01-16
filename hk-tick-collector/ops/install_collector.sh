#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "please run as root"
  exit 1
fi

APP_DIR=${APP_DIR:-/opt/hk-tick-collector}
PYTHON_BIN=${PYTHON_BIN:-python3.11}
DATA_ROOT=${DATA_ROOT:-/data/sqlite/HK}
ENV_FILE=${ENV_FILE:-/etc/hk-tick-collector.env}
SERVICE_FILE=/etc/systemd/system/hk-tick-collector.service

if [ ! -d "$APP_DIR" ]; then
  echo "repo not found at $APP_DIR"
  echo "copy this repo to $APP_DIR before running"
  exit 1
fi

if ! id -u hkcollector >/dev/null 2>&1; then
  useradd --system --home /opt/hk-tick-collector --shell /usr/sbin/nologin hkcollector
fi

$PYTHON_BIN -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

chown -R hkcollector:hkcollector "$APP_DIR"

install -d -m 0755 "$DATA_ROOT"
chown -R hkcollector:hkcollector "$DATA_ROOT"

if [ ! -f "$ENV_FILE" ]; then
  install -m 0640 "$APP_DIR/.env.example" "$ENV_FILE"
  chown root:hkcollector "$ENV_FILE"
fi

install -m 0644 "$APP_DIR/ops/hk-tick-collector.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable --now hk-tick-collector.service

echo "install complete"

#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/local_pull_convert.sh [--day YYYYMMDD] [--force 0|1]

Reads settings from:
  ~/.hk_tick_pull.env

Required env key:
  SSH_HOST=ubuntu@server-ip

Optional env keys:
  REMOTE_ARCHIVE_DIR=/data/sqlite/HK/_archive
  LOCAL_EXPORT_ROOT=$HOME/hk_tick_exports
  REPO_ROOT=/Users/you/path/to/futu_tick_downloader
  KEEP_LOCAL_DB=1
  PULL_WAIT_MINUTES=90
  PULL_RETRY_SEC=300
  SSH_PORT=22
EOF
}

DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
FORCE="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --day)
      DAY="${2:-}"
      shift 2
      ;;
    --force)
      FORCE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "${DAY}" =~ ^[0-9]{8}$ ]]; then
  echo "Invalid --day: ${DAY} (expected YYYYMMDD)" >&2
  exit 2
fi

if [[ "${FORCE}" != "0" && "${FORCE}" != "1" ]]; then
  echo "Invalid --force: ${FORCE} (expected 0 or 1)" >&2
  exit 2
fi

ENV_FILE="${HK_TICK_PULL_ENV:-$HOME/.hk_tick_pull.env}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  echo "Create it first. Example keys: SSH_HOST, REMOTE_ARCHIVE_DIR, LOCAL_EXPORT_ROOT, REPO_ROOT." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT_DEFAULT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SSH_HOST="${SSH_HOST:-}"
REMOTE_ARCHIVE_DIR="${REMOTE_ARCHIVE_DIR:-/data/sqlite/HK/_archive}"
LOCAL_EXPORT_ROOT="${LOCAL_EXPORT_ROOT:-$HOME/hk_tick_exports}"
REPO_ROOT="${REPO_ROOT:-${REPO_ROOT_DEFAULT}}"
KEEP_LOCAL_DB="${KEEP_LOCAL_DB:-1}"
PULL_WAIT_MINUTES="${PULL_WAIT_MINUTES:-90}"
PULL_RETRY_SEC="${PULL_RETRY_SEC:-300}"
SSH_PORT="${SSH_PORT:-22}"

if [[ -z "${SSH_HOST}" ]]; then
  echo "SSH_HOST is required in ${ENV_FILE}" >&2
  exit 1
fi

if [[ "${KEEP_LOCAL_DB}" != "0" && "${KEEP_LOCAL_DB}" != "1" ]]; then
  echo "Invalid KEEP_LOCAL_DB: ${KEEP_LOCAL_DB} (expected 0 or 1)" >&2
  exit 2
fi

if [[ ! "${PULL_WAIT_MINUTES}" =~ ^[0-9]+$ ]]; then
  echo "Invalid PULL_WAIT_MINUTES: ${PULL_WAIT_MINUTES}" >&2
  exit 2
fi

if [[ ! "${PULL_RETRY_SEC}" =~ ^[0-9]+$ ]] || [[ "${PULL_RETRY_SEC}" == "0" ]]; then
  echo "Invalid PULL_RETRY_SEC: ${PULL_RETRY_SEC}" >&2
  exit 2
fi

if [[ ! "${SSH_PORT}" =~ ^[0-9]+$ ]]; then
  echo "Invalid SSH_PORT: ${SSH_PORT}" >&2
  exit 2
fi

for bin in ssh scp shasum zstd python3; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "Required command not found: ${bin}" >&2
    exit 1
  fi
done

EXPORT_SCRIPT="${REPO_ROOT}/scripts/export_symbol_zip.py"
if [[ ! -f "${EXPORT_SCRIPT}" ]]; then
  echo "Cannot find export script: ${EXPORT_SCRIPT}" >&2
  exit 1
fi

LOCAL_DIR="${LOCAL_EXPORT_ROOT}/${DAY}"
ARCHIVE_NAME="${DAY}.db.zst"
CHECKSUM_NAME="${ARCHIVE_NAME}.sha256"
REMOTE_ARCHIVE_PATH="${REMOTE_ARCHIVE_DIR}/${ARCHIVE_NAME}"
REMOTE_CHECKSUM_PATH="${REMOTE_ARCHIVE_DIR}/${CHECKSUM_NAME}"
LOCAL_ARCHIVE_PATH="${LOCAL_DIR}/${ARCHIVE_NAME}"
LOCAL_CHECKSUM_PATH="${LOCAL_DIR}/${CHECKSUM_NAME}"
LOCAL_DB_PATH="${LOCAL_DIR}/${DAY}.backup.db"
LOCAL_ZIP_PATH="${LOCAL_DIR}/${DAY}.futu_csv.zip"
DONE_MARKER="${LOCAL_DIR}/${DAY}.done"

mkdir -p "${LOCAL_DIR}"

if [[ "${FORCE}" == "0" && -f "${DONE_MARKER}" && -f "${LOCAL_ZIP_PATH}" ]]; then
  echo "SKIP day=${DAY} reason=already_done zip=${LOCAL_ZIP_PATH}"
  exit 0
fi

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -p "${SSH_PORT}")
SCP_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -P "${SSH_PORT}")

remote_artifacts_exist() {
  ssh "${SSH_OPTS[@]}" "${SSH_HOST}" \
    "test -f '${REMOTE_ARCHIVE_PATH}' && test -f '${REMOTE_CHECKSUM_PATH}'" >/dev/null 2>&1
}

download_artifacts() {
  local tmp_archive="${LOCAL_ARCHIVE_PATH}.tmp"
  local tmp_checksum="${LOCAL_CHECKSUM_PATH}.tmp"

  rm -f "${tmp_archive}" "${tmp_checksum}"

  if ! scp "${SCP_OPTS[@]}" "${SSH_HOST}:${REMOTE_ARCHIVE_PATH}" "${tmp_archive}"; then
    rm -f "${tmp_archive}" "${tmp_checksum}"
    return 1
  fi
  if ! scp "${SCP_OPTS[@]}" "${SSH_HOST}:${REMOTE_CHECKSUM_PATH}" "${tmp_checksum}"; then
    rm -f "${tmp_archive}" "${tmp_checksum}"
    return 1
  fi

  mv "${tmp_archive}" "${LOCAL_ARCHIVE_PATH}"
  mv "${tmp_checksum}" "${LOCAL_CHECKSUM_PATH}"
  return 0
}

deadline_ts="$(( $(date +%s) + PULL_WAIT_MINUTES * 60 ))"
attempt=1
while true; do
  if remote_artifacts_exist && download_artifacts; then
    echo "READY day=${DAY} remote=${REMOTE_ARCHIVE_PATH}"
    break
  fi
  now_ts="$(date +%s)"
  if (( now_ts >= deadline_ts )); then
    echo "FAIL day=${DAY} reason=timeout_waiting_or_downloading_remote_archive wait_minutes=${PULL_WAIT_MINUTES}" >&2
    exit 1
  fi
  echo "WAIT day=${DAY} attempt=${attempt} retry_in=${PULL_RETRY_SEC}s reason=remote_not_ready_or_download_failed"
  attempt="$((attempt + 1))"
  sleep "${PULL_RETRY_SEC}"
done

echo "STEP day=${DAY} action=checksum_verify"
(
  cd "${LOCAL_DIR}"
  shasum -a 256 -c "${CHECKSUM_NAME}"
)

if [[ "${FORCE}" == "1" ]]; then
  rm -f "${LOCAL_DB_PATH}" "${LOCAL_ZIP_PATH}" "${DONE_MARKER}"
fi

echo "STEP day=${DAY} action=decompress"
zstd -d "${LOCAL_ARCHIVE_PATH}" -o "${LOCAL_DB_PATH}" -f

echo "STEP day=${DAY} action=convert_zip"
python3 "${EXPORT_SCRIPT}" --db "${LOCAL_DB_PATH}" --out "${LOCAL_ZIP_PATH}"

if [[ "${KEEP_LOCAL_DB}" == "0" ]]; then
  rm -f "${LOCAL_DB_PATH}"
fi

{
  echo "day=${DAY}"
  echo "created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "ssh_host=${SSH_HOST}"
  echo "remote_archive=${REMOTE_ARCHIVE_PATH}"
  echo "local_zip=${LOCAL_ZIP_PATH}"
  echo "keep_local_db=${KEEP_LOCAL_DB}"
} > "${DONE_MARKER}"

echo "OK day=${DAY} zip=${LOCAL_ZIP_PATH} done_marker=${DONE_MARKER}"

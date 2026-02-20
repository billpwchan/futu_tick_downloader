#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy/scripts/eod_archive.sh [--day YYYYMMDD]

Environment (optional):
  REPO_ROOT=/opt/futu_tick_downloader
  DATA_ROOT=/data/sqlite/HK
  ARCHIVE_DIR=/data/sqlite/HK/_archive
  RAW_KEEP_DAYS=3
  LOCK_FILE=/tmp/hk-tick-eod-archive.lock
  TZ_NAME=Asia/Hong_Kong
EOF
}

DAY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --day)
      DAY="${2:-}"
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

REPO_ROOT="${REPO_ROOT:-/opt/futu_tick_downloader}"
DATA_ROOT="${DATA_ROOT:-/data/sqlite/HK}"
ARCHIVE_DIR="${ARCHIVE_DIR:-${DATA_ROOT}/_archive}"
RAW_KEEP_DAYS="${RAW_KEEP_DAYS:-3}"
LOCK_FILE="${LOCK_FILE:-/tmp/hk-tick-eod-archive.lock}"
TZ_NAME="${TZ_NAME:-Asia/Hong_Kong}"

if [[ -z "${DAY}" ]]; then
  DAY="$(TZ="${TZ_NAME}" date +%Y%m%d)"
fi

if [[ ! "${DAY}" =~ ^[0-9]{8}$ ]]; then
  echo "Invalid --day: ${DAY} (expected YYYYMMDD)" >&2
  exit 2
fi

if [[ ! "${RAW_KEEP_DAYS}" =~ ^[0-9]+$ ]]; then
  echo "Invalid RAW_KEEP_DAYS: ${RAW_KEEP_DAYS}" >&2
  exit 2
fi

CTL="${REPO_ROOT}/scripts/hk-tickctl"
if [[ ! -x "${CTL}" ]]; then
  echo "hk-tickctl not found or not executable: ${CTL}" >&2
  exit 1
fi

DB_PATH="${DATA_ROOT}/${DAY}.db"
if [[ ! -f "${DB_PATH}" ]]; then
  echo "SKIP day=${DAY} reason=db_not_found db_path=${DB_PATH}"
  exit 0
fi

mkdir -p "$(dirname "${LOCK_FILE}")"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "SKIP day=${DAY} reason=lock_busy lock_file=${LOCK_FILE}"
  exit 0
fi

echo "RUN day=${DAY} data_root=${DATA_ROOT} archive_dir=${ARCHIVE_DIR} raw_keep_days=${RAW_KEEP_DAYS}"
"${CTL}" archive \
  --data-root "${DATA_ROOT}" \
  --day "${DAY}" \
  --archive-dir "${ARCHIVE_DIR}" \
  --keep-days "${RAW_KEEP_DAYS}" \
  --delete-original 1 \
  --verify 1
echo "DONE day=${DAY}"

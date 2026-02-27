#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Batch convert *.backup.db to YYYYMMDD.zip (Futu CSV zip format).

Usage:
  scripts/convert_all_backup_to_futu_zip.command [--input-dir PATH] [--out-dir PATH] [--compress-level 0-9]

Defaults:
  --input-dir      current directory
  --out-dir        <input-dir>/zip_out
  --compress-level 1

Examples:
  cd ~/Downloads/hk_batch_20260226 && /Users/billpwchan/Documents/futu_tick_downloader/scripts/convert_all_backup_to_futu_zip.command
  /Users/billpwchan/Documents/futu_tick_downloader/scripts/convert_all_backup_to_futu_zip.command --input-dir ~/Downloads/hk_batch_20260226 --out-dir ~/Downloads/hk_zip
EOF
}

INPUT_DIR="$(pwd)"
OUT_DIR=""
COMPRESS_LEVEL="1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-dir)
      INPUT_DIR="${2:-}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --compress-level)
      COMPRESS_LEVEL="${2:-}"
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

if [[ -z "${OUT_DIR}" ]]; then
  OUT_DIR="${INPUT_DIR}/zip_out"
fi

if [[ ! -d "${INPUT_DIR}" ]]; then
  echo "Input directory not found: ${INPUT_DIR}" >&2
  exit 1
fi

if [[ ! "${COMPRESS_LEVEL}" =~ ^[0-9]$ ]]; then
  echo "Invalid --compress-level: ${COMPRESS_LEVEL} (expected 0-9)" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXPORTER="${SCRIPT_DIR}/export_symbol_zip.py"

if [[ ! -f "${EXPORTER}" ]]; then
  echo "Exporter script not found: ${EXPORTER}" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi

shopt -s nullglob
DB_FILES=( "${INPUT_DIR}"/*.backup.db )
if (( ${#DB_FILES[@]} == 0 )); then
  echo "No .backup.db files found in ${INPUT_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

echo "INPUT_DIR=${INPUT_DIR}"
echo "OUT_DIR=${OUT_DIR}"
echo "FILES=${#DB_FILES[@]}"
echo ""

for db in "${DB_FILES[@]}"; do
  day="$(basename "${db}" .backup.db)"
  out_zip="${OUT_DIR}/${day}.zip"
  echo "[CONVERT] $(basename "${db}") -> $(basename "${out_zip}")"
  python3 "${EXPORTER}" \
    --db "${db}" \
    --out "${out_zip}" \
    --compress-level "${COMPRESS_LEVEL}"
done

echo ""
echo "DONE. Generated files:"
ls -lh "${OUT_DIR}"/*.zip

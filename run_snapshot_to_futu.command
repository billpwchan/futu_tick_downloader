#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNNER="${SCRIPT_DIR}/scripts/local_pull_convert.sh"

if [[ ! -x "${RUNNER}" ]]; then
  echo "Cannot execute ${RUNNER}"
  echo "Tip: run 'chmod +x ${RUNNER}' first."
  read -r -p "Press Enter to close..."
  exit 1
fi

DEFAULT_DAY="$(TZ=Asia/Hong_Kong date +%Y%m%d)"
DAY="${1:-}"
FORCE="0"

if [[ -z "${DAY}" ]]; then
  read -r -p "Trading day (YYYYMMDD) [${DEFAULT_DAY}]: " DAY_INPUT
  DAY="${DAY_INPUT:-${DEFAULT_DAY}}"
fi

if [[ ! "${DAY}" =~ ^[0-9]{8}$ ]]; then
  echo "Invalid day: ${DAY}"
  read -r -p "Press Enter to close..."
  exit 1
fi

read -r -p "Force rebuild if already done? [y/N]: " FORCE_INPUT
if [[ "${FORCE_INPUT:-N}" =~ ^[Yy]$ ]]; then
  FORCE="1"
fi

echo ""
echo "Running local pull + convert..."
echo "day=${DAY} force=${FORCE}"
echo ""

RESULT=0
if "${RUNNER}" --day "${DAY}" --force "${FORCE}"; then
  echo ""
  echo "Success."
else
  RESULT="$?"
  echo ""
  echo "Failed (exit=${RESULT})."
fi

read -r -p "Press Enter to close..."
exit "${RESULT}"

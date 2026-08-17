#!/usr/bin/env bash
# Telegram notice for a systemd unit that has entered the failed state.
#
# Wired via `OnFailure=` so it covers the one gap the in-process host monitor
# cannot: if the collector itself dies, whatever it was watching dies with it.
# systemd only reaches the failed state after Restart=always has exhausted
# StartLimitBurst, so this fires on a genuine crashloop rather than on a single
# blip.
#
# Deliberately bash + curl: this runs while the service is already broken, so
# it must not depend on the project's venv, its imports, or the SDK.
#
# Usage: notify_unit_failure.sh <unit-name>

set -uo pipefail

UNIT="${1:-hk-tick-collector.service}"
ENV_FILE="${ENV_FILE:-/etc/hk-tick-collector.env}"

if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

[[ "${TG_ENABLED:-0}" == "1" ]] || exit 0
[[ -n "${TG_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]] || {
  echo "notify_unit_failure: TG_TOKEN/TG_CHAT_ID missing, skipping" >&2
  exit 0
}

host="$(hostname 2>/dev/null || echo unknown)"
when="$(date -u '+%Y-%m-%d %H:%M:%SZ')"
state="$(systemctl show "${UNIT}" -p Result --value 2>/dev/null || echo unknown)"
nrestarts="$(systemctl show "${UNIT}" -p NRestarts --value 2>/dev/null || echo '?')"
# -o cat drops the syslog prefix so more of the useful tail fits in the message.
tail_log="$(journalctl -u "${UNIT}" -n 12 --no-pager -o cat 2>/dev/null | tail -c 900)"

text="$(cat <<EOF
🔴 ${UNIT} 已進入 failed 狀態

主機：${host}
時間：${when}
Result=${state}  NRestarts=${nrestarts}

systemd 已用盡自動重啟次數，採集目前是停止的。
在恢復前，主機層面的監控也不會有任何告警（它跑在這個行程內）。

最近日誌：
${tail_log}

建議：
  systemctl status ${UNIT}
  journalctl -u ${UNIT} -n 200 --no-pager
  systemctl reset-failed ${UNIT} && systemctl start ${UNIT}
EOF
)"

args=(
  --data-urlencode "chat_id=${TG_CHAT_ID}"
  --data-urlencode "text=${text}"
  --data-urlencode "disable_web_page_preview=true"
)
# Route to the ops topic when the group uses them; fall back to the default.
thread="${TG_THREAD_OPS_ID:-${TG_MESSAGE_THREAD_ID:-}}"
[[ -n "${thread}" ]] && args+=(--data-urlencode "message_thread_id=${thread}")

curl -sS -m 20 -o /dev/null \
  -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
  "${args[@]}" || {
  echo "notify_unit_failure: telegram send failed" >&2
  exit 0
}
exit 0

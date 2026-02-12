# Telegram Group Notification Setup

This project sends notifications with Telegram Bot API `sendMessage`.

Supported message types:

- `HEALTH` digest (default every 10 minutes, change-driven suppression)
- `ALERT` for key incidents (`PERSIST_STALL`, sqlite busy/locked spikes, fatal service exit)

## 1) Create a Bot (@BotFather)

1. Open Telegram and chat with [@BotFather](https://t.me/BotFather).
2. Run `/newbot`, follow prompts.
3. Save the token (format like `123456:ABC...`).

Security:

- keep token in private env/secrets only.
- never paste token in public issue/PR/chat logs.

## 2) Add Bot to Group

1. Add the bot user to your target group.
2. Grant permission to send messages.
3. If using forum topics, note the target topic/thread id (`message_thread_id`).

Privacy mode note:

- For this collector (send-only), privacy mode usually does not block sends.
- If you later rely on bot reading group messages, privacy mode may matter (`/setprivacy` in BotFather).

## 3) Find `chat_id` (2-3 methods)

## Method A: `getUpdates` (recommended)

1. Send one message in the group where bot is present.
2. Run:

```bash
TOKEN="<your_bot_token>"
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

3. Find `chat.id` in response (`-100...` for supergroup).

## Method B: Temporary local script

```python
import json
import urllib.request

TOKEN = "<your_bot_token>"
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
print(json.loads(urllib.request.urlopen(url, timeout=10).read().decode()))
```

## Method C: Bot-based lookup tools

Some helper bots can show chat ids after forwarding a group message; verify trust level before use.

## 4) Configure `.env`

```dotenv
TELEGRAM_ENABLED=1
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=
TELEGRAM_DIGEST_INTERVAL_SEC=600
TELEGRAM_ALERT_COOLDOWN_SEC=600
TELEGRAM_RATE_LIMIT_PER_MIN=18
TELEGRAM_INCLUDE_SYSTEM_METRICS=1
INSTANCE_ID=hk-prod-a1
```

Optional low-noise tuning:

```dotenv
TELEGRAM_DIGEST_QUEUE_CHANGE_PCT=20
TELEGRAM_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC=60
TELEGRAM_DIGEST_DRIFT_THRESHOLD_SEC=60
TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE=0
TELEGRAM_SQLITE_BUSY_ALERT_THRESHOLD=3
```

## 5) Common Errors

## `403 Forbidden`

Typical causes:

- bot not in group
- bot has no send permission
- group/channel policy blocks bot

Actions:

- verify bot membership and permission
- recheck `TELEGRAM_CHAT_ID`

## `400 Bad Request`

Typical causes:

- wrong `chat_id`
- wrong `message_thread_id`
- malformed payload

Actions:

- re-run `getUpdates` and copy exact ids
- clear `TELEGRAM_THREAD_ID` to validate base group send first

## `429 Too Many Requests`

Meaning:

- Telegram throttled requests. Response includes `retry_after`.

Collector behavior:

- notifier sleeps `retry_after` seconds then retries (bounded retries)
- local sender rate limiter caps total sends (`TELEGRAM_RATE_LIMIT_PER_MIN`)
- failures are logged but never block ingest/persist pipeline

## 6) Noise Tuning Cheat Sheet

- Reduce digest frequency: increase `TELEGRAM_DIGEST_INTERVAL_SEC` (e.g. `900`).
- Suppress idle alive message: keep `TELEGRAM_DIGEST_SEND_ALIVE_WHEN_IDLE=0` (default).
- Reduce repeated incident spam: increase `TELEGRAM_ALERT_COOLDOWN_SEC`.
- Keep Telegram headroom: keep `TELEGRAM_RATE_LIMIT_PER_MIN <= 18`.

## 7) Validation Checklist

1. Start/restart service:

```bash
sudo systemctl restart hk-tick-collector
```

2. Check logs for notifier startup and errors:

```bash
sudo journalctl -u hk-tick-collector --since "5 minutes ago" --no-pager \
  | grep -E "telegram|health|WATCHDOG|sqlite_busy"
```

3. Confirm group receives:

- one `HEALTH` digest at configured interval (default 600s)
- `ALERT` only on meaningful incidents

# Troubleshooting

## Service Not Starting

Checks:

```bash
sudo systemctl status hk-tick-collector --no-pager
sudo journalctl -u hk-tick-collector --since "10 minutes ago" --no-pager
```

Common causes:

- `FUTU_SYMBOLS` empty
- invalid numeric env value
- data directory permission denied

## OpenD Connectivity Fails

Symptoms:

- subscribe failures
- reconnect loop logs

Actions:

```bash
sudo systemctl status futu-opend --no-pager
nc -vz 127.0.0.1 11111
```

Verify `FUTU_HOST/FUTU_PORT` match OpenD settings.

## `WATCHDOG persistent_stall`

Use dedicated incident runbook:

- [`docs/runbook/incident-watchdog-stall.md`](runbook/incident-watchdog-stall.md)

## SQLite Busy / Locked

- check lock holders and retries
- inspect `sqlite_busy_backoff` frequency
- see [`docs/runbook/sqlite-wal.md`](runbook/sqlite-wal.md)

## WAL File Keeps Growing

- verify auto-checkpoint setting
- verify writer is still progressing
- run controlled checkpoint if required

## Timezone / Timestamp Confusion

- `ts_ms` is UTC epoch ms
- SQLite `datetime(...,'unixepoch')` renders UTC
- convert at query or visualization layer when local-time display is needed

See [`docs/runbook/data-quality.md`](runbook/data-quality.md).

# Deployment: Docker (Optional)

`systemd` on Linux hosts is the primary production target. Use Docker only when your environment requires containerization.

## Notes

- OpenD and collector can run in separate containers, but latency and networking should be validated.
- Bind mount `DATA_ROOT` to persistent storage.
- Keep timezone handling explicit in analytics; collector stores UTC epoch ms.

## Minimal Example

```bash
docker run --rm \
  --network host \
  -e FUTU_HOST=127.0.0.1 \
  -e FUTU_PORT=11111 \
  -e FUTU_SYMBOLS=HK.00700 \
  -e DATA_ROOT=/data/sqlite/HK \
  -v /data/sqlite/HK:/data/sqlite/HK \
  hk-tick-collector:latest
```

For long-running production environments, prefer `systemd` unless container orchestration is already standard in your platform.

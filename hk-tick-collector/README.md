# hk-tick-collector

HK tick-by-tick collector using Futu OpenAPI and SQLite with JChart-compatible schema.

## Quick Start
```bash
cp .env.example .env
# Edit .env and set HKTC_SYMBOLS=HK.00700,HK.09988 ...

docker compose up -d --build
```

## Configuration
- Default config: `config/collector.yaml`
- ENV overrides use prefix `HKTC_` (see `.env.example`)
- SQLite shards: `/data/sqlite/HK/YYYYMMDD.db`

## 2FA / Login
- First run may require OpenD login or 2FA
- Check OpenD logs and follow prompts:
  ```bash
  docker logs -f futu-opend
  ```

## Logs and Health
- Collector logs: `docker logs -f hk-tick-collector`
- OpenD logs: `docker logs -f futu-opend`
- Optional health check (inside container):
  ```bash
  docker exec -it hk-tick-collector python - <<'PY'
  import urllib.request, json
  print(json.loads(urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()))
  PY
  ```

## Verify Writes
```bash
ls /data/sqlite/HK
sqlite3 /data/sqlite/HK/$(date +%Y%m%d).db "select count(*) from ticks;"
```

## Repo Layout
- `hk_tick_collector/` core logic
- `config/collector.yaml` default config
- `docs/architecture.md` architecture and data flow
- `docs/runbook.md` operations guide

## Development
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check .
black --check .
```

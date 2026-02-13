.PHONY: setup lint test run run-mock logs down db-stats

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -e .[dev]

lint:
	ruff check .
	ruff format --check .

test:
	pytest -q

run:
	docker compose --profile opend up -d --build collector

run-mock:
	docker compose --profile mock up -d --build mock-replay

logs:
	docker compose logs -f --tail=200 collector mock-replay

down:
	docker compose down

db-stats:
	DATA_ROOT=./data/sqlite scripts/hk-tickctl db stats

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md requirements.txt requirements-dev.txt ./
COPY hk_tick_collector ./hk_tick_collector
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install -e .

CMD ["python", "-m", "hk_tick_collector.main"]

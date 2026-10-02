FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Fixed uid/gid so a host bind mount can be chowned to match (10001:10001).
RUN groupadd --system --gid 10001 hkcollector \
    && useradd --system --uid 10001 --gid hkcollector --create-home \
       --home-dir /home/hkcollector --shell /usr/sbin/nologin hkcollector

WORKDIR /app

COPY pyproject.toml README.md requirements.txt requirements-dev.txt ./
COPY hk_tick_collector ./hk_tick_collector
COPY scripts ./scripts

RUN pip install --upgrade pip \
    && pip install -e . \
    && install -d -m 0750 -o hkcollector -g hkcollector /data/sqlite/HK

# The Futu SDK writes its logs under $HOME/.com.futunn.FutuOpenD at import time.
USER hkcollector

CMD ["python", "-m", "hk_tick_collector.main"]

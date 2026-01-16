FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY hk_tick_collector /app/hk_tick_collector
COPY config /app/config

CMD ["python", "-m", "hk_tick_collector"]

FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY bot.py .

CMD ["python", "bot.py"]

# Scout — образ для деплоя (Railway / Fly.io / VPS)

FROM python:3.12-slim

# tgif/gif для Pillow (не обязателен, но полезен для превью в чатах)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# том подключается на стороне хостинга (Railway Volume /app/data)
ENV DATA_DIR=/app/data

EXPOSE 8080

# healthcheck: FastAPI /healthz
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8080/healthz || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

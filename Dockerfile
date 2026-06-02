FROM python:3.12-slim

WORKDIR /app

# Системные зависимости (минимум для healthcheck / requests)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Код приложения (UI = index.html внутри образа, без отдельной сборки)
COPY config.py database.py dashboard.py server.py manage_keys.py ./
COPY prompts/ ./prompts/
COPY guides/ ./guides/
COPY index.html ./

# Персистентные данные (БД, история, экспорт Figma)
RUN mkdir -p /app/data/references
ENV DATA_DIR=/app/data
VOLUME ["/app/data"]

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f "http://127.0.0.1:${PORT}/" || exit 1

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]

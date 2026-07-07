# Собирается из корня репозитория:
#   docker build -f images/Dockerfile -t litestar-grom:latest .
# или через docker-compose (context уже настроен в images/docker-compose.yml):
#   docker compose -f images/docker-compose.yml build

# ---- builder: ставим зависимости отдельным слоем, чтобы в финальном образе
# не оставался pip-кэш и build-инструменты ----
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- runtime ----
FROM python:3.12-slim

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app

WORKDIR /app

COPY --from=builder /root/.local /home/app/.local

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Только то, что реально нужно в рантайме — без tests/, venv/, .idea/, docx и т.д.
# (полный список исключений см. images/Dockerfile.dockerignore)
COPY app.py config.py db_manager.py logging_config.py middleware.py models.py parser_storage.py schemas.py sql_utils.py ./
COPY controllers ./controllers
COPY templates ./templates
COPY static ./static

# parser_data/ и log/ создаёт сам код (parser_storage.py, mkdir(exist_ok=True)),
# но директории нужно завести заранее, чтобы ими владел non-root пользователь app
RUN mkdir -p parser_data log \
    && chown -R app:app /app /home/app/.local

USER app

EXPOSE 8011

# Секреты (DB_*, SESSION_SECRET) в образ не закладываются — передаются переменными
# окружения при запуске (см. docker-compose.yml, env_file: ../.env)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/', timeout=3)" || exit 1

# ${SERVER_PORT} читается из окружения контейнера (см. config.py); по умолчанию 8011
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${SERVER_PORT:-8011}"]

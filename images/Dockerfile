# Built from the repository root:
#   docker build -f images/Dockerfile -t litestar-grom:latest .
# or through docker-compose (the context is already set in images/docker-compose.yml):
#   docker compose -f images/docker-compose.yml build

# ---- builder: install the dependencies as a separate layer so the final image
# carries neither the pip cache nor the build tools ----
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

# Only what is genuinely needed at runtime — no tests/, venv/, .idea/, docx and so on
# (see images/Dockerfile.dockerignore for the full exclusion list)
COPY app.py config.py db_manager.py db_profiles.py jira_client.py logging_config.py middleware.py models.py parser_storage.py schemas.py sql_utils.py ./
COPY controllers ./controllers
COPY templates ./templates
COPY static ./static

# parser_data/, log/ and config_data/ are created by the code itself
# (parser_storage.py, db_profiles.py — mkdir(exist_ok=True)), but the directories
# must exist up front so the non-root user app owns them
RUN mkdir -p parser_data log config_data \
    && chown -R app:app /app /home/app/.local

USER app

EXPOSE 8011

# Secrets (DB_*, SESSION_SECRET) are not baked into the image — they are passed as
# environment variables at run time (see docker-compose.yml, env_file: ../.env)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/', timeout=3)" || exit 1

# ${SERVER_PORT} is read from the container environment (see config.py); defaults to 8011
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${SERVER_PORT:-8011}"]

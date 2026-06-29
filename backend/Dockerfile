# syntax=docker/dockerfile:1.7
# Backend (FastAPI + ansible-core + ansible-runner) — 07-deployment.md#backend-образ.
# Multi-stage: deps в builder (uv), минимальный non-root runtime.
#
# Pinning-политика (единая для всех образов проекта): базовые образы закреплены
# ровно на тегах из docs/02-tech-stack.md, без :latest и без лишних суффиксов.
#   backend → python:3.12-slim · proxy → node:20-alpine + nginx:1.27-alpine
#   postgres:16 · prom/prometheus:v2.54.1 · grafana/grafana:11.2.2 · uv 0.5.11

# --- builder: установка зависимостей в /app/.venv через uv (uv.lock, --frozen) ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_PYTHON=python3.12 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.5.11

WORKDIR /app

# Сначала только манифесты — слой кэшируется, пока не менялись зависимости.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- runtime: минимальный образ, non-root, ssh-клиент для ansible (09-provisioning.md) ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    FILE_SD_DIR=/etc/prometheus/targets

# openssh-client + sshpass — Ansible ходит по SSH с password-аутентификацией
# (ansible_password): без sshpass ssh-коннектор падает с "you must install the
# sshpass program" (09-provisioning.md). ca-certificates — TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client sshpass ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --no-create-home --home-dir /app app

WORKDIR /app

# Виртуальное окружение из builder.
COPY --from=builder /app/.venv /app/.venv

# Код приложения, миграции, плейбуки (контекст сборки = ./backend).
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY ansible ./ansible

# Каталог file_sd создаём с владельцем app: при первом монтировании пустого
# named-volume Docker наследует эти права (backend пишет targets/<id>.json).
RUN mkdir -p "$FILE_SD_DIR" && chown -R app:app /app "$FILE_SD_DIR"

USER app

EXPOSE 8000

# /api/health отдаёт 200 даже при degraded (health.py) — годится для readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"]

# Миграции применяются в entrypoint — единый источник (07-deployment.md#порядок-запуска, NFR-1).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]

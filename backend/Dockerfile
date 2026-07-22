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
    FILE_SD_DIR=/etc/prometheus/targets \
    DOCUMENTS_ATTACHMENTS_DIR=/var/lib/crm/documents/attachments \
    ANSIBLE_PRIVATE_DATA_ROOT=/var/run/crm/ansible

# openssh-client + sshpass — Ansible ходит по SSH с password-аутентификацией
# (ansible_password): без sshpass ssh-коннектор падает с "you must install the
# sshpass program" (09-provisioning.md). ca-certificates — TLS.
#
# ⚠️ uid/gid пользователя `app` ЗАПИНЕНЫ (999:999) — НЕ менять и не убирать.
# На volume'ах и в tar-бэкапах хранится ЧИСЛОВОЙ uid, а не имя. У documents-attachments
# права 0700/0600 (единственный читатель), поэтому сдвиг uid делает уже лежащие вложения
# нечитаемыми для backend — все картинки в документах молча ломаются, и восстановление
# из бэкапа воспроизводит СТАРЫЙ uid, т.е. само не чинит. Без --uid/--gid значение
# выбирал бы `useradd --system` динамически (сверху вниз от 999), а тег python:3.12-slim
# мутабелен и apt может добавить своих системных пользователей ⇒ CI-пересборка при любом
# деплое могла бы сдвинуть распределение. 999 — не произвольное число, а ЗАФИКСИРОВАННОЕ
# де-факто значение уже развёрнутых образов (проверено: id app → uid=999(app)); брать
# другое нельзя без перечиповки существующих volume'ов (file_sd, documents-attachments).
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client sshpass ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 999 app \
    && useradd --system --uid 999 --gid app --no-create-home --home-dir /app app

WORKDIR /app

# Виртуальное окружение из builder.
COPY --from=builder /app/.venv /app/.venv

# Код приложения, миграции, плейбуки (контекст сборки = ./backend).
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY ansible ./ansible

# Каталоги, в которые пишет backend, создаём в образе с владельцем app —
# 07-deployment.md#каталоги-создаваемые-в-образе-нормативно. Образ работает под
# non-root (USER app), а Docker монтирует ПУСТОЙ named-volume и tmpfs под root:root;
# права из образа наследуются пустым volume только при первом монтировании — без
# mkdir+chown первая же запись падает с PermissionError.
#   FILE_SD_DIR                — targets/<id>.json; ВТОРОЙ читатель (prometheus, :ro)
#                                под другим uid ⇒ режим по умолчанию (0755/0644).
#   DOCUMENTS_ATTACHMENTS_DIR  — точка монтирования volume documents-attachments
#                                (ADR-068 §8): монтируется ТОЛЬКО в backend, читатель
#                                один ⇒ 0700 (каталоги) / 0600 (файлы). ⛔ НЕ приводить
#                                «к единому виду» с file_sd — это сделало бы приватные
#                                изображения world-readable внутри контейнера.
#                                Шард-каталоги <aa>/<bb> backend создаёт сам.
#   ANSIBLE_PRIVATE_DATA_ROOT  — корень private_data_dir ansible-runner (ADR-067 §5):
#                                tempfile.mkdtemp(dir=<этот каталог>). 0700 — рабочий
#                                fallback, если tmpfs-mount не задан; в проде каталог
#                                перекрыт tmpfs с mode: 0o1777 (infra/docker-compose.yml).
RUN mkdir -p "$FILE_SD_DIR" "$DOCUMENTS_ATTACHMENTS_DIR" "$ANSIBLE_PRIVATE_DATA_ROOT" \
    && chown -R app:app /app "$FILE_SD_DIR" "$DOCUMENTS_ATTACHMENTS_DIR" "$ANSIBLE_PRIVATE_DATA_ROOT" \
    && chmod 0700 "$DOCUMENTS_ATTACHMENTS_DIR" "$ANSIBLE_PRIVATE_DATA_ROOT"

USER app

EXPOSE 8000

# /api/health отдаёт 200 даже при degraded (health.py) — годится для readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"]

# Миграции применяются в entrypoint — единый источник (07-deployment.md#порядок-запуска, NFR-1).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]

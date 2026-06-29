#!/usr/bin/env bash
# ============================================================================
# LEGACY / ОПЦИОНАЛЬНЫЙ ручной инструмент. НЕ используется в CI.
# ----------------------------------------------------------------------------
# Основной деплой/откат — через GitHub Actions + docker compose на сервере
# (07-deployment.md#cicd, #откат-и-восстановление). Этот скрипт — ручной откат
# на ранее собранный локальный тег (:previous / git-SHA), парный к legacy
# deploy.sh. CI его НЕ вызывает.
# ============================================================================
# Откат приложения на предыдущий тег (07-deployment.md#откат-и-восстановление).
# Использование:
#   infra/scripts/rollback.sh                 # откат backend+proxy на :previous
#   infra/scripts/rollback.sh <тег>           # откат на конкретный тег (git-SHA/semver)
#   infra/scripts/rollback.sh previous --db   # + откат миграции на одну ревизию
#
# Для деплоя С миграциями: сначала остановить backend, затем downgrade либо restore
# из pre-deploy бэкапа (см. «Откат миграций БД» в docs), потом поднять предыдущий тег.
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/infra/docker-compose.yml")

TAG="${1:-previous}"
DB_DOWNGRADE="false"
[ "${2:-}" = "--db" ] && DB_DOWNGRADE="true"

if ! docker image inspect "crm-backend:$TAG" >/dev/null 2>&1; then
  echo "[rollback] ОШИБКА: образ crm-backend:$TAG не найден локально" >&2
  exit 1
fi

if [ "$DB_DOWNGRADE" = "true" ]; then
  echo "[rollback] останавливаю backend и откатываю миграцию на одну ревизию"
  "${COMPOSE[@]}" stop backend
  # downgrade ОБЯЗАН выполняться ТЕКУЩИМ (:current) образом — именно он содержит
  # функцию downgrade() новой ревизии. После него БД на предыдущей ревизии, и
  # entrypoint предыдущего образа (alembic upgrade head) станет no-op.
  IMAGE_TAG="current" "${COMPOSE[@]}" run --rm backend alembic downgrade -1
fi

echo "[rollback] поднимаю backend+proxy на теге :$TAG"
IMAGE_TAG="$TAG" "${COMPOSE[@]}" up -d backend proxy

echo "[rollback] готово. Проверьте: curl -k https://localhost/api/health"

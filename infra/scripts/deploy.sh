#!/usr/bin/env bash
# ============================================================================
# LEGACY / ОПЦИОНАЛЬНЫЙ ручной инструмент. НЕ используется в CI.
# ----------------------------------------------------------------------------
# Основной деплой — GitHub Actions (.github/workflows/ci.yml): rsync рабочего
# дерева на сервер + `docker compose --env-file .env -f infra/docker-compose.yml
# up -d --build` (образы собираются на сервере как crm-*:current). См.
# 07-deployment.md#cicd.
#
# Этот скрипт — необязательный ручной поток с git-SHA тегированием и алиасами
# :current/:previous для локального single-host (без registry, Q-DEP-2). CI его
# НЕ вызывает. Используйте только для ручных экспериментов/локального отката.
# Откат — rollback.sh (07-deployment.md#откат-и-восстановление).
# ============================================================================
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/infra/docker-compose.yml")
GIT_SHA="$(git -C "$ROOT" rev-parse --short HEAD)"

echo "[deploy] git-SHA=$GIT_SHA"

# 1. Pre-deploy бэкап Postgres (страховка для отката через restore).
"$ROOT/infra/scripts/backup-db.sh"

# 2. Сохранить текущие образы как :previous (если уже деплоились).
for svc in crm-backend crm-proxy; do
  if docker image inspect "$svc:current" >/dev/null 2>&1; then
    docker tag "$svc:current" "$svc:previous"
    echo "[deploy] $svc:current → $svc:previous (точка отката сохранена)"
  fi
done

# 3. Собрать новые образы с тегом git-SHA.
IMAGE_TAG="$GIT_SHA" "${COMPOSE[@]}" build

# 4. Назначить свежесобранные образы алиасом :current.
docker tag "crm-backend:$GIT_SHA" crm-backend:current
docker tag "crm-proxy:$GIT_SHA"   crm-proxy:current

# 5. Поднять стек на :current (backend сам применит alembic upgrade head).
IMAGE_TAG="current" "${COMPOSE[@]}" up -d

echo "[deploy] готово. Проверьте: curl -k https://localhost/api/health"
echo "[deploy] откат при неудаче: infra/scripts/rollback.sh"

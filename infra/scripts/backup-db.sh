#!/usr/bin/env bash
# Pre-deploy бэкап Postgres (07-deployment.md#бэкапы-и-данные).
# Снимает pg_dump БД crm в $ROOT/backups/crm-<timestamp>.sql.
# Восстановление: docker compose exec -T postgres psql -U "$POSTGRES_USER" "$POSTGRES_DB" < <файл>
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/infra/docker-compose.yml")

BACKUP_DIR="$ROOT/backups"
mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/crm-$TS.sql"

echo "[backup] pg_dump → $OUT"
# Креды читаются из env самого postgres-контейнера, не передаются с хоста.
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$OUT"
echo "[backup] готово: $OUT"

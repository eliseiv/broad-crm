#!/usr/bin/env bash
# ============================================================================
# Продление TLS-сертификата Let's Encrypt (HTTP-01 standalone). Идемпотентно.
# ----------------------------------------------------------------------------
# `certbot renew` продлевает серт только если он близок к истечению (<30 дней),
# иначе ничего не делает (exit 0). После продления обновлённые fullchain/privkey
# копируются в volume proxy-certs и proxy перезапускается, чтобы подхватить их.
#
# НЕ меняет docker-compose.yml и nginx-конфиг. На время продления proxy
# кратковременно останавливается (standalone занимает :80).
#
# Запуск (на сервере), удобно по расписанию (cron / systemd timer):
#   bash infra/scripts/renew-cert.sh
#   # пример cron (дважды в сутки): 17 3,15 * * * bash /opt/crm/infra/scripts/renew-cert.sh >> /var/log/crm-renew.log 2>&1
#
# Первичный выпуск — infra/scripts/issue-cert.sh.
# ============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/crm}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$APP_DIR/infra/docker-compose.yml}"

[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE" 2>/dev/null || true; set +a; }

DOMAIN="${DOMAIN:-${PUBLIC_HOSTNAME:-broadappsdev.shop}}"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot:v3.0.1}"
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.20}"
CERTBOT_ETC_VOLUME="${CERTBOT_ETC_VOLUME:-crm_certbot-etc}"
PROXY_CERTS_VOLUME="${PROXY_CERTS_VOLUME:-crm_proxy-certs}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "[renew] домен=$DOMAIN, certbot=$CERTBOT_IMAGE"

start_proxy() { echo "[renew] поднимаю proxy"; "${COMPOSE[@]}" up -d proxy; }

echo "[renew] останавливаю proxy на время продления (освобождаю :80)"
"${COMPOSE[@]}" stop proxy
trap start_proxy EXIT

echo "[renew] certbot renew (standalone)"
docker run --rm -p 80:80 \
  -v "$CERTBOT_ETC_VOLUME:/etc/letsencrypt" \
  "$CERTBOT_IMAGE" renew --standalone --preferred-challenges http

# Копируем актуальные серты (после возможного продления). Если live-каталога нет
# (серт ещё не выпускался) — подсказываем запустить issue-cert.sh, без падения.
if docker run --rm -v "$CERTBOT_ETC_VOLUME:/le:ro" "$ALPINE_IMAGE" \
     test -f "/le/live/$DOMAIN/fullchain.pem"; then
  echo "[renew] копирую актуальные fullchain/privkey в volume $PROXY_CERTS_VOLUME"
  docker run --rm \
    -v "$CERTBOT_ETC_VOLUME:/le:ro" \
    -v "$PROXY_CERTS_VOLUME:/certs" \
    "$ALPINE_IMAGE" sh -c "set -e; cp -L /le/live/$DOMAIN/fullchain.pem /certs/fullchain.pem && cp -L /le/live/$DOMAIN/privkey.pem /certs/privkey.pem && chmod 600 /certs/privkey.pem"
else
  echo "[renew] ПРЕДУПРЕЖДЕНИЕ: серт для $DOMAIN не найден — сначала запустите issue-cert.sh" >&2
fi

echo "[renew] готово."

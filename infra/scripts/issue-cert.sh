#!/usr/bin/env bash
# ============================================================================
# Первичный выпуск валидного TLS-сертификата Let's Encrypt (HTTP-01 standalone).
# ----------------------------------------------------------------------------
# Назначение: получить доверенный серт для DOMAIN и положить его в volume
# proxy-certs (fullchain.pem + privkey.pem). nginx-proxy подхватит реальный серт
# автоматически (имеет приоритет над self-signed, см. 07-deployment.md#tls-сертификаты).
#
# НЕ меняет docker-compose.yml и nginx-конфиг. Подход — standalone: на время
# выпуска proxy кратковременно останавливается, чтобы certbot занял :80.
#
# Требования:
#   - DNS A-запись DOMAIN → IP этого сервера (HTTP-01 валидация ходит на :80);
#   - порт 80 доступен снаружи; Docker установлен; стек развёрнут в /opt/crm.
#
# Запуск (на сервере):
#   bash infra/scripts/issue-cert.sh
#   # переопределения: DOMAIN=example.com LETSENCRYPT_EMAIL=a@b.c bash infra/scripts/issue-cert.sh
#
# Продление — infra/scripts/renew-cert.sh (по cron/systemd timer).
# ============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/crm}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$APP_DIR/infra/docker-compose.yml}"

# Подтянуть DOMAIN/LETSENCRYPT_EMAIL/PUBLIC_HOSTNAME из .env, если заданы там.
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE" 2>/dev/null || true; set +a; }

DOMAIN="${DOMAIN:-${PUBLIC_HOSTNAME:-broadappsdev.shop}}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/certbot:v3.0.1}"
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.20}"
CERTBOT_ETC_VOLUME="${CERTBOT_ETC_VOLUME:-crm_certbot-etc}"
PROXY_CERTS_VOLUME="${PROXY_CERTS_VOLUME:-crm_proxy-certs}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if [ -n "$LETSENCRYPT_EMAIL" ]; then
  EMAIL_ARGS=(--email "$LETSENCRYPT_EMAIL")
else
  EMAIL_ARGS=(--register-unsafely-without-email)
fi

echo "[issue] домен=$DOMAIN, certbot=$CERTBOT_IMAGE"
docker volume create "$CERTBOT_ETC_VOLUME" >/dev/null

start_proxy() { echo "[issue] поднимаю proxy"; "${COMPOSE[@]}" up -d proxy; }

echo "[issue] останавливаю proxy (освобождаю :80)"
"${COMPOSE[@]}" stop proxy
# В любом исходе (успех/ошибка) вернуть proxy в работу.
trap start_proxy EXIT

echo "[issue] запускаю certbot standalone (HTTP-01) на :80"
docker run --rm -p 80:80 \
  -v "$CERTBOT_ETC_VOLUME:/etc/letsencrypt" \
  "$CERTBOT_IMAGE" certonly --standalone \
  --preferred-challenges http \
  -d "$DOMAIN" \
  --agree-tos -n --keep-until-expiring "${EMAIL_ARGS[@]}"

echo "[issue] копирую fullchain/privkey в volume $PROXY_CERTS_VOLUME"
docker run --rm \
  -v "$CERTBOT_ETC_VOLUME:/le:ro" \
  -v "$PROXY_CERTS_VOLUME:/certs" \
  "$ALPINE_IMAGE" sh -c "set -e; cp -L /le/live/$DOMAIN/fullchain.pem /certs/fullchain.pem && cp -L /le/live/$DOMAIN/privkey.pem /certs/privkey.pem && chmod 600 /certs/privkey.pem"

echo "[issue] готово: реальный серт установлен в proxy-certs. proxy перезапускается с ним (trap)."

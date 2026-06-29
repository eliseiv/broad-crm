#!/bin/sh
# Генерирует self-signed TLS-сертификат, если он ещё не смонтирован/не создан.
# Запускается официальным nginx-entrypoint из /docker-entrypoint.d/ ДО старта nginx.
#
# Приоритет (05-security.md#tls-сертификаты, 07-deployment.md#tls-сертификаты):
#   реальные fullchain.pem+privkey.pem, смонтированные в volume proxy-certs, имеют
#   приоритет — если они уже есть, генерация пропускается (идемпотентно).
# Этап 1: self-signed для локального single-host. ACME/Let's Encrypt — вне scope (TD-011).
set -eu

CERT_DIR="${TLS_CERT_DIR:-/etc/nginx/certs}"
CERT_FILE="$CERT_DIR/fullchain.pem"
KEY_FILE="$CERT_DIR/privkey.pem"
CN="${PUBLIC_HOSTNAME:-crm.local}"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "[cert] сертификат уже присутствует в $CERT_DIR — генерация пропущена"
    exit 0
fi

mkdir -p "$CERT_DIR"
echo "[cert] генерирую self-signed сертификат для CN=$CN в $CERT_DIR"
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$KEY_FILE" -out "$CERT_FILE" \
    -subj "/CN=$CN" \
    -addext "subjectAltName=DNS:$CN,DNS:localhost" >/dev/null 2>&1
chmod 600 "$KEY_FILE"

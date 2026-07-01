# syntax=docker/dockerfile:1.7
# Frontend (proxy): сборка SPA на Node 20 LTS → раздача + reverse-proxy на nginx
# (02-tech-stack.md#frontend, 07-deployment.md). Это сервис `proxy` из топологии.
#
# Pinning-политика (единая, см. backend/Dockerfile): теги ровно из docs/02-tech-stack.md,
# без :latest. node:20-alpine (сборка) + nginx:1.27-alpine (runtime).

# --- build: Vite-сборка SPA на закреплённой Node 20 LTS ---
FROM node:20-alpine AS build

WORKDIR /app

# Build-time переменные SPA (VITE_*). Топология за reverse-proxy (один origin).
# VITE_API_BASE_URL ПУСТ по умолчанию: buildUrl (src/lib/api.ts) сам добавляет '/api'
# → same-origin запросы /api/...; непустое значение дало бы /api/api/... .
ARG VITE_API_BASE_URL=
ARG VITE_POLL_INTERVAL_MS=15000
ARG VITE_STATUS_POLL_INTERVAL_MS=2500
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_POLL_INTERVAL_MS=$VITE_POLL_INTERVAL_MS \
    VITE_STATUS_POLL_INTERVAL_MS=$VITE_STATUS_POLL_INTERVAL_MS

# Слой зависимостей кэшируется, пока не менялись манифесты.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .
RUN npm run build

# --- runtime: nginx раздаёт собранный SPA и проксирует /api, /grafana ---
FROM nginx:1.27-alpine AS runtime

# openssl нужен скрипту генерации self-signed TLS-сертификата.
RUN apk add --no-cache openssl

# Конфиг reverse-proxy + скрипт генерации TLS-сертификата (выполняется до старта nginx).
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/10-gen-selfsigned-cert.sh /docker-entrypoint.d/10-gen-selfsigned-cert.sh
# Надёжно убираем возможные CRLF (Windows-авторы/git autocrlf): shebang #!/bin/sh\r
# ломает запуск в Linux (exit 127). Затем делаем скрипт исполняемым.
RUN sed -i 's/\r$//' /docker-entrypoint.d/10-gen-selfsigned-cert.sh \
    && chmod +x /docker-entrypoint.d/10-gen-selfsigned-cert.sh

# Собранный SPA.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1

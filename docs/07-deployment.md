# 07 · Развёртывание

## Топология (docker-compose, один хост)

```mermaid
flowchart TB
    subgraph net["docker network: crm-net"]
        proxy["nginx (reverse-proxy + SPA static)<br/>:80/:443 → хост"]
        be["backend (FastAPI/uvicorn)<br/>:8000 (внутр.)"]
        db[("postgres:16<br/>:5432 (внутр.)")]
        prom["prometheus<br/>:9090 (внутр.)"]
        graf["grafana<br/>:3000 (внутр.)"]
        vol_sd[("volume: file_sd<br/>targets/*.json")]
    end
    proxy -->|/api| be
    proxy -->|/| spa["SPA (статика, собрана Vite)"]
    proxy -->|/grafana| graf
    be --> db
    be -->|PromQL| prom
    be -->|пишет| vol_sd
    prom -->|читает file_sd| vol_sd
    graf --> prom
    prom -->|scrape :9100| targets["целевые серверы (node_exporter)"]
```

> Целевые серверы и их node_exporter НЕ часть compose — они провижинятся Ansible (см. [09-provisioning.md](09-provisioning.md)).

## Состав сервисов

| Сервис | Образ | Порт (наружу) | Назначение |
|--------|-------|----------------|-----------|
| `proxy` | `nginx:1.27-alpine` (+ entrypoint автогенерации TLS) | `80`, `443` | Reverse-proxy, TLS (volume `proxy-certs`), раздача SPA |
| `frontend-build` | node:20 (multi-stage) | — | Сборка SPA, артефакт копируется в `proxy` |
| `backend` | собственный (python:3.12-slim) | — (внутр. 8000) | FastAPI + ansible-runner + ansible-core |
| `postgres` | `postgres:16` | — (внутр. 5432) | БД (volume `pgdata`) |
| `prometheus` | `prom/prometheus:v2.54` | — (внутр. 9090) | Метрики, file_sd (volume `file_sd`) |
| `grafana` | `grafana/grafana:11.2` | — (через proxy `/grafana`) | Drill-down дашборды (volume `grafana-data`) |

**Наружу публикуется только `proxy`.** `postgres`, `prometheus`, `grafana`, `backend` доступны лишь внутри `crm-net` (NFR-9, [05-security.md](05-security.md)).

### Backend-образ
Базовый `python:3.12-slim`; устанавливается `ansible-core`, `openssh-client`, зависимости проекта (через `uv`). Backend-контейнер должен иметь сетевой доступ по SSH (порт 22) к целевым серверам и доступ к volume `file_sd`.

### proxy-образ (nginx + TLS)
Базовый `nginx:1.27-alpine` + кастомный entrypoint автогенерации self-signed TLS ([TLS-сертификаты](#tls-сертификаты)). **Требует `openssl`** для генерации серта: `apk add --no-cache openssl` в Dockerfile (в `nginx:alpine` openssl не предустановлен). Без него entrypoint падает при первом старте.

### Сборка образов: переводы строк и зависимости (нормативно)

Усвоенный урок (runtime-баг): shell-скрипт с CRLF (`#!/bin/sh\r`) в Linux-контейнере не запускается — интерпретатор не находит `/bin/sh\r`, контейнер падает с `exit 127`. Требования, чтобы это не повторялось:

1. **LF во всех shell-скриптах/entrypoint'ах, попадающих в образы.** Двойная защита:
   - В репозитории — `.gitattributes`: `*.sh text eol=lf` (и для entrypoint-файлов без расширения — явные правила). Это нормализует EOL на checkout независимо от ОС разработчика (Windows/`core.autocrlf`).
   - В Dockerfile — **защитная нормализация** копируемых скриптов: `sed -i 's/\r$//' <script>` или `dos2unix <script>` перед `chmod +x`. Это страхует даже если файл попал с CRLF.
2. **Зависимости рантайма образа объявлены явно** (не полагаться на «есть в базовом образе»): для `proxy` — `openssl` (см. выше); для `backend` — `ansible-core`, `openssh-client` (см. [Backend-образ](#backend-образ)).
3. Скрипты — с корректным shebang (`#!/bin/sh` для alpine/busybox) и `chmod +x`.

> Не противоречит зафиксированным решениям: self-signed TLS ([ADR/секция TLS](#tls-сертификаты)) и разделение security-заголовков ([05-security.md](05-security.md#http-заголовки-безопасности-нормативно)) сохраняются — это лишь требования к корректной упаковке скриптов и зависимостей в образы.

## Reverse-proxy (nginx) — требования

nginx терминирует TLS, проксирует `/api`→backend, `/`→SPA, `/grafana`→grafana. Для корректной работы rate-limit аутентификации backend определяет реальный IP клиента ([05-security.md](05-security.md#аутентификация-администратора)); поэтому для `location /api` nginx **ОБЯЗАН** пробрасывать заголовки с реальным адресом:

```nginx
# API: backend сам ставит security-заголовки — здесь их НЕ добавляем (без дублей)
location /api {
    proxy_pass http://backend:8000;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# SPA: статику отдаёт nginx → security-заголовки + CSP ставит nginx
location / {
    root /usr/share/nginx/html;
    try_files $uri /index.html;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy no-referrer always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
}
```

- Backend читает IP в порядке `X-Real-IP` → `X-Forwarded-For[0]` → `client.host`. Без проброса все запросы будут с IP прокси → rate-limit заблокирует всех.
- **Разделение ответственности за security-заголовки** (нормативно, без дублей — [05-security.md](05-security.md#http-заголовки-безопасности-нормативно)): для `/api` 4 заголовка (+HSTS) ставит backend-middleware (`setdefault`); для SPA (`location /`) те же 4 + CSP ставит nginx (`add_header ... always`). HSTS не дублировать.
- **Значение CSP — нормативное** ([05-security.md](05-security.md#content-security-policy-spa-location-)); строка в nginx обязана побайтово совпадать с зафиксированной там.
- `proxy_set_header X-Forwarded-Proto $scheme;` нужен, чтобы backend корректно понимал, что соединение за TLS (для условного HSTS).

### TLS-сертификаты

Продакшен-домен: **`broadappsdev.shop`** (`PUBLIC_HOSTNAME=broadappsdev.shop`).

**Предусловие (DNS):** A-запись `broadappsdev.shop` → **`37.27.192.211`** (опционально `www.broadappsdev.shop` → тот же IP). Без указывающей на сервер A-записи выпуск Let's Encrypt (HTTP-01) невозможен.

Приоритет источников серта (nginx уже отдаёт приоритет реальному серту в `proxy-certs` над self-signed):

1. **Production — реальный сертификат Let's Encrypt (основной путь).** Выпуск через **certbot standalone (HTTP-01)** скриптом `infra/scripts/issue-cert.sh`: кладёт `fullchain.pem` + `privkey.pem` в volume `proxy-certs` (`${TLS_CERT_DIR}`, по умолчанию `/etc/nginx/certs`), nginx использует их.
   - Требования issue-cert.sh: DNS A-запись домена указывает на сервер; свободный порт `:80` на время валидации → **кратковременная остановка `proxy`** (standalone поднимает свой listener на 80), затем рестарт `proxy`. Email для LE — `LETSENCRYPT_EMAIL` (опционально, для уведомлений о продлении).
   - **Продление:** `infra/scripts/renew-cert.sh` (certbot renew → обновляет файлы в `proxy-certs` → reload/restart `proxy`). Рекомендуется автозапуск через **systemd timer** (или cron) ~2×/сутки; LE-серты живут 90 дней, продление при остатке <30 дней.
2. **Self-signed (fallback).** Если реального серта в `proxy-certs` нет (нет домена / окружение разработки / до первого выпуска LE) — entrypoint `proxy` идемпотентно генерирует self-signed пару (CN/SAN = `${PUBLIC_HOSTNAME}`). Даёт рабочий HTTPS без внешних зависимостей (браузер предупредит о доверии). Self-signed остаётся для окружений без домена.

- Приватные ключи в volume `proxy-certs` — не в репозитории, не в образе. См. [05-security.md](05-security.md#tls-сертификаты).
- Zero-downtime продление (webroot/ACME-companion вместо standalone) — улучшение ([TD-011](100-known-tech-debt.md)).

## Переменные окружения

`.env` в корне (в `.gitignore`); `.env.example` — в репозитории.

| Переменная | Пример | Описание |
|-----------|--------|----------|
| `APP_ENV` | `production` | `development` \| `production`. На `production` отключает `/api/docs`, `/api/redoc`, `/api/openapi.json` ([05-security.md](05-security.md#документация-api-apidocs-apiopenapijson)) |
| `IMAGE_TAG` | `current` | Опциональный override тега образов в `docker-compose.yml` (`crm-*:${IMAGE_TAG:-current}`); по умолчанию `current`. Используется при ручных операциях/legacy-скриптах, CI его не задаёт |
| `PUBLIC_HOSTNAME` | `broadappsdev.shop` | Продакшен-домен; CN/SAN серта (LE и self-signed), ссылки. Требует DNS A → `37.27.192.211` |
| `TLS_CERT_DIR` | `/etc/nginx/certs` | Каталог сертификатов в `proxy` (volume `proxy-certs`): `fullchain.pem` + `privkey.pem` |
| `LETSENCRYPT_EMAIL` | `admin@broadappsdev.shop` | Email для Let's Encrypt (уведомления о продлении); опционально, используется `issue-cert.sh`/`renew-cert.sh` |
| `ADMIN_USER` | `admin` | Логин администратора |
| `ADMIN_PASSWORD` | `change-me` | Пароль администратора |
| `JWT_SECRET` | `<32+ random bytes>` | Подпись JWT (HS256) |
| `JWT_EXPIRES_MIN` | `60` | TTL access-токена, мин |
| `JWT_ALGORITHM` | `HS256` | Алгоритм |
| `FERNET_KEY` | `<base64 32 bytes>` | Ключ шифрования SSH-паролей |
| `DATABASE_URL` | `postgresql+asyncpg://crm:pwd@postgres:5432/crm` | Подключение к БД |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `crm` / `pwd` / `crm` | Инициализация postgres |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Базовый URL Prometheus API |
| `PROM_QUERY_TIMEOUT_SEC` | `10` | Таймаут PromQL-запроса |
| `EXPORTER_PORT` | `9100` | Порт node_exporter по умолчанию |
| `FILE_SD_DIR` | `/etc/prometheus/targets` | Каталог file_sd (общий volume) |
| `ANSIBLE_TIMEOUT_SEC` | `300` | Таймаут плейбука |
| `ANSIBLE_HOST_KEY_CHECKING` | `false` | (Этап 1) [TD-007](100-known-tech-debt.md) |
| `CORS_ALLOW_ORIGINS` | `https://broadappsdev.shop` | Разрешённые origin (если нужно) |
| `GRAFANA_BASE_URL` | `https://broadappsdev.shop/grafana` | Для drill-down ссылок |
| `GF_SECURITY_ADMIN_PASSWORD` | `change-me` | Grafana admin |
| `VITE_API_BASE_URL` | `/api` | База API для SPA (build-time) |
| `VITE_POLL_INTERVAL_MS` | `15000` | Интервал polling карточек |
| `VITE_GRAFANA_URL` | `/grafana` | Ссылка drill-down во фронте |

## Конфигурация Prometheus

`infra/prometheus/prometheus.yml` (концепт):
```yaml
global:
  scrape_interval: 15s
  scrape_timeout: 10s
scrape_configs:
  - job_name: 'node'
    file_sd_configs:
      - files:
          - /etc/prometheus/targets/*.json
        refresh_interval: 30s
```
- `file_sd` обновляется без рестарта; интервал перечитывания 30 с ([ADR-004](adr/ADR-004-file-sd-registraciya-targetov.md)).
- Формат `targets/<id>.json` — см. [09-provisioning.md](09-provisioning.md).

## Конфигурация Grafana

- Provisioning datasource → Prometheus (`http://prometheus:9090`). **Это весь scope Grafana на Этапе 1: только датасорс (datasource-only).**
- **Преднастроенный дашборд node_exporter — ВНЕ scope Этапа 1** ([TD-010](100-known-tech-debt.md)). Drill-down из карточки ведёт на Grafana (`VITE_GRAFANA_URL`/`GRAFANA_BASE_URL`), где администратор использует Explore по готовому датасорсу или импортирует дашборд вручную. Автопровижининг дашборда — будущий этап.
- `GF_AUTH_ANONYMOUS_ENABLED=false`, сменённый admin-пароль.
- Доступ через proxy `/grafana` (sub-path, `GF_SERVER_ROOT_URL`).

## Порядок запуска

1. Заполнить `.env` (сгенерировать `JWT_SECRET`, `FERNET_KEY`; задать `PUBLIC_HOSTNAME=broadappsdev.shop`, `LETSENCRYPT_EMAIL`).
2. Убедиться, что **DNS A `broadappsdev.shop` → `37.27.192.211`** уже распространилась (предусловие для LE).
3. `docker compose up -d postgres` → дождаться healthy.
4. **Миграции применяются в entrypoint backend-контейнера**: команда запуска backend — `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000`. Отдельный шаг/job для миграций не используется (один источник, NFR-1).
5. `docker compose up -d` (поднимает все сервисы; backend сам мигрирует БД при старте; при отсутствии реального серта `proxy` поднимется на self-signed).
6. **Выпустить реальный TLS:** `infra/scripts/issue-cert.sh` (certbot standalone; кратковременно остановит `proxy` для валидации :80, затем рестарт с LE-сертом). Настроить автопродление (`renew-cert.sh` + systemd timer/cron).
7. Открыть `https://broadappsdev.shop/` → экран входа.

## CI/CD

Движок — **GitHub Actions** (`.github/workflows/ci.yml`; [Q-DEP-1](99-open-questions.md) закрыт в пользу GitHub Actions). Pipeline single-host:

1. **lint** (на `pull_request` и `push`): `ruff check`, `ruff format --check`, `mypy app` в `backend/` (uv + кэш зависимостей).
2. **test** (на `pull_request` и `push`): `pytest --cov=app --cov-report=term-missing --cov-fail-under=80` (gate ≥80 %) с сервисом `postgres:16`; `APP_ENV=development`, `DATABASE_URL` на локальный postgres; эфемерные `JWT_SECRET`/`FERNET_KEY` генерируются в job (не хардкодятся).
3. **deploy** (только `push` в `main`, `needs: [lint, test]`): доставка на прод-сервер по SSH:
   - `rsync -az --delete` рабочего дерева на `${SSH_USER}@${SSH_HOST}:/opt/crm/` с исключениями `.git`, `.env`, `.env.*`, `node_modules`, `.venv`, `frontend/dist`, `backups`, `.idea`, `.cursor`, `__pycache__`. **Серверный `/opt/crm/.env` НЕ перезаписывается** (`--exclude='.env'`);
   - по SSH на сервере: `cd /opt/crm && docker compose --env-file .env -f infra/docker-compose.yml up -d --build && ... ps`. **Образы собираются на сервере** при `--build` (registry не используется — [Q-DEP-2](99-open-questions.md) закрыт: на Этапе 1 деплой = rsync + сборка на хосте). Миграции применяются backend-контейнером в entrypoint (`alembic upgrade head`) при `up` — отдельного шага нет (NFR-1).

**Секреты CI — ровно три (GitHub Secrets):** `SSH_USER`, `SSH_HOST`, `SSH_DEPLOY_KEY` (приватный ed25519; публичный ключ — в `authorized_keys` на сервере). Прод-секреты приложения живут в `/opt/crm/.env` на сервере — вне git и вне CI.

**Вне CI на Этапе 1:** линт/сборка frontend, `vitest`/Playwright e2e, `ansible-lint`, отдельная сборка/публикация образов. Сборка SPA выполняется на сервере в составе `docker compose build` (proxy-образ); Node 20 LTS закреплён в Dockerfile (`node:20-alpine`, см. [02-tech-stack.md](02-tech-stack.md#frontend)). Возврат frontend-линта и тестовых гейтов (vitest gate ≥70 %, e2e) в CI — этап qa ([06-testing-strategy.md](06-testing-strategy.md)).

**runtime-smoke (обязательно перед объявлением релиза готовым):** `docker compose config` (валидация YAML) НЕ достаточен. Нужно поднять стек `docker compose --env-file .env -f infra/docker-compose.yml up -d --build`, дождаться, пока **все контейнеры `healthy` без рестартов**, и проверить рантайм: `GET /api/health` → `200`, отдача SPA (`GET /` → `200`), запуск `proxy` (TLS-серт сгенерирован, nginx слушает 80/443). Ловит баги, невидимые статической проверке (CRLF в entrypoint → `exit 127`, отсутствие `openssl`). На Этапе 1 — ручной/полуавтоматический прогон; автоматизация в CI — [TD-014](100-known-tech-debt.md).

### Версионирование образов

- **CI (GitHub Actions) НЕ использует git-SHA тегирование и registry** ([Q-DEP-2](99-open-questions.md): registry не используется). CI собирает образы на сервере через `docker compose up -d --build`; по умолчанию образы получают тег `current` (`image: crm-*:${IMAGE_TAG:-current}` в `docker-compose.yml`). Идентификатор версии = **git-коммит**, доставленный rsync'ом (рекомендуется деплоить из чистого `main`, фиксируя SHA релиза в логе деплоя).
- **`IMAGE_TAG` — опциональный override** тега образов в `docker-compose.yml` (значение по умолчанию `current`). Позволяет вручную зафиксировать/переключить версию образов при ручных операциях.
- **`infra/scripts/deploy.sh` и `infra/scripts/rollback.sh` — опциональный legacy-инструментарий** ручного деплоя/отката (тегирование git-SHA, alias'ы `:current`/`:previous`). **CI их не использует**; сохранены как справка/ручной путь. Push в registry — не используется ([Q-DEP-2](99-open-questions.md)); может быть задействован опционально вместе с этими скриптами.

## Бэкапы и данные

- `pgdata` — единственное состояние, требующее бэкапа (реестр серверов). Метрики восстанавливаются Prometheus'ом со временем.
- Бэкап Postgres: `pg_dump` БД `crm` перед каждым деплоем с миграциями (рекомендуемый snapshot-перед-релизом), файл с временной меткой в каталоге бэкапов хоста. Восстановление — `pg_restore`/`psql` в свежий контейнер `postgres`.
- `file_sd` восстановим из БД (backend может перегенерировать `targets/*.json` из реестра) — рекомендация для backend ([modules/provisioning](modules/provisioning/README.md)).
- `proxy-certs` (TLS) и `grafana-data` — бэкапятся опционально; self-signed серт регенерируется, дашборды на Этапе 1 не провижинятся.

## Откат и восстановление

Single-host docker-compose, Этап 1. Цель — быстро вернуть рабочую версию при неудачном деплое.

### Откат приложения (backend/frontend)
Деплой = rsync + сборка на сервере ([версионирование образов](#версионирование-образов)):
- **Основной путь:** повторный CI-деплой предыдущего рабочего коммита (`git revert`/`checkout` → rsync → `docker compose up -d --build`). Сборка из прошлого исходника восстанавливает предыдущую версию backend и SPA.
- **Опциональный ручной путь:** legacy-скрипт `infra/scripts/rollback.sh` (переключение на образ `:previous` через `IMAGE_TAG`) либо поднятие предыдущих образов из кэша Docker (`docker compose up -d` без `--build`).
- Откат приложения без миграций БД — безопасен; серверный `/opt/crm/.env` не затрагивается.

### Откат миграций БД
Миграции forward-only применяются в entrypoint backend (`alembic upgrade head`). Политика отката на Этапе 1 — **двухуровневая, в порядке предпочтения**:

1. **`alembic downgrade` (основной путь).** Требование к backend (нормативно): **каждая миграция Alembic ОБЯЗАНА иметь рабочую функцию `downgrade()`**, протестированную на откат на одну ревизию. Откат: `docker compose run --rm backend alembic downgrade -1` (или до конкретной ревизии), затем деплой предыдущего коммита приложения (rsync + `up -d --build`). Это требование закреплено в [03-data-model.md](03-data-model.md) и [modules/servers](modules/servers/README.md).
2. **Восстановление из бэкапа (fallback).** Если `downgrade` невозможен (например, миграция с потерей данных) — восстановить Postgres из snapshot'а, снятого перед деплоем (см. [«Бэкапы и данные»](#бэкапы-и-данные)), затем задеплоить предыдущий коммит приложения.

> На Этапе 1 (одна таблица `servers`, простая схема) `downgrade` реализуем для всех миграций — это основной механизм. Бэкап-восстановление — страховка.

### Порядок действий при неудачном деплое
1. Зафиксировать симптом (health `degraded`/ошибки backend, не стартует сервис).
2. Если деплой **без миграций** → задеплоить предыдущий рабочий коммит (rsync + `docker compose up -d --build`) либо поднять предыдущие образы из кэша (`up -d` без `--build`) — см. [«Откат приложения»](#откат-приложения-backendfrontend).
3. Если деплой **с миграциями**:
   a. Остановить backend (`docker compose stop backend`).
   b. Откатить миграцию: `alembic downgrade -1` (основной путь) либо восстановить БД из pre-deploy бэкапа (fallback).
   c. Задеплоить предыдущий рабочий коммит (rsync + `docker compose up -d --build`).
4. Проверить `GET /api/health` (`status: ok`, `db: up`, `prometheus: up`).
5. Зафиксировать инцидент; при необходимости расхождения docs↔реализация — эскалировать architect.

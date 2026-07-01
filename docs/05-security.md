# 05 · Безопасность

## Аутентификация администратора

- Единственная учётная запись Этапа 1 хранится в `.env`: `ADMIN_USER`, `ADMIN_PASSWORD`. В БД админ НЕ хранится ([ADR-008](adr/ADR-008-admin-iz-env.md)).
- Двухшаговый UI-вход; backend проверяет креды единым запросом `POST /api/auth/login` ([ADR-002](adr/ADR-002-dvuhshagovyy-auth.md)).
- Сравнение пароля — **constant-time** (`hmac.compare_digest` / `secrets.compare_digest`) для логина и пароля, чтобы исключить timing-атаки.
- Сообщение об ошибке входа одинаково для неверного логина и неверного пароля (`invalid_credentials`) — не раскрывает существование пользователя.
- Защита от перебора: rate-limit на `/api/auth/login` (по IP, по умолчанию 10 попыток / 5 мин, далее `429`). Реализация — in-memory счётчик на Этапе 1 (один воркер), вынос в Redis — будущий этап ([TD-005](100-known-tech-debt.md)).
- **Определение реального IP клиента за reverse-proxy** (нормативно): backend берёт IP в порядке `X-Real-IP` → первый адрес из `X-Forwarded-For` → `request.client.host`. Поэтому nginx ОБЯЗАН проставлять эти заголовки для `location /api` (см. [07-deployment.md](07-deployment.md#reverse-proxy-nginx-требования)). Без корректного проброса rate-limit считал бы все запросы с одного IP (адрес прокси) и блокировал всех. Доверять `X-Forwarded-For`/`X-Real-IP` допустимо, только когда backend доступен исключительно через доверенный прокси (как в нашей топологии — backend не публикуется наружу).

### Хранение `ADMIN_PASSWORD`
- На Этапе 1 допускается plaintext в `.env` (это секрет окружения, не в репозитории). Рекомендация: bcrypt-хэш `ADMIN_PASSWORD_HASH` как опция — зафиксировано как [Q-SEC-1](99-open-questions.md). По умолчанию — plaintext-сравнение constant-time.

## JWT

| Параметр | Значение |
|----------|----------|
| Алгоритм | `HS256` (симметричный, `JWT_SECRET` из `.env`) |
| TTL | `JWT_EXPIRES_MIN`, по умолчанию 60 мин |
| Claims | `sub` (=username), `iat`, `exp`, `type:"access"` |
| Передача | заголовок `Authorization: Bearer <token>` |

- Выбор HS256 (а не RS256) — один сервис, симметричный ключ проще; обоснование в [ADR-002](adr/ADR-002-dvuhshagovyy-auth.md). При появлении нескольких сервисов-валидаторов — пересмотр на RS256.
- Refresh-токенов на Этапе 1 нет: по истечении TTL — повторный вход. Хранение access-токена на фронте — **в памяти (Zustand)**, не в `localStorage` (снижает риск XSS-кражи). Допустимо `sessionStorage` для переживания перезагрузки — решение фронта, зафиксировано в [modules/auth](modules/auth/README.md).
- Все эндпоинты, кроме `/api/auth/login` и `/api/health`, требуют валидный JWT → иначе `401 unauthorized`.

## Защита SSH-кредов целевых серверов

- SSH-пароль шифруется **Fernet** (`cryptography`) сразу при `POST /api/servers`; в БД — только `ssh_password_encrypted` (`bytea`).
- Ключ `FERNET_KEY` (base64, 32 байта) — из `.env`, никогда в коде/репозитории/логах/ответах API.
- Расшифровка — только в памяти провижининг-сервиса непосредственно перед запуском Ansible; расшифрованное значение не логируется и не покидает процесс.
- Пароль (в любом виде) НЕ возвращается ни в одном ответе API.
- Ротация `FERNET_KEY` — `MultiFernet` (новый + старый ключ) — будущий этап ([TD-006](100-known-tech-debt.md)).

## Защита AI-ключей

Ключи AI-провайдеров (OpenAI/Anthropic) — секреты того же класса, что и SSH-пароли ([modules/ai-keys](modules/ai-keys/README.md#безопасность-ключа-нормативно), [ADR-010](adr/ADR-010-ai-key-monitor-vnutri-backend.md)).

- Полный ключ шифруется **Fernet** тем же `FERNET_KEY` сразу при `POST /api/ai-keys`; в БД — только `key_encrypted bytea` ([03-data-model.md](03-data-model.md#таблица-ai_keys)).
- Расшифровка — только в памяти монитора/проверки непосредственно перед HTTP-запросом к провайдеру (`GET /v1/models`); расшифрованное значение не логируется и не покидает процесс.
- **Полный ключ (в любом виде) НЕ возвращается ни в одном ответе API.** В ответах — только маска `key_masked` (первые 4 … последние 4 символа; для ключа короче 8 символов — полная маска `********`).
- `key_prefix`/`key_last4` (по 4 plaintext-символа) хранятся ради маски и текста Telegram-алерта — осознанное раскрытие 8 символов, секрет из них не восстанавливается.
- Ключ провайдера **не передаётся** в query-строке/URL и не пишется в structlog (фильтр секретов); заголовки `Authorization: Bearer`/`x-api-key` не логируются.

## Ansible и секреты

- Креды передаются в Ansible через переменные среды/`extravars` в памяти ansible-runner, не через файлы на диске (или через временные файлы с `0600`, удаляемые в `finally`).
- `no_log: true` на тасках, использующих пароль (см. [09-provisioning.md](09-provisioning.md)).
- SSH host key checking: на Этапе 1 `ANSIBLE_HOST_KEY_CHECKING=false` (новые серверы без known_hosts) — задокументированный риск MITM при первом подключении ([TD-007](100-known-tech-debt.md), [Q-SEC-2](99-open-questions.md)).
- **Привилегии (`become`):** Этап 1 предполагает целевого SSH-пользователя `root` ИЛИ sudoer с passwordless `sudo` (`NOPASSWD`) — `ansible_become_password` не передаётся. Sudoer с паролем не поддерживается ([Q-SEC-3](99-open-questions.md)). Детали — [09-provisioning.md](09-provisioning.md#привилегии-become).

## Сетевая безопасность инфраструктуры

- **Prometheus и Grafana не публикуются наружу** (NFR-9). В docker-compose их порты не маппятся на хост-интерфейс `0.0.0.0`; доступ — только внутри docker-сети или через защищённый reverse-proxy с auth.
- Grafana: сменить дефолтный admin-пароль (`GF_SECURITY_ADMIN_PASSWORD` из `.env`), `GF_AUTH_ANONYMOUS_ENABLED=false`.
- Grafana защищена собственным логином; доступ — напрямую через proxy `/grafana` (drill-down ссылки из карточки в UI нет — [ADR-005, поправка](adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки)).
- **Telegram-нотификатор:** `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` — секреты, только из env; в код/логи/ответы API не попадают ([modules/notifier](modules/notifier/README.md)). Исходящий трафик — HTTPS к `api.telegram.org`.
- Reverse-proxy (nginx) терминирует TLS, проксирует `/api`→backend, `/`→SPA.

## TLS-сертификаты

- Продакшен-домен — **`broadappsdev.shop`** (DNS A → `37.27.192.211`).
- **Production (основной путь):** реальный сертификат **Let's Encrypt** (certbot standalone, HTTP-01), выпуск/продление скриптами `infra/scripts/issue-cert.sh` / `renew-cert.sh`; `fullchain.pem`+`privkey.pem` кладутся в volume `proxy-certs` (`TLS_CERT_DIR`), nginx отдаёт им приоритет над self-signed.
- **Self-signed (fallback):** автогенерируется entrypoint'ом `proxy` в `proxy-certs` (CN/SAN = `PUBLIC_HOSTNAME`), когда реального серта нет (окружение без домена / до первого выпуска LE).
- Приватные ключи — только в volume `proxy-certs`, НЕ в репозитории и НЕ в образе.
- Выпуск LE через standalone требует кратковременной остановки `proxy` (порт :80) — допустимо; zero-downtime продление (webroot/ACME-companion) — улучшение ([TD-011](100-known-tech-debt.md)).
- Конфигурация и процедура — [07-deployment.md](07-deployment.md#tls-сертификаты).

## Документация API (`/api/docs`, `/api/openapi.json`)

- В **production** интерактивная документация и спецификация **отключены**: FastAPI инициализируется с `docs_url=None`, `redoc_url=None`, `openapi_url=None`, когда `APP_ENV=production`. Тогда `/api/docs` и `/api/openapi.json` отдают `404`.
- В **development** (`APP_ENV=development`, по умолчанию для локальной разработки) они доступны по `/api/docs` и `/api/openapi.json` без дополнительной auth (среда разработки изолирована).
- Управляющая переменная — `APP_ENV` (`development` | `production`), фиксируется в [07-deployment.md](07-deployment.md#переменные-окружения). На проде SPA, API и (закрытые) docs за одним reverse-proxy; OpenAPI наружу не публикуется.
- Требование к backend: значение `docs_url`/`redoc_url`/`openapi_url` вычисляется из `APP_ENV` в фабрике приложения.

## HTTP-заголовки безопасности (нормативно)

Заголовки выставляются **по зоне ответственности, без дублирования** (закрытие [Q-SEC-4](99-open-questions.md)):

- **Backend (FastAPI middleware, `setdefault`)** — на ответы API (`/api/*`, отдаёт backend): `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`. `setdefault` не перетирает уже заданный заголовок. **CSP backend для JSON-API не выставляет** (CSP применяется к HTML-документу SPA).
- **nginx (`add_header ... always`)** — на ответы SPA (`location /`, статику отдаёт nginx, backend не участвует): те же `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` + **`Content-Security-Policy`** (см. ниже). HSTS на проде также может ставиться на уровне `proxy`/TLS-терминатора единожды.
- **Без дублей:** `/api/*` отдаёт backend (nginx в `location /api` security-заголовки НЕ добавляет), `/` отдаёт nginx. Зоны не пересекаются — двойных заголовков нет.
- CORS: разрешён только origin фронтенда (`CORS_ALLOW_ORIGINS` из `.env`); на проде SPA и API за одним origin — CORS можно не открывать.

### Content-Security-Policy (SPA, `location /`)

Точное нормативное значение (должно **побайтово** совпадать с конфигом nginx):

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Обоснование директив:

| Директива | Значение | Причина |
|-----------|----------|---------|
| `default-src` | `'self'` | База: всё только со своего origin (внешних доменов нет) |
| `script-src` | `'self'` | Скрипты только из собранной статики SPA; inline-скриптов нет |
| `style-src` | `'self' 'unsafe-inline'` | Tailwind/Radix используют inline-стили (`style=...`) — без `'unsafe-inline'` UI ломается. `'unsafe-inline'` для стилей — осознанный компромисс ([TD-012](100-known-tech-debt.md)) |
| `img-src` | `'self' data:` | Иконки/инлайн-SVG и data:-изображения |
| `font-src` | `'self' data:` | Шрифты self-hosted (`@fontsource`, Inter/JetBrains Mono); `data:` — на случай инлайна шрифтов сборщиком |
| `connect-src` | `'self'` | XHR/fetch только на свой origin (`/api`) — backend за тем же origin |
| `frame-ancestors` | `'none'` | CRM нельзя встраивать во фрейм (анти-clickjacking; усиливает `X-Frame-Options: DENY`) |
| `base-uri` | `'self'` | Запрет подмены `<base>` |
| `form-action` | `'self'` | Формы только на свой origin |

- **Grafana** доступна **напрямую** по `/grafana` (тот же origin); **ссылки/drill-down из карточки в UI нет** ([ADR-005, поправка](adr/ADR-005-custom-gauge-vs-grafana-embed.md#поправка-2026-06-30--удаление-drill-down-ссылки-из-карточки), см. также строку про сетевую безопасность выше). На главной — кастомные SVG-гейджи, Grafana **не встраивается во фрейм**. Поэтому `frame-src` не требуется (`frame-ancestors 'none'` достаточно), а `connect-src`/расширений под Grafana не нужно. Grafana под `/grafana` имеет собственную CSP, выставляемую самим Grafana.
- Поскольку внешних CDN/доменов нет (шрифты и ассеты self-hosted), ослаблять директивы внешними источниками не требуется.

## Управление секретами

| Секрет | Источник | Примечание |
|--------|----------|-----------|
| `ADMIN_USER` / `ADMIN_PASSWORD` | `.env` | Не в БД, не в репо |
| `JWT_SECRET` | `.env` | ≥ 32 байта случайных |
| `FERNET_KEY` | `.env` | base64 32 байта |
| `POSTGRES_PASSWORD` | `.env` | — |
| `GF_SECURITY_ADMIN_PASSWORD` | `.env` | Grafana admin |
| `TELEGRAM_BOT_TOKEN` | `.env` | секрет, маскируется в логах; нотификатор ([modules/notifier](modules/notifier/README.md)) |
| `TELEGRAM_CHAT_ID` | `.env` | не секрет в строгом смысле, но не в репо; вместе с токеном активирует нотификатор и Telegram-алерты AI-ключей |
| AI-ключи (OpenAI/Anthropic) | БД (`ai_keys.key_encrypted`) | вводятся через API, шифруются `FERNET_KEY`; не в env/логах/ответах ([modules/ai-keys](modules/ai-keys/README.md)) |

- `.env` — в `.gitignore`; в репозитории только `.env.example` без значений.
- Логи проходят через structlog с фильтром секретов (пароли, токены, ключи маскируются).

## Модель угроз (Этап 1)

| Угроза | Митигация |
|--------|-----------|
| Перебор пароля админа | rate-limit + constant-time сравнение |
| Кража JWT через XSS | токен в памяти, CSP, экранирование, no `localStorage` |
| Утечка SSH-паролей из БД | Fernet at-rest, ключ вне БД |
| Утечка AI-ключей из БД / логов / API | Fernet at-rest (`key_encrypted`), полный ключ не в ответах/логах, в UI/API только маска ([modules/ai-keys](modules/ai-keys/README.md)) |
| Утечка секретов в логи | маскирование, `no_log` в Ansible |
| Доступ к Prometheus/Grafana извне | не публикуются наружу |
| User enumeration на входе | единое сообщение об ошибке, шаг 1 без запроса |
| MITM при первом SSH | принятый риск Этапа 1 ([TD-007](100-known-tech-debt.md)) |
| SSRF/инъекции в IP-поле | строгая валидация `inet`, без выполнения произвольных команд по вводу |

## Вне scope безопасности Этапа 1

- Многофакторная аутентификация, OAuth/SSO.
- RBAC (одна роль — админ).
- Аудит-лог действий ([TD-001](100-known-tech-debt.md)).

# 05 · Безопасность

## Аутентификация (логин и выпуск JWT)

С Спринта 3 система **многопользовательская** с ролями и RBAC ([ADR-021](adr/ADR-021-rbac-users-roles.md)); `.env`-учётка становится несменяемым **супер-админом (bootstrap)** ([ADR-008](adr/ADR-008-admin-iz-env.md) с амендментом). Порядок проверки при `POST /api/auth/login`:

1. **Сначала супер-админ (`.env`).** Логин/пароль сравниваются constant-time с `ADMIN_USER`/`ADMIN_PASSWORD`. В БД супер-админ НЕ хранится. Успех → JWT: `sub=ADMIN_USER`, `role="admin"`, `superadmin=true` (без `uid`).
2. **Иначе БД-пользователь.** Ищется `users.username`; проверяется `verify_password` (bcrypt) и `is_active=true`. Успех → JWT: `sub=username`, `uid=users.id`, `role=role.name`, `superadmin=false`.
3. Неудача любой ветки → единое `401 invalid_credentials`.

- Двухшаговый UI-вход; backend проверяет креды единым запросом `POST /api/auth/login` ([ADR-002](adr/ADR-002-dvuhshagovyy-auth.md)).
- Сравнение кредов **супер-админа** — **constant-time** (`secrets.compare_digest`) для логина и пароля, чтобы исключить timing-атаки. Пароли **БД-пользователей** проверяются bcrypt (`verify_password`, [«Хэширование паролей»](#хэширование-паролей-bcrypt)).
- Сообщение об ошибке входа одинаково для неверного логина и неверного пароля и для несуществующего/деактивированного пользователя (`invalid_credentials`) — не раскрывает существование пользователя.
- Защита от перебора: rate-limit на `/api/auth/login` (по IP, по умолчанию 10 попыток / 5 мин, далее `429`). Реализация — in-memory счётчик на Этапе 1 (один воркер), вынос в Redis — будущий этап ([TD-005](100-known-tech-debt.md)).
- **Определение реального IP клиента за reverse-proxy** (нормативно): backend берёт IP в порядке `X-Real-IP` → первый адрес из `X-Forwarded-For` → `request.client.host`. Поэтому nginx ОБЯЗАН проставлять эти заголовки для `location /api` (см. [07-deployment.md](07-deployment.md#reverse-proxy-nginx-требования)). Без корректного проброса rate-limit считал бы все запросы с одного IP (адрес прокси) и блокировал всех. Доверять `X-Forwarded-For`/`X-Real-IP` допустимо, только когда backend доступен исключительно через доверенный прокси (как в нашей топологии — backend не публикуется наружу).

### Хранение `ADMIN_PASSWORD`
- На Этапе 1 допускается plaintext в `.env` (это секрет окружения, не в репозитории). Рекомендация: bcrypt-хэш `ADMIN_PASSWORD_HASH` как опция — зафиксировано как [Q-SEC-1](99-open-questions.md). По умолчанию — plaintext-сравнение constant-time.

## JWT

| Параметр | Значение |
|----------|----------|
| Алгоритм | `HS256` (симметричный, `JWT_SECRET` из `.env`) |
| TTL | `JWT_EXPIRES_MIN`, по умолчанию **1440 мин (24 часа)** |
| Claims | `sub` (=username), `role`, `superadmin` (bool), `uid` (uuid — только у БД-пользователя), `iat`, `exp`, `type:"access"` ([ADR-021](adr/ADR-021-rbac-users-roles.md#4-auth-поток-см-modulesauth-05-securitymd)) |
| Передача | заголовок `Authorization: Bearer <token>` |

**Claim'ы RBAC (побуквенно, [ADR-021](adr/ADR-021-rbac-users-roles.md)):** `sub` — username; `role` — имя роли (`"admin"` у супер-админа); `superadmin` — `true` у `.env`-супер-админа, `false` у БД-пользователя; `uid` — `users.id` (присутствует **только** у БД-пользователя, отсутствует у супер-админа). Токен без `superadmin=true` и без `uid` (легаси до Спринта 3) → `401` (повторный вход). Права в токен **не кладутся** — грузятся из БД на каждый запрос (см. [«RBAC»](#rbac--роли-права-и-enforcement)).

- Выбор HS256 (а не RS256) — один сервис, симметричный ключ проще; обоснование в [ADR-002](adr/ADR-002-dvuhshagovyy-auth.md). При появлении нескольких сервисов-валидаторов — пересмотр на RS256.
- **TTL access-токена — 1440 мин (24 часа).** По запросу пользователя срок жизни увеличен с 60 мин до 24 ч, чтобы снизить частоту релогина (админ-панель, одна учётка). Осознанный trade-off: более длинное окно валидности украденного токена. Компенсируется тем, что токен хранится только в памяти SPA (не в `localStorage`), CSP/no-referrer снижают риск XSS-кражи, а refresh-токенов нет (по истечении 24 ч — повторный вход). Значение — env-параметр `JWT_EXPIRES_MIN`, при необходимости ужесточается без изменения кода.
- Refresh-токенов на Этапе 1 нет: по истечении TTL — повторный вход. Хранение access-токена на фронте — **в памяти (Zustand)**, не в `localStorage` (снижает риск XSS-кражи). Допустимо `sessionStorage` для переживания перезагрузки — решение фронта, зафиксировано в [modules/auth](modules/auth/README.md).
- Все эндпоинты, кроме `/api/auth/login` и `/api/health`, требуют валидный JWT → иначе `401 unauthorized`.

## RBAC — роли, права и enforcement

Многопользовательский режим с правами на все страницы — [ADR-021](adr/ADR-021-rbac-users-roles.md); модель — [03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac); API — [04-api.md](04-api.md#rbac-и-enforcement-прав). **RBAC обеспечивается на сервере (`403 forbidden`); UI-гейтинг — только UX.**

### Каталог прав (канон на сервере)

Единственный источник — константа `app/domain/permissions.py::CATALOG`. Страница → допустимые действия:

| Страница | Действия |
|----------|----------|
| `dashboard` | `view` |
| `servers` / `ai-keys` / `proxies` / `backends` | `view`, `create`, `edit`, `delete` |
| `mail` | `view` |

- Страница **«Пользователи» в каталог не входит** — управление пользователями/ролями гейтится `require_admin` (`is_superadmin || role=="admin"`).
- Формат прав роли (`roles.permissions`, jsonb): `{ "<page>": ["<action>", ...] }`. Валиден ⇔ каждый ключ — известная страница (кроме `users`), каждое действие ∈ `CATALOG[page]`, без дублей → иначе `422 unprocessable`.
- Каталог отдаётся UI через `GET /api/permissions/catalog` (`require_admin`).

### Enforcement (свежая загрузка прав из БД)

`get_current_principal` декодирует JWT и **на каждый запрос** формирует `Principal(username, role, permissions, is_superadmin)`:

- `superadmin=true` → полный доступ (`permissions` = полный каталог, все `require(...)` и `require_admin` проходят).
- иначе по `uid` грузятся `users`+`roles`; если пользователь не найден **или** `is_active=false` → `401 unauthorized` (действующий JWT аннулируется **без пере-логина**). Иначе `permissions = roles.permissions`.

Свежая загрузка → **правки прав роли применяются мгновенно** (без отзыва токена; refresh-токенов нет). Стоимость — один SELECT на защищённый запрос БД-пользователя (приемлемо при NFR-1).

- Фабрика `require(page, action)` → `403 forbidden`, если не супер-админ и `action ∉ permissions[page]`. Применена ко **всем** ресурсным эндпоинтам (маппинг метод→действие — [04-api.md](04-api.md#rbac-и-enforcement-прав)).
- Фабрика `require_admin` → `403 forbidden`, если не (`is_superadmin || role=="admin"`). Гейтит Users/Roles/Permissions API.
- `forbidden()` — новая фабрика в `app/errors.py` (403, `code="forbidden"`, message «Недостаточно прав»).

## Хэширование паролей (bcrypt)

Пароли **БД-пользователей** ([03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac), [ADR-021](adr/ADR-021-rbac-users-roles.md)):

- Библиотека — **`bcrypt`** напрямую (без `passlib`), новая зависимость ([02-tech-stack.md](02-tech-stack.md#backend)). Модуль `app/infra/passwords.py`: `hash_password(plain) -> str`, `verify_password(plain, hashed) -> bool`. Cost — дефолт bcrypt (12 раундов, `bcrypt.gensalt()`).
- В БД хранится **только** `users.password_hash`. **Plaintext-пароль никогда** не хранится, не логируется (structlog-фильтр секретов), не возвращается ни в одном ответе API (в схемах `User*` поля `password` нет на выход).
- Политика пароля: 8–128 символов (валидация Pydantic). **Известное поведение bcrypt:** значимы только первые 72 **байта** (для кириллицы в UTF-8 — ~36 символов); это документированное ограничение bcrypt, принято осознанно (не дефект).
- Пароль **супер-админа** (`.env`) bcrypt НЕ хэшируется — сравнение plaintext constant-time ([ADR-008](adr/ADR-008-admin-iz-env.md) амендмент); опция `ADMIN_PASSWORD_HASH` ([Q-SEC-1](99-open-questions.md)) для супер-админа не вводится.

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

## Защита ключа почты

Ключ внешнего почтового сервиса (`postapp.store`) — системный секрет того же класса, что AI-ключи и `TELEGRAM_*`. Модуль «Почты» — read-through-прокси ([ADR-012](adr/ADR-012-mail-read-through-proxy.md), [modules/mail](modules/mail/README.md)).

- `MAIL_API_KEY` — **только из env**, задаётся администратором развёртывания (НЕ через UI). В БД не хранится (у модуля почты хранилища нет).
- Ключ подставляется backend'ом **только** в заголовок `X-API-Key` исходящего запроса к `postapp.store`. **Никогда** не возвращается в ответах CRM API, не логируется (structlog-фильтр секретов), не передаётся в SPA и не попадает в query-строку/URL.
- **Фронт наружу не ходит** — SPA обращается только к `/api/mail/*` (тот же origin, CSP `connect-src 'self'`); прямой вызов `postapp.store` из браузера исключён.
- HTML-тело письма — недоверенный контент третьих лиц — рендерится **только** в sandbox-iframe (`srcDoc` + `sandbox` без `allow-scripts`/`allow-same-origin`): скрипты письма не исполняются, доступа к origin/куки/JWT CRM нет ([ADR-012](adr/ADR-012-mail-read-through-proxy.md), [modules/mail](modules/mail/README.md#изоляция-html-тела-нормативно)). Согласуется с CSP SPA (`frame-ancestors 'none'`, `script-src 'self'`). Удалённые (remote https) изображения тела письма отрисовываются — `img-src` расширен до `'self' data: https:` ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)); при этом sandbox без `allow-scripts`/`allow-same-origin` и `script-src 'self'` не изменены — грузятся только пассивные `<img>`.

## Защита паролей прокси

Пароль прокси — секрет того же класса, что SSH-пароли и AI-ключи ([modules/proxies](modules/proxies/README.md#безопасность-пароля-нормативно), [ADR-019](adr/ADR-019-proxies-availability-monitor.md)).

- Пароль прокси (опциональный) шифруется **Fernet** тем же `FERNET_KEY` сразу при `POST /api/proxies`; в БД — только `password_encrypted bytea` (`NULL`, если пароль не задан) ([03-data-model.md](03-data-model.md#таблица-proxies)). Переиспользуются `encrypt_secret`/`decrypt_secret`.
- Расшифровка — только в памяти монитора непосредственно перед сборкой URL (`scheme://user:pass@host:port`) и HTTP-запросом через прокси; расшифрованное значение и собранный URL не логируются и не покидают процесс.
- **Пароль (в любом виде) НЕ возвращается ни в одном ответе API.** Вместо него — производный флаг `has_password: bool`; фрагменты пароля не хранятся и не раскрываются (маски по фрагментам, как у AI-ключей, здесь нет).
- **`username` (логин прокси) — не секрет:** хранится plaintext, возвращается в API как есть. Осознанно (нужен для отображения и сборки URL; сам по себе доступа не даёт без пароля/хоста).
- Пароль/URL прокси **не передаются** в query-строке и не пишутся в structlog (фильтр секретов).

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
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Обоснование директив:

| Директива | Значение | Причина |
|-----------|----------|---------|
| `default-src` | `'self'` | База: всё только со своего origin (внешних доменов нет) |
| `script-src` | `'self'` | Скрипты только из собранной статики SPA; inline-скриптов нет |
| `style-src` | `'self' 'unsafe-inline'` | Tailwind/Radix используют inline-стили (`style=...`) — без `'unsafe-inline'` UI ломается. `'unsafe-inline'` для стилей — осознанный компромисс ([TD-012](100-known-tech-debt.md)) |
| `img-src` | `'self' data: https:` | Иконки/инлайн-SVG, data:-изображения **и удалённые (remote https) изображения писем**. `https:` добавлен для отрисовки картинок в HTML-теле писем (sandbox-iframe наследует CSP страницы и не может ослабить её через meta) — [ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md). Только `https:` (не `http:`/`*`); грузятся **пассивные** `<img>`, скрипты остаются заблокированы (sandbox без `allow-scripts`, `script-src 'self'` не тронут). Компромисс — допускаются трекинг-пиксели (отправитель видит факт открытия письма); referrer не утекает (`Referrer-Policy: no-referrer` + `referrerPolicy=no-referrer` на iframe). `cid:`-инлайн-картинки не резолвятся ([TD-026](100-known-tech-debt.md)) |
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
| `ADMIN_USER` / `ADMIN_PASSWORD` | `.env` | Супер-админ (bootstrap); не в БД, не в репо ([ADR-008](adr/ADR-008-admin-iz-env.md)+[ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Пароли БД-пользователей | БД (`users.password_hash`) | bcrypt-хэш at-rest; plaintext не в env/логах/ответах ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| `JWT_SECRET` | `.env` | ≥ 32 байта случайных |
| `FERNET_KEY` | `.env` | base64 32 байта |
| `POSTGRES_PASSWORD` | `.env` | — |
| `GF_SECURITY_ADMIN_PASSWORD` | `.env` | Grafana admin |
| `TELEGRAM_BOT_TOKEN` | `.env` | секрет, маскируется в логах; нотификатор ([modules/notifier](modules/notifier/README.md)) |
| `TELEGRAM_CHAT_ID` | `.env` | не секрет в строгом смысле, но не в репо; вместе с токеном активирует нотификатор и Telegram-алерты AI-ключей |
| AI-ключи (OpenAI/Anthropic) | БД (`ai_keys.key_encrypted`) | вводятся через API, шифруются `FERNET_KEY`; не в env/логах/ответах ([modules/ai-keys](modules/ai-keys/README.md)) |
| Пароли прокси | БД (`proxies.password_encrypted`) | опциональны; вводятся через API, шифруются `FERNET_KEY`; не в env/логах/ответах (в API — только `has_password`); `username` — не секрет ([modules/proxies](modules/proxies/README.md)) |
| `MAIL_API_KEY` | `.env` | секрет внешнего почтового API; только в заголовке `X-API-Key` backend→`postapp.store`; не в БД/логах/ответах/SPA/URL ([modules/mail](modules/mail/README.md)) |

- `.env` — в `.gitignore`; в репозитории только `.env.example` без значений.
- Логи проходят через structlog с фильтром секретов (пароли, токены, ключи маскируются).

## Модель угроз (Этап 1)

| Угроза | Митигация |
|--------|-----------|
| Перебор пароля админа | rate-limit + constant-time сравнение |
| Кража JWT через XSS | токен в памяти, CSP, экранирование, no `localStorage` |
| Утечка SSH-паролей из БД | Fernet at-rest, ключ вне БД |
| Утечка AI-ключей из БД / логов / API | Fernet at-rest (`key_encrypted`), полный ключ не в ответах/логах, в UI/API только маска ([modules/ai-keys](modules/ai-keys/README.md)) |
| Утечка паролей прокси из БД / логов / API | Fernet at-rest (`password_encrypted`), пароль/URL не в ответах/логах, в API только `has_password` ([modules/proxies](modules/proxies/README.md)) |
| Утечка секретов в логи | маскирование, `no_log` в Ansible |
| Доступ к Prometheus/Grafana извне | не публикуются наружу |
| User enumeration на входе | единое сообщение об ошибке, шаг 1 без запроса; та же ошибка для несуществующего/деактивированного БД-пользователя |
| Обход UI-гейтинга прямым запросом к API | RBAC на сервере (`403 forbidden`); UI-скрытие — только UX ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Эскалация привилегий через устаревший токен после смены роли | права грузятся из БД на каждый запрос; деактивация/смена роли применяется без пере-логина ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Утечка паролей пользователей из БД/логов/API | bcrypt-хэш at-rest (`users.password_hash`); plaintext не хранится/не логируется/не в ответах ([ADR-021](adr/ADR-021-rbac-users-roles.md)) |
| Lockout (потеря доступа через данные) | супер-админ вне БД — вход работает даже при пустой/битой таблице ролей ([ADR-008](adr/ADR-008-admin-iz-env.md) амендмент) |
| MITM при первом SSH | принятый риск Этапа 1 ([TD-007](100-known-tech-debt.md)) |
| SSRF/инъекции в IP-поле | строгая валидация `inet`, без выполнения произвольных команд по вводу |
| Утечка `MAIL_API_KEY` в SPA/логи/URL | ключ только на backend, в заголовке `X-API-Key`; не в ответах/логах/SPA; фронт наружу не ходит ([modules/mail](modules/mail/README.md)) |
| XSS/кража JWT через HTML-тело письма | рендер только в sandbox-iframe (без `allow-scripts`/`allow-same-origin`), CSP SPA; скрипты письма не исполняются. `img-src ... https:` ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) разрешает только пассивные `<img>` — XSS-инвариант не ослаблен ([ADR-012](adr/ADR-012-mail-read-through-proxy.md)) |
| Трекинг-пиксели в письме (отправитель узнаёт факт открытия) | принятый компромисс remote-картинок ([ADR-015](adr/ADR-015-csp-img-src-remote-mail-images.md)) — стандартно для почтовых клиентов; referrer не утекает (`Referrer-Policy: no-referrer` + `referrerPolicy=no-referrer` на iframe), только `https:` (не `http:`); анти-трекинг-прокси отложён |

## Вне scope безопасности

- Многофакторная аутентификация, OAuth/SSO.
- ~~RBAC (одна роль — админ)~~ — **реализован** в Спринте 3 ([ADR-021](adr/ADR-021-rbac-users-roles.md)): роли + права на все страницы, серверный enforcement, bcrypt-хэш паролей БД-пользователей, `.env`-супер-админ как bootstrap.
- Аудит-лог действий пользователей ([TD-001](100-known-tech-debt.md)).
- UI-смена пароля супер-админа (`.env`) — by design только через `.env`/деплой; UI-управление паролями есть для БД-пользователей ([TD-009](100-known-tech-debt.md)).
- Refresh-токены, отзыв конкретного токена, история сессий.

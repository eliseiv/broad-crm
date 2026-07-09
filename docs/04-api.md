# 04 · API-контракты

Базовый префикс: `/api`. Формат — JSON (UTF-8). Аутентификация — `Authorization: Bearer <JWT>` (кроме `/auth/*` и `/health`). Все временные метки — ISO 8601 UTC.

## Общие правила

### Заголовки
- Запрос с телом: `Content-Type: application/json`.
- Защищённые эндпоинты: `Authorization: Bearer <token>`.

### Единый формат ошибки

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "Неверный логин или пароль",
    "details": null
  }
}
```

| HTTP | `code` | Когда |
|------|--------|-------|
| 400 | `validation_error` | Невалидное тело/параметры (детали в `details[]`) |
| 401 | `invalid_credentials` | Неверные логин/пароль на `/auth/login` |
| 401 | `unauthorized` | Отсутствует/просрочен/невалиден JWT |
| 403 | `forbidden` | Аутентифицирован, но нет права на действие/страницу; **либо** попытка эскалации прав роли / правки встроенной роли `admin` не-админом ([RBAC](#rbac-и-enforcement-прав), [Roles](#roles), [ADR-022](adr/ADR-022-teams-nav-categories.md)) |
| 404 | `server_not_found` | Сервера с таким `id` нет |
| 404 | `ai_key_not_found` | AI-ключа с таким `id` нет |
| 404 | `proxy_not_found` | Прокси с таким `id` нет |
| 404 | `backend_not_found` | Бэка с таким `id` нет |
| 404 | `user_not_found` | Пользователя с таким `id` нет ([Users](#users)) |
| 404 | `role_not_found` | Роли с таким `id` нет ([Roles](#roles)) |
| 404 | `team_not_found` | Команды с таким `id` нет ([Teams](#teams)) |
| 404 | `mail_message_not_found` | Письма с таким `id` нет (проброс от внешнего сервиса при reply) |
| 404 | `sms_number_not_found` | Номера с таким `id` нет ([SMS](#sms)) |
| 404 | `secret_not_set` | Reveal-эндпоинт секрета вызван для ресурса без секрета (напр. прокси без пароля) — [reveal секретов](#reveal-секретов-по-требованию-adr-035) ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)) |
| 404 | `sms_team_not_found` | Команда для переноса номера не найдена ([SMS](#sms)) |
| 401 | `invalid_twilio_signature` | Неверная/отсутствующая подпись `X-Twilio-Signature` на webhook приёма SMS ([SMS](#sms)) |
| 401 | `invalid_init_data` | Невалидный HMAC/структура Telegram `init_data` ([SMS](#sms)) |
| 401 | `init_data_expired` | Протух `auth_date` в Telegram `init_data` ([SMS](#sms)) |
| 403 | `invalid_webhook_secret` | Неверный секрет-токен Telegram-webhook SMS-бота ([SMS](#sms)) |
| 403 | `sms_operator_not_provisioned` | Беспарольный Telegram-SSO (`POST /api/sms/telegram/auth`) не сопоставил Telegram ни с одним активным CRM-оператором ([SMS](#sms), [ADR-031](adr/ADR-031-sms-operator-mini-app.md)) |
| 409 | `server_conflict` | Сервер с таким `ip` уже существует |
| 409 | `backend_code_taken` | Бэк с таким `code` уже существует ([Backends](#backends)) |
| 409 | `username_taken` | Пользователь с таким `username` уже существует ([Users](#users)) |
| 409 | `telegram_taken` | Пользователь с таким `telegram` уже существует ([Users](#users), [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md); заменяет прежний `email_taken`) |
| 409 | `role_name_taken` | Роль с таким `name` уже существует ([Roles](#roles)) |
| 409 | `role_in_use` | Роль назначена ≥1 пользователю — удаление запрещено ([Roles](#roles)) |
| 409 | `team_name_taken` | Команда с таким `name` уже существует ([Teams](#teams)) |
| 409 | `password_already_set` | Установка пароля первого входа для пользователя, у которого пароль уже задан ([Auth](#post-apiauthset-password), [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)) |
| 422 | `unprocessable` | Семантически некорректные данные (напр. невалидный IP; невалидный `username`/`telegram`; `permissions` вне каталога; несуществующий `role_id` при создании/правке пользователя — [Users](#users)/[Roles](#roles)) |
| 429 | `rate_limited` | Превышен лимит попыток входа |
| 500 | `internal_error` | Непредвиденная ошибка |
| 502 | `prometheus_unavailable` | Prometheus недоступен/таймаут при запросе метрик |
| 502 | `mail_unavailable` | Внешний почтовый сервис недоступен/таймаут/`5xx` ([Mail](#mail)) |
| 503 | `provisioning_unavailable` | Невозможно запустить фоновую задачу провижининга |
| 503 | `mail_not_configured` | Почта не настроена (`MAIL_API_KEY` пуст) ([Mail](#mail)) |
| 400 | `invalid_cursor` | Битый/недекодируемый keyset-курсор ленты SMS ([SMS](#sms)) |
| 400 | `invalid_limit` | `limit` ленты SMS вне диапазона `[1,100]` ([SMS](#sms)) |
| 502 | `twilio_error` | Сбой Twilio API при `POST /api/sms/numbers/sync` (сеть/5xx/таймаут/аутентификация) ([SMS](#sms)) |
| 503 | `twilio_not_configured` | Twilio не настроен (`TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` пусты) ([SMS](#sms)) |

`validation_error.details` — массив `{ "field": "ip", "message": "..." }`.

---

## Reveal секретов по требованию ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md))

Секреты сущностей (`ssh_password` сервера, `password` прокси, полный `key` ИИ-ключа) **никогда** не отдаются в общих list/detail-ответах. Раскрытие — **только** по явному действию через выделенный per-resource эндпоинт, гейт **`require("<page>", "edit")`** соответствующей страницы (супер-админ/роль `admin` — всегда). Решение и обоснование гейта/аудита — [05-security.md](05-security.md#reveal-секретов-по-требованию-adr-035), [ADR-035](adr/ADR-035-detail-view-secret-reveal.md).

Эндпоинты:

| Эндпоинт | Секрет | Гейт | Пусто |
|----------|--------|------|-------|
| `GET /api/servers/{id}/ssh-password` | `ssh_password` (расшифровка `ssh_password_encrypted`) | `servers:edit` | секрет всегда есть (`NOT NULL`) |
| `GET /api/proxies/{id}/password` | `password` (расшифровка `password_encrypted`) | `proxies:edit` | нет пароля → `404 secret_not_set` |
| `GET /api/ai-keys/{id}/key` | полный `key` (расшифровка `key_encrypted`) | `ai-keys:edit` | секрет всегда есть |

### Схема `SecretRevealResponse`
```json
{ "value": "s3cr3t-plaintext" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `value` | string | Расшифрованный секрет (plaintext). Расшифровка — `app/infra/crypto.decrypt_secret` в памяти обработчика; не логируется |

**Общие правила (нормативно):**
- **Response 200** — `SecretRevealResponse`, заголовок ответа **`Cache-Control: no-store`** обязателен (секрет не кэшируется прокси/браузером).
- HTTP-метод **GET**: секрет — в теле ответа, **не** в URL (в URL только `id`) → в access-логах секрета нет.
- Каждый успешный reveal порождает аудит-лог `secret_revealed` (`actor`/`resource_type`/`resource_id`/`at`, **без** значения) — [05-security.md](05-security.md#reveal-секретов-по-требованию-adr-035).
- **Ошибки (общие для всех трёх):** `401 unauthorized`; `403 forbidden` (нет права `<page>:edit`); `404 <resource>_not_found` (`server_not_found`/`proxy_not_found`/`ai_key_not_found`); для `GET /api/proxies/{id}/password` дополнительно `404 secret_not_set` (у прокси нет пароля, `has_password=false`).

---

## Auth

Двухшаговый вход — это UX (два экрана). Бэкенд проверяет креды единым запросом на шаге 2. Обоснование — [ADR-002](adr/ADR-002-dvuhshagovyy-auth.md). Эндпоинт шага 1 на сервере НЕ требуется (шаг 1 — клиентский переход), что исключает user-enumeration.

### POST `/api/auth/login`
Проверяет идентификатор (+пароль) и возвращает JWT **или** сигнал «требуется установка пароля первого входа». Решение — [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md), [05-security.md](05-security.md#аутентификация-логин-и-выпуск-jwt).

**Ветки (нормативно):** сперва `.env`-**супер-админ** (constant-time против `ADMIN_USER`/`ADMIN_PASSWORD` → JWT `role="admin"`, `superadmin=true`); иначе **БД-пользователь** — поиск по `username` точно, иначе по нормализованному `telegram`; при `is_active`:
- `password_hash IS NOT NULL` (парольный): `verify_password` bcrypt → JWT `uid`, `role=role.name`, `superadmin=false`. Неверный/отсутствующий пароль → `401`.
- `password_hash IS NULL` (беспарольный): **вход не выполняется** — возвращается `password_setup_required: true` + limited-scope **setup-token** (см. ниже; `password` из запроса игнорируется).

Неудача (не найден / `is_active=false` / неверный пароль парольной ветки) → единое `401 invalid_credentials`.

> **Метка первого входа ([ADR-028](adr/ADR-028-user-status-first-login.md)).** При **успешном** входе БД-пользователя по паролю (парольная ветка) сервер идемпотентно проставляет `users.first_login_at = now()`, если оно ещё `NULL` (первый вход). Беспарольная ветка (`password_setup_required: true`) метку **не** ставит — вход ещё не выполнен (выдан только setup-token); её проставляет `POST /api/auth/set-password`. Влияет на производный `UserListItem.status` («Ожидает входа» → «Активен»).

**Request**
```json
{ "username": "admin", "password": "secret" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `username` | string | required, 1–128. **Идентификатор входа** — логин **или** телеграм-ник ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). Имя поля сохранено для совместимости; семантика расширена |
| `password` | string? | **опц.**, 1–256. Для парольного пользователя — обязателен по смыслу (пустой → `401`); для беспарольного — игнорируется (ответ `password_setup_required`) |

**Response 200** — дискриминированный по `password_setup_required` (схема `LoginResponse`):

- **Успех** (`password_setup_required: false`):
```json
{ "password_setup_required": false, "access_token": "eyJ...", "token_type": "bearer", "expires_in": 86400 }
```
- **Требуется установка пароля первого входа** (`password_setup_required: true`) — беспарольный пользователь:
```json
{ "password_setup_required": true, "setup_token": "eyJ...", "token_type": "bearer", "expires_in": 600 }
```

| Поле | Тип | Примечание |
|------|-----|-----------|
| `password_setup_required` | boolean | Дискриминатор. `false` — обычный вход; `true` — нужно задать пароль (см. [`POST /api/auth/set-password`](#post-apiauthset-password)) |
| `access_token` | string | Только при `false`. Обычный access-JWT (`type:"access"`) |
| `setup_token` | string | Только при `true`. **Limited-scope** JWT (`type:"pwd_setup"`, `uid`), принимается **только** `set-password`; TTL `PWD_SETUP_TOKEN_EXPIRES_MIN` (default 10 мин). Ресурсные/Users/Roles/Teams-эндпоинты его отвергают (`401`) |
| `token_type` | string | `"bearer"` |
| `expires_in` | integer | TTL выданного токена в секундах (`access` — 86400; `setup` — 600 при default 10 мин) |

**Ошибки:** `401 invalid_credentials` (идентификатор и/или пароль неверны — без указания, что именно), `400 validation_error`, `429 rate_limited`.

> Защита от перебора: rate-limit по IP (например, 10 попыток / 5 мин). Конкретный механизм — [modules/auth](modules/auth/README.md). Для **парольных** пользователей сообщение об ошибке одинаково для неверного логина и пароля (не раскрывать существование). Для **беспарольных** ответ `password_setup_required` раскрывает беспарольность идентификатора — осознанный побочный эффект модели «открытого первого входа» ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md), [05-security.md](05-security.md#модель-открытого-первого-входа-нормативно)).

### POST `/api/auth/set-password`
Установка пароля **первого входа** беспарольным пользователем (модель «открытого первого входа», [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). Auth — `Authorization: Bearer <setup_token>` (limited-scope `type:"pwd_setup"` из ответа login). **Обычный access-токен здесь не принимается**; ресурсные эндпоинты, наоборот, setup-token не принимают.

**Request** — схема `SetPasswordRequest`:
```json
{ "password": "my-new-pass-123" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `password` | string | required, 8–128. Хэшируется bcrypt; в ответе не возвращается |

**Response 200** — схема `LoginResponse` (успех, `password_setup_required: false`): пользователь **сразу залогинен** обычным access-токеном.
```json
{ "password_setup_required": false, "access_token": "eyJ...", "token_type": "bearer", "expires_in": 86400 }
```

> Установка успешна только если пользователь **всё ещё беспарольный** (`password_hash IS NULL`) и активен. После неё `password_hash != NULL` → вход только по паролю. Так как пользователь сразу залогинен, сервер идемпотентно проставляет `users.first_login_at = now()`, если оно `NULL` — это его **первый вход** ([ADR-028](adr/ADR-028-user-status-first-login.md)).

**Ошибки:** `401 unauthorized` (нет/просрочен/невалиден setup-token, либо предъявлен обычный access-token), `422 unprocessable` (слабый/короткий пароль), `409 password_already_set` (пароль уже задан — повтор/гонка), `400 validation_error`.

### GET `/api/auth/me`
Проверка валидности токена / получение профиля сессии **и прав текущего принципала** (для UI-гейтинга). Требует JWT. Права берутся из свежей загрузки принципала ([RBAC](#users), [ADR-021](adr/ADR-021-rbac-users-roles.md)).

**Response 200** — схема `MeResponse`:
```json
{
  "username": "Никита",
  "role": "Оператор",
  "is_superadmin": false,
  "sees_all_sms_teams": false,
  "sees_all_mail_teams": false,
  "permissions": {
    "dashboard": ["view"],
    "servers": ["view"],
    "mail": ["view"]
  }
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `username` | string | `sub` токена (для супер-админа — `ADMIN_USER`, для БД-пользователя — `username`) |
| `role` | string | Имя роли. Для супер-админа — `"admin"` |
| `is_superadmin` | boolean | `true` — `.env`-супер-админ (полный доступ); `false` — БД-пользователь |
| `sees_all_sms_teams` | boolean | **Производный admin-уровень видимости SMS** ([ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md), [ADR-036](adr/ADR-036-sms-team-filter-admin-only.md)): `= is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)` — тот же предикат, что backend `get_sms_scope`. `true` ⇔ актор видит **все** SMS-команды (супер-админ / роль с полным каталогом). Frontend по нему решает, показывать ли фильтр «Все команды» на `/sms` (см. [08-design-system.md](08-design-system.md#вкладка-сообщения)). Backend — единственный источник (фронт не дублирует `permissions_subset`) |
| `sees_all_mail_teams` | boolean | **Производный admin-уровень видимости почты** ([ADR-038](adr/ADR-038-mail-headless-integration.md) §3): тот же предикат `is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)`, что backend `get_mail_scope`. `true` ⇔ актор видит **все** почтовые команды. Frontend по нему решает, показывать ли фильтр «Все команды» на `/mail`. Backend — единственный источник |
| `permissions` | object | Права `{ "<page>": ["<action>", ...] }`. Для супер-админа — **полный каталог** (все страницы/действия). Для БД-пользователя — `roles.permissions` |

> `permissions` — производное для UI-гейтинга (фильтрация вкладок по `view`, скрытие кнопок Создать/Редактировать/Удалить). **Безопасность обеспечивается сервером** (`403 forbidden`), UI-гейтинг — только UX. Ключи `permissions` — из каталога ([`GET /api/permissions/catalog`](#permissions)); страница `users` в `permissions` не фигурирует (гейтится по `is_superadmin || role=="admin"`). `sees_all_sms_teams` — производный булев признак admin-уровня для SMS (вычисляется backend по [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)); фронт использует его как источник истины вместо повторного вычисления полного каталога.

**Ошибки:** `401 unauthorized`.

---

## Servers

### GET `/api/servers`
Список серверов с последними метриками и статусом. Требует JWT.

**Сортировка:** `position ASC, created_at DESC, id` — порядок задаётся пользователем через drag-and-drop (`PATCH /api/servers/order`, [«Перестановка»](#перестановка-порядок-карточек)); при равных `position` новые выше; `id` — финальный тай-брейк ([03-data-model.md](03-data-model.md#колонка-position-порядок-карточек)). Пагинации на Этапе 1 нет (до ~50 серверов, NFR-5).

**Кэш и устойчивость:** ответ может отдаваться из короткого TTL-кэша (`METRICS_CACHE_TTL_SEC`, default 5 с) с single-flight, и устойчив к транзиентным ошибкам Prometheus (ретраи на `429`/`5xx`/таймаут, ограничение конкурентности). Деградация (`metrics=null`/`online=false`) наступает только при **устойчивой** недоступности Prometheus (после исчерпания ретраев); кэш не маскирует недоступность дольше своего TTL. Детали — [modules/monitoring](modules/monitoring/README.md#устойчивость-read-path-нормативно).

**Query (опц.):** `status` ∈ {`pending`,`installing`,`online`,`error`} — фильтр.

**Response 200**
```json
{
  "items": [
    {
      "id": "8f1d...e2",
      "name": "Server 01",
      "ip": "10.0.0.12",
      "ssh_user": "root",
      "exporter_port": 9100,
      "provision_status": "online",
      "position": 0,
      "online": true,
      "uptime_seconds": 1323120,
      "last_updated": "2026-06-28T18:55:00Z",
      "metrics": {
        "cpu":  { "usage_percent": 65.0, "zone": "green",  "detail": { "value": null, "total": 8, "unit": "cores" } },
        "ram":  { "usage_percent": 72.0, "zone": "green",  "detail": { "value": 11.5, "total": 16.0, "unit": "GB" } },
        "ssd":  { "usage_percent": 48.0, "zone": "green",  "detail": { "value": 238.0, "total": 500.0, "unit": "GB" } }
      }
    }
  ]
}
```
- `online` — из Prometheus `up`. Если `provision_status != online` или `up == 0` → `online=false`, `metrics` могут быть `null`.
- `ssh_user` — string, SSH-логин целевого сервера ([03-data-model.md](03-data-model.md#таблица-servers)). **Не секрет** (по аналогии с `username` прокси, [ADR-019](adr/ADR-019-proxies-availability-monitor.md)); отображается в read-only detail-view сервера ([ADR-035](adr/ADR-035-detail-view-secret-reveal.md)). SSH-пароль здесь **не** отдаётся — только через reveal-эндпоинт [`GET /api/servers/{id}/ssh-password`](#get-apiserversidssh-password).
- `position` — `integer`, порядок карточки (drag-and-drop). Меньше = выше. Изменяется через `PATCH /api/servers/order`.
- `zone` ∈ {`green`,`yellow`,`red`} вычисляется backend по порогам (см. ниже) для единообразия с UI.
- Для серверов в `pending`/`installing`/`error` поля `metrics`, `uptime_seconds` = `null`.

#### Схема объекта метрики и `detail`

Каждая из `cpu`/`ram`/`ssd` — объект `{ usage_percent: number, zone: "green"|"yellow"|"red", detail: Detail }`, где `Detail = { value: number|null, total: number|null, unit: string }`.

`detail` различается по метрике (закрытие [Q-MON-1](99-open-questions.md)):

| Метрика | `unit` | `value` | `total` | Условие |
|---------|--------|---------|---------|---------|
| RAM | `"GB"` | used GB | total GB | всегда (метрики памяти доступны) |
| SSD | `"GB"` | used GB | total GB | всегда (метрики ФС `/` доступны) |
| CPU | `"cores"` | `null` | число логических ядер | всегда (стандартизировано) |

> **CPU `detail` ВСЕГДА в ядрах** (переоткрытие/обновление [Q-MON-1](99-open-questions.md)): `unit:"cores"`, `value:null`, `total:<число логических ядер>`. Вариант с частотой (`GHz`) убран из scope ради единообразия отображения между серверами (на VM частота часто недоступна → был разнобой). UI показывает только `total` (например, `8 ядер` — локализацию единицы и формы мн.ч. см. [08-design-system.md](08-design-system.md#локализация-ui-русский-словарь-строк)). Если число ядер недоступно — `total:null` (UI скрывает строку абсолютных значений CPU). Поддержка частоты (GHz) — отложена ([TD-013](100-known-tech-debt.md)). `usage_percent` для CPU не зависит от `detail` и считается всегда по `node_cpu_seconds_total`.
>
> `unit` в API остаётся техническим строковым идентификатором `"cores"` (`"GB"` для RAM/SSD); локализованное отображение единиц (`ядра`/`ГБ`) — на стороне frontend.

#### Доступность метрик: `up==0` / отсутствие данных vs Prometheus down

Различаются два независимых случая (нормативно для backend и frontend):

| Ситуация | Поведение | HTTP |
|----------|-----------|------|
| **Prometheus доступен**, но `up==0` (сервер offline) ИЛИ конкретная метрика отсутствует в ответе | `online=false`; объекты `cpu`/`ram`/`ssd` присутствуют, но их `detail.value`/`detail.total = null` и/или `metrics=null` — ложные/нулевые значения НЕ подставляются. `usage_percent` отдаётся только если метрика реально получена | `200` |
| **Prometheus недоступен** (соединение/таймаут к самому Prometheus) | в `GET /api/servers` — graceful degradation (`metrics=null`, `online=false`, список `200`); в `GET /api/servers/{id}/metrics` — явная ошибка | список: `200`; одиночные метрики: `502 prometheus_unavailable` |

Ключевое: `502` — **только** при недоступности самого Prometheus. Случай «Prometheus ответил, но сервер `up==0` или метрики нет» НЕ является `502` — это валидный `200` с `online=false` и `null`-полями, чтобы не показывать пользователю выдуманные значения.

**Ошибки:** `401 unauthorized`. Недоступность Prometheus НЕ роняет список: для таких серверов `metrics=null`, `online=false` (graceful degradation), список возвращается `200`.

### POST `/api/servers`
Создаёт сервер и запускает асинхронный провижининг. Требует JWT.

**Request**
```json
{ "name": "Server 02", "ip": "10.0.0.13", "ssh_user": "root", "ssh_password": "p@ss" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |
| `ip` | string | required, валидный IPv4/IPv6 |
| `ssh_user` | string | required, 1–64 |
| `ssh_password` | string | required, 1–256 |

**Response 202 Accepted**
```json
{ "id": "a1b2...", "name": "Server 02", "ip": "10.0.0.13", "ssh_user": "root", "exporter_port": 9100, "provision_status": "pending", "position": 0 }
```
> `202`, т.к. установка не мгновенна; статус отслеживается через `GET /api/servers/{id}/status`. Пароль в ответе не возвращается. `position` берёт `DEFAULT 0` — новая карточка появляется вверху списка (тай-брейк `created_at DESC`).

**Ошибки:** `400 validation_error`, `409 server_conflict` (дубликат `ip`), `422 unprocessable` (невалидный IP), `503 provisioning_unavailable`.

### PATCH `/api/servers/{id}`
Редактирование сервера. На Этапе 1 меняется **только `name`**. Требует JWT. `ip`/`ssh_user`/`ssh_password`/`exporter_port`/провижининг НЕ затрагиваются (переустановка агента вне scope).

**Request**
```json
{ "name": "Server 01 (renamed)" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |

**Response 200** — обновлённый summary-объект сервера (без метрик):
```json
{ "id": "8f1d...e2", "name": "Server 01 (renamed)", "ip": "10.0.0.12", "ssh_user": "root", "exporter_port": 9100, "provision_status": "online", "position": 0, "created_at": "2026-06-28T18:00:00Z", "updated_at": "2026-07-01T12:00:00Z" }
```
> Смена `name` обновляет `updated_at` и, при `provision_status=online`, отражается в file_sd-таргете (label `name`) при следующей записи. Немедленная перезапись таргета для переименования не требуется (label `name` информативный, скрейп идёт по `instance`).

**Ошибки:** `401 unauthorized`, `404 server_not_found`, `400 validation_error` (пустое/слишком длинное `name`).

### GET `/api/servers/{id}/metrics`
Текущие метрики одного сервера (для polling карточки). Требует JWT.

**Response 200**
```json
{
  "id": "8f1d...e2",
  "online": true,
  "uptime_seconds": 1323120,
  "last_updated": "2026-06-28T18:55:00Z",
  "cpu":  { "usage_percent": 65.0, "zone": "green",  "detail": { "value": null, "total": 8,    "unit": "cores" } },
  "ram":  { "usage_percent": 72.0, "zone": "green",  "detail": { "value": 11.5, "total": 16.0, "unit": "GB" } },
  "ssd":  { "usage_percent": 48.0, "zone": "green",  "detail": { "value": 238.0,"total": 500.0,"unit": "GB" } }
}
```
При `up==0` или отсутствии метрики (Prometheus доступен) → `200` с `online=false` и `null`-полями `detail`/значений (см. [«Доступность метрик»](#доступность-метрик-up0--отсутствие-данных-vs-prometheus-down)), а не `502`.

**Ошибки:** `401`, `404 server_not_found`, `502 prometheus_unavailable` — **только** при недоступности самого Prometheus (соединение/таймаут), т.к. это endpoint про метрики.

### GET `/api/servers/{id}/status`
Лёгкий endpoint статуса провижининга (для прогресс-индикатора). Требует JWT.

**Response 200**
```json
{ "id": "a1b2...", "provision_status": "installing", "error_message": null, "updated_at": "2026-06-28T18:50:11Z" }
```
**Ошибки:** `401`, `404 server_not_found`.

### DELETE `/api/servers/{id}`
Удаляет сервер из мониторинга (снимает file_sd-таргет, удаляет запись). Требует JWT.

**Response 204** (без тела).

**Ошибки:** `401`, `404 server_not_found`.
> node_exporter на целевом сервере не удаляется ([TD-002](100-known-tech-debt.md)).

### GET `/api/servers/{id}/ssh-password`
Reveal SSH-пароля сервера по требованию (для detail-view). Гейт **`require("servers","edit")`** (супер-админ/`admin` — всегда). Расшифровка `ssh_password_encrypted` (`decrypt_secret`) в памяти обработчика. Общие правила reveal (метод, `Cache-Control: no-store`, аудит) — [«Reveal секретов»](#reveal-секретов-по-требованию-adr-035).

**Response 200** — `SecretRevealResponse`:
```json
{ "value": "p@ss" }
```
**Ошибки:** `401 unauthorized`, `403 forbidden` (нет `servers:edit`), `404 server_not_found`.

---

## AI Keys

Реестр API-ключей AI-провайдеров с автоматической проверкой валидности. Модуль — [modules/ai-keys](modules/ai-keys/README.md), модель — [03-data-model.md](03-data-model.md#таблица-ai_keys), решение — [ADR-010](adr/ADR-010-ai-key-monitor-vnutri-backend.md). Все эндпоинты требуют JWT. **Полный ключ никогда не возвращается** — только маска `key_masked`.

### Схема `AiKeyListItem`

```json
{
  "id": "3f2a...c1",
  "name": "OpenAI Prod",
  "provider": "openai",
  "key_masked": "sk-p…bA3T",
  "check_status": "working",
  "error_message": null,
  "position": 0,
  "last_checked_at": "2026-07-01T10:15:00Z",
  "created_at": "2026-07-01T09:00:00Z",
  "updated_at": "2026-07-01T10:15:00Z"
}
```

- `provider` ∈ {`openai`,`anthropic`}.
- `position` — `integer`, порядок карточки **внутри провайдер-группы** (drag-and-drop). Меньше = выше. Изменяется через `PATCH /api/ai-keys/order`.
- `key_masked` — производное: `"<первые4>…<последние4>"` (разделитель `…` U+2026), например `sk-p…bA3T`. Для ключа короче 8 символов — полная маска `"********"`. Правило — [modules/ai-keys](modules/ai-keys/README.md#правило-маски-key_masked). Backend НИКОГДА не отдаёт полный ключ или его расшифровку.
- `check_status` ∈ {`pending`,`working`,`error`}. `error_message` — рус. причина при `error` (иначе `null`).

### GET `/api/ai-keys`
Список AI-ключей. Требует JWT. Сортировка `position ASC, created_at DESC, id`. Пагинации нет.

Backend отдаёт **единый плоский список** (без секций); frontend группирует по `provider` в секции OpenAI/Anthropic, сохраняя относительный порядок внутри каждой (см. [08-design-system.md](08-design-system.md#группировка-ии-ключей-по-провайдерам)). Так как перестановка идёт внутри провайдер-группы, `position` непрерывен `0..M-1` в пределах группы; между провайдерами значения могут совпадать — это ожидаемо (frontend сначала группирует).

**Response 200**
```json
{ "items": [ { "id": "3f2a...c1", "name": "OpenAI Prod", "provider": "openai", "key_masked": "sk-p…bA3T", "check_status": "working", "error_message": null, "position": 0, "last_checked_at": "2026-07-01T10:15:00Z", "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-01T10:15:00Z" } ] }
```
**Ошибки:** `401 unauthorized`.

### POST `/api/ai-keys`
Создаёт ключ и запускает **немедленную фоновую проверку** валидности. Требует JWT.

**Request**
```json
{ "name": "OpenAI Prod", "provider": "openai", "key": "sk-proj-...bA3T" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |
| `provider` | string | required, ∈ {`openai`,`anthropic`} |
| `key` | string | required, 1–512 |

**Response 202 Accepted** — созданный `AiKeyListItem` с `check_status:"pending"`:
```json
{ "id": "3f2a...c1", "name": "OpenAI Prod", "provider": "openai", "key_masked": "sk-p…bA3T", "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": null, "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-01T09:00:00Z" }
```
> `202`, т.к. проверка провайдера асинхронна; статус отслеживается через `GET /api/ai-keys/{id}/status`. Ключ (plaintext) в ответе не возвращается. `position` берёт `DEFAULT 0` — новая карточка вверху своей провайдер-секции.

**Ошибки:** `400 validation_error`, `422 unprocessable` (невалидный `provider`).

### PATCH `/api/ai-keys/{id}`
Редактирование ключа. Требует JWT. Изменяемые поля — `name`, `provider`, `key`. **Все поля опциональны**; переданы только изменяемые.

**Request**
```json
{ "name": "OpenAI Prod (rotated)", "provider": "openai", "key": "sk-proj-...NEW" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string? | опц., 1–64 |
| `provider` | string? | опц., ∈ {`openai`,`anthropic`} |
| `key` | string? | опц., 1–512. **Пустая строка `""` или отсутствие поля = «не менять ключ»** |

**Семантика секрета (нормативно):**
- **`key` отсутствует или `""`** → текущий ключ, `key_encrypted`, `key_prefix`/`key_last4` НЕ меняются. Форма редактирования секрет не префилит (backend не хранит и не отдаёт plaintext) — поэтому пустое поле = «оставить как есть».
- **`key` непустой** → новый секрет: **re-encrypt** (Fernet, тем же `FERNET_KEY`), пересчёт маски `key_prefix`/`key_last4`, `key_masked` в ответе — по новому ключу.

**Триггер повторной проверки (нормативно):** если изменился `provider` **ИЛИ** передан непустой `key` → `check_status='pending'`, `error_message=NULL`, и запускается **немедленная фоновая проверка** у провайдера (`asyncio.create_task`, тот же путь, что при `POST`). Первый переход считается от `prev_status='pending'` — то есть неуспешная проверка после edit шлёт 🔴, как для нового ключа ([modules/ai-keys](modules/ai-keys/README.md#переходы-статуса-и-алерты-нормативно)). Если изменился только `name` — проверка НЕ перезапускается, `check_status` сохраняется.
- Смена `provider` без нового `key`: `key_encrypted`/маска остаются прежними (тот же секрет), но проверка идёт против нового провайдера → `check_status='pending'` + re-check.

**Response 200** — обновлённый `AiKeyListItem` (полный ключ не возвращается никогда):
```json
{ "id": "3f2a...c1", "name": "OpenAI Prod (rotated)", "provider": "openai", "key_masked": "sk-p…9QzK", "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": "2026-07-01T10:15:00Z", "created_at": "2026-07-01T09:00:00Z", "updated_at": "2026-07-01T12:00:00Z" }
```
> При перезапуске проверки `check_status` в ответе = `pending`; frontend опрашивает `GET /api/ai-keys/{id}/status` до выхода из `pending` (как после создания). `last_checked_at` не сбрасывается (остаётся временем последней конклюзивной проверки до завершения новой).

**Ошибки:** `401 unauthorized`, `404 ai_key_not_found`, `422 unprocessable` (невалидный `provider`), `400 validation_error` (длина `name`/`key`).

### GET `/api/ai-keys/{id}/status`
Лёгкий endpoint статуса проверки (для polling после добавления). Требует JWT.

**Response 200**
```json
{ "id": "3f2a...c1", "check_status": "error", "error_message": "Недостаточно средств", "last_checked_at": "2026-07-01T10:15:00Z" }
```
**Ошибки:** `401`, `404 ai_key_not_found`.

### DELETE `/api/ai-keys/{id}`
Удаляет ключ из реестра (hard delete). Требует JWT.

**Response 204** (без тела).

**Ошибки:** `401`, `404 ai_key_not_found`.

### GET `/api/ai-keys/{id}/key`
Reveal **полного** ключа по требованию (для detail-view; в обычных ответах — только `key_masked`). Гейт **`require("ai-keys","edit")`** (супер-админ/`admin` — всегда). Расшифровка `key_encrypted` (`decrypt_secret`) в памяти обработчика. Общие правила reveal — [«Reveal секретов»](#reveal-секретов-по-требованию-adr-035).

**Response 200** — `SecretRevealResponse`:
```json
{ "value": "sk-proj-...bA3T" }
```
**Ошибки:** `401 unauthorized`, `403 forbidden` (нет `ai-keys:edit`), `404 ai_key_not_found`.

---

## Proxies

Реестр прокси (HTTP/HTTPS/SOCKS5) с автоматической проверкой доступности. Модуль — [modules/proxies](modules/proxies/README.md), модель — [03-data-model.md](03-data-model.md#таблица-proxies), решение — [ADR-019](adr/ADR-019-proxies-availability-monitor.md). Все эндпоинты требуют JWT. **Пароль прокси никогда не возвращается** — вместо него флаг `has_password`. `username` (логин) — не секрет, возвращается как есть.

### Схема `ProxyListItem`

```json
{
  "id": "9c4f...a2",
  "name": "DE Residential",
  "proxy_type": "socks5",
  "host": "proxy.example.com",
  "port": 1080,
  "username": "user01",
  "has_password": true,
  "check_status": "working",
  "error_message": null,
  "position": 0,
  "last_checked_at": "2026-07-07T10:15:00Z",
  "created_at": "2026-07-07T09:00:00Z",
  "updated_at": "2026-07-07T10:15:00Z"
}
```

- `proxy_type` ∈ {`http`,`https`,`socks5`}.
- `host` — string (1–255), `port` — integer (1–65535).
- `username` — `string | null` (логин прокси, не секрет; `null` — без авторизации).
- `has_password` — `boolean`, производное: `password_encrypted IS NOT NULL`. **Сам пароль (в любом виде) не возвращается** — ни фрагментами, ни маской.
- `position` — `integer`, порядок карточки в **едином списке** (drag-and-drop). Меньше = выше. Изменяется через `PATCH /api/proxies/order`.
- `check_status` ∈ {`pending`,`working`,`error`}. `error_message` — рус. причина при `error` (иначе `null`): «Таймаут подключения»/«Прокси недоступен»/«Ошибка прокси».

### Схема `ProxyListResponse`

```json
{ "items": [ /* ProxyListItem[] */ ] }
```

### GET `/api/proxies`
Список прокси. Требует JWT. Сортировка `position ASC, created_at DESC, id`. Единый плоский список (без группировки). Пагинации нет.

**Response 200** — `ProxyListResponse`:
```json
{ "items": [ { "id": "9c4f...a2", "name": "DE Residential", "proxy_type": "socks5", "host": "proxy.example.com", "port": 1080, "username": "user01", "has_password": true, "check_status": "working", "error_message": null, "position": 0, "last_checked_at": "2026-07-07T10:15:00Z", "created_at": "2026-07-07T09:00:00Z", "updated_at": "2026-07-07T10:15:00Z" } ] }
```
**Ошибки:** `401 unauthorized`.

### POST `/api/proxies`
Создаёт прокси и запускает **немедленную фоновую проверку** доступности. Требует JWT.

**Request** — `ProxyCreateRequest`
```json
{ "name": "DE Residential", "proxy_type": "socks5", "host": "proxy.example.com", "port": 1080, "username": "user01", "password": "s3cr3t" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 |
| `proxy_type` | string | required, ∈ {`http`,`https`,`socks5`} |
| `host` | string | required, 1–255 |
| `port` | integer | required, 1–65535 |
| `username` | string? | опц., 1–255. Отсутствует/`null`/`""` → без логина |
| `password` | string? | опц., 1–512. Отсутствует/`null`/`""` → без пароля. Шифруется Fernet; в ответе не возвращается |

**Response 202 Accepted** — созданный `ProxyListItem` с `check_status:"pending"`:
```json
{ "id": "9c4f...a2", "name": "DE Residential", "proxy_type": "socks5", "host": "proxy.example.com", "port": 1080, "username": "user01", "has_password": true, "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": null, "created_at": "2026-07-07T09:00:00Z", "updated_at": "2026-07-07T09:00:00Z" }
```
> `202`, т.к. проверка асинхронна; статус отслеживается через `GET /api/proxies/{id}/status`. Пароль в ответе не возвращается. `position` берёт `DEFAULT 0` — новая карточка вверху списка.

**Ошибки:** `400 validation_error`, `422 unprocessable` (невалидный `proxy_type` / `port` вне диапазона), `401 unauthorized`.

### PATCH `/api/proxies/{id}`
Редактирование прокси. Требует JWT. Изменяемые поля — `name`, `proxy_type`, `host`, `port`, `username`, `password`. **Все поля опциональны**; передаются только изменяемые. «Переданное поле» определяется по множеству заданных полей запроса (Pydantic v2 `model_dump(exclude_unset=True)` / `__pydantic_fields_set__`) — это позволяет отличить «поле отсутствует» от «поле передано пустым».

**Request** — `ProxyUpdateRequest`
```json
{ "name": "DE Residential (rotated)", "proxy_type": "socks5", "host": "proxy.example.com", "port": 1080, "username": "user01", "password": "n3w-s3cr3t" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string? | опц., 1–64 |
| `proxy_type` | string? | опц., ∈ {`http`,`https`,`socks5`} |
| `host` | string? | опц., 1–255 |
| `port` | integer? | опц., 1–65535 |
| `username` | string? | опц. Не передано → не менять; передано (`null`/`""` → убрать логин; значение → установить) |
| `password` | string? | опц. Не передано → **не менять**; `null`/`""` → **очистить** (убрать пароль); непустая строка → **заменить** (re-encrypt) |

**Семантика пароля (нормативно):**
- **`password` не передано** → текущий `password_encrypted` НЕ меняется. Форма редактирования секрет не префилит (backend не хранит/не отдаёт plaintext) — поэтому пустое неотправленное поле = «оставить как есть».
- **`password` = `null`/`""`** → `password_encrypted = NULL` (`has_password` → `false`): пароль убран.
- **`password` непустой** → новый секрет: **re-encrypt** (Fernet, тем же `FERNET_KEY`).

**Триггер повторной проверки (нормативно):** если изменилось хотя бы одно **связанное с подключением** поле — `proxy_type`, `host`, `port`, `username` **или** `password` (передан непустой либо явно очищен) → `check_status='pending'`, `error_message=NULL`, и запускается **немедленная фоновая проверка** (`asyncio.create_task`, тот же путь, что при `POST`). Первый переход считается от `prev_status='pending'` — неуспешная проверка после edit шлёт 🔴, как для нового прокси ([modules/proxies](modules/proxies/README.md#переходы-статуса-и-алерты-нормативно)). Если изменился только `name` — проверка НЕ перезапускается, `check_status` сохраняется.

**Response 200** — обновлённый `ProxyListItem` (пароль не возвращается никогда):
```json
{ "id": "9c4f...a2", "name": "DE Residential (rotated)", "proxy_type": "socks5", "host": "proxy.example.com", "port": 1080, "username": "user01", "has_password": true, "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": "2026-07-07T10:15:00Z", "created_at": "2026-07-07T09:00:00Z", "updated_at": "2026-07-07T12:00:00Z" }
```
> При перезапуске проверки `check_status` в ответе = `pending`; frontend опрашивает `GET /api/proxies/{id}/status` до выхода из `pending`. `last_checked_at` не сбрасывается.

**Ошибки:** `401 unauthorized`, `404 proxy_not_found`, `422 unprocessable` (невалидный `proxy_type` / `port`), `400 validation_error` (длины `name`/`host`/`username`/`password`).

### GET `/api/proxies/{id}/status`
Лёгкий endpoint статуса проверки (для polling после добавления/редактирования). Требует JWT.

**Response 200** — `ProxyStatusResponse`
```json
{ "id": "9c4f...a2", "check_status": "error", "error_message": "Прокси недоступен", "last_checked_at": "2026-07-07T10:15:00Z" }
```
**Ошибки:** `401`, `404 proxy_not_found`.

### DELETE `/api/proxies/{id}`
Удаляет прокси из реестра (hard delete). Требует JWT.

**Response 204** (без тела).

**Ошибки:** `401`, `404 proxy_not_found`.

### GET `/api/proxies/{id}/password`
Reveal пароля прокси по требованию (для detail-view). Гейт **`require("proxies","edit")`** (супер-админ/`admin` — всегда). Расшифровка `password_encrypted` (`decrypt_secret`) в памяти обработчика. Общие правила reveal — [«Reveal секретов»](#reveal-секретов-по-требованию-adr-035).

**Response 200** — `SecretRevealResponse`:
```json
{ "value": "s3cr3t" }
```
**Ошибки:** `401 unauthorized`, `403 forbidden` (нет `proxies:edit`), `404 proxy_not_found`, `404 secret_not_set` (у прокси нет пароля — `has_password=false`). Frontend показывает кнопку-глаз **только** при `has_password=true`, поэтому `secret_not_set` — защитный кейс.

---

## Backends

Реестр бэков (backend-сервисов) с автоматической проверкой доступности `GET https://{domain}/health`. Модуль — [modules/backends](modules/backends/README.md), модель — [03-data-model.md](03-data-model.md#таблица-backends), решение — [ADR-020](adr/ADR-020-backends-healthcheck-monitor.md). Все эндпоинты требуют JWT. Секрета у сущности нет — все поля (`code`/`name`/`domain`) публичны и возвращаются как есть. `code` **уникален** — дубликат → `409 backend_code_taken`.

### Схема `BackendListItem`

```json
{
  "id": "7a1e...b9",
  "code": "api-eu",
  "name": "API EU",
  "domain": "api.example.com",
  "check_status": "working",
  "error_message": null,
  "position": 0,
  "last_checked_at": "2026-07-07T10:15:00Z",
  "created_at": "2026-07-07T09:00:00Z",
  "updated_at": "2026-07-07T10:15:00Z"
}
```

- `code` — string (1–64), **уникален** по реестру. Бизнес-код сервиса.
- `name` — string (1–64), отображаемое имя.
- `domain` — string (1–255), нормализованный домен (`host[:port]`, без схемы/пути). URL проверки — `https://{domain}/health`.
- `position` — `integer`, порядок карточки в **едином списке** (drag-and-drop). Меньше = выше. Изменяется через `PATCH /api/backends/order`.
- `check_status` ∈ {`pending`,`working`,`error`}. `error_message` — рус. причина при `error` (иначе `null`): «Таймаут подключения»/«Бэк недоступен»/«Ошибка бэка (HTTP N)»/«Ошибка бэка».

### Схема `BackendListResponse`

```json
{ "items": [ /* BackendListItem[] */ ] }
```

### GET `/api/backends`
Список бэков. Требует JWT. Сортировка `position ASC, created_at DESC, id`. Единый плоский список (без группировки). Пагинации нет.

**Response 200** — `BackendListResponse`:
```json
{ "items": [ { "id": "7a1e...b9", "code": "api-eu", "name": "API EU", "domain": "api.example.com", "check_status": "working", "error_message": null, "position": 0, "last_checked_at": "2026-07-07T10:15:00Z", "created_at": "2026-07-07T09:00:00Z", "updated_at": "2026-07-07T10:15:00Z" } ] }
```
**Ошибки:** `401 unauthorized`.

### POST `/api/backends`
Создаёт бэк и запускает **немедленную фоновую проверку** доступности. Требует JWT.

**Request** — `BackendCreateRequest`
```json
{ "code": "api-eu", "name": "API EU", "domain": "api.example.com" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `code` | string | required, 1–64. **Уникален** — дубликат → `409 backend_code_taken` |
| `name` | string | required, 1–64 |
| `domain` | string | required, 1–255. Нормализуется (принять с/без схемы `http(s)://`, с завершающим `/`; итог — `host[:port]`). Невалидный формат домена → `422 unprocessable` |

**Response 202 Accepted** — созданный `BackendListItem` с `check_status:"pending"`:
```json
{ "id": "7a1e...b9", "code": "api-eu", "name": "API EU", "domain": "api.example.com", "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": null, "created_at": "2026-07-07T09:00:00Z", "updated_at": "2026-07-07T09:00:00Z" }
```
> `202`, т.к. проверка асинхронна; статус отслеживается через `GET /api/backends/{id}/status`. `position` берёт `DEFAULT 0` — новая карточка вверху списка.

**Прецеденция ошибок (нормативно):** схемная валидация Pydantic (`400 validation_error` — битое тело/длины; `422 unprocessable` — невалидный формат домена) выполняется **до** проверки уникальности `code` (`409 backend_code_taken`). Если тело валидно, но `code` занят → `409`.

**Ошибки:** `400 validation_error`, `422 unprocessable` (невалидный формат `domain`), `409 backend_code_taken` (дубликат `code`), `401 unauthorized`.

### PATCH `/api/backends/{id}`
Редактирование бэка. Требует JWT. Изменяемые поля — `code`, `name`, `domain`. **Все поля опциональны**; передаются только изменяемые. «Переданное поле» определяется по множеству заданных полей запроса (Pydantic v2 `model_dump(exclude_unset=True)` / `__pydantic_fields_set__`).

**Request** — `BackendUpdateRequest`
```json
{ "code": "api-eu", "name": "API EU (Frankfurt)", "domain": "api-eu.example.com" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `code` | string? | опц., 1–64. Не передано → не менять; передано → заменить (**уникальность** проверяется; смена на занятый другим бэком `code` → `409 backend_code_taken`) |
| `name` | string? | опц., 1–64. Не передано → не менять |
| `domain` | string? | опц., 1–255. Не передано → не менять; передано → нормализовать и заменить (невалидный формат → `422`) |

**Триггер повторной проверки (нормативно):** если изменился **`domain`** → `check_status='pending'`, `error_message=NULL`, и запускается **немедленная фоновая проверка** (`asyncio.create_task`, тот же путь, что при `POST`). Первый переход считается от `prev_status='pending'` — неуспешная проверка после edit шлёт 🔴, как для нового бэка ([modules/backends](modules/backends/README.md#переходы-статуса-и-алерты-нормативно)). Если изменились только `code`/`name` — проверка НЕ перезапускается, `check_status` сохраняется. `updated_at` обновляется при изменении хотя бы одного поля.

**Response 200** — обновлённый `BackendListItem`:
```json
{ "id": "7a1e...b9", "code": "api-eu", "name": "API EU (Frankfurt)", "domain": "api-eu.example.com", "check_status": "pending", "error_message": null, "position": 0, "last_checked_at": "2026-07-07T10:15:00Z", "created_at": "2026-07-07T09:00:00Z", "updated_at": "2026-07-07T12:00:00Z" }
```
> При смене домена `check_status` в ответе = `pending`; frontend опрашивает `GET /api/backends/{id}/status` до выхода из `pending`. `last_checked_at` не сбрасывается.

**Прецеденция ошибок (нормативно):** `404 backend_not_found` (нет такого `id`) → схемная валидация (`400`/`422`) → уникальность `code` (`409 backend_code_taken`).

**Ошибки:** `401 unauthorized`, `404 backend_not_found`, `422 unprocessable` (невалидный формат `domain`), `400 validation_error` (длины `code`/`name`/`domain`), `409 backend_code_taken` (смена `code` на занятый другим бэком).

### GET `/api/backends/{id}/status`
Лёгкий endpoint статуса проверки (для polling после добавления/редактирования). Требует JWT.

**Response 200** — `BackendStatusResponse`
```json
{ "id": "7a1e...b9", "check_status": "error", "error_message": "Бэк недоступен", "last_checked_at": "2026-07-07T10:15:00Z" }
```
**Ошибки:** `401`, `404 backend_not_found`.

### DELETE `/api/backends/{id}`
Удаляет бэк из реестра (hard delete). Требует JWT.

**Response 204** (без тела).

**Ошибки:** `401`, `404 backend_not_found`.

---

## Mail

Страница «Почты» — **headless-прокси** к внешнему почтовому сервису `postapp.store` (mail-агрегатор), **без хранения** в CRM: read-through (лента/справочники) + **write** (CRUD почт и тегов, форс-синк). CRM — единственный UI и источник истины по командам/правам ([ADR-038](adr/ADR-038-mail-headless-integration.md)); агрегатор — движок IMAP/SMTP/тегов/доставки. Backend подставляет системный ключ `MAIL_API_KEY` в заголовок `X-API-Key` исходящего запроса; ключ никогда не возвращается и не логируется. Модуль — [modules/mail](modules/mail/README.md), базовое решение — [ADR-012](adr/ADR-012-mail-read-through-proxy.md), внешний write-контракт — mail-агрегатор `ADR-0039`/`ADR-0040`. Все эндпоинты требуют JWT. Внешний контракт (поля DTO) проксируется 1:1 в нормативные схемы ниже.

### Ролевая видимость (`MailScope`, нормативно — [ADR-038](adr/ADR-038-mail-headless-integration.md) §3)

Фабрика `get_mail_scope` (`api/deps.py`, образец `get_sms_scope`) даёт `MailScope(sees_all_teams, group_ids)`: `sees_all_teams = is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)`; иначе `group_ids` = непустые `teams.mail_group_id` по командам пользователя из `user_teams`. Граница безопасности — **backend**:
- **Чтение/список ленты и ящиков** (`GET /api/mail/messages`, `/mailboxes`) при `sees_all_teams=false`: сервис инъектирует во внешний API `group_id` = `MailScope.group_ids` (если пользователь задал фильтр «Команда» — пересечение выбранной группы со scope; выбранная группа ∉ scope → **пустая страница**) **вместе** с пользовательским `mail_account_id` (фильтр «Почта»). Внешний API **AND-комбинирует** оба фильтра (`ADR-0039` §3), поэтому чужой `mail_account_id` (вне scope-групп) даёт **пустое пересечение → пустую страницу** — без локального маппинга ящик→группа в CRM. Вне scope → **пустой результат** (анти-энумерация), не `403`/`404`; пустой `group_ids` → пустая страница без вызова внешнего API. `mail_account_id` и `group_id` **комбинируемы** (не взаимоисключающи).
- **Мутация ящика** (`POST/PATCH/DELETE /mailboxes`, `sync`) при `sees_all_teams=false`: целевой ящик обязан принадлежать группе из `group_ids`, иначе `403 forbidden` (не `404`).
- **Теги** — глобальный админский каталог, к командам не привязан: чтение под `mail:view`, управление под `mail:tags` (scope команд не применяется).

### RBAC (нормативно — [ADR-038](adr/ADR-038-mail-headless-integration.md) §4)

`CATALOG["mail"] = ("view","create","edit","delete","sync","tags")`. Маппинг действие→эндпоинты — [05-security.md](05-security.md#каталог-прав-канон-на-сервере). `reply` на письмо остаётся под `view` (не расширяется).

### Коды ошибок модуля

| HTTP | `code` | Когда |
|------|--------|-------|
| `400` | `validation_error` | Невалидные параметры/тело (`limit` вне 1..200; взаимоисключение режимов пагинации; взаимоисключение фильтров `field="filter"`; ошибка соединения при `test` без валидных кред; проброс внешнего `400`) |
| `403` | `forbidden` | Мутация/синк ящика вне `MailScope` (не-admin, ящик чужой группы); нет действия в правах роли (RBAC `require`) |
| `404` | `mail_message_not_found` | Письмо не найдено (проброс `404` от внешнего сервиса при reply) |
| `404` | `mail_mailbox_not_found` | Ящик не найден (`PATCH`/`DELETE`/`sync` по неизвестному `id`; проброс внешнего `404`) |
| `404` | `mail_tag_not_found` | Тег/правило не найдены (проброс внешнего `404`) |
| `409` | `mail_conflict` | Дубликат ящика (email уже заведён) или тега (имя занято) — проброс внешнего `409` |
| `422` | `unprocessable` | Семантически некорректное тело (пустой `body` reply, невалидный `color`/`type` правила — проброс внешнего `422`) |
| `502` | `mail_unavailable` | Внешний сервис `postapp.store` недоступен/таймаут/вернул `5xx` (исчерпаны ретраи); ошибка IMAP/SMTP-соединения при create/test (проброс внешнего `502`) |
| `503` | `mail_not_configured` | Почта не настроена (`MAIL_API_KEY` пуст → `mail_enabled=false`); все эндпоинты |

Фабрики `mail_unavailable`, `mail_message_not_found`, `mail_not_configured`, `mail_mailbox_not_found`, `mail_tag_not_found`, `mail_conflict` — в `app/errors.py`. Тело ошибки внешнего сервиса в ответ CRM дословно не пробрасывается (только нормативный `code` + рус. `message`).

**Ретраи (нормативно, `mail_client.py`).** GET-методы ретраятся на транзиентных `{429,500,502,503,504}` + `Connect*`/read-timeout (backoff `(0.2, 0.5)`). Мутирующие `POST/PATCH/DELETE` (create/update/delete/sync ящика, reply, CRUD тегов/правил, apply, `test`) ретраятся **только** на `ConnectError`/`ConnectTimeout` — защита от двойной записи; read-timeout/`5xx` на write → сразу `502 mail_unavailable`.

**Кэш ответов записи.** Эндпоинты записи (`POST/PATCH /mailboxes*`, `test`) отвечают заголовком **`Cache-Control: no-store`** (в запросе транзитом идут IMAP/SMTP-креды — [05-security.md](05-security.md#транзит-imapsmtp-кредов-mail-нормативно)).

### Схема `MailMessage`

```json
{
  "id": 1042,
  "subject": "Отчёт за июнь",
  "internal_date": "2026-07-02T09:15:00Z",
  "from_addr": "sender@example.com",
  "from_name": "Иван Петров",
  "to_addrs": "inbox@postapp.store",
  "cc_addrs": "copy@example.com",
  "mail_account": { "id": 3, "email": "inbox@postapp.store", "display_name": "Входящие" },
  "body_text": "Здравствуйте, во вложении отчёт...",
  "body_html": "<p>Здравствуйте, во вложении отчёт...</p>",
  "body_present": true,
  "body_truncated": false,
  "tags": [ { "id": 7, "name": "важное", "color": "#EF4444" } ]
}
```

| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID письма во внешнем сервисе (ключ пагинации) |
| `subject` | string \| null | Тема; `null` — без темы |
| `internal_date` | datetime (ISO 8601 UTC) | Время письма |
| `from_addr` | string | Адрес отправителя |
| `from_name` | string \| null | Имя отправителя |
| `to_addrs` | string | Получатели (строка адресов, как отдаёт внешний сервис) |
| `cc_addrs` | string \| null | Копия |
| `mail_account` | `MailAccount` | Почтовый аккаунт-получатель (см. ниже) |
| `body_text` | string | Текстовое тело |
| `body_html` | string \| null | HTML-тело (рендерится **только** в sandbox-iframe — [modules/mail](modules/mail/README.md#изоляция-html-тела-нормативно)) |
| `body_present` | boolean | Тело доступно |
| `body_truncated` | boolean | Тело обрезано внешним сервисом |
| `tags` | `MailTag[]` | Теги письма |

`MailAccount = { id: integer, email: string, display_name: string | null }`.
`MailTag = { id: integer, name: string, color: string }` (`color` — HEX, для `Badge`).

### GET `/api/mail/messages`
Лента писем (прокси к внешнему `GET /api/external/messages`). Требует JWT. Поддерживает **два режима** пагинации: `desc` (backward, newest-first — основной для страницы «Почты») и `asc` (keyset вперёд, обратная совместимость). Внешний backward-контракт — mail-агрегатор ADR-0036; решение CRM — [ADR-013](adr/ADR-013-mail-newest-first-master-detail-inline-reply.md).

**Query**
| Параметр | Тип | Правила |
|----------|-----|---------|
| `order` | enum? | опц., ∈ {`asc`,`desc`}, **default `desc`**. Определяет режим пагинации |
| `since_id` | integer? | опц., **только при `order=asc`**: keyset вперёд, возвращаются письма с `id > since_id`. При `order=desc` передан → `400 validation_error` |
| `before_id` | integer? | опц., `ge=1`, **только при `order=desc`**: backward, возвращаются письма с `id < before_id` по `id DESC`. Не задан (при `order=desc`) → последние `limit` писем (самые свежие). При `order=asc` передан → `400 validation_error` |
| `limit` | integer? | опц., `1..200`, default `50`. Вне диапазона → `400 validation_error`. Страница «Почты» шлёт `limit=20` |
| `mail_account_id` | integer? | опц., `ge=1`. Серверный фильтр по почтовому ящику. **Комбинируем с `group_id`** (AND, `ADR-0039` §3 — взаимоисключение ADR-0037 снято). Пробрасывается во внешний API; для не-админа пересекается со scope-группами |
| `group_id` | integer? | опц., `ge=1`. Серверный фильтр по команде (`groups`). **Комбинируем с `mail_account_id`** (AND). Для не-админа сервис инъектирует/пересекает со `MailScope.group_ids`. Пробрасывается во внешний API |

> **Проброс во внешний API (нормативно).** CRM всегда передаёт `order` во внешний `GET /api/external/messages` **явно** (не полагается на внешний default `asc`). CRM default `order=desc` отличается от внешнего default `asc` осознанно — отражает основной сценарий страницы (newest-first); frontend всё равно шлёт `order=desc` явно. `since_id`/`next_since_id` — маппинг asc-режима 1:1; `before_id`/`next_before_id` — маппинг desc-режима 1:1.
>
> **Серверные фильтры `mail_account_id`/`group_id` (нормативно, external ADR-0037).** Опциональны и **взаимоисключающи**: оба переданы → `400 validation_error` (CRM валидирует локально до вызова внешнего API, `details:[{field:"filter", message:"…"}]`; внешний `400` взаимоисключения также маппится в `400`). Работают **совместно** с любым режимом пагинации (`order`/`since_id`/`before_id`/`limit`) — фильтр применяется на стороне внешнего сервиса ко **всему** набору, курсоры не меняются. **Несуществующий / чужой / non-canonical `id`** (ящика или команды) → внешний сервис возвращает **пустую страницу** (`messages:[]`), а не `404`; CRM проксирует её как обычный `200`. Даёт серверную фильтрацию ленты по ящику **или** команде (частично снимает [TD-024](100-known-tech-debt.md) — [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)).

**Response 200** — схема `MailListResponse` (единая для обоих режимов; заполнен курсор запрошенного режима, второй — `null`):
```json
{
  "messages": [ /* MailMessage[] */ ],
  "next_since_id": null,
  "next_before_id": 1001,
  "has_more": true
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `messages` | `MailMessage[]` | Батч писем. В `desc`-режиме — по `id DESC` (свежие первыми) как отдаёт внешний API; в `asc` — по `id ASC` |
| `next_since_id` | integer \| null | **asc-режим:** максимальный `id` в батче (курсор следующего `since_id`); `null` для пустого батча (нет новых вперёд). **desc-режим:** всегда `null` |
| `next_before_id` | integer \| null | **desc-режим:** минимальный `id` в батче (курсор следующего `before_id` — догрузка более старых); `null`, если старее нет (`has_more=false`) или батч пуст. **asc-режим:** всегда `null` |
| `has_more` | boolean | Есть ли ещё письма в запрошенном направлении (вперёд для asc, в прошлое для desc) |

- Backend реализует оба курсора как `int | None` (безопасный супертип). Синхронизировано с [modules/mail](modules/mail/README.md#пагинация-нормативно).
- **desc (основной):** первый запрос — `order=desc` без `before_id` → новейшие `limit`; догрузка старых — `order=desc&before_id=<next_before_id>`, пока `has_more=true`. Даёт **строгий глобальный newest-first**.
- **asc (совместимость):** `order=asc` (+опц. `since_id`) — keyset вперёд по `id ASC`, как прежде.
- **Server-side фильтров/поиска нет** (внешний API их не предоставляет) — остаток [TD-024](100-known-tech-debt.md); newest-first backward-пагинацией снят.

**Ошибки:** `401 unauthorized`, `400 validation_error` (`limit` вне 1..200; взаимоисключение режимов: `before_id` при `order=asc` ИЛИ `since_id` при `order=desc`; **взаимоисключение фильтров:** `mail_account_id` И `group_id` одновременно, `field="filter"`; внешний `400` взаимоисключения также → `400`), `502 mail_unavailable`, `503 mail_not_configured`.

### POST `/api/mail/messages/{id}/reply`
Ответ на письмо (прокси к внешнему `POST /api/external/messages/{id}/reply`). Требует JWT.

**Request** — схема `MailReplyRequest`:
```json
{
  "to": ["sender@example.com"],
  "cc": null,
  "subject": "Re: Отчёт за июнь",
  "body": "Спасибо, получил."
}
```
| Поле | Тип | Правила |
|------|-----|---------|
| `to` | string[]? | опц., адреса получателей (по умолчанию — отправитель исходного письма, определяет внешний сервис) |
| `cc` | string[] \| null? | опц., копия |
| `subject` | string? | опц., тема ответа |
| `body` | string | required, непустой — текст ответа |

**Response 200** — схема `MailReplyResponse`:
```json
{ "sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `sent_id` | integer | ID отправленного письма |
| `smtp_message_id` | string | SMTP Message-ID |

**Ошибки:** `401 unauthorized`, `400 validation_error` (битое тело), `422 unprocessable` (пустой `body` / семантически некорректное тело), `404 mail_message_not_found` (письмо не найдено — проброс от внешнего), `502 mail_unavailable`, `503 mail_not_configured`.

> Нормативный контракт внешнего reply-эндпоинта фиксирует architect mail-агрегатора; CRM проксирует его в схемы `MailReplyRequest`/`MailReplyResponse` выше. При расхождении — синхронизация через architect.

### Схема `MailTeam`

Команда — это `groups` внешнего сервиса (external ADR-0037). **Команда ≠ тег** (`MailTag`): теги остаются отдельной сущностью письма.

```json
{ "id": 3, "name": "Продажи" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID команды (`group`) во внешнем сервисе |
| `name` | string | Название команды |

### Схема `MailMailbox`

Почтовый ящик внешнего сервиса (external ADR-0037, расширен статусом синка — `ADR-0039`). Привязка к команде — через `group_id`; `is_active` — статус ящика. Поля статуса синка (`last_synced_at`/`last_sync_error`/`consecutive_failures`) — для кружка статуса и диагностики на вкладке «Почты». **Пароли (`password`/`smtp_password`) в схему НЕ входят и в ответах не возвращаются** ([05-security.md](05-security.md#транзит-imapsmtp-кредов-mail-нормативно)).

```json
{
  "id": 7, "email": "inbox@postapp.store", "display_name": "Входящие",
  "group_id": 3, "is_active": true,
  "last_synced_at": "2026-07-09T08:00:00Z", "last_sync_error": null, "consecutive_failures": 0
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID почтового ящика во внешнем сервисе (используется как `mail_account_id` в фильтре `GET /api/mail/messages` и в `PATCH/DELETE/sync`) |
| `email` | string | Адрес ящика |
| `display_name` | string \| null | Отображаемое имя; `null` — нет |
| `group_id` | integer \| null | ID команды (`MailTeam.id`), к которой привязан ящик; `null` — не привязан |
| `is_active` | boolean | Активен ли ящик (дашборд — подсчёт Активные/Неактивные; кружок статуса) |
| `last_synced_at` | datetime \| null | Время последней успешной синхронизации; `null` — ещё не синхронизировался |
| `last_sync_error` | string \| null | Текст последней ошибки синка; `null` — ошибок нет |
| `consecutive_failures` | integer | Число подряд идущих неудачных синков (0 — здоров) |

### GET `/api/mail/teams`
Список команд (прокси к внешнему `GET /api/external/teams`). Требует JWT. Без параметров.

**Response 200** — схема `MailTeamsResponse`:
```json
{ "teams": [ { "id": 3, "name": "Продажи" } ] }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `teams` | `MailTeam[]` | Список команд (может быть пустым) |

**Ошибки:** `401 unauthorized`, `502 mail_unavailable`, `503 mail_not_configured`.

### GET `/api/mail/mailboxes`
Список почтовых ящиков (прокси к внешнему `GET /api/external/mailboxes`). Требует JWT (`mail:view`). Используется вкладкой «Почты», дропдауном «Почта» на вкладке «Сообщения» и карточкой «Почты» на «Дашборде». **Фильтруется `MailScope`** (не-admin видит только ящики групп своих команд).

**Query** (все опц.)
| Параметр | Тип | Правила |
|----------|-----|---------|
| `is_active` | boolean? | Фильтр активности: `true` — только активные, `false` — только неактивные, не задан — все. Пробрасывается во внешний API |
| `group_id` | integer? | `ge=1`. Фильтр по команде (`groups`). Пробрасывается во внешний API (пересекается с `MailScope.group_ids` для не-admin) |

**Response 200** — схема `MailMailboxesResponse`:
```json
{ "mailboxes": [ { "id": 7, "email": "inbox@postapp.store", "display_name": "Входящие", "group_id": 3, "is_active": true, "last_synced_at": "2026-07-09T08:00:00Z", "last_sync_error": null, "consecutive_failures": 0 } ] }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `mailboxes` | `MailMailbox[]` | Список ящиков (может быть пустым; вне `MailScope` — пустой) |

**Ошибки:** `401 unauthorized`, `400 validation_error` (`group_id < 1`), `502 mail_unavailable`, `503 mail_not_configured`.

> Read-through-прокси без хранения; `MAIL_API_KEY` только в заголовке `X-API-Key`; `mail_enabled=false` → `503`; недоступность внешнего сервиса → `502`. Схемы `MailTeam`/`MailMailbox` проксируются 1:1 из external DTO (`MailMailbox` расширен статусом синка — `ADR-0039`).

### POST `/api/mail/mailboxes/test`
Проверка IMAP/SMTP-соединения **без сохранения** (прокси к внешнему `POST /api/external/mailboxes/test`). Требует JWT (`mail:create`). Ответ — `Cache-Control: no-store`. Пароли — транзитом, не логируются, в ответ не попадают.

**Request** — схема `MailMailboxTestRequest`:
```json
{
  "email": "inbox@example.com",
  "imap_host": "imap.example.com", "imap_port": 993, "imap_ssl": true,
  "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_ssl": true, "smtp_starttls": false,
  "smtp_username": null,
  "password": "secret", "smtp_password": null
}
```
| Поле | Тип | Правила |
|------|-----|---------|
| `email` | string | required, адрес ящика (он же IMAP-логин по умолчанию) |
| `imap_host` | string | required |
| `imap_port` | integer | required, `1..65535` |
| `imap_ssl` | boolean | required |
| `smtp_host` | string | required |
| `smtp_port` | integer | required, `1..65535` |
| `smtp_ssl` | boolean | required (взаимоисключ. с `smtp_starttls`) |
| `smtp_starttls` | boolean | required (взаимоисключ. с `smtp_ssl` → `409`/`400` внешнего сервиса) |
| `smtp_username` | string \| null? | опц.; `null` → берётся `email` |
| `password` | string | required, IMAP-пароль (транзит) |
| `smtp_password` | string \| null? | опц.; `null` → берётся `password` |

**Response 200** — `MailMailboxTestResponse`:
```json
{ "imap_ok": true, "smtp_ok": true }
```
**Ошибки:** `401`, `403 forbidden` (нет `mail:create`), `400 validation_error`, `502 mail_unavailable` (соединение не удалось), `503 mail_not_configured`.

### POST `/api/mail/mailboxes`
Создание ящика (прокси к внешнему `POST /api/external/mailboxes`). Требует JWT (`mail:create`). Внешний сервис перед сохранением проверяет соединение и SSRF-guard хостов; владелец ящика в агрегаторе — техпользователь `crm-service` (`ADR-0039` §Q-0039-1). Ответ — `Cache-Control: no-store`.

**Request** — схема `MailMailboxCreateRequest` = поля `MailMailboxTestRequest` **плюс**:
| Поле | Тип | Правила |
|------|-----|---------|
| `display_name` | string \| null? | опц., отображаемое имя ящика |
| `group_id` | integer \| null? | опц., `ge=1`, команда (`MailTeam.id`), к которой привязать ящик. `null` — без команды. Для не-admin: `group_id` обязан ∈ `MailScope.group_ids`, иначе `403 forbidden` |

**Response 201** — схема `MailMailbox` (расширенная, с полями статуса; пароля нет).

**Ошибки:** `401`, `403 forbidden` (нет `mail:create` ИЛИ `group_id` вне `MailScope`), `400 validation_error`, `409 mail_conflict` (email уже заведён), `422 unprocessable`, `502 mail_unavailable`, `503 mail_not_configured`.

### PATCH `/api/mail/mailboxes/{id}`
Правка ящика (прокси к внешнему `PATCH /api/external/mailboxes/{id}`). Требует JWT (`mail:edit`). Ответ — `Cache-Control: no-store`. Для не-admin: ящик `{id}` обязан принадлежать группе из `MailScope.group_ids`, иначе `403 forbidden`.

**Request** — схема `MailMailboxUpdateRequest` (все поля опц., присутствие поля = «изменить»):
| Поле | Тип | Правила |
|------|-----|---------|
| `email` | string? | новый адрес |
| `display_name` | string \| null? | новое имя (`null` — очистить) |
| `imap_host`/`imap_port`/`imap_ssl` | ? | параметры IMAP |
| `smtp_host`/`smtp_port`/`smtp_ssl`/`smtp_starttls`/`smtp_username` | ? | параметры SMTP |
| `password` | string? | новый IMAP-пароль (транзит; при передаче внешний сервис ре-проверяет соединение) |
| `smtp_password` | string? | новый SMTP-пароль (транзит) |
| `is_active` | boolean? | активация/деактивация ящика |
| `group_id` | integer \| null? | `ge=1`, смена команды (перенос). Для не-admin — целевая группа ∈ `MailScope.group_ids`, иначе `403` |

**Response 200** — схема `MailMailbox` (расширенная). **Ошибки:** `401`, `403 forbidden`, `400 validation_error`, `404 mail_mailbox_not_found`, `409 mail_conflict`, `422 unprocessable`, `502 mail_unavailable`, `503 mail_not_configured`.

### DELETE `/api/mail/mailboxes/{id}`
Удаление ящика (прокси к внешнему `DELETE /api/external/mailboxes/{id}`). Требует JWT (`mail:delete`). Для не-admin — ящик ∈ `MailScope`. **Response 204.** **Ошибки:** `401`, `403 forbidden`, `404 mail_mailbox_not_found`, `502 mail_unavailable`, `503 mail_not_configured`.

### POST `/api/mail/mailboxes/{id}/sync`
Форс-синхронизация ящика (прокси к внешнему `POST /api/external/mailboxes/{id}/sync`). Требует JWT (`mail:sync`). Для не-admin — ящик ∈ `MailScope`. **Response 202** — `{ "queued": true }`. **Ошибки:** `401`, `403 forbidden`, `404 mail_mailbox_not_found`, `502 mail_unavailable`, `503 mail_not_configured`.

### Схема `MailTagRule`

```json
{ "id": 12, "type": "subject_contains", "pattern": "счёт", "created_at": "2026-07-01T10:00:00Z" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID правила |
| `type` | enum | ∈ {`subject_contains`, `body_contains`, `sender_contains`, `sender_exact`}. Человекочитаемые подписи — [08-design-system.md](08-design-system.md#вкладка-теги-нормативно) |
| `pattern` | string | Паттерн (1..256) |
| `created_at` | datetime | Время создания |

### Схема `MailTagFull`

Полный тег с правилами (для вкладки «Теги»). Глобальный админский каталог (`ADR-0040`).

```json
{
  "id": 7, "name": "Счета", "color": "#2563eb", "match_mode": "any", "is_builtin": false,
  "rules": [ { "id": 12, "type": "subject_contains", "pattern": "счёт", "created_at": "2026-07-01T10:00:00Z" } ],
  "created_at": "2026-07-01T10:00:00Z", "updated_at": "2026-07-01T10:00:00Z"
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID тега |
| `name` | string | Имя тега (1..64) |
| `color` | string | HEX из палитры (`^#[0-9A-Fa-f]{6}$`, 8 цветов — [08-design-system.md](08-design-system.md#вкладка-теги-нормативно)) |
| `match_mode` | enum | ∈ {`any`, `all`}: `any` — тег вешается при совпадении любого правила, `all` — всех |
| `is_builtin` | boolean | Встроенный тег (удалять нельзя → `409`; правка имени/правил разрешена) |
| `rules` | `MailTagRule[]` | Правила тега |
| `created_at` / `updated_at` | datetime | Время создания/изменения |

### GET `/api/mail/tags`
Список глобальных тегов с правилами (прокси к внешнему `GET /api/external/tags`). Требует JWT (`mail:view`). Без параметров.

**Response 200** — `MailTagsResponse`: `{ "tags": [ /* MailTagFull[] */ ] }`. **Ошибки:** `401`, `502 mail_unavailable`, `503 mail_not_configured`.

### POST `/api/mail/tags`
Создание тега (прокси к внешнему `POST /api/external/tags`). Требует JWT (`mail:tags`).

**Request** — `MailTagCreateRequest`: `{ "name": string, "color": string, "match_mode": "any"|"all" }` (`match_mode` опц., default `any`). **Response 201** — `MailTagFull` (без правил). **Ошибки:** `401`, `403 forbidden` (нет `mail:tags`), `400`/`422 unprocessable` (невалидный `color`/`name`), `409 mail_conflict` (имя занято), `502`, `503`.

### PATCH `/api/mail/tags/{id}`
Правка тега (прокси к внешнему `PATCH /api/external/tags/{id}`). Требует JWT (`mail:tags`).

**Request** — `MailTagUpdateRequest` (все опц.): `{ "name"?, "color"?, "match_mode"? }`. **Response 200** — `MailTagFull`. **Ошибки:** `401`, `403 forbidden`, `422 unprocessable`, `404 mail_tag_not_found`, `409 mail_conflict`, `502`, `503`.

### DELETE `/api/mail/tags/{id}`
Удаление тега (прокси к внешнему `DELETE /api/external/tags/{id}`). Требует JWT (`mail:tags`). **Response 204.** **Ошибки:** `401`, `403 forbidden`, `404 mail_tag_not_found`, `409 mail_conflict` (встроенный тег — удалять нельзя), `502`, `503`.

### POST `/api/mail/tags/{id}/rules`
Добавление правила тегу (прокси к внешнему `POST /api/external/tags/{id}/rules`). Требует JWT (`mail:tags`).

**Request** — `MailTagRuleCreateRequest`: `{ "type": enum, "pattern": string }` (`type` ∈ 4 значений; `pattern` 1..256). **Response 201** — `MailTagRule`. **Ошибки:** `401`, `403 forbidden`, `400`/`422 unprocessable`, `404 mail_tag_not_found`, `502`, `503`.

### DELETE `/api/mail/tags/{id}/rules/{rule_id}`
Удаление правила (прокси к внешнему `DELETE /api/external/tags/{id}/rules/{rule_id}`). Требует JWT (`mail:tags`). **Response 204.** **Ошибки:** `401`, `403 forbidden`, `404 mail_tag_not_found`, `502`, `503`.

### POST `/api/mail/tags/{id}/apply-to-existing`
Применить правила тега ко всем существующим письмам (прокси к внешнему `POST /api/external/tags/{id}/apply-to-existing`). Требует JWT (`mail:tags`). Идемпотентно на стороне агрегатора (`ON CONFLICT DO NOTHING`). **Response 200** — `{ "applied_count": integer }`. **Ошибки:** `401`, `403 forbidden`, `404 mail_tag_not_found`, `422 unprocessable` (слишком много писем — лимит агрегатора), `502`, `503`.

---

## Dashboard

Страница «Дашборд» **не имеет собственных backend-эндпоинтов**. Счётчики собираются **на фронте** (клиентская агрегация) из существующих list-эндпоинтов — отдельный backend-агрегатор не вводится ([ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md), NFR-1):

| Блок дашборда | Источник | Счётчики |
|---------------|----------|----------|
| **«Почты»** → `/mail` | `GET /api/mail/mailboxes` | Активные = `mailboxes` с `is_active=true`; Неактивные = `is_active=false` |
| **«Серверы»** → `/servers` | `GET /api/servers` | online = `items` с `online=true`; offline = `online=false` |
| **«ИИ-ключи»** → `/ai-keys` | `GET /api/ai-keys` | Активные = `check_status='working'`; Неактивные = `check_status='error'`; опц. «проверяется» = `check_status='pending'` |

Композиция/состояния карточек — [08-design-system.md · Страница «Дашборд»](08-design-system.md#страница-дашборд).

---

## Перестановка (порядок карточек)

Сохранение пользовательского порядка карточек (drag-and-drop), решение — [ADR-011](adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md), модель — [03-data-model.md](03-data-model.md#колонка-position-порядок-карточек). Все четыре endpoint'а (`servers`/`ai-keys`/`proxies`/`backends`) требуют JWT и принимают **полный упорядоченный список `id`**; backend в одной транзакции присваивает `position = индекс` (`0..N-1`). Идемпотентны (повторная отправка того же порядка не меняет результат). `servers`, `proxies` и `backends` — единый список; `ai-keys` — внутри провайдер-группы.

#### Прецеденция ошибок валидации (нормативно, едино для всех order-эндпоинтов)

Проверки выполняются в фиксированном порядке; возвращается код **первого** сработавшего шага (последующие не проверяются). Это снимает неоднозначность, когда один и тот же `id` одновременно «неизвестен» (→404) и «лишний» относительно ожидаемого множества (→422).

1. **Форма тела** — тело не соответствует схеме (не массив UUID; для ai-keys отсутствует/невалиден `provider`) → `400 validation_error`.
2. **Существование всех переданных `id`** — если **любой** `id` из `ids` не существует в БД → `404` (`server_not_found` / `ai_key_not_found` / `proxy_not_found` / `backend_not_found`). Проверяется раньше полноты множества.
3. **Полнота перестановки** — только если все `id` существуют. Проверяется, что `ids` — строго полная перестановка ожидаемого множества (та же длина, без дублей, без пропусков и лишних). Для ai-keys ожидаемое множество — ровно ключи переданного `provider` (любой существующий `id`, принадлежащий другому провайдеру, здесь трактуется как «лишний/чужой») → `422 unprocessable`.

Итог: несуществующий `id` всегда даёт `404` (даже если он же нарушает полноту); `422` возможен только когда все `id` существуют, но множество собрано неверно (пропуск/дубль/лишний-своего-провайдера/чужой-провайдер).

Для ai-keys `provider` присутствует по схеме, но вне enum {`openai`,`anthropic`} → `422 unprocessable` (семантически некорректный, по аналогии с невалидным IP); эта проверка `provider` идёт до шагов 2–3 (нельзя определить ожидаемое множество группы без валидного `provider`). Отсутствие поля `provider` в теле — `400 validation_error` (шаг 1).

### PATCH `/api/servers/order`
Перестановка серверов (единый список, свободный порядок).

**Request**
```json
{ "ids": ["8f1d...e2", "a1b2...", "c3d4..."] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `ids` | string[] | required. **Полная перестановка** текущего множества серверов: та же длина, без дублей, все `id` существуют |

**Response 204** (без тела). Frontend после `204` инвалидирует запрос `GET /api/servers` (канонический порядок + свежие метрики).

**Ошибки** (порядок проверки — см. [«Прецеденция ошибок валидации»](#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)):
- `400 validation_error` — тело некорректно (не массив UUID). Проверяется первым.
- `404 server_not_found` — какой-либо `id` не существует. Проверяется **до** полноты множества: несуществующий `id` даёт `404`, даже если он же нарушает перестановку.
- `422 unprocessable` — **только если все `id` существуют**, но `ids` не является полной перестановкой (пропущены/дублируются/лишние элементы относительно текущего множества серверов).

### PATCH `/api/ai-keys/order`
Перестановка AI-ключей **внутри одной провайдер-группы**. Провайдер у ключа при перестановке фиксирован — между секциями OpenAI/Anthropic карточки не перемещаются (сменить провайдера можно только через `PATCH /api/ai-keys/{id}`).

**Request**
```json
{ "provider": "openai", "ids": ["3f2a...c1", "7b8c...", "d9e0..."] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `provider` | string | required, ∈ {`openai`,`anthropic`} |
| `ids` | string[] | required. **Полная перестановка** множества ключей указанного `provider`: та же длина, без дублей, все `id` существуют И принадлежат этому провайдеру |

Backend присваивает `position = 0..M-1` только ключам этой группы (`WHERE provider = :provider`).

**Response 204** (без тела). Frontend инвалидирует `GET /api/ai-keys`.

**Ошибки** (порядок проверки — см. [«Прецеденция ошибок валидации»](#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)):
- `400 validation_error` — тело некорректно (не массив UUID / отсутствует `provider`). Проверяется первым.
- `422 unprocessable` (`provider` вне enum) — если `provider` присутствует, но не ∈ {`openai`,`anthropic`}. Проверяется до шагов существования/полноты (без валидного `provider` не определить ожидаемую группу).
- `404 ai_key_not_found` — какой-либо `id` не существует. Проверяется **до** полноты группы: несуществующий `id` даёт `404`, даже если он же лишний/чужой.
- `422 unprocessable` (неполная группа) — **только если все `id` существуют**: `ids` не является полной перестановкой ключей этого `provider` (пропуски/дубли/лишние) ЛИБО какой-либо существующий `id` принадлежит другому провайдеру.

### PATCH `/api/proxies/order`
Перестановка прокси (единый список, свободный порядок — как серверы; группировки нет).

**Request**
```json
{ "ids": ["9c4f...a2", "1b2c...", "3d4e..."] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `ids` | string[] | required. **Полная перестановка** текущего множества прокси: та же длина, без дублей, все `id` существуют |

**Response 204** (без тела). Frontend после `204` инвалидирует `GET /api/proxies`.

**Ошибки** (порядок проверки — см. [«Прецеденция ошибок валидации»](#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)):
- `400 validation_error` — тело некорректно (не массив UUID). Проверяется первым.
- `404 proxy_not_found` — какой-либо `id` не существует. Проверяется **до** полноты множества: несуществующий `id` даёт `404`, даже если он же нарушает перестановку.
- `422 unprocessable` — **только если все `id` существуют**, но `ids` не является полной перестановкой (пропущены/дублируются/лишние элементы относительно текущего множества прокси).

### PATCH `/api/backends/order`
Перестановка бэков (единый список, свободный порядок — как серверы/прокси; группировки нет).

**Request**
```json
{ "ids": ["7a1e...b9", "1b2c...", "3d4e..."] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `ids` | string[] | required. **Полная перестановка** текущего множества бэков: та же длина, без дублей, все `id` существуют |

**Response 204** (без тела). Frontend после `204` инвалидирует запрос `GET /api/backends`.

**Ошибки** (порядок проверки — см. [«Прецеденция ошибок валидации»](#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)):
- `400 validation_error` — тело некорректно (не массив UUID). Проверяется первым.
- `404 backend_not_found` — какой-либо `id` не существует. Проверяется **до** полноты множества: несуществующий `id` даёт `404`, даже если он же нарушает перестановку.
- `422 unprocessable` — **только если все `id` существуют**, но `ids` не является полной перестановкой (пропущены/дублируются/лишние элементы относительно текущего множества бэков).

---

## RBAC и enforcement прав

Многопользовательский режим с ролями и правами на все страницы — [ADR-021](adr/ADR-021-rbac-users-roles.md), модель — [03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac), права/пароли — [05-security.md](05-security.md#rbac--роли-права-и-enforcement). **Безопасность обеспечивается на сервере** (`403 forbidden`); UI-гейтинг — только UX.

- Каждый ресурсный эндпоинт защищён зависимостью `require(page, action)` (заменяет прежний «любой аутентифицированный»): нет права → `403 forbidden`. Маппинг метод→действие (нормативно):

| Роутер | GET (list/status/metrics) | POST | PATCH `/{id}` | PATCH `/order` | DELETE |
|--------|---------------------------|------|----------------|-----------------|--------|
| `servers` | `servers:view` | `servers:create` | `servers:edit` | `servers:edit` | `servers:delete` |
| `ai-keys` | `ai-keys:view` | `ai-keys:create` | `ai-keys:edit` | `ai-keys:edit` | `ai-keys:delete` |
| `proxies` | `proxies:view` | `proxies:create` | `proxies:edit` | `proxies:edit` | `proxies:delete` |
| `backends` | `backends:view` | `backends:create` | `backends:edit` | `backends:edit` | `backends:delete` |
| `mail` | `mail:view` (`/messages`, `/teams`, `/mailboxes`) | `POST /messages/{id}/reply` → `mail:view` | — | — | — |
| `roles` | `roles:view` | `roles:create` | `roles:edit` | — | `roles:delete` ([ADR-022](adr/ADR-022-teams-nav-categories.md)) |
| `teams` | `teams:view` | `teams:create` | `teams:edit` | — | `teams:delete` ([ADR-022](adr/ADR-022-teams-nav-categories.md)) |
| `sms` | `sms:view` (`/messages`, `/numbers`) | `POST /numbers/sync` → `sms:sync`; `POST /numbers/{id}/transfer` → `sms:transfer` | `sms:edit` (`PATCH /numbers/{id}`) | — | `sms:delete` ([ADR-030](adr/ADR-030-sms-module-full-merge.md)) |

- **Супер-админ** (`.env`, `superadmin=true`) проходит любой `require(...)` и `require_admin`.
- **Reply почты** гейтится `mail:view` (у почты в каталоге одно действие `view`).
- **Roles/Permissions API** — со Спринта A гейтятся **матрицей** `roles:*` ([ADR-022](adr/ADR-022-teams-nav-categories.md)): `/api/roles` (методы по таблице выше), `GET /api/permissions/catalog` → `require("roles","view")` (каталог нужен редактору роли). **Teams API** — `require("teams", <action>)`.
- **Users API** — **остаётся** `require_admin` (`is_superadmin || role=="admin"`), **не** через матрицу: создание/удаление пользователей, сброс паролей, назначение ролей — admin-only ([ADR-022](adr/ADR-022-teams-nav-categories.md) §4в, замыкает эскалацию).
- **SMS API** ([ADR-030](adr/ADR-030-sms-module-full-merge.md)) — матрица `sms:*` (см. таблицу). `POST /api/sms/telegram/link` — **только аутентификация** (вне матрицы `sms`): доставка операторам — функция членства в команде (`user_teams`), а не права на страницу. `GET /api/teams/{id}/numbers` гейтится `teams:view`. Публичные webhook'и Twilio/Telegram и `POST /api/sms/telegram/auth` — вне JWT/RBAC (гейт — подпись/секрет/HMAC).
- **Security-инвариант эскалации** (`POST`/`PATCH /api/roles`, реализует backend): не-супер-админ/не-`admin` не может выдать роли права сверх собственных (subset), а встроенную роль `admin` может менять/удалять только `is_superadmin || role=="admin"` — иначе `403 forbidden`. Полностью — [Roles](#roles), [ADR-022](adr/ADR-022-teams-nav-categories.md#4-security-инвариант-эскалации-привилегий-обязательно-реализует-backend).
- `403 forbidden` — единый код и тело `{ "error": { "code": "forbidden", "message": "Недостаточно прав", "details": null } }`. Фабрика `forbidden()` добавляется в `app/errors.py`.

---

## Permissions

Каталог прав (канон на сервере) для построения UI-матрицы. Модель — [ADR-021](adr/ADR-021-rbac-users-roles.md#1-каталог-прав-канон-на-сервере).

### GET `/api/permissions/catalog`
Отдаёт канонический каталог «страница × действия». Гейт **`require("roles","view")`** ([ADR-022](adr/ADR-022-teams-nav-categories.md); прежде `require_admin`) — каталог нужен редактору роли для построения матрицы, поэтому доступен носителю `roles:view` (супер-админ и роль `admin` проходят как обладатели полного каталога).

**Response 200** — схема `PermissionsCatalogResponse`:
```json
{
  "pages": [
    { "page": "dashboard", "actions": ["view"] },
    { "page": "servers",  "actions": ["view", "create", "edit", "delete"] },
    { "page": "ai-keys",  "actions": ["view", "create", "edit", "delete"] },
    { "page": "proxies",  "actions": ["view", "create", "edit", "delete"] },
    { "page": "backends", "actions": ["view", "create", "edit", "delete"] },
    { "page": "mail",     "actions": ["view"] },
    { "page": "sms",      "actions": ["view", "edit", "transfer", "sync", "delete"] },
    { "page": "roles",    "actions": ["view", "create", "edit", "delete"] },
    { "page": "teams",    "actions": ["view", "create", "edit", "delete"] }
  ]
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `pages` | `PermissionCatalogPage[]` | Упорядоченный список страниц каталога (порядок = порядок строк матрицы в UI: dashboard, servers, ai-keys, proxies, backends, mail, sms, roles, teams) |

`PermissionCatalogPage = { page: string, actions: string[] }`. Страница `users` в каталог **не входит** (гейтится `require_admin`, не матрицей — [ADR-022](adr/ADR-022-teams-nav-categories.md)). Страницы `roles`/`teams` добавлены Спринтом A. Локализованные подписи страниц/действий — на стороне frontend ([08-design-system.md](08-design-system.md#страница-роли)).

**Ошибки:** `401 unauthorized`, `403 forbidden`.

---

## Users

Реестр дополнительных пользователей (супер-админ из `.env` сюда не входит). Модель — [03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac), решения — [ADR-021](adr/ADR-021-rbac-users-roles.md), [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md). Все эндпоинты — гейт `require_admin`. **Пароль (plaintext) никогда не возвращается** — только на вход; **пароль опционален** (беспарольные пользователи — [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)).

### Схема `UserListItem`
```json
{
  "id": "b7c1...e4",
  "username": "Никита",
  "telegram": "nikita_ops",
  "has_password": true,
  "role_id": "2a9f...c0",
  "role_name": "Оператор",
  "is_active": true,
  "status": "active",
  "teams": [ { "id": "d3f0...a1", "name": "Продажи" } ],
  "created_at": "2026-07-07T09:00:00Z",
  "updated_at": "2026-07-07T09:00:00Z"
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | string (uuid) | Идентификатор пользователя |
| `username` | string | Логин (кириллица/юникод допускаются). **Идентификатор входа** (логин или `telegram`) |
| `telegram` | string \| null | Опциональный телеграм-ник ([ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md); заменяет прежний `email`); `null` — не задан. Нормализован (без `@`, lower-case). Второй идентификатор входа |
| `has_password` | boolean | Производное: `password_hash IS NOT NULL`. `false` — **беспарольный** пользователь (ещё не прошёл «открытый первый вход», [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). Сам хэш/пароль не возвращается |
| `role_id` | string (uuid) | ID роли |
| `role_name` | string | Имя роли (денормализовано для UI-списка) |
| `is_active` | boolean | Активен ли пользователь (используется формой редактирования как тумблер «Активен») |
| `status` | string | **Производный** тристатус ([ADR-028](adr/ADR-028-user-status-first-login.md)) ∈ `"pending"` \| `"active"` \| `"inactive"`. Правило (приоритет `is_active`): `is_active==false` → `"inactive"`; `is_active==true` И ещё не входил (`first_login_at IS NULL`) → `"pending"`; `is_active==true` И входил хотя бы раз → `"active"`. UI-лейблы: `"inactive"`→«Неактивен», `"pending"`→«Ожидает входа», `"active"`→«Активен» ([08-design-system.md](08-design-system.md#страница-пользователи)). Сама метка `first_login_at` наружу не отдаётся |
| `teams` | `TeamRef[]` | CRM-команды пользователя (может быть пустым). `TeamRef = { id: string(uuid), name: string }`. Денормализовано для группировки списка «Пользователи» по командам |
| `created_at` / `updated_at` | datetime | Метки |

Пароль (`password`/`password_hash`) в ответах **отсутствует** всегда (есть лишь производный `has_password`). `teams` — CRM-команды ([Teams](#teams)), **не** mail-«команды».

### GET `/api/users`
Список пользователей. Гейт `require_admin`.

**Response 200** — схема `UserListResponse`:
```json
{ "items": [ /* UserListItem[] */ ] }
```
**Ошибки:** `401 unauthorized`, `403 forbidden`.

### POST `/api/users`
Создаёт пользователя. Гейт `require_admin`.

**Request** — схема `UserCreateRequest`:
```json
{ "username": "Никита", "telegram": "@nikita_ops", "password": "s3cret-pass", "role_id": "2a9f...c0", "team_ids": ["d3f0...a1"] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `username` | string | required, 1–64 после `strip()`, кириллица-допускающий формат ([03-data-model.md](03-data-model.md#правило-username-кириллица-допускающее-нормативно)). Уникален → дубликат `409 username_taken` |
| `telegram` | string? | **опц.** Отсутствует/`null`/`""` → без телеграма. Задан → формат телеграм-ника (опц. ведущий `@`, 5–32 `[A-Za-z0-9_]`; нормализуется — снять `@`, lower-case, [03-data-model.md](03-data-model.md#правило-telegram-телеграм-ник-нормативно)); невалидный → `422 unprocessable` (`details[].field="telegram"`). Уникален среди заданных → дубликат `409 telegram_taken` |
| `password` | string? | **опц.**, 8–128 при наличии. Отсутствует/`null`/`""` → пользователь создаётся **беспарольным** (`password_hash=NULL`; задаст пароль при «открытом первом входе», [ADR-025](adr/ADR-025-passwordless-users-login-identifier-open-first-login.md)). Задан → хэшируется bcrypt; в ответе не возвращается |
| `role_id` | string (uuid) | required. Роль должна существовать → иначе `422 unprocessable` (`details[].field="role_id"`) |
| `team_ids` | string[]? | **опц.**, default `[]`. Список `id` CRM-команд пользователя ([Teams](#teams)); каждый должен существовать → иначе `422 unprocessable` (`details[].field="team_ids"`). Без дублей. Команда при создании **необязательна** (роль — обязательна) |

**Response 201** — созданный `UserListItem` (с `telegram`/`has_password`/`teams`, без пароля).

**Прецеденция ошибок (нормативно):** схемная валидация (`400`/`422` — форма/`username`/`telegram`/`password`) → существование `role_id` и всех `team_ids` (`422 unprocessable`) → уникальность `username` (`409 username_taken`) → уникальность `telegram` (`409 telegram_taken`).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `400 validation_error`, `422 unprocessable` (невалидный `username`/`telegram`/`password`/несуществующий `role_id`/`team_ids`), `409 username_taken`, `409 telegram_taken`.

### PATCH `/api/users/{id}`
Редактирование пользователя: **роль**, **статус активности**, **сброс пароля**. `username` не редактируется. Гейт `require_admin`. Все поля опциональны; передаются только изменяемые (`model_dump(exclude_unset=True)`).

**Request** — схема `UserUpdateRequest`:
```json
{ "telegram": "new_nick", "role_id": "5b1e...aa", "is_active": false, "password": "new-pass-123", "team_ids": ["d3f0...a1", "e4a1...b2"] }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `telegram` | string? | опц. Не передано → не менять; `null`/`""` → **убрать** телеграм (`telegram=NULL`); валидный → установить (нормализация — снять `@`, lower-case; дубль → `409 telegram_taken`; невалидный формат → `422`, [03-data-model.md](03-data-model.md#правило-telegram-телеграм-ник-нормативно)) |
| `role_id` | string (uuid)? | опц. Передано → сменить роль (роль должна существовать → `422`) |
| `is_active` | boolean? | опц. Передано → установить статус. Деактивация аннулирует действующий JWT пользователя на следующем запросе (`401`) |
| `password` | string? | опц. **Не передано → не менять**; передан непустой (8–128) → **сброс/установка пароля** (re-hash bcrypt; для беспарольного — задаёт пароль). Пустая строка `""` → `422 unprocessable` (не «очистка»; сброс в беспарольное состояние через PATCH не предусмотрен) |
| `team_ids` | string[]? | опц. Не передано → членство не менять; передано → **полностью заменяет** набор CRM-команд пользователя (каждый `id` должен существовать → `422`; без дублей; `[]` → выйти из всех команд). Если пользователь — **лидер** команды, из которой его исключают, — лидерство **авто-передаётся** следующему участнику по дате добавления (или команда становится без лидера, если участников не осталось) — [ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md), [Teams](#teams). Прежнее «лидер не исключается» отменено |

**Response 200** — обновлённый `UserListItem` (с `telegram`/`has_password`/`teams`).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 user_not_found`, `400 validation_error`, `422 unprocessable` (несуществующий `role_id`/`team_ids` / невалидный `telegram`/`password`), `409 telegram_taken`.

### DELETE `/api/users/{id}`
Удаляет пользователя (hard delete). Гейт `require_admin`. Членства в командах (`user_teams`) снимаются `ON DELETE CASCADE`. **Если пользователь — лидер команд(ы), удаление НЕ блокируется** ([ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md)): для каждой такой команды лидерство **авто-передаётся** следующему участнику по дате добавления (`user_teams.created_at`); если других участников нет → команда становится **без лидера** (`leader_id=NULL`). Затем пользователь удаляется. Код `409 user_is_team_leader` **упразднён**.

**Response 204** (без тела).

> Удаление собственной учётки БД-администратором допустимо; его действующий JWT аннулируется на следующем запросе (`401`). Супер-админ (`.env`) не удаляется (его нет в таблице). Авто-передача лидерства выполняется в той же транзакции до удаления (правило порядка — [Teams](#teams), [ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md)).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 user_not_found`.

---

## Roles

Реестр ролей с правами (`permissions`-матрица). Модель — [03-data-model.md](03-data-model.md#таблицы-roles-и-users-rbac), решения — [ADR-021](adr/ADR-021-rbac-users-roles.md), [ADR-022](adr/ADR-022-teams-nav-categories.md). Со Спринта A эндпоинты гейтятся **матрицей** `roles:*` (было `require_admin`): `GET`→`roles:view`, `POST`→`roles:create`, `PATCH`→`roles:edit`, `DELETE`→`roles:delete`. Супер-админ и роль `admin` (полный каталог) проходят все.

> **Security-инвариант эскалации (нормативно, [ADR-022](adr/ADR-022-teams-nav-categories.md#4-security-инвариант-эскалации-привилегий-обязательно-реализует-backend)).** Раз редактирование ролей гейтится матрицей, backend ОБЯЗАН запрещать эскалацию (проверка в handler после гейта):
> - **(а) subset:** для актора, который **не** супер-админ и **не** роль `admin`, при `POST`/`PATCH` `permissions` создаваемой/изменяемой роли ⊆ `permissions` актора (по каждой `page` набор `actions` — подмножество actions актора). Нарушение → `403 forbidden`.
> - **(б) защита `admin`:** роль с `name == "admin"` может менять (`PATCH`) / удалять (`DELETE`) **только** `is_superadmin || role == "admin"`. Иначе → `403 forbidden` (даже при наличии `roles:edit`/`roles:delete`).
> - **(в)** назначение ролей пользователям и управление учётками — под `require_admin` ([Users](#users)), вне матрицы: замыкает эскалацию.

### Схема `RoleListItem`
```json
{
  "id": "2a9f...c0",
  "name": "Оператор",
  "permissions": {
    "dashboard": ["view"],
    "servers": ["view"],
    "mail": ["view"]
  },
  "user_count": 3,
  "created_at": "2026-07-07T09:00:00Z",
  "updated_at": "2026-07-07T09:00:00Z"
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | string (uuid) | Идентификатор роли |
| `name` | string | Имя роли (уникально). `admin` — зарезервированное имя (доступ к «Пользователям», защита от правки не-админом) |
| `permissions` | object | `{ "<page>": ["<action>", ...] }`; ключи/действия — из каталога ([Permissions](#permissions)) |
| `user_count` | integer | Число пользователей с этой ролью (`COUNT(users) GROUP BY role_id`, [ADR-022](adr/ADR-022-teams-nav-categories.md)). `≥1` → удаление запрещено (`409 role_in_use`). Супер-админ (`.env`) не учитывается (его нет в `users`) |
| `created_at` / `updated_at` | datetime | Метки |

### GET `/api/roles`
Список ролей с `user_count`. Гейт `require("roles","view")`.

**Response 200** — схема `RoleListResponse`:
```json
{ "items": [ /* RoleListItem[] */ ] }
```
**Ошибки:** `401 unauthorized`, `403 forbidden`.

### POST `/api/roles`
Создаёт роль. Гейт `require("roles","create")`.

**Request** — схема `RoleCreateRequest`:
```json
{ "name": "Оператор", "permissions": { "dashboard": ["view"], "servers": ["view"], "mail": ["view"] } }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 после `strip()`, кириллица-допускающий формат (как `username`). Уникально → `409 role_name_taken` |
| `permissions` | object | required. Валидируется против каталога: ключи ∈ страниц каталога (кроме `users`; допустимы `roles`/`teams`), действия ∈ `CATALOG[page]`, без дублей. Нарушение → `422 unprocessable` (`details[].field="permissions"`). Subset-инвариант эскалации для не-админа → `403` (см. врезку выше) |

**Response 201** — созданный `RoleListItem` (`user_count: 0`).

**Прецеденция ошибок (нормативно):** схемная/каталожная валидация (`400`/`422` — форма/`name`/`permissions` вне каталога) → subset-инвариант эскалации (`403 forbidden` для не-админа, [ADR-022](adr/ADR-022-teams-nav-categories.md)) → уникальность `name` (`409 role_name_taken`).

**Ошибки:** `401 unauthorized`, `403 forbidden` (нет `roles:create` / эскалация), `400 validation_error`, `422 unprocessable` (невалидный `name`/`permissions` вне каталога), `409 role_name_taken`.

### PATCH `/api/roles/{id}`
Редактирование роли (`name` и/или `permissions`-матрица). Гейт `require("roles","edit")`. Все поля опциональны; передаются только изменяемые. Правки прав применяются **без пере-логина** носителей роли (принципал грузится из БД на каждый запрос, [ADR-021](adr/ADR-021-rbac-users-roles.md#5-enforcement-сервер--единственная-граница-безопасности)).

**Request** — схема `RoleUpdateRequest`:
```json
{ "name": "Оператор+", "permissions": { "dashboard": ["view"], "servers": ["view", "edit"], "mail": ["view"] } }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string? | опц., 1–64, формат как при создании. Смена на занятое имя → `409 role_name_taken` |
| `permissions` | object? | опц. Передано → полностью заменяет матрицу прав (валидируется против каталога → `422`; subset-инвариант эскалации для не-админа → `403`) |

**Response 200** — обновлённый `RoleListItem`.

**Прецеденция ошибок (нормативно):** `404 role_not_found` → схемная/каталожная валидация (`400`/`422`) → защита `admin` + subset-инвариант эскалации (`403 forbidden` для не-админа: правка роли `admin` не-админом или выдача прав сверх своих) → уникальность `name` (`409 role_name_taken`).

**Ошибки:** `401 unauthorized`, `403 forbidden` (нет `roles:edit` / правка `admin` не-админом / эскалация), `404 role_not_found`, `400 validation_error`, `422 unprocessable` (невалидный `name`/`permissions` вне каталога), `409 role_name_taken`.

### DELETE `/api/roles/{id}`
Удаляет роль (hard delete). Гейт `require("roles","delete")`. **Запрещено удалять роль, назначенную хотя бы одному пользователю** (`ON DELETE RESTRICT`) → `409 role_in_use`. Роль `admin` может удалить **только** `is_superadmin || role=="admin"` (иначе `403 forbidden`, [ADR-022](adr/ADR-022-teams-nav-categories.md)).

**Response 204** (без тела).

**Ошибки:** `401 unauthorized`, `403 forbidden` (нет `roles:delete` / удаление `admin` не-админом), `404 role_not_found`, `409 role_in_use`.

---

## Teams

Реестр **CRM-команд** (группировка пользователей вокруг лидера). Модуль — [modules/teams](modules/teams/README.md), модель — [03-data-model.md](03-data-model.md#таблицы-teams-и-user_teams-crm-команды), решение — [ADR-022](adr/ADR-022-teams-nav-categories.md). Все эндпоинты гейтятся матрицей `teams:*`: `GET`→`teams:view`, `POST`→`teams:create`, `PATCH`→`teams:edit`, `DELETE`→`teams:delete`. Супер-админ и роль `admin` проходят все.

> **`teams:create`/`teams:edit` — необходимое, но на практике не достаточное условие полного управления составом (нормативно, [ADR-022](adr/ADR-022-teams-nav-categories.md#3-гейтинг-api-нормативно)).** Серверные гейты `teams:*` корректны и не меняются, но выбор `leader_id`/`member_ids` требует справочника пользователей из `GET /api/users`, который под `require_admin`. Поэтому у не-admin с `teams:create`/`teams:edit` источник кандидатов пуст → создание/редактирование состава де-факто доступно только `admin`/супер-админу; `teams:view` даёт полноценный просмотр. Это осознанное следствие §4в (замыкание эскалации через `require_admin` на `/api/users`), а не пробел контракта.

> **CRM-команды ≠ mail-«команды».** Это отдельная сущность в неймспейсе `/api/teams` (uuid, БД CRM, лидер+участники), **не** путать с `GET /api/mail/teams` (`groups` внешнего сервиса, `MailTeam`, integer, прокси без хранения — [ADR-017](adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)). Дизамбигуация — [ADR-022](adr/ADR-022-teams-nav-categories.md#дизамбигуация-crm-команды--mail-команды-нормативно).

### Схема `TeamListItem`
```json
{
  "id": "d3f0...a1",
  "name": "Продажи",
  "leader_id": "b7c1...e4",
  "leader_username": "Никита",
  "member_count": 3,
  "number_count": 2,
  "mail_group_id": 3,
  "members": [
    { "id": "b7c1...e4", "username": "Никита" },
    { "id": "a2c9...f0", "username": "Мария" },
    { "id": "c5e1...d2", "username": "Иван" }
  ],
  "created_at": "2026-07-08T09:00:00Z",
  "updated_at": "2026-07-08T09:00:00Z"
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | string (uuid) | Идентификатор команды |
| `name` | string | Название (уникально). Дубликат → `409 team_name_taken` |
| `mail_group_id` | integer \| null | **Привязка к группе mail-агрегатора** (`teams.mail_group_id`, [ADR-038](adr/ADR-038-mail-headless-integration.md), 1:1 команда↔группа). `null` — команда без привязки к почте. Значения — из `GET /api/mail/teams` (`MailTeam.id`). Используется резолвом `MailScope` и вкладкой «Почты команды» на `/teams` |
| `leader_id` | string (uuid) \| null | ID пользователя-лидера. **`null` — команда без лидера** ([ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md)) |
| `leader_username` | string \| null | Логин лидера (денормализовано, `JOIN users`). **`null` — без лидера** |
| `member_count` | integer | Число участников (`= members.length`; включает лидера, если он есть). Может быть `0` (пустая команда) |
| `number_count` | integer | Число SMS-номеров команды (`COUNT(sms_phone_numbers WHERE team_id = teams.id)`, [SMS](#sms), [ADR-030](adr/ADR-030-sms-module-full-merge.md)). Может быть `0`. Денормализованный агрегат для чипа «N номеров» на карточке команды; список номеров — `GET /api/teams/{id}/numbers` |
| `members` | `TeamMember[]` | Участники команды (включая лидера, если задан; может быть пустым). `TeamMember = { id: string(uuid), username: string }`. Отдаётся в списке для prefill формы редактирования (участников/команд немного, NFR-1) — отдельного `GET /api/teams/{id}` нет |
| `created_at` / `updated_at` | datetime | Метки |

### Схема `TeamListResponse`
```json
{ "items": [ /* TeamListItem[] */ ] }
```

### GET `/api/teams`
Список команд. Гейт `require("teams","view")`. Сортировка `created_at DESC, id` (новые выше; drag-and-drop у команд нет). Пагинации нет (NFR-1).

**Response 200** — `TeamListResponse`.

**Ошибки:** `401 unauthorized`, `403 forbidden`.

### POST `/api/teams`
Создаёт команду. Гейт `require("teams","create")`. **Лидер и участники — опциональны** ([ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md)): можно создать пустую команду без лидера. Если лидер задан — он автоматически добавляется в участники (инвариант «если лидер задан — он ∈ участники»).

**Request** — схема `TeamCreateRequest`:
```json
{ "name": "Продажи", "leader_id": "b7c1...e4", "member_ids": ["a2c9...f0", "c5e1...d2"], "mail_group_id": 3 }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string | required, 1–64 после `strip()`, кириллица-допускающий формат (как `username`). Уникально → `409 team_name_taken` |
| `leader_id` | string (uuid)? | **опц.** Если задан — пользователь-лидер должен существовать → иначе `422 unprocessable` (`details[].field="leader_id"`), добавляется в участники. Если **не** задан — лидер определяется авто-назначением (см. ниже) |
| `member_ids` | string[]? | **опц.**, default `[]`. Участники. Каждый `id` должен существовать → `422 unprocessable` (`details[].field="member_ids"`). Без дублей. `leader_id`, продублированный в `member_ids`, — не ошибка (идемпотентно) |
| `mail_group_id` | integer \| null? | **опц.** ([ADR-038](adr/ADR-038-mail-headless-integration.md)). Привязка к группе mail-агрегатора (`MailTeam.id`). `null`/не задан — без привязки. Значение должно быть свободно (не занято другой командой) → иначе `409 team_mail_group_taken`. Существование группы во внешнем сервисе CRM **не** валидирует (агрегатор — источник истины; несуществующая группа даст пустую видимость почты) |

**Авто-назначение лидера (нормативно, [ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md)):**
- `leader_id` задан → он лидер.
- `leader_id` не задан, `member_ids` непуст → лидером становится **первый** участник (первый в `member_ids`).
- оба не заданы/пусты → команда **без лидера** (`leader_id=null`), пустой состав.

**Response 201** — созданный `TeamListItem` (лидер, если есть, присутствует в `members`; `leader_id`/`leader_username` могут быть `null`; `member_count` может быть `0`).

**Прецеденция ошибок (нормативно):** схемная валидация (`400`/`422` — форма/`name`) → существование `leader_id` (если задан) и всех `member_ids` (`422 unprocessable`) → уникальность `name` (`409 team_name_taken`).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `400 validation_error`, `422 unprocessable` (невалидный `name`/несуществующий `leader_id`/`member_ids`), `409 team_name_taken`, `409 team_mail_group_taken` (`mail_group_id` занят другой командой).

### PATCH `/api/teams/{id}`
Редактирование команды. Гейт `require("teams","edit")`. Все поля опциональны; передаются только изменяемые (`model_dump(exclude_unset=True)`).

**Request** — схема `TeamUpdateRequest`:
```json
{ "name": "Продажи EU", "leader_id": "a2c9...f0", "member_ids": ["a2c9...f0", "c5e1...d2"], "mail_group_id": 3 }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `name` | string? | опц., формат как при создании. Смена на занятое имя → `409 team_name_taken` |
| `leader_id` | string (uuid) \| null? | опц. Задан → сменить лидера (должен существовать → `422`), новый лидер добавляется в участники. `null` → снять лидера (команда без лидера, если авто-передача не применяется) |
| `member_ids` | string[]? | опц. Не передано → состав не менять; передано → **полностью заменяет** набор участников (каждый существует → `422`; без дублей) |
| `mail_group_id` | integer \| null? | опц. (presence-семантика: передано → изменить). `integer` → привязать/сменить группу mail-агрегатора (свободна, иначе `409 team_mail_group_taken`); `null` → снять привязку к почте. Не передано → не менять ([ADR-038](adr/ADR-038-mail-headless-integration.md)) |

**Авто-передача лидерства при `PATCH` (нормативно, [ADR-026](adr/ADR-026-teams-optional-leader-auto-transfer.md)):**
- Если `leader_id` задан → он лидер и включается в участники (даже если отсутствует в `member_ids`).
- Если `leader_id` **не** задан, но текущий лидер **исключён** из нового `member_ids` → лидерство **авто-передаётся** первому из оставшихся участников (по `user_teams.created_at`; для добавленных этой операцией — по порядку в `member_ids`); если участников не осталось → `leader_id=null` (команда без лидера).
- Если у команды не было лидера и добавлены участники → первый становится лидером.
- Инвариант: «если лидер задан — он ∈ участники».

**Response 200** — обновлённый `TeamListItem` (`leader_id`/`leader_username` могут стать `null`).

**Прецеденция ошибок (нормативно):** `404 team_not_found` → схемная валидация (`400`/`422`) → существование `leader_id`/`member_ids` (`422`) → уникальность `name` (`409 team_name_taken`).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 team_not_found`, `400 validation_error`, `422 unprocessable` (невалидный `name`/несуществующий `leader_id`/`member_ids`), `409 team_name_taken`, `409 team_mail_group_taken` (`mail_group_id` занят другой командой).

### DELETE `/api/teams/{id}`
Удаляет команду (hard delete). Гейт `require("teams","delete")`. Строки `user_teams` снимаются `ON DELETE CASCADE`; пользователи не удаляются.

**Response 204** (без тела).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 team_not_found`.

### GET `/api/teams/{id}/numbers`
Список SMS-номеров команды — для detail-панели `/teams` (ленивая загрузка). Гейт `require("teams","view")`. Решение — [ADR-030](adr/ADR-030-sms-module-full-merge.md); модель — [03-data-model.md](03-data-model.md#таблицы-sms-модуля-sms_phone_numbers-sms_inbound-sms_deliveries-sms_telegram_links).

> **Авторизационное сужение полей (нормативно, [ADR-030](adr/ADR-030-sms-module-full-merge.md) §8 + [ADR-034](adr/ADR-034-teams-number-login-app.md)).** Держатель `teams:view` видит **состав** номеров любой команды и их **слабо-чувствительный идентифицирующий контекст `login`/`app_name`** ([ADR-034](adr/ADR-034-teams-number-login-app.md)), но **НЕ** `note`/`label`. Поэтому эндпоинт отдаёт схему `TeamNumberItem` (номер + ссылка на команду + `login`/`app_name`), **не** полный `SmsNumberItem`. Полный контекст с `note`/`label` доступен **только** на эндпоинтах страницы «СМС» (`GET /api/sms/messages`, `GET /api/sms/numbers`) под матрицей `sms:*` и SMS-scope. Гейт этого эндпоинта — `teams:view` (не `sms:view`). Security-трейдофф — [05-security.md](05-security.md#видимость-номеров-в-get-apiteamsidnumbers-adr-034), [ADR-034](adr/ADR-034-teams-number-login-app.md).

**Схема `TeamNumberItem`:**
```json
{ "id": 42, "phone_number": "+13105551234", "team": { "id": "d3f0...a1", "name": "Продажи" }, "login": "acme", "app_name": "WhatsApp" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | `sms_phone_numbers.id` |
| `phone_number` | string | E.164 |
| `team` | `SmsTeamRef` | Команда номера (= запрошенная команда `{id}`). Схема — [SmsTeamRef](#схема-smsteamref) |
| `login` | string \| null | Логин учётной записи номера ([ADR-034](adr/ADR-034-teams-number-login-app.md)); `null` — не задан |
| `app_name` | string \| null | Приложение номера ([ADR-034](adr/ADR-034-teams-number-login-app.md)); `null` — не задано |

**Response 200** — `TeamNumbersResponse`:
```json
{ "numbers": [ /* TeamNumberItem[] */ ] }
```
- Номера, у которых `sms_phone_numbers.team_id = {id}`. Элемент — `TeamNumberItem` (с `login`/`app_name`, **без** `note`/`label` — [ADR-034](adr/ADR-034-teams-number-login-app.md)). Сортировка `created_at DESC, id DESC`. Пагинации нет (номеров немного, NFR-1).

### GET `/api/teams/{id}/mailboxes`
Список почтовых ящиков команды — для detail-панели `/teams` (секция «Почты команды», ленивая загрузка). Гейт `require("teams","view")`. Решение — [ADR-038](adr/ADR-038-mail-headless-integration.md); по образцу `GET /api/teams/{id}/numbers`.

> **Минимальная схема без учётных данных (нормативно).** Держатель `teams:view` видит **состав** ящиков команды, но **НЕ** IMAP/SMTP-креды/хосты/статус синка. Поэтому эндпоинт отдаёт схему `TeamMailboxItem` (id, email, display_name, is_active), **не** полный `MailMailbox`. Полный контекст (статус синка, управление) — на эндпоинтах страницы «Почты» под матрицей `mail:*`. Гейт — `teams:view` (не `mail:view`).

**Резолв.** Команда `{id}` → `teams.mail_group_id`. Если `mail_group_id = NULL` → пустой список (`{ "mailboxes": [] }`, не `404`). Иначе — прокси к внешнему `GET /api/external/mailboxes?group_id=<mail_group_id>`; проекция в `TeamMailboxItem`. `mail_enabled=false` → пустой список (секция «Почты команды» показывает пустое состояние, не ломает detail-панель `/teams`).

**Схема `TeamMailboxItem`:**
```json
{ "id": 7, "email": "inbox@postapp.store", "display_name": "Входящие", "is_active": true }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID ящика во внешнем сервисе |
| `email` | string | Адрес ящика |
| `display_name` | string \| null | Имя; `null` — нет |
| `is_active` | boolean | Активен ли ящик (для кружка статуса) |

**Response 200** — `TeamMailboxesResponse`:
```json
{ "mailboxes": [ /* TeamMailboxItem[] */ ] }
```
- Ящики, у которых `group_id == team.mail_group_id`. Сортировка — как отдаёт внешний сервис. Пагинации нет (NFR-1).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 team_not_found` (нет такой CRM-команды), `502 mail_unavailable` (внешний сервис недоступен при непустом `mail_group_id`).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 team_not_found`.

---

## SMS

Модуль **«СМС»** — приём входящих SMS от Twilio и доставка операторам в Telegram ([ADR-030](adr/ADR-030-sms-module-full-merge.md), [modules/sms](modules/sms/README.md)). Модель — [03-data-model.md](03-data-model.md#таблицы-sms-модуля-sms_phone_numbers-sms_inbound-sms_deliveries-sms_telegram_links); безопасность — [05-security.md](05-security.md#защита-модуля-смс-twilio--telegram).

**RBAC (нормативно).** Приватные эндпоинты гейтятся матрицей `sms:*`: `GET /api/sms/messages`→`sms:view`, `GET /api/sms/numbers`→`sms:view`, `PATCH /api/sms/numbers/{id}`→`sms:edit`, `POST /api/sms/numbers/{id}/transfer`→`sms:transfer`, `DELETE /api/sms/numbers/{id}`→`sms:delete`, `POST /api/sms/numbers/sync`→`sms:sync`. Супер-админ и роль `admin` проходят все. **Действия `create` в каталоге `sms` нет** — номера создаются автоматически. `POST /api/sms/telegram/link` — **только аутентификация** (любой валидный JWT), вне матрицы `sms`. Webhook'и Twilio/Telegram и `POST /api/sms/telegram/auth` — **публичны** (гейт — подпись/секрет/HMAC).

**Видимость (scope, нормативно — [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)).** «Видит все команды» ⇔ **`is_superadmin` ИЛИ роль владеет полным каталогом прав** (`permissions_subset(full_catalog_permissions(), principal.permissions)` истинно — роль покрывает каждое действие каждой страницы `CATALOG`); такой актор (консольный супер-админ и любая admin-роль, напр. seed-`admin` или кастомная «Админ» с полным каталогом) видит **все** SMS/номера, включая unassigned-номера и SMS удалённых номеров. **Прочие роли** (неполный каталог: PM, «Пользователь» и т.п.) видят только SMS/номера **своих команд** — по **текущей** принадлежности номера (`sms_phone_numbers.team_id ∈ team_ids` пользователя из `user_teams`), не по снимку `sms_inbound.team_id`. Запрос к `number_id`/`team_id` вне scope → **пустая страница/список** (анти-энумерация, не `403`/`404`). Правило симметрично для `GET /api/sms/messages` и `GET /api/sms/numbers`. Требует `Principal.user_id` ([05-security.md](05-security.md#расширение-principal-полем-user_id-нормативно)).

### Схема `SmsTeamRef`
```json
{ "id": "d3f0...a1", "name": "Продажи" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | string (uuid) | ID CRM-команды |
| `name` | string | Название команды (текущее) |

### Схема `SmsNumberRef`
Ссылка на **текущий** номер (по `to_number` сообщения). `null`, если номер удалён.
```json
{ "id": 42, "phone_number": "+13105551234", "team": { "id": "d3f0...a1", "name": "Продажи" }, "login": "acme", "app_name": "WhatsApp", "note": "резерв" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | ID номера (`sms_phone_numbers.id`) |
| `phone_number` | string | E.164 |
| `team` | `SmsTeamRef` \| null | **Текущая** команда номера; `null` — unassigned |
| `login` | string \| null | Редактируемое поле |
| `app_name` | string \| null | Редактируемое поле |
| `note` | string \| null | Редактируемое поле |

### Схема `SmsNumberItem`
```json
{
  "id": 42,
  "phone_number": "+13105551234",
  "label": "Sales US",
  "team": { "id": "d3f0...a1", "name": "Продажи" },
  "login": "acme",
  "app_name": "WhatsApp",
  "note": "резерв",
  "is_active": true,
  "created_at": "2026-07-09T09:00:00Z",
  "updated_at": "2026-07-09T09:00:00Z"
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | `sms_phone_numbers.id` |
| `phone_number` | string | E.164, уникален |
| `label` | string \| null | **Системный** никнейм (Twilio `friendly_name`), редактированию через API не подлежит |
| `team` | `SmsTeamRef` \| null | Текущая команда; `null` — unassigned |
| `login` / `app_name` / `note` | string \| null | Редактируемые поля (`PATCH`) |
| `is_active` | boolean | Активность номера |
| `created_at` / `updated_at` | datetime | Метки |

### Схема `SmsMessageItem`
```json
{
  "id": 1057,
  "from_number": "+79161234567",
  "to_number": "+13105551234",
  "body": "Ваш код: 123456",
  "received_at": "2026-07-09T12:34:56Z",
  "number": { "id": 42, "phone_number": "+13105551234", "team": { "id": "d3f0...a1", "name": "Продажи" }, "login": "acme", "app_name": "WhatsApp", "note": "резерв" }
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | integer | `sms_inbound.id` |
| `from_number` | string | Отправитель (E.164) |
| `to_number` | string | Наш номер-получатель (E.164) |
| `body` | string | Текст SMS |
| `received_at` | datetime | Момент приёма |
| `number` | `SmsNumberRef` \| null | **Текущий** номер (по `to_number`); `null` — номер удалён. Источник бейджа команды и пилюль `Логин/Приложение/Примечание` на карточке ([ADR-030](adr/ADR-030-sms-module-full-merge.md) §6) |

### GET `/api/sms/messages`
Лента входящих SMS (newest-first, keyset-курсор). Гейт `require("sms","view")` + scope.

**Query:**
| Параметр | Тип | Правила |
|----------|-----|---------|
| `number_id` | integer? | Фильтр по номеру (`sms_phone_numbers.id`). Резолвится в его `phone_number` → `sms_inbound.to_number = <phone>`. Несуществующий/вне scope → пустая страница |
| `team_id` | string (uuid)? | Фильтр по команде — по **текущей** принадлежности номера (номера команды → их `to_number`). Несуществующая/вне scope → пустая страница |
| `cursor` | string? | Opaque keyset-курсор (`next_cursor` прошлой страницы). Битый → `400 invalid_cursor` |
| `limit` | integer? | Размер страницы, `[1,100]`, default `50`. Вне диапазона → `400 invalid_limit` |

- Фильтры `number_id` и `team_id` **комбинируемы** (AND-пересечение множеств видимых `to_number`). Оба опциональны.
- Порядок — `received_at DESC, id DESC` (индекс `ix_sms_inbound_to_number_received`).

**Response 200** — `SmsMessagesResponse`:
```json
{ "messages": [ /* SmsMessageItem[] */ ], "next_cursor": "eyJ...=" }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `messages` | `SmsMessageItem[]` | Страница SMS (до `limit`) |
| `next_cursor` | string \| null | Курсор следующей (более старой) страницы; `null` — старее нет |

**Ошибки:** `401 unauthorized`, `403 forbidden`, `400 invalid_cursor`, `400 invalid_limit`.

### GET `/api/sms/numbers`
Список номеров. Гейт `require("sms","view")` + scope (admin-уровень — `is_superadmin` ИЛИ полный каталог прав — видит все, включая unassigned; прочие роли — номера своих команд; [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)). Пагинации нет (номеров немного, NFR-1); клиентский поиск по номеру.

**Response 200** — `SmsNumbersResponse`:
```json
{ "numbers": [ /* SmsNumberItem[] */ ] }
```
Сортировка `created_at DESC, id DESC`.

**Ошибки:** `401 unauthorized`, `403 forbidden`.

### PATCH `/api/sms/numbers/{id}`
Правка редактируемых полей `login`/`app_name`/`note`. Гейт `require("sms","edit")` + scope (не admin-уровень — только номер своей команды; unassigned-номер такому актору недоступен → `403 forbidden`; admin-уровень — `is_superadmin` ИЛИ полный каталог прав, [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)). `label` не редактируется.

**Request** — схема `SmsNumberUpdateRequest` (все поля опциональны; передаётся только изменяемое):
```json
{ "login": "acme", "app_name": "WhatsApp", "note": "" }
```
**Presence-семантика затирания (нормативно):** различается на уровне роутера по наличию ключа в теле (идиома проекта, как `PATCH /api/numbers/{id}` донора / `PATCH /api/admin/users`):
- ключ **присутствует**, значение (после `strip`) **непустое** → установить (`max_length=200`; превышение → `400 validation_error`);
- ключ **присутствует**, значение пустое/пробельное **или** `null` → **затереть** (`NULL`);
- ключ **отсутствует** → поле не меняется.

**Response 200** — обновлённый `SmsNumberItem`.

**Ошибки:** `401 unauthorized`, `403 forbidden` (номер вне scope), `404 sms_number_not_found`, `400 validation_error`.

### POST `/api/sms/numbers/{id}/transfer`
Назначить/переназначить/снять команду у номера. Гейт `require("sms","transfer")`.

**Request** — схема `SmsNumberTransferRequest`:
```json
{ "team_id": "d3f0...a1" }
```
| Поле | Тип | Правила |
|------|-----|---------|
| `team_id` | string (uuid) \| null | `null` → снять команду (unassigned); иначе привязать к существующей команде |

**Response 200** — обновлённый `SmsNumberItem` (при `team_id=null` → `team=null`).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 sms_number_not_found`, `404 sms_team_not_found` (переданный `team_id` не существует).

### DELETE `/api/sms/numbers/{id}`
Удалить номер. Гейт `require("sms","delete")`. История SMS сохраняется (`sms_inbound` не затрагивается); SMS удалённого номера остаются видны только actor'у admin-уровня (`sees_all_teams` — `is_superadmin` ИЛИ полный каталог прав, [ADR-032](adr/ADR-032-sms-visibility-admin-full-catalog.md)).

**Response 204** (без тела).

**Ошибки:** `401 unauthorized`, `403 forbidden`, `404 sms_number_not_found`.

### POST `/api/sms/numbers/sync`
On-demand синхронизация входящих номеров Twilio-аккаунта в `sms_phone_numbers` как unassigned. Гейт `require("sms","sync")`. Тело пустое.

- Через Twilio API (аутентификация `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`) получить **все** входящие номера (все страницы пагинации). Каждый E.164 нормализуется, upsert `ON CONFLICT (phone_number) DO NOTHING` как unassigned (`team_id=NULL`, `added_by_user_id=NULL`). Существующие номера **не** перепривязываются; `label` обновляется из Twilio `friendly_name`. Авто-назначения команд нет.
- Twilio SDK синхронный → вызов из async-хендлера через `asyncio.to_thread`.

**Response 200** — `SmsSyncResult`:
```json
{ "synced_total": 12, "added": 3, "skipped_existing": 9 }
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `synced_total` | integer | Получено из Twilio (все страницы) |
| `added` | integer | Реально вставлено новых |
| `skipped_existing` | integer | `synced_total − added` (уже были) |

**Ошибки:** `401 unauthorized`, `403 forbidden`, `502 twilio_error` (сбой Twilio API), `503 twilio_not_configured`.

### POST `/api/sms/telegram/link`
Привязка Telegram-аккаунта оператора к его CRM-пользователю. **Auth — только JWT** (любой валидный access-токен; вне матрицы `sms`, [ADR-030](adr/ADR-030-sms-module-full-merge.md) §7). Требует `Principal.user_id` (супер-админ без `uid` привязать линк не может → `403 forbidden`).

> **Вне критического пути Mini App (ADR-031).** В беспарольной модели операторская Mini App (`/tg/sms`) привязывается **автоматически** внутри Telegram-SSO (`POST /api/sms/telegram/auth`, см. ниже) — отдельный вызов `link` ей не нужен. Этот эндпоинт остаётся валидным для self-link аутентифицированного CRM-пользователя (напр. из админ-SPA); Mini App его не использует.

**Request** — схема `TelegramLinkRequest`:
```json
{ "init_data": "<raw Telegram WebApp initData>" }
```
- Валидация HMAC-SHA256 (`WebAppData`-ключ из `SMS_TELEGRAM_BOT_TOKEN`) + TTL `auth_date`. Успех → upsert `sms_telegram_links(telegram_user_id, user_id = principal.user_id, dead_at = NULL)` (идемпотентно, `ON CONFLICT (telegram_user_id) DO UPDATE`). Привязывает **свой** Telegram.

**Response 200** — `TelegramLinkResponse`:
```json
{ "linked": true, "telegram_user_id": 123456789 }
```

**Ошибки:** `401 unauthorized` (нет JWT), `403 forbidden` (супер-админ без `uid`), `401 invalid_init_data`, `401 init_data_expired`, `400 validation_error`.

### POST `/api/sms/telegram/auth`
**Беспарольный Telegram-SSO** операторской Mini App (`/tg/sms`, [ADR-031](adr/ADR-031-sms-operator-mini-app.md)). **Публичный**, CSRF/JWT-exempt (гейт — HMAC `init_data`). Определяет CRM-оператора по Telegram-идентичности и **выдаёт CRM access-JWT** (как `POST /api/auth/login`), авто-привязывая линк. Redis/pending не используются — SSO stateless ([ADR-030](adr/ADR-030-sms-module-full-merge.md) §3).

**Резолв оператора (нормативно):**
1. Валидировать `init_data` (HMAC-SHA256 `WebAppData` из `SMS_TELEGRAM_BOT_TOKEN` + TTL `auth_date`). Извлечь `telegram_user_id` и `username` (из `user`-payload initData; может отсутствовать).
2. **Первично — по иммутабельному `telegram_user_id`:** линк `sms_telegram_links WHERE telegram_user_id = X` (независимо от `dead_at`). Есть → `user_id` линка; `dead_at IS NOT NULL` → revive (`dead_at=NULL`).
3. **Bootstrap — по username (только если линка нет):** `username` присутствует → `normalize_telegram(username)` → `users WHERE telegram = norm AND is_active`. Найден → upsert линк (`telegram_user_id → user_id`, `dead_at=NULL`).
4. `user_id` получен и пользователь активен → идемпотентно `first_login_at=now()` если `NULL` ([ADR-028](adr/ADR-028-user-status-first-login.md)) → выпуск access-JWT (`uid`, `role`, `superadmin:false`, `type:"access"`) → `200`.
5. Иначе → `403 sms_operator_not_provisioned`.

**Приоритет:** `telegram_user_id` (через линк) первичен (иммутабелен, переживает смену ника); `username` — только bootstrap первого контакта. После первой привязки резолв идёт по `telegram_user_id`, устаревший `users.telegram` не мешает.

**Request** — схема `TelegramAuthRequest`: `{ "init_data": "<raw initData>" }`.

**Response 200** — схема `TelegramAuthResponse` (успешный SSO):
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "telegram_user_id": 123456789,
  "linked": true
}
```
| Поле | Тип | Примечание |
|------|-----|-----------|
| `access_token` | string | Обычный CRM access-JWT — как у `POST /api/auth/login`: `sub`=**`users.username`** резолвнутого CRM-пользователя (не Telegram-`username` из `init_data`; тот в id-first-пути может быть `None`), `uid`=`users.id`, `role`=`role.name`, `superadmin:false`, `type:"access"`. Хранится Mini App в памяти auth-store |
| `token_type` | string | `"bearer"` |
| `expires_in` | integer | TTL access-токена в секундах (`JWT_EXPIRES_MIN`×60; default 86400) |
| `telegram_user_id` | integer | Из проверенного `init_data` |
| `linked` | boolean | Всегда `true` при успехе (линк upserted/revived на этот `telegram_user_id`) |

**Ошибки:**
- `401 invalid_init_data` — плохой HMAC/структура `init_data`.
- `401 init_data_expired` — протух `auth_date`.
- `403 sms_operator_not_provisioned` — Telegram не сопоставлен ни с одним **активным** CRM-оператором: нет линка по `telegram_user_id` **и** нет активного `users.telegram = normalize(username)` (в т.ч. `username` в `init_data` отсутствует, либо найденный пользователь `is_active=false`). Mini App показывает «обратитесь к администратору». `init_data` не логируется.
- `400 validation_error` — пустой/невалидный `init_data`.

> **Смена контракта относительно ADR-030.** Прежняя редакция возвращала только `{ linked, telegram_user_id }` без JWT (статус-bootstrap). По [ADR-031](adr/ADR-031-sms-operator-mini-app.md) (решение пользователя) эндпоинт стал беспарольным SSO с выдачей JWT — это нормативный источник; OpenAPI и реализация обязаны соответствовать этой (SSO) схеме.

### POST `/api/sms/webhooks/twilio/sms` (публичный)
Приём входящего SMS от Twilio. **Публичный**, CSRF/JWT-exempt. **Auth — подпись Twilio.** `Content-Type: application/x-www-form-urlencoded`.

- **Тело (Twilio-поля):** `MessageSid`, `From`, `To`, `Body`, + прочие поля Twilio (сохраняются целиком в `sms_inbound.raw_payload`).
- **Валидация подписи:** при `VERIFY_TWILIO_SIGNATURE=true` — проверка `X-Twilio-Signature` по `TWILIO_AUTH_TOKEN`; URL для подписи реконструируется **из `SMS_PUBLIC_BASE_URL` + путь** (единственный источник истины; `X-Forwarded-*` для подписи не используется — [05-security.md](05-security.md#подпись-twilio-post-apismswebhookstwiliosms)). Затем `handle_incoming_sms` (дедуп по `MessageSid` → сохранение → fan-out по команде, [modules/sms](modules/sms/README.md#приём-sms-и-fan-out-нормативно)).

**Ответы:**
- `200` — `<Response></Response>` (`application/xml`). Всегда при успешной обработке, включая неизвестный номер (`team_id=NULL`, доставок нет) и дубликат по `MessageSid`.
- `401 invalid_twilio_signature` — неверная/отсутствующая подпись.
- `503 twilio_not_configured` — `VERIFY_TWILIO_SIGNATURE=true`, но `TWILIO_AUTH_TOKEN` не задан.

### POST `/api/sms/telegram/webhook` (публичный)
Приём апдейтов **SMS-delivery-бота**. **Публичный**, CSRF/JWT-exempt. **Auth — секрет-токен** `X-Telegram-Bot-Api-Secret-Token` (`SMS_TELEGRAM_WEBHOOK_SECRET`, constant-time compare до разбора тела). `Content-Type: application/json`, тело — Telegram Update as-is.

- Бот обрабатывает **только `/start`** → `sendMessage(chat_id, приветствие)` с кнопкой `web_app` (`url = SMS_TELEGRAM_WEBAPP_URL`). Прочие апдейты → `200` no-op. Ошибка `sendMessage` не роняет обработчик (лог без секретов, `200`). Тело апдейта и токены не логируются.

**Ответы:** `200` (обработано/no-op); `403 invalid_webhook_secret` (неверный/отсутствующий секрет).

---

## Health

### GET `/api/health`
Без JWT. Liveness/readiness.

**Response 200**
```json
{ "status": "ok", "db": "up", "prometheus": "up" }
```
- `db`/`prometheus` ∈ {`up`,`down`}. Если зависимость down → статус `200` с `status:"degraded"` (для мониторинга самого backend), решение не ронять health на 503 при деградации Prometheus — он не критичен для liveness.

---

## Пороги зон (используются backend и frontend одинаково)

Цвет дуги спидометра СТРОГО по нагрузке:

| Зона | Условие (`usage_percent`) | Цвет |
|------|---------------------------|------|
| `green` | `< 80` | зелёный |
| `yellow` | `>= 80` и `<= 90` | жёлтый |
| `red` | `> 90` | красный |

Граничные значения: 80 → yellow, 90 → yellow, 90.01 → red. Backend проставляет `zone` в ответах; frontend дублирует ту же логику для оптимистичной отрисовки (значения порогов — единый конфиг, см. [08-design-system.md](08-design-system.md#зоны-нагрузки)).

## Версионирование и OpenAPI

- На Этапе 1 без версии в пути (`/api/...`). При несовместимых изменениях — `/api/v2`.
- FastAPI публикует OpenAPI в `/api/openapi.json` и Swagger UI в `/api/docs` только в dev; в production они отключены (`404`) — см. [05-security.md](05-security.md#документация-api-apidocs-apiopenapijson).
- Этот документ — нормативный источник; OpenAPI должен ему соответствовать.

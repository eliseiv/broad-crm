# Модуль `backends` — Реестр бэков (сервисов) с healthcheck по домену и Telegram-алертами

Статус: `implemented` (Спринт 2) · Исполнитель: backend, frontend

## Scope

Управление списком бэков (backend-сервисов): добавление, список, **редактирование** (`code`/`name`/`domain` + доп. поля — ниже), удаление, **перестановка порядка (drag-and-drop, единый список)**, **поиск** и **группировка по одинаковому `name`** ([ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md)) и **периодическая автоматическая проверка доступности** каждого бэка запросом `GET {domain}health` (`domain` — канон `https://<host>/`, [ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md)) с уведомлением администратора в Telegram при недоступности (🔴) и восстановлении (🟢). Базовые поля — **Код** (`code`, **уникален**) / **Название** (`name`, **НЕ уникален**) / **Домен** (`domain`). **Доп. опциональные поля ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)):** связи **Сервер** (`server_id` → `servers`) и **ИИ-ключ** (`ai_key_id` → `ai_keys`), секреты **API KEY**/**ADMIN API KEY** (`api_key`/`admin_api_key`, Fernet, reveal под `backends:edit`), **Git** (`git`), **Примечания** (`note`). Модель — [03-data-model.md](../../03-data-model.md#таблица-backends), API-контракт — [04-api.md](../../04-api.md#backends), решения — [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md), [ADR-011](../../adr/ADR-011-poryadok-blokov-server-side-dnd-kit.md) (drag-and-drop/`position`).

Образец модуля целиком — **прокси** ([modules/proxies](../proxies/README.md), [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)): та же схема «модель со статусом в БД + отдельный фоновый монитор + собственный `TelegramClient`». Отличия — см. [«Отличия от прокси»](#отличия-от-прокси-нормативно).

## Out of scope (Этап 1)

- Ручной триггер «проверить сейчас», настраиваемый интервал проверки, per-backend health-path/схема ([TD-029](../../100-known-tech-debt.md)).
- Windowed-детект доступности (сглаживание транзиентных всплесков за окно, по образцу [ADR-018](../../adr/ADR-018-notifier-windowed-offline-recovery-alert-log.md)) — на Этапе 1 транзиентность гасится ретраями внутри одной проверки ([TD-029](../../100-known-tech-debt.md)).
- Карточка «Бэки» на «Дашборде» (сводный счётчик) — [TD-029](../../100-known-tech-debt.md).
- Конфигурируемый путь healthcheck (фиксирован `/health`) и схема (фиксирована `https://`).
- Измерение задержки/латентности, тела ответа, парсинг JSON `/health`, аутентификация к `/health`.

## Отличия от прокси (нормативно)

| Аспект | Прокси | Бэки |
|--------|--------|------|
| Секрет | `password` (опц., Fernet, `password_encrypted`) | **два опц. секрета** `api_key`/`admin_api_key` (Fernet, `*_encrypted`, [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)); прежде секрета не было ([ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md)) |
| Идентификатор | `id` (UUID); `name` не уникален | `code` — **уникальный** бизнес-код (дубликат → `409 backend_code_taken`); `name` **НЕ уникален** (дубли группируются на UI, [ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md)) |
| Поля | `name`/`proxy_type`/`host`/`port`/`username`/`password` | **`code`/`name`/`domain`** + опц. `server_id`/`ai_key_id` (FK), `api_key`/`admin_api_key` (секреты), `git`/`note` ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)) |
| Проверка | `GET` эталонного URL **через** `httpx.AsyncClient(proxy=...)` | `GET {domain}health` **напрямую** (без прокси-туннеля), строго `2xx`; `domain` — канон `https://<host>/` ([ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md)) |
| Эталонный URL | `PROXY_CHECK_URL` (конфиг) | путь **фиксирован** `/health`, URL = `{domain}health` = `https://<host>/health` |
| Исход `unknown` | нет | **нет** (как у прокси) |
| Интервал (default) | `PROXY_CHECK_INTERVAL_SEC=60` | `BACKEND_CHECK_INTERVAL_SEC=60` |
| Overall-deadline проверки | `PROXY_CHECK_DEADLINE_SEC=30` | `BACKEND_CHECK_DEADLINE_SEC=30` ([ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) |
| Модель алерта недоступности | **grace-порог 30 мин** непрерывной недоступности перед 🔴 (`PROXY_ALERT_AFTER_SEC=1800`, поля `error_since`/`alert_sent` — [ADR-027](../../adr/ADR-027-proxies-alert-grace.md); ранее была immediate — снято) | **grace-порог 30 мин** непрерывной недоступности перед 🔴 (`BACKEND_ALERT_AFTER_SEC=1800`, поля `error_since`/`alert_sent` — [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)) |
| Re-check при `PATCH` | смена `proxy_type`/`host`/`port`/`username`/`password` | смена **`domain`** (только оно связано с подключением) |

Всё остальное (статус в БД, `error→working` recovery, монитор стартует всегда, Telegram гейтится `notifier_enabled`, немедленная проверка при создании, единый список с reorder как у серверов) — как у прокси. **Модель алерта у прокси и бэков теперь единая** ([ADR-027](../../adr/ADR-027-proxies-alert-grace.md)): 🔴 откладывается на grace-порог 30 мин у обеих сущностей (`check_status` в обоих случаях меняется сразу — реальность в UI). Прежнее отличие «прокси — немедленно» из [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md) снято.

## Безопасность (нормативно)

- **Публичные поля:** `code`, `name`, `domain`, `git`, `note`, `server_id`, `ai_key_id` — plaintext, возвращаются в API как есть.
- **Секреты ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)):** `api_key`, `admin_api_key` шифруются **Fernet** (`FERNET_KEY`, `encrypt_secret`/`decrypt_secret`); в БД — `api_key_encrypted`/`admin_api_key_encrypted bytea NULL`. В обычных ответах — только `has_api_key`/`has_admin_api_key`; plaintext — только on-demand reveal `GET /api/backends/{id}/api-key` · `/admin-api-key` под `backends:edit` (`no-store`, аудит `secret_revealed`, `404 secret_not_set` если не задан — [ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md), [05-security.md](../../05-security.md#защита-api-ключей-бэка-adr-040)). Prior [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md) «у бэка нет секрета» — снят.
- В логах монитора собранный URL (`{domain}health` = `https://<host>/health`) не секретен, но лог ошибок остаётся без чувствительных данных; `api_key`/`admin_api_key` в лог **не** пишутся (фильтр секретов). Логируется `backend_check_error` (warning) с `code`/`domain`/причиной — без тел ответов/секретов.

## Backend — ТЗ

Слои и стек — как в модулях `servers`/`proxies`: router → service → repository (SQLAlchemy async), Pydantic-схемы = контракт. Образцы для переиспользования: `app/api/*`, `app/services/proxy_service.py`, `app/services/proxy_monitor_service.py`, `app/infra/proxy_check.py`, `app/infra/telegram.py`, `app/domain/notifications.py`, `app/repositories/*`, `app/models/*`, `app/schemas/*`; фоновая задача — паттерн `asyncio.create_task` + сильная ссылка (как прокси-монитор в `app/main.py` lifespan).

### Endpoints (все под JWT, префикс `/api`)

- `GET /api/backends` → список `BackendListItem`. Сортировка `position ASC, created_at DESC, id`. Единый плоский список. Пагинации нет. См. [04-api.md](../../04-api.md#get-apibackends).
- `POST /api/backends {code, name, domain, server_id?, ai_key_id?, api_key?, admin_api_key?, git?, note?}` → `202`; валидация, канонизация домена к `https://<host>/`, проверка уникальности `code` (дубль → `409 backend_code_taken`) и существования `server_id`/`ai_key_id` (несуществующий → `422`), шифрование секретов (Fernet), `INSERT check_status='pending'` (`position` = `DEFAULT 0`), запуск **немедленной фоновой проверки** (`asyncio.create_task`). Возвращает созданный `BackendListItem` (`check_status:"pending"`). См. [04-api.md](../../04-api.md#post-apibackends).
- `PATCH /api/backends/order {ids}` → `204`; перестановка **единого списка** (как `PATCH /api/servers/order`), `position = 0..N-1` в одной транзакции. Прецеденция кодов — [04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов).
- `PATCH /api/backends/{id} {code?, name?, domain?, server_id?, ai_key_id?, api_key?, admin_api_key?, git?, note?}` → `200`; редактирование (presence-семантика; секреты/FK/`git`/`note` — `null`/`""` очищает). Уникальность `code`, существование FK и **триггер re-check ТОЛЬКО при смене `domain`** — см. [«Редактирование бэка»](#редактирование-бэка-patch-нормативно) и [04-api.md](../../04-api.md#patch-apibackendsid).
- `GET /api/backends/{id}/status` → `{id, check_status, error_message, last_checked_at}`. Лёгкий endpoint для polling статуса после добавления/редактирования.
- `DELETE /api/backends/{id}` → `204`; hard delete. Повтор → `404 backend_not_found`.
- **Reveal секретов ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md), по образцу [ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)):** `GET /api/backends/{id}/api-key` и `GET /api/backends/{id}/admin-api-key` → `SecretRevealResponse {value}`, гейт `backends:edit`, `Cache-Control: no-store`, аудит `secret_revealed`, `404 secret_not_set` если ключ не задан. См. [04-api.md](../../04-api.md#reveal-секретов-по-требованию-adr-035).
- **Reverse-lookup для detail сервера/ключа ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)):** `GET /api/servers/{id}/backends` (гейт `servers:view`) и `GET /api/ai-keys/{id}/backends` (гейт `ai-keys:view`) → `{backends: BackendRef[]}` (`{code,name,domain}`). Свёрнутый счётчик секции — `ServerListItem.backend_count`/`AiKeyListItem.backend_count`. См. [04-api.md](../../04-api.md#get-apiserversidbackends).

Коды ошибок и точные схемы — [04-api.md](../../04-api.md#backends). Невалидный формат `domain` → `422 unprocessable`; дубликат `code` → `409 backend_code_taken`.

### Редактирование бэка (`PATCH`, нормативно)

`PATCH /api/backends/{id}` принимает `{code?, name?, domain?, server_id?, ai_key_id?, api_key?, admin_api_key?, git?, note?}` (все опциональны). «Переданное поле» определяется по множеству заданных полей запроса (`model_dump(exclude_unset=True)` / `__pydantic_fields_set__` в Pydantic v2).

1. **`code`** — если передан, заменяет значение (с валидацией длины). **Уникальность повторно проверяется**: смена на `code`, занятый **другим** бэком → `409 backend_code_taken`. Не передан — не меняется.
2. **`name`** — если передан, заменяет (валидация длины). Не передан — не меняется.
3. **`domain`** — если передан, **канонизируется к `https://<host>/`** ([ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md), см. ниже) и заменяет (невалидный host → `422`). Не передан — не меняется.
4. **`server_id`/`ai_key_id`** — не переданы → не менять; `null` → обнулить связь; `uuid` → проверить существование (несуществующий → `422 unprocessable`, `details[].field`) и установить.
5. **`api_key`/`admin_api_key`** (секреты) — не переданы → не менять; непустая строка → зашифровать (Fernet) и установить; `null`/`""` → очистить (`NULL`). **`git`/`note`** (не секреты) — аналогично (непустая → установить; `null`/`""` → очистить).
6. **Re-check триггерится ТОЛЬКО если изменился `domain`** (единственное поле, связанное с подключением): `check_status='pending'`, `error_message=NULL`, запуск немедленной фоновой проверки (тот же путь, что `POST`; первый переход считается от `prev_status='pending'`). Первая неуспешная проверка после edit шлёт **🔴** (как для нового бэка), успешная — молча (`pending→working`).
7. **Смена только `code`/`name`/`server_id`/`ai_key_id`/`api_key`/`admin_api_key`/`git`/`note`** — `check_status` не трогается, проверка не перезапускается.
8. `updated_at` обновляется всегда при изменении хотя бы одного поля. `last_checked_at` при re-check не сбрасывается.

### Перестановка (нормативно) — эндпоинт живёт, но UI его НЕ вызывает

> **⚠️ DnD на странице «Бэки» УБРАН ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2а).** Порядок карточек `/backends` задаётся **клиентской сортировкой по `name`** (регистронезависимо, `localeCompare` ru; tie-break — `code`, UNIQUE ⇒ детерминированно), а не рукой. **Колонка `backends.position` и эндпоинт `PATCH /api/backends/order` СОХРАНЯЮТСЯ** в БД/API (удаление контракта ради нулевой выгоды отклонено): `position` по-прежнему определяет порядок ответа `GET /api/backends` **и** порядок [перечня бэков в Telegram-алертах](#формат-сообщений-telegram-точно-нормативно--источник-истины). Неиспользуемый UI-путь reorder — [TD-054](../../100-known-tech-debt.md).

- Бэки — **единый список** (без группировки), reorder по образцу **серверов/прокси** ([04-api.md](../../04-api.md#patch-apiserversorder)). `PATCH /api/backends/order {ids}` принимает полный упорядоченный список `id` и в одной транзакции присваивает `position = 0..N-1`.
- Прецеденция ошибок — общая для всех order-эндпоинтов ([04-api.md](../../04-api.md#прецеденция-ошибок-валидации-нормативно-едино-для-всех-order-эндпоинтов)): битое тело → `400`; любой несуществующий `id` → `404 backend_not_found`; только если все `id` существуют — неполная перестановка → `422`.
- Правило сортировки и присвоения `position` — общее с серверами ([03-data-model.md](../../03-data-model.md#колонка-position-порядок-карточек)).

### Требования

1. `code` уникален (`UNIQUE`); дубликат при `POST`/`PATCH` → `409 backend_code_taken` (детерминированно, из `IntegrityError`/предварительной проверки). Прецеденция: схемная валидация (`400`/`422`) до `409`.
2. `check_status` ∈ {`pending`,`working`,`error`}, default `pending`. `error_message` — русскоязычная причина при `error`, иначе `NULL`.
3. `updated_at`/`last_checked_at` обновляются при каждой проверке с конклюзивным исходом (`working`/`error`) атомарным `UPDATE`. (У бэков исхода `unknown` нет — любой провал после ретраев конклюзивен.)
4. **Каждая Alembic-миграция обязана иметь рабочий `downgrade()`** ([07-deployment.md](../../07-deployment.md#откат-миграций-бд), [03-data-model.md](../../03-data-model.md)).
5. Таблица создаётся миграцией **`0007_create_backends`** (`down_revision="0006_create_proxies"` — текущая голова цепочки), с уникальным индексом `uq_backends_code`, колонкой `position` (`integer NOT NULL DEFAULT 0`) и индексом `ix_backends_position` ([03-data-model.md](../../03-data-model.md#миграция-0007_create_backends-концепт)).

### Нормализация домена и проверка (нормативно)

**Нормализация домена к канону `https://<host>/` (нормативно, [ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md))** — на входе `POST`/`PATCH`, чистая функция, тестируется без сети:

1. Trim пробелов.
2. Снять схему `http://` / `https://`, если присутствует (регистронезависимо).
3. Снять всё, начиная с первого `/` (путь/query/fragment) — оставить authority `host[:port]`.
4. Привести host к нижнему регистру.
5. **Валидация host:** непустой `host` из валидных DNS-меток (буквы/цифры/дефис, точки-разделители) с опциональным `:port` (`1..65535`); без пробелов/`/`. Невалидный → `422 unprocessable` (`details:[{field:"domain", ...}]`).
6. **Собрать канон:** `"https://" + host[:port] + "/"` — это значение сохраняется в `domain`.

Примеры: `https://lumorixsite.shop/` → `https://lumorixsite.shop/`; `https://lumorixsite.shop` → `https://lumorixsite.shop/`; `lumorixsite.shop` → `https://lumorixsite.shop/`; `HTTP://API.Example.com:8443/path?x=1` → `https://api.example.com:8443/`.

**Построение health-URL (нормативно — анти-двойная-схема, [ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md)).** Так как `domain` уже содержит схему и завершающий `/`, health-URL строится **дописыванием `health`**, а НЕ склейкой `https://{domain}/health` (иначе битый `https://https://…//health`):

```
health_url = domain + "health"      # domain = "https://<host>/"  →  "https://<host>/health"
```

`https://lumorixsite.shop/` → `https://lumorixsite.shop/health`. Сборка URL — отдельная чистая функция (`app/infra/backend_check.py`), тестируется на побайтовое совпадение (в т.ч. анти-регресс двойной схемы). Путь `/health` и схема `https://` **фиксированы**.

**Проверка доступности** = `GET {health_url}` (`= https://<host>/health`). HTTP-клиент — `httpx.AsyncClient(timeout=<явный httpx.Timeout по всем фазам>, verify=True, follow_redirects=False)` с ограниченными ретраями на транзиентные ошибки (backoff-паттерн `app/infra/ai_provider.py` / проверки прокси).

> **Анти-зависание (нормативно, [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)).** Как у прокси: `BACKEND_CHECK_TIMEOUT_SEC` применяется как явный `httpx.Timeout(connect=t, read=t, write=t, pool=t)` (не одиночный float), а проверка одного бэка (`check_one`, вкл. все ретраи) оборачивается `asyncio.wait_for(..., BACKEND_CHECK_DEADLINE_SEC)` (default **30 с**) → при превышении исход `error` «Таймаут подключения». Проверка всегда завершается конклюзивно; `deadline` (30 с) < интервал (60 с).

**Маппинг результата → исход проверки:**

| Ответ / событие | Исход | `check_status` | `error_message` (рус.) |
|-----------------|-------|----------------|-------------------------|
| `2xx` | `working` | `working` | `NULL` |
| Таймаут (после ретраев) | `error` | `error` | **«Таймаут подключения»** |
| Сетевая/DNS/TLS/транспортная ошибка (после ретраев) | `error` | `error` | **«Бэк недоступен»** |
| Не-2xx ответ (`3xx`/`4xx`/`5xx`) | `error` | `error` | **«Ошибка бэка (HTTP N)»** (N = код статуса) |
| Прочая ошибка httpx | `error` | `error` | **«Ошибка бэка»** |

- **Строго `2xx`** → `working` (в отличие от прокси, где принимаются и `3xx`): `/health` должен отвечать `200`; редиректы не следуются (`follow_redirects=False`), `3xx` = ошибка здоровья.
- **Нет исхода `unknown`** (как у прокси, [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md)): недоступность бэка и есть отслеживаемое событие. Чтобы единичный сетевой всплеск не давал ложный флип, проверка делает **ограниченные ретраи внутри себя** (backoff, ≈3 попытки) и только затем заключает `error`.
- Причины (`error_message`) — русскоязычные, приходят в API готовыми; frontend показывает их как есть.

### Фоновый монитор `BackendMonitorService` (нормативно)

Отдельная фоновая asyncio-задача (**по образцу `ProxyMonitorService`**, [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md), [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)). Состояние переходов берётся из БД `backends.check_status` (персистентно, переживает рестарт).

- **Запуск:** в `lifespan` (`app/main.py`), рядом с прокси- и AI-монитором. Монитор **стартует ВСЕГДА** (не гейтится Telegram) — обновление `check_status` для UI работает независимо от бота. Telegram-клиент передаётся как `None` при отключённом боте.
- **Остановка:** отмена задачи при shutdown (`task.cancel()` + `suppress(CancelledError)`).
- **Цикл:** бесконечный `while True`: одна итерация проверки всех бэков → `asyncio.sleep(BACKEND_CHECK_INTERVAL_SEC)` (default 60 с). Необработанное исключение внутри итерации логируется и **не валит задачу**.
- **Итерация (`poll_once`):** открыть короткоживущую сессию БД, получить снимок всех бэков (`id, code, name, domain, prev_status=check_status`), закрыть сессию. Для каждого бэка (под семафором ограничения конкурентности, образец прокси-монитора): собрать health-URL **`{domain}health`** (домен — канон `https://<host>/`, [ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md); НЕ склейка `https://{domain}/health`), выполнить проверку, вычислить исход; **при конклюзивном исходе** — обновить БД (`check_status`, `error_message`, `last_checked_at`, `updated_at`) отдельным атомарным `UPDATE`; вычислить переход относительно `prev_status`, при необходимости отправить алерт (если `notifier_enabled`).
- **Немедленная проверка при создании (`POST`)** и при re-check (`PATCH` со сменой `domain`): та же логика проверки одного бэка (`check_one`) запускается фоново сразу после `INSERT`/`UPDATE`. Первый переход считается от `prev_status='pending'`.

### Переходы статуса и алерты (нормативно)

**Grace-порог 30 минут перед 🔴 (нормативно, [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)).** `check_status` переходит в `error` **немедленно** (реальность в UI сразу), но Telegram-🔴 шлётся только если бэк недоступен **непрерывно ≥ `BACKEND_ALERT_AFTER_SEC` (default 1800 с = 30 мин)** — устраняет ложные алерты при штатных перезагрузках бэка (1–2 мин). Состояние эпизода — персистентные поля `backends.error_since` (начало недоступности) и `backends.alert_sent` (был ли 🔴) ([03-data-model.md](../../03-data-model.md#таблица-backends), миграция `0013_backends_alert_grace`).

Чистая функция перехода (time-aware) `evaluate_transition(prev_status, result, error_since, alert_sent, now) -> (new_status, error_message, new_error_since, new_alert_sent, alert)`, `alert ∈ {None, "error", "recovery"}`:

| `prev` | `cur` (result) | `new_status` | `error_since` | `alert_sent` | Алерт |
|--------|----------------|--------------|---------------|--------------|-------|
| `pending` / `working` | `error` | `error` | ← `now` (старт эпизода) | остаётся `false` | **нет** (grace-окно только началось) |
| `error` | `error` (прошло `< 30 мин`) | `error` | без изменений | `false` | молча (обновляется `error_message`) |
| `error` | `error` (прошло `≥ 30 мин`, `alert_sent=false`) | `error` | без изменений | → `true` | **🔴 «Бэк не работает»** |
| `error` | `error` (`alert_sent=true`) | `error` | без изменений | `true` | молча (уже слали) |
| `error` | `working` (`alert_sent=true`) | `working` | → `NULL` | → `false` | **🟢 «Бэк снова работает»** (отбой) |
| `error` | `working` (`alert_sent=false`) | `working` | → `NULL` | `false` | молча (🔴 не слали, напр. рестарт < 30 мин) |
| `pending` / `working` | `working` | `working` | `NULL` | `false` | молча |

- Telegram-отправка выполняется **только если** `settings.notifier_enabled` (`TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID` заданы). Иначе переход только фиксируется в БД (статус + `error_since`/`alert_sent` для UI/grace), лог `backend_alert_suppressed_no_telegram` (info) — не ошибка.
- `check_status`/`error_since`/`alert_sent` в БД обновляются **всегда**, независимо от `notifier_enabled` и результата отправки Telegram.
- Персистентность (`check_status` + `error_since` + `alert_sent`) переживает рестарт backend: grace-отсчёт и признак отправки корректны между рестартами (сломанный бэк не переоткрывает окно, нет дубль-🔴; recovery-🟢 шлётся только если 🔴 был отправлен).

### Формат сообщений Telegram (точно, нормативно — источник истины)

Метки — как у серверов/прокси/AI-ключей ([modules/notifier](../notifier/README.md), [domain/notifications](../proxies/README.md#формат-сообщений-telegram-точно)). Текст — plain (без parse_mode/Markdown). Имя бэка — в двойных кавычках, код — в квадратных скобках, домен — как есть. Билдеры (чистые функции, в `app/domain/notifications.py`, рядом с `build_proxy_error`/`build_proxy_recovery`):

```
build_backend_error(code: str, name: str, domain: str, reason: str) -> str
build_backend_recovery(code: str, name: str, domain: str) -> str
```

> **Порядок перечня в алертах ≠ порядок API reverse-lookup (намеренно, [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §1):** в **тексте алерта** — `position ASC, code ASC` (`code` UNIQUE ⇒ тотальный порядок, побайтовая воспроизводимость сообщения); в **эндпоинтах** `GET /api/servers|ai-keys/{id}/backends` — `position ASC, created_at DESC, id ASC` ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md), контракт **не меняется**). Переупорядочение под алерт — in-memory поверх результата репозитория.

> **Этот блок — ИСТОЧНИК ИСТИНЫ формата строки бэка и ПЕРЕИСПОЛЬЗУЕТСЯ в чужих алертах.** Перечень бэков, добавляемый в алерты об **ошибках сервера** (`warning`/`critical`/`offline`) и **ошибке ИИ-ключа** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §1, [modules/notifier](../notifier/README.md#блок-бэки-в-алертах-об-ошибках-нормативно-adr-046-1), [modules/ai-keys](../ai-keys/README.md#формат-сообщений-telegram-точно)), состоит из строк **этого же** `_backend_block` — побуквенно. Менять формат здесь = менять его во всех трёх местах.

Блок идентификации (внутренний хелпер `_backend_block(code, name, domain)`):

```
Бэк "<name>" [<code>] <domain>
```

**🔴 Бэк не работает** (переход `pending|working → error`), `build_backend_error(code, name, domain, reason)`:

```
🔴🔴🔴СРОЧНО🔴🔴🔴
Бэк "<name>" [<code>] <domain>
Бэк не работает: "<reason>"
```

`<reason>` = актуальный `error_message` («Таймаут подключения» / «Бэк недоступен» / «Ошибка бэка (HTTP N)» / «Ошибка бэка»).

**🟢 Бэк восстановлен** (переход `error → working`), `build_backend_recovery(code, name, domain)`:

```
🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢
Бэк "<name>" [<code>] <domain>
Бэк снова работает
```

Доставка — через тот же `TelegramClient.send_message` (best-effort, at-least-once, ограниченные ретраи; секреты не логируются) — см. [modules/notifier](../notifier/README.md#доставка-в-telegram).

### Backend — ориентиры реализации (структура — на усмотрение)

1. **Настройки** (`config.py`): `backend_check_interval_sec: int = 60`, `backend_check_timeout_sec: float = 10.0`, **`backend_check_deadline_sec: float = 30.0`** (overall-deadline, анти-зависание — [ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)), **`backend_alert_after_sec: int = 1800`** (grace-порог 30 мин перед 🔴). `notifier_enabled` переиспользуется. Путь `/health` и схема `https://` — константы, не конфиг. Монитор ведёт grace-состояние в `backends.error_since`/`alert_sent`.
2. **Проверка бэка** (`infra/`, напр. `backend_check.py`): чистый результат `BackendCheckResult{outcome, reason}` (`working`/`error`) — маппинг тестируется без сети (моки httpx). Нормализация домена и сборка URL — отдельные чистые функции.
3. **Билдеры сообщений** (`domain/notifications.py`): `build_backend_error(code, name, domain, reason)` / `build_backend_recovery(code, name, domain)` → строка. qa проверяет побайтовое совпадение формата.
4. **BackendMonitorService** (`services/`): цикл + **чистая функция перехода** `evaluate_transition(prev_status, result) -> (new_status, error_message, alert)` (образец прокси-монитора) для тестируемости матрицы без сети/БД. Исхода `unknown` нет.
5. **Роутер/сервис/репозиторий** (`api/`, `services/`, `repositories/`, `models/`, `schemas/`): CRUD по образцу серверов/прокси. Уникальность `code` — `UNIQUE`-констрейнт + маппинг `IntegrityError` → `409 backend_code_taken`.
6. **Запуск** — в `lifespan` (`main.py`): `asyncio.create_task` монитора при старте (всегда, рядом с прокси-монитором), отмена при shutdown.

## Frontend — ТЗ

Единая плоская сетка карточек, **БЕЗ drag-and-drop** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2а) и — с [ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) — **БЕЗ detail-модалки**: страница **card-first**, вся информация о бэке живёт **на карточке** (свёрнутый блок «Информация»), карандаш — в блоке действий карточки. Паттерн «клик=detail→карандаш=edit» ([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)) для `/backends` **отменён** (для `/servers`/`/proxies`/`/ai-keys` — в силе). Детальный UI-гайд — [08-design-system.md](../../08-design-system.md#страница-бэки). Реализация строк — русский словарь ([08-design-system.md](../../08-design-system.md#локализация-страницы-бэки)).

### Навигация

- Добавить вкладку **«Бэки»** (`/backends`) в `AppLayout` — [08-design-system.md](../../08-design-system.md#навигация-плоская-applayout). Защищённый маршрут внутри `AppLayout`, не-full-bleed ветка. («Бэки» — пункт **плоской навигации** со Спринта C, [ADR-033](../../adr/ADR-033-flat-nav-theme-toggle-numbers-table.md); ранее — категория «Мониторинг», [ADR-022](../../adr/ADR-022-teams-nav-categories.md).)

### Страница `BackendsPage`

- **Шапка страницы** — [заголовок + правая зона действий](../../08-design-system.md#заголовок-страницы-и-правая-зона-действий-нормативно) ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2б): слева `h1` «Бэки», справа кнопка **«Добавить»** (`Plus`, гейт `backends:create`).
- Адаптивная сетка карточек (`grid-cols-1 md:grid-cols-2 xl:grid-cols-3`, gap 24px), как «Серверы»/«Прокси». **Одна плоская сетка** (без секций, без кластеров-групп), **сортировка по `name`** (ci, `localeCompare` ru; tie-break `code`) — [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2а. Ячейки — только `BackendCard` (~~`AddBackendCard`~~ **упразднена**).
- `BackendCard`: имя (`name`), код (`code`, моношрифт/чип), домен (`domain`, моношрифт), статус-бейдж (**Работает** / **Не работает** / **Проверка…**), причина ошибки при `error`; блок действий — иконка **`Pencil`** (гейт **`backends:edit`**) → `AddBackendModal mode='edit'` **и единственная** кнопка **Удалить** (гейт `backends:delete`; дубль в error/недоступном состоянии **не рендерится** — [ADR-023](../../adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)); внизу — **свёрнутый блок «Информация»** (см. ниже).
- **⚠️ `BackendDetailModal` УПРАЗДНЕНА** ([ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §3 — **разворот [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2в и [ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md) для этой страницы; файл удаляется**). **Клик по телу карточки больше НЕ открывает ничего.** **С тела карточки СНИМАЮТСЯ интерактивные семантики** ([ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §3, a11y): нет `role="button"`, `tabIndex={0}`, `onClick`, `cursor-pointer`, focus-ring — тело обычный контейнер (кликабельная по ARIA карточка без действия = дефект доступности). **Интерактивны только:** триггер «Информация», карандаш (`backends:edit`), «Удалить» (`backends:delete`), глаз-reveal, ссылка Git. Паттерн «клик → detail-модалка → карандаш» остаётся в силе на `/servers`/`/proxies`/`/ai-keys`.
- **Блок «Информация» на карточке** ([ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §3) — **свёрнут по умолчанию** (кнопка-триггер, `aria-expanded`/`aria-controls`, `ChevronDown` c `rotate-180`; содержимое монтируется только при раскрытии). Состав и порядок: **Сервер** (`server_name`) → **ИИ-ключ** (`ai_key_name`) → **API KEY** (`••••••••` + глаз-reveal под `backends:edit`, только при `has_api_key`) → **ADMIN API KEY** (то же, при `has_admin_api_key`) → **Git** (ссылка) → **Примечания** ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)).
  - **Пустые поля не рендерятся** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §3 в силе; прочерк «—» упразднён). **Не осталось ни одной строки → блок «Информация» не рендерится вовсе.**
  - **Раскрытие НЕ делает ни одного запроса:** все шесть значений уже пришли в `BackendListItem` вместе с `GET /api/backends`.
  - **⚠️ Секреты не преднагружаются:** рендерится маска по флагу `has_*`; значение — **только по клику на глаз**, по одному ресурсу за раз (`backends:edit`, `no-store`, аудит). **ЗАПРЕЩЕНЫ** батч-reveal, `useQueries` по карточкам, авто-reveal при раскрытии, «раскрыть все»: раскрытие «Информации» у 20 бэков обязано давать **ноль** обращений к reveal-эндпоинтам ([05-security.md](../../05-security.md#секреты-в-card-first-ui-нормативно-adr-049)).
- **Поиск ([ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md)):** поле над сеткой (плейсхолдер «Поиск по бэкам…»), **клиентский** фильтр по загруженному списку — подстрока (регистронезависимо) в `code`/`name`/`domain`; без совпадений — «Ничего не найдено». По образцу поиска `/sms`.
- **~~Группировка по `name`~~ — УПРАЗДНЕНА** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2а): кластеры-контейнеры с заголовком «`name` · N» ([ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md)) **больше не рисуются**. Дубли имён просто стоят рядом благодаря сортировке по `name`.
- Кнопка **«Добавить»** (шапка) → `AddBackendModal` (Radix Dialog) в режиме **add**: основные поля **Код**/**Название**/**Домен** + **сворачиваемая секция «Информация»** (опц., [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)): **Сервер** (`Select`), **ИИ-ключ** (`Select`), **API KEY**, **ADMIN API KEY** (маска+глаз), **Git**, **Примечания** (`Textarea`, последнее поле). Кнопки **Отмена** / **Добавить**. Ошибка `409 backend_code_taken` — пофилдово под «Код»; несуществующий `server_id`/`ai_key_id` → `422` инлайн.
- **Режим edit `AddBackendModal`:** префил `code`/`name`/`domain`/`server_id`/`ai_key_id`/`git`/`note`; поля секретов пустые («Оставьте пустым, чтобы не менять»; очистка секрета через UI не выполняется — [TD-035](../../100-known-tech-debt.md)). Кнопка действия — **Сохранить**. Отправляются только изменённые поля. После смены `domain` карточка возвращается в **Проверка…** и polling статуса возобновляется; смена связей/секретов/`git`/`note` статус не меняет.
- **Перестановки НЕТ** ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2а): `DndContext`/`SortableContext`/`SortableItem`/`PointerSensor`/`reorderMutation` на этой странице **не используются**; `PATCH /api/backends/order` фронт не вызывает ([TD-054](../../100-known-tech-debt.md)).
- Данные и polling — через feature-слой `features/backends` (`api.ts`, `hooks.ts`) на TanStack Query, по образцу `features/proxies`/`features/servers`. Типы — в `types/api.ts`. Статус `pending` → «Проверка…», лёгкий polling `GET /api/backends/{id}/status` до выхода из `pending`.

### Состояния UI

Loading (skeleton), **empty — текст «Бэков пока нет»** (карточек-плейсхолдеров нет; добавление — кнопкой в шапке, [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2б), «Ничего не найдено» (поиск без совпадений), pending («Проверка…», спиннер), error (акцентная граница + причина + «Удалить»), toast «Бэк добавлен» / «Бэк обновлён» / «Бэк удалён», `409` «Код занят» пофилдово, обработка `422`/сетевых ошибок — по образцу прокси/серверов ([08-design-system.md](../../08-design-system.md#состояния-ui-обязательны)).

## DoD

- [x] Endpoints и коды ошибок соответствуют [04-api.md](../../04-api.md#backends); `code` уникален (дубль → `409 backend_code_taken`); прецеденция `400`/`422` → `409`.
- [x] Нормализация домена к **канону `https://<host>/`** ([ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md); с/без схемы, path → authority, host lower-case) и валидация формата host (`422` на невалидном); в БД хранится канон `https://<host>/`.
- [x] Проверка идёт `GET {domain}health` (= `https://<host>/health`; **дописывание `health`**, не склейка; `follow_redirects=False`, `verify=True`); строго `2xx` → `working`; таймаут/сеть/не-2xx (после ретраев) → `error` с рус. причиной, содержащей код статуса при не-2xx.
- [x] Формат обоих сообщений Telegram побайтово соответствует спецификации (`build_backend_error`/`build_backend_recovery`, блок `Бэк "<name>" [<code>] <domain>`).
- [x] `PATCH /api/backends/{id}`: смена `code` проверяет уникальность (`409`); re-check только при смене `domain`; смена `code`/`name` статус не трогает.

**Правки [ADR-023](../../adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)/[ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md) (spec-ready, требуют реализации):**
- [ ] Grace-порог: `check_status→error` немедленно, **🔴 только после непрерывной недоступности ≥ `BACKEND_ALERT_AFTER_SEC` (30 мин)**; recovery `error→working` шлёт 🟢 **только если 🔴 был отправлен** (`alert_sent`). `evaluate_transition` — time-aware (пять аргументов: `+error_since, alert_sent, now`).
- [ ] Проверка не зависает: явный `httpx.Timeout` по всем фазам + overall-deadline `BACKEND_CHECK_DEADLINE_SEC` (`asyncio.wait_for`) → таймаут-исход.
- [ ] Монитор пишет `check_status`/`error_since`/`alert_sent` независимо от бота; grace-состояние переживает рестарт.
- [ ] Alembic-миграция `0013_backends_alert_grace` (`error_since`/`alert_sent`) с рабочим `downgrade()`.
- [ ] Единственная кнопка «Удалить» на карточке (дубль в error-состоянии убран).
- [ ] ~~**([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md)/[ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)):** клик по карточке → read-only `BackendDetailModal` → карандаш~~ — **ОТМЕНЕНО [ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §3** (см. пункт ниже).
- [ ] **UI-пакет ([ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2):** плоская сетка + сортировка по `name` (кластеры «name · N» и DnD **убраны**, разворот [ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md)); кнопка «Добавить» в правой зоне заголовка (`AddBackendCard` удалена); empty → «Бэков пока нет»; пустые поля не рендерятся (§3). **Действует из [ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md):** клиентский поиск по `code`/`name`/`domain` («Поиск по бэкам…», «Ничего не найдено»). *(Норму §2в «detail-модалка = идентификаторы + свёрнутая «Информация»» для бэка **отменил [ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md)**.)*
- [ ] **Card-first ([ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §3, spec-ready) — frontend, контракт не меняется:** **`BackendDetailModal` удалена**; свёрнутый блок **«Информация»** (Сервер / ИИ-ключ / API KEY / ADMIN API KEY / Git / Примечания) — **на `BackendCard`**; раскрытие **не делает запросов** (все значения уже в `BackendListItem`); **карандаш `Pencil` (гейт `backends:edit`) — в блоке действий карточки** → `AddBackendModal mode='edit'`; клик по телу карточки не открывает ничего; блок без единой строки не рендерится.
- [ ] **Секреты при card-first НЕ преднагружаются** ([ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §4): маска по `has_*`, значение — только по клику на глаз (`backends:edit`, `no-store`, аудит); батч-reveal / `useQueries` по карточкам / авто-reveal при раскрытии / «раскрыть все» — **запрещены**; раскрытие 20 карточек = **0** обращений к reveal-эндпоинтам.
- [ ] **Orphaned-тесты** на удалённую `BackendDetailModal` (`frontend/src/components/__tests__/DetailModals.test.tsx`) переписаны/удалены силами **`qa`** (исполнитель кода тест-файлы не трогает) — до финального build-гейта ([ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md) §6).
- [ ] **([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md), spec-ready):** reveal-эндпоинты `api-key`/`admin-api-key` под `backends:edit`; reverse-lookup `GET /api/servers/{id}/backends` и `GET /api/ai-keys/{id}/backends`; `server_id`/`ai_key_id` FK (`SET NULL`), секреты Fernet, `git`/`note`; миграция `0019`; канон домена `https://<host>/` + миграция `0020` ([ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md)).
- [x] `PATCH /api/backends/order`: перестановка единого списка; полная перестановка валидируется (иначе `422`); несуществующий `id` → `404 backend_not_found`.
- [x] Frontend: вкладка «Бэки» в `AppLayout`, `BackendsPage` (единый список), `BackendCard`/`AddBackendModal` (add+edit), `409` пофилдово, все состояния UI, русские строки из словаря. *(Историческая запись Спринта 2: тогда были `AddBackendCard` и drag-and-drop с идиомой «клик=edit / зажатие=drag». **Обе сняты:** короткий клик=detail-view с [ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md); `AddBackendCard` и DnD **упразднены** [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2 — см. пункт выше.)*
- [x] Coverage ≥90 % для функций нормализации/проверки/перехода/билдеров сообщений ([06-testing-strategy.md](../../06-testing-strategy.md)).
- [x] Lint/type-check/format проходят (backend и frontend).

## Changelog

- 2026-07-13: **Card-first `/backends`: блок «Информация» на карточке, detail-модалка упразднена** (architect, [ADR-049](../../adr/ADR-049-servers-backends-card-first-detail.md); **разворот [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2в и [ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md) для этой страницы**; **контракт/БД не затронуты — правка чисто frontend**): свёрнутый блок **«Информация»** (Сервер / ИИ-ключ / API KEY / ADMIN API KEY / Git / Примечания) переехал из `BackendDetailModal` **на `BackendCard`** — раскрытие **не делает ни одного запроса** (все значения уже в `BackendListItem`); **`BackendDetailModal` удалена**, карандаш `Pencil` (гейт `backends:edit`) переехал **в блок действий карточки**; клик по телу карточки больше ничего не открывает (DnD здесь уже убран — [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md) §2а, жестовых конфликтов нет). **Гарантии reveal секретов не ослаблены**: маска по `has_*`, значение только по клику на глаз, массовая преднагрузка секретов **запрещена**. Долг [TD-057](../../100-known-tech-debt.md) (нет общего примитива `ui/Collapsible`; UX-неоднородность с `/servers`/`/proxies`/`/ai-keys`).

- 2026-07-11: **UI-пакет страницы «Бэки» + перечень бэков в чужих алертах** (architect, [ADR-046](../../adr/ADR-046-ui-infra-fix-pack.md); **контракт API и БД не затронуты**). (1) **Группировка-кластеры «Имя · N» и DnD УБРАНЫ** (разворот [ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md)): одна плоская сетка, стабильная сортировка по `name` (ci, `localeCompare` ru; tie-break `code`). **Колонка `position` и `PATCH /api/backends/order` СОХРАНЕНЫ** в БД/API, но UI их не вызывает ([TD-054](../../100-known-tech-debt.md)) — `position` по-прежнему задаёт порядок ответа `GET /api/backends` и порядок перечня бэков в алертах. (2) Кнопка **«Добавить» → правая зона заголовка**; `AddBackendCard` **удалена** (в т.ч. из пустого состояния — норма ADR-039 «empty = только карточка без текста» **отменена**; empty → «Бэков пока нет»). (3) **Detail-модалка:** сразу видны только **Код/Название/Домен**; связи, секреты, Git, Примечания — в свёрнутом блоке **«Информация»**; **пустые поля не рендерятся** (прочерк «—» упразднён). (4) Формат `_backend_block` (`Бэк "<name>" [<code>] <domain>`) объявлен **источником истины** и **переиспользуется** в алертах об ошибках сервера/ИИ-ключа ([modules/notifier](../notifier/README.md#блок-бэки-в-алертах-об-ошибках-нормативно-adr-046-1), [modules/ai-keys](../ai-keys/README.md#формат-сообщений-telegram-точно)).

- 2026-07-09: **связи + секреты + доп. поля + канон домена + поиск/группировка/пустое состояние** ([ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)/[ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md)/[ADR-039](../../adr/ADR-039-ui-server-inline-edit-backends-search-empty-sms-label.md), spec-ready). `backends += server_id`/`ai_key_id` (FK `SET NULL`), `api_key`/`admin_api_key` (**секреты**, Fernet, reveal под `backends:edit` — **разворот «секрета нет» из [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md)**), `git`/`note` (не секреты), миграция `0019`. Домен → **канон `https://<host>/`** ([ADR-042](../../adr/ADR-042-backend-domain-canonical-https.md), миграция `0020`); health-URL = `{domain}health` (не склейка). `BackendListItem += server_id/server_name/ai_key_id/ai_key_name/has_api_key/has_admin_api_key/git/note`; reveal `GET /api/backends/{id}/api-key`·`/admin-api-key`; reverse-lookup `GET /api/servers|ai-keys/{id}/backends`. UI: секция «Информация» в форме, detail с reveal, поиск по `code`/`name`/`domain`, группировка по `name`, пустое состояние без текста. Отменяет прежнюю запись «секрета нет → reveal не применяется».
- 2026-07-09: **detail-view** ([ADR-035](../../adr/ADR-035-detail-view-secret-reveal.md), spec-ready): клик по карточке → read-only `BackendDetailModal` (Код/Название/Домен), карандаш → edit. ~~Секрета нет → reveal не применяется~~ (снято [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md), см. выше). Контракт `BackendListItem` не меняется (расширен [ADR-040](../../adr/ADR-040-backend-relations-secrets-reverse-lookup.md)).
- 2026-07-08: **спецификация правок [ADR-023](../../adr/ADR-023-ui-nav-dropdown-proxy-ip-single-delete.md)/[ADR-024](../../adr/ADR-024-monitor-hard-deadline-backend-alert-grace.md)** (architect, требуют реализации): overall-deadline проверки `BACKEND_CHECK_DEADLINE_SEC`=30 (анти-зависание) + явный `httpx.Timeout` по всем фазам; **grace-порог 30 мин** непрерывной недоступности перед 🔴 (`BACKEND_ALERT_AFTER_SEC`=1800, поля `error_since`/`alert_sent`, миграция `0013_backends_alert_grace`, time-aware `evaluate_transition`); единственная кнопка «Удалить» на карточке. Устраняет ложные алерты при перезагрузке бэка (1–2 мин). Модель алерта у бэков теперь отличается от прокси (немедленно) — grace-порог.
- 2026-07-07: модуль реализован (Спринт 2) — backend + frontend + qa завершены, reviewer approve/production_ready. Статус `implemented`, DoD выполнен. Косметические minor architect-reviewer (полный список причин `error_message` в 03-data-model/04-api, переход `pending → [*]: DELETE` в state-диаграмме) синхронизированы (architect).
- 2026-07-07: backend-реализация (backend). Модель `Backend`/миграция `0007_create_backends`; схемы/репозиторий/сервис (CRUD + 409 `backend_code_taken` + нормализация домена → 422 + re-check при смене `domain`); `infra/backend_check.py` (нормализация/валидация домена, `GET https://{domain}/health`, строго 2xx); `BackendMonitorService` (стартует всегда, Telegram гейтится `notifier_enabled`); билдеры `build_backend_error`/`build_backend_recovery`. Frontend — отдельной задачей.
- 2026-07-07: спецификация создана (architect). Решение об отдельном in-backend-мониторе healthcheck бэков (по образцу прокси [ADR-019](../../adr/ADR-019-proxies-availability-monitor.md)), без секрета/Fernet, с уникальным `code`, фиксированным `GET https://{domain}/health` и строгим `2xx` — [ADR-020](../../adr/ADR-020-backends-healthcheck-monitor.md). Отложенные пункты — [TD-029](../../100-known-tech-debt.md).

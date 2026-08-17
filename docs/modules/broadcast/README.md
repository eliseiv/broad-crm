# Модуль `broadcast` — Рассылка через Telegram ИИ-бота

Статус: `implemented` (сверка состава кода 2026-08-17; прогон qa architect'ом не выполнялся) · Исполнители: backend, frontend · Соседний репозиторий: `ba-knowledge-base` (контракт регистрации линка — обязан выкатить **вместе**)

Решение — [ADR-076](../../adr/ADR-076-knowledge-bot-broadcast-and-admin-level.md). Контракт — [04-api.md](../../04-api.md#broadcast). Модель — [03-data-model.md](../../03-data-model.md#таблица-knowledge_bot_links-adr-076). UI — [08-design-system.md](../../08-design-system.md#страница-рассылка).

## Scope

- Страница **«Рассылка»** (`/broadcast`): текст, чекбоксы ролей, «Всем», «Отправить».
- Fan-out в Telegram **токеном ИИ-бота базы знаний** (`KNOWLEDGE_BOT_TOKEN` = `BOT_TOKEN` соседнего проекта).
- Таблица `knowledge_bot_links`: факт «сотрудник написал ИИ-боту» + chat_id доставки.
- Поле `UserListItem.bot_started` и бейдж на `/users`.
- Внешний `POST /api/external/knowledge-bot/link` (X-API-Key) — регистрация запуска ботом.
- Доработка резолва `GET /api/external/documents/user-access/{id}` (линк ИИ-бота + bootstrap по нику).

## Out of scope

- HTTP-вызов процесса ba-knowledge-base (его `/sync` без auth, порт localhost-only).
- Доставка через mail/sms/notifier-ботов.
- История рассылок, retry-монитор, брокер, HTML/Markdown в тексте.
- Ключ `users` в матрице прав.

## Backend — ТЗ

### Данные

Миграция **`0037_knowledge_bot_links`**: таблица `knowledge_bot_links` (DDL — [03-data-model.md](../../03-data-model.md#таблица-knowledge_bot_links-adr-076)); backfill extra-действий ролей и `broadcast` для сида `admin` / ролей с полным прежним каталогом — [ADR-076](../../adr/ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4.

Каталог `CATALOG` += `"broadcast": ("view", "send")` **последним ключом**. `full_catalog_permissions()` подхватывает сам.

`require_admin` → `is_admin_level` ([ADR-076](../../adr/ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4). Функция — в `app/domain/permissions.py` (чистая, рядом с `permissions_subset`).

### Env

`KNOWLEDGE_BOT_TOKEN` (пусто = выключено). Клиент — **новый** модуль класса `SmsBotClient` / `MailBotClient` (напр. `app/infra/knowledge_bot_telegram.py`): `send_message(chat_id, text)` **без** `parse_mode`; `TelegramForbiddenError` → `dead_at`. **⛔ Не** `app/infra/telegram.py` (`TelegramClient`: один `chat_id`, `send_message(text)→bool`, 403 глотается).

### Эндпоинты

Внутренние (JWT) — [04-api.md#broadcast](../../04-api.md#broadcast):

- `GET /api/broadcasts/audience` — `require("broadcast","view")`.
- `POST /api/broadcasts` — `require("broadcast","send")`. Пустой токен → `503 knowledge_bot_not_configured`.

Внешние (X-API-Key, тот же `require_documents_api_key`):

- `POST /api/external/knowledge-bot/link` — upsert линка + `ExternalUserAccessResponse`.
- `GET /api/external/documents/user-access/{telegram_user_id}?username=` — единый резолв (шаги ADR-076 §2). Документируется в [04-api.md](../../04-api.md#get-apiexternaldocumentsuser-accesstelegram_user_id) (дрейф закрыть).

Резолв и fan-out **обязаны** нести `WHERE NOT users.is_system` (инвариант якоря, [ADR-051](../../adr/ADR-051-superadmin-db-anchor-personal-state.md)).

`GET /api/users`: в каждый `UserListItem` добавить `bot_started` (EXISTS активный линк).

### Адресаты `POST /api/broadcasts` (нормативно)

```
кандидаты = users WHERE is_active AND NOT is_system
            AND (all OR role_id ∈ role_ids)
линки     = knowledge_bot_links WHERE dead_at IS NULL AND user_id ∈ кандидаты
адресаты  = UNIQUE(telegram_user_id) по линкам
skipped_not_started = |кандидаты без активного линка|
```

Последовательная отправка. `403` Telegram → `dead_at=now()`. Частичный успех = `200`.

### Слои

| Слой | Назначение |
|------|-----------|
| `api/broadcasts.py` | JWT-роутер `/api/broadcasts` |
| `api/knowledge_bot_external.py` | `POST /api/external/knowledge-bot/link` |
| `services/broadcast_service.py` | audience + fan-out |
| `services/knowledge_bot_link_service.py` | резолв + upsert (общий с user-access) |
| `repositories/knowledge_bot_link_repository.py` | upsert / exists_for_user / recipients_for_roles |
| `infra/knowledge_bot_telegram.py` | новый клиент класса `SmsBotClient`/`MailBotClient`; `send_message(chat_id, text)` без `parse_mode`; `TelegramForbiddenError` / `TelegramApiError`. **⛔ не** `infra/telegram.py` |

## Frontend — ТЗ

- Маршрут `/broadcast`, пункт навигации «Рассылка» (#13), `broadcast:view`.
- `DefaultRoute` += `broadcast` в конце.
- Страница — [08-design-system.md](../../08-design-system.md#страница-рассылка).
- `/users`: бейдж «Бот» / «Бот не запущен» из `bot_started`.
- Матрица ролей — столбцы из каталога (закрытие TD-068). Словарь: `PAGE_LABEL.broadcast = «Рассылка»`; `ACTION_LABEL` += `share/send/sync/tags/transfer`.
- UI-гейт `/users` и `AdminRoute` — `is_admin_level` с клиента (`frontend/src/features/auth/adminLevel.ts`): `is_superadmin || role === "admin" ||` полное покрытие серверного каталога. Каталог грузится только при `roles:view` у не-супер-админа / не-сида `admin` (`needsPermissionsCatalog`). Пока каталог грузится — `catalogPending`: пункт «Пользователи» и `AdminRoute` показывают Spinner, не заглушку. Не дублировать предикат «на глаз».

## Контракт ba-knowledge-base (исполняет соседний репо)

Middleware доступа: **вместо** `GET …/user-access/{id}` (не «сразу после») вызвать `POST {CRM_BASE_URL}/api/external/knowledge-bot/link` с `X-API-Key: CRM_API_KEY`, тело `{ telegram_user_id, username }` — на cache-miss **каждого** сообщения, не только `/start`. `404` с `error.code=user_not_linked` = отказ; прочий 404 = сбой CRM; `200` = `crm_access`. RAG/sync/handlers не менять. Спека соседа — ba-knowledge-base ADR-013.

## DoD

- [x] Миграция `0037`, каталог += `broadcast`, `is_admin_level`, backfill (сверка состава 2026-08-17).
- [x] `GET/POST /api/broadcasts*`, `POST /api/external/knowledge-bot/link`, user-access с `username` и шагом 1 (линк ИИ-бота).
- [x] `UserListItem.bot_started`; страница `/broadcast`; бейдж на `/users`; матрица из каталога.
- [ ] Тесты [06-testing-strategy.md](../../06-testing-strategy.md#broadcast--adr-076) зелёные (прогон qa не зафиксирован).

## Changelog

- 2026-08-17: контракт соседа уточнён (ba-knowledge-base ADR-013): POST **вместо** GET, не только `/start`, отказ только при `user_not_linked`.
- 2026-08-17: сверка состава кода — статус `implemented`; уточнены `catalogPending` и empty-state 503 только после POST.
- 2026-08-17: спецификация создана (architect, [ADR-076](../../adr/ADR-076-knowledge-bot-broadcast-and-admin-level.md)).

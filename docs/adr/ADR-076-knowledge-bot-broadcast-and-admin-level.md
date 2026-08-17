# ADR-076 — Рассылка через Telegram ИИ-бота, статус «бот запущен», admin-уровень по полному каталогу

- **Статус:** implemented (сверка состава кода 2026-08-17; прогон qa architect'ом не выполнялся)
- **Дата:** 2026-08-17
- **Контекст-модули:** [broadcast](../modules/broadcast/README.md), [auth](../modules/auth/README.md), [documents](../modules/documents/README.md)
- **Связано:** [ADR-021](ADR-021-rbac-users-roles.md), [ADR-022](ADR-022-teams-nav-categories.md), [ADR-032](ADR-032-sms-visibility-admin-full-catalog.md), [ADR-033](ADR-033-flat-nav-theme-toggle-numbers-table.md), [ADR-051](ADR-051-superadmin-db-anchor-personal-state.md), [ADR-059](ADR-059-documents-module.md), [ADR-060](ADR-060-documents-external-readonly-api-key.md), [ADR-061](ADR-061-documents-sidebar-two-panel-nav.md), [ADR-063](ADR-063-documents-editor-cache-lifecycle-focus.md), [ADR-065](ADR-065-users-flat-list-team-chips.md), [ADR-077](ADR-077-broadcast-page-visual-redesign.md) (визуальная композиция `/broadcast`)
- **Соседний репозиторий (проверено чтением):** `/Users/elisejverbickij/Desktop/BA/ba-knowledge-base` — Telegram ИИ-бот (aiogram long-polling, FastAPI `/health` + `POST /sync`). Доступ к боту — `GET /api/external/documents/user-access/{telegram_id}` ([ADR-012 бота](file:///Users/elisejverbickij/Desktop/BA/ba-knowledge-base/docs/adr/ADR-012-crm-access-and-role-filter.md)). HTTP бота слушает только `127.0.0.1:8000`, `POST /sync` **без auth** (их TD-002).

## Контекст

Владелец просит три связанные вещи:

1. Страница CRM **«Рассылка»**: текст + «Отправить» + чекбоксы ролей + «Всем». После отправки сообщение уходит сотрудникам в Telegram **от ИИ-бота базы знаний** (тот же бот, которому пишут вопросы по регламентам).
2. На `/users` рядом с «Активен / Ожидает входа» — зелёный **«Бот»** или красный **«Бот не запущен»**.
3. Роль с кириллическим именем **«Админ»** имеет все видимые чекбоксы матрицы, но не видит «Пользователи» и не может сменить видимость документов.

### Что проверено в репозиториях (снимок на момент спецификации, до реализации)

> Таблица ниже — факты репозитория **на дату принятия ADR (2026-08-17), до backend/frontend**. Это обоснование решения, не текущее состояние. После реализации несколько строк ложны (своя таблица `knowledge_bot_links`; `user-access` в [04-api.md](../04-api.md); `require_admin` → `is_admin_level`; матрица `/roles` из каталога). Актуальная норма — [Decision](#decision).

| Факт | Где |
|------|-----|
| Числовой `telegram_user_id` (= `chat_id` приватного чата) в CRM хранится **только** в `sms_telegram_links` и `mail_telegram_links` | `backend/app/models/sms_delivery.py`, `backend/app/models/mail_telegram.py`; [03-data-model.md](../03-data-model.md) |
| `users.telegram` — это **ник** (`@username`), не числовой id | [ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md), `backend/app/models/user.py` |
| Резолв доступа ИИ-бота: активный sms-линк → активный mail-линк с `user_id` → активный несистемный пользователь. **Своей таблицы линков у ИИ-бота нет** | `backend/app/api/documents_external.py:79-125` (прочитано) |
| Эндпоинт `GET /api/external/documents/user-access/{telegram_user_id}` **есть в коде**, в `docs/04-api.md` **отсутствует** (дрейф) | код vs docs |
| ИИ-бот, почтовый бот, SMS-бот и notifier — **четыре разных** `BOT_TOKEN`. Bot API шлёт только тем, кто написал **этому** боту; линк mail/sms **не** даёт права доставки от ИИ-бота | Telegram Bot API; env CRM vs `BOT_TOKEN` бота (`ba-knowledge-base/.env.example`) |
| HTTP ИИ-бота наружу не публикуется и без auth; CRM уже вызывает Telegram Bot API (notifier `TelegramClient`; SMS `SmsBotClient`; почта `MailBotClient`) | `ba-knowledge-base/docs/07-deployment.md` §1.1; `backend/app/infra/telegram.py`, `sms_telegram.py`, `mail_telegram.py` |
| «Пользователи» гейтится `require_admin` = `is_superadmin \|\| role == "admin"` (**точное английское имя**). Роль «Админ» этот гейт не проходит | `backend/app/api/deps.py:202-206`; [ADR-021](ADR-021-rbac-users-roles.md) §5 |
| Матрица `/roles` рендерит только `view/create/edit/delete` (`ACTION_ORDER`) → `documents:share` через UI выдать нельзя | `frontend/src/features/users/labels.ts:12`; [TD-068](../100-known-tech-debt.md) |
| Admin-уровень «видит все документы/SMS/почты» уже считается как `is_superadmin OR permissions_subset(full_catalog, permissions)` | [ADR-032](ADR-032-sms-visibility-admin-full-catalog.md), `principal_sees_all_documents` |

## Decision

### 1. «Бот запущен» = активный линк ИИ-бота, не mail/sms

Новая таблица **`knowledge_bot_links`** (образец `sms_telegram_links`, прочитано `backend/app/repositories/sms_telegram_link_repository.py`):

| Поле | Тип | Ограничения |
|------|-----|-------------|
| `telegram_user_id` | `bigint` | PK, не serial — внешний chat_id |
| `user_id` | `uuid` | `NOT NULL`, FK `users(id) ON DELETE CASCADE` |
| `username` | `text` | NULL, нормализованный ник (без `@`, lower-case) |
| `started_at` | `timestamptz` | `NOT NULL DEFAULT now()` |
| `dead_at` | `timestamptz` | NULL = активен |

- 1:N `user_id` (без UNIQUE): один CRM-юзер может написать боту с нескольких аккаунтов.
- Активна ⇔ `dead_at IS NULL`.
- **Не** использовать mail/sms-линки как факт запуска ИИ-бота и как адрес доставки.
- Системный якорь (`users.is_system`) в линки не попадает: резолв идёт через `UserRepository` с `WHERE NOT is_system`.
- Миграция **`0037_knowledge_bot_links`** (≤32 симв.), `down_revision` = фактическая голова (`0036_ai_keys_credit_probe` на момент спецификации; backend сверяет `alembic_version`). Чистый expand: `CREATE TABLE` + индексы. `downgrade()` = `DROP TABLE`.

`UserListItem += bot_started: bool` — `true` ⇔ есть хотя бы одна активная строка `knowledge_bot_links` на этого пользователя. Наружу числовой id и `started_at` не отдаются.

UI `/users` (рядом с тристатус-бейджем, тот же примитив `Badge`): `bot_started=true` → **«Бот»** (`tone="green"`); `false` → **«Бот не запущен»** (`tone="red"`). Нормативные строки — [08-design-system.md](../08-design-system.md#страница-пользователи).

### 2. Регистрация запуска — бот пишет в CRM (уже существующее направление)

ИИ-бот уже ходит в CRM с `X-API-Key` (`CRM_API_KEY` = `DOCUMENTS_API_KEY`). HTTP бота наружу не открываем и auth на его `/sync` не чиним этим ADR.

**Новый write-роутер** `/api/external/knowledge-bot/*` (тот же `X-API-Key`, CSRF-exempt, `Cache-Control: no-store`). Роутер документов `/api/external/documents/*` остаётся **только GET** ([ADR-060](ADR-060-documents-external-readonly-api-key.md) §3 не ломаем).

`POST /api/external/knowledge-bot/link` — тело `{ "telegram_user_id": int, "username": string|null }`:

1. Резолв пользователя (единый, ниже).
2. Не найден / неактивен / системный → `404 user_not_linked` (боту это «доступа нет»).
3. Иначе upsert линка (`ON CONFLICT (telegram_user_id) DO UPDATE`: `user_id`, `username`, `dead_at=NULL`, `started_at` не затирается если уже был).
4. Ответ 200 — тот же `ExternalUserAccessResponse`, что у `user-access`.

**Единый резолв** `telegram_user_id` (+ опц. `username`) → CRM-пользователь (и для `POST …/link`, и для существующего `GET …/user-access/{id}`):

1. Активный `knowledge_bot_links` по `telegram_user_id`.
2. Иначе активный `sms_telegram_links`.
3. Иначе активный `mail_telegram_links` с `user_id IS NOT NULL`.
4. Иначе bootstrap: `username` задан → `UserRepository.get_by_telegram(normalize_telegram(username))` (активный, не системный).
5. Иначе — нет.

`GET /api/external/documents/user-access/{telegram_user_id}` остаётся; добавляется опциональный query `username` для шага 4. Порядок шагов — тот же. Эндпоинт **вписывается в [04-api.md](../04-api.md#external-documents-read-only-rag)** (закрытие дрейфа).

**Контракт для ba-knowledge-base (не код этого репо):**
> **Амендмент (ADR-013 бота, 2026-08-17):** утверждение «`404` = отказ (как сейчас на 404 user-access)» уточнено — отказ только при `error.code=user_not_linked`; прочий 404 = сбой CRM (не «всем отказ»). Вызов — на cache-miss **каждого** сообщения, не только `/start`. POST **заменяет** GET как путь доступа (не «сразу после GET»). Спека соседа — ba-knowledge-base [ADR-013](file:///Users/elisejverbickij/Desktop/BA/ba-knowledge-base/docs/adr/ADR-013-crm-knowledge-bot-link.md).

middleware после получения апдейта вызывает `POST /api/external/knowledge-bot/link` с `from_user.id` и `from_user.username`; `404` с `error.code=user_not_linked` = отказ; `200` = пустить в хендлеры и положить роль в `crm_access`. Без этого вызова статус «Бот» в CRM не появится. Менять RAG/sync не требуется.

### 3. Рассылка — CRM шлёт через Bot API токеном ИИ-бота

Страница и API живут в CRM. Сообщения в Telegram приходят **от того же бота**, которому сотрудники задают вопросы (тот же `BOT_TOKEN`). Процесс ba-knowledge-base fan-out не делает: его HTTP нельзя безопасно вызвать (localhost + без auth).

Новый env **`KNOWLEDGE_BOT_TOKEN`** — секрет, класс `SMS_TELEGRAM_BOT_TOKEN`. Значение = `BOT_TOKEN` соседнего проекта. Пусто → `knowledge_bot_enabled=false`: `POST /api/broadcasts` → `503 knowledge_bot_not_configured`; `GET /audience` токен не проверяет. Empty-state страницы **«ИИ-бот не настроен»** — после 503 на POST (класс empty-state «Сервис почт не настроен», но не тот же триггер: GET форму не прячет). Токен в ответы/логи/SPA/URL не попадает.

**Клиент Bot API (нормативно).** Новый клиент класса `SmsBotClient` / `MailBotClient` (отдельный модуль, напр. `app/infra/knowledge_bot_telegram.py`), токен `KNOWLEDGE_BOT_TOKEN`. Контракт: `send_message(chat_id: int, text: str)` **без** `parse_mode` (параметр не передавать в Bot API). Успех → payload. `403` / forbidden-маркеры → типизированный `TelegramForbiddenError` (как в `backend/app/infra/sms_telegram.py:35-36`, прочитано) → сервис ставит `dead_at=now()`. Прочая ошибка Bot API → `TelegramApiError` (линк живой). **⛔ Запрещено** переиспользовать notifier `TelegramClient` (`backend/app/infra/telegram.py`, прочитано): он фиксирует **один** `chat_id` в конструкторе, `send_message(text) -> bool` и **глотает** 403 (возвращает `False`, исключения нет) — fan-out по многим чатам и `dead_at` на нём невозможны.

Каталог прав += страница **`broadcast`**: действия `view`, `send`. Порядок ключей (после `documents`): `…, documents, broadcast`. Сид-роль `admin` и полный каталог супер-админа получают оба действия (миграция `0037` обновляет jsonb сид-роли `admin`, как `0010` добавляла `roles`/`teams`).

Эндпоинты (JWT):

| Метод | Путь | Гейт | Назначение |
|-------|------|------|------------|
| `GET` | `/api/broadcasts/audience` | `broadcast:view` | Роли для чекбоксов + счётчики «запустили бота» / «не запустили» |
| `POST` | `/api/broadcasts` | `broadcast:send` | Отправить |

`GET /api/broadcasts/audience` — `{ "roles": [{ "id", "name", "started_count", "not_started_count" }], "all_started_count", "all_not_started_count" }`. Считаются только активные несистемные пользователи. Не admin-gated `/api/roles` (иначе носитель `broadcast:view` без `roles:view` получил бы пустые чекбоксы — класс TD-050).

`POST /api/broadcasts` — тело `{ "text": string, "all": bool, "role_ids": uuid[] }`:

- `text` — 1…4096 символов после `strip()` (лимит Telegram `sendMessage`); пусто / длиннее → `422`. HTML/Markdown **не** включаются (`parse_mode` не передаётся) — анти-инъекция разметки.
- Ровно одно: `all=true` **или** непустой `role_ids`. `all=true` + непустой `role_ids` → `422`. `all=false` и пустой `role_ids` → `422`. Несуществующий `role_id` → `422`.
- Адресаты = активные несистемные пользователи выбранных ролей (или все при `all`) **∩** активные `knowledge_bot_links`. Дедуп по `telegram_user_id` (один чат — одно сообщение, даже если 1:N). Пользователи роли без линка в адресаты **не входят** — они в `skipped_not_started`.
- Fan-out последовательный (NFR-1, десятки сотрудников). Успех Bot API → `sent++`. `TelegramForbiddenError` → `dead_at=now()` на линке + `failed++`. Прочая `TelegramApiError` → `failed++`, линк живой (можно повторить). Частичный успех — **`200`**, не 5xx.
- Ответ: `{ "sent": int, "failed": int, "skipped_not_started": int }`. Состав получателей и тексты ошибок Telegram наружу не раскрываются (анти-энумерация чатов).
- История рассылок / retry-монитор / брокер — **не вводятся** (повтор = новый `POST`). Аудит админских действий — [TD-001](../100-known-tech-debt.md).

Пустой пересечение (все выбранные без бота) → `200` с `sent=0`, `skipped_not_started=N` — не ошибка.

### 4. RBAC-баг: admin-уровень, не кириллическое имя

**Причина 1.** Гейт «Пользователи» смотрит на `role.name == "admin"`. Роль «Админ» с полным набором чекбоксов — другая строка.

**Причина 2.** [TD-068](../100-known-tech-debt.md): матрица не показывает `share`/`sync`/`tags`/`transfer` → даже «все галочки» не дают `documents:share`.

**Решение.**

`is_admin_level(principal)` (единое место — `app/domain/permissions.py` + `require_admin` в `deps.py`):

```
is_superadmin OR role == "admin" OR permissions_subset(full_catalog_permissions(), principal.permissions)
```

Тот же предикат, что `sees_all_documents` / `sees_all_sms_teams` / `sees_all_mail_teams` ([ADR-032](ADR-032-sms-visibility-admin-full-catalog.md)). Страница `users` **остаётся вне каталога** (эскалация через назначение ролей по-прежнему не выдаётся отдельным чекбоксом).

`require_admin`, UI-гейт `/users`, защита сид-роли `admin` ([ADR-022](ADR-022-teams-nav-categories.md) §4б) и subset-исключение «актор сам admin-уровня» переводятся на `is_admin_level`. Имя `admin` остаётся зарезервированным сидом; кириллическое «Админ» проходит **только если** jsonb роли покрывает **полный** текущий каталог (включая `share`/`send` и прочие extra-действия).

**Матрица `/roles` строится из `GET /api/permissions/catalog`**, не из зашитого `ACTION_ORDER`. Столбцы = объединение действий каталога; подписи — `ACTION_LABEL` (`share` → **«Видимость»**, `send` → **«Отправка»**, `sync` → **«Синк»**, `tags` → **«Теги»**, `transfer` → **«Перенос»**). Фолбэк на сырой ключ — только авария ([ADR-063](ADR-063-documents-editor-cache-lifecycle-focus.md) §D). **[TD-068](../100-known-tech-debt.md) закрыт в коде frontend** (`catalogActionColumns`); машинный гейт подписей — [TD-071](../100-known-tech-debt.md).

**Backfill в `0037`:** для каждой роли, у которой на странице уже есть **все CRUD-действия этой страницы из каталога** (`view`/`create`/`edit`/`delete` — какие есть у страницы), дописать недостающие extra-действия той же страницы (`documents.share`, `mail.sync`/`tags`, `sms.transfer`/`sync`). Сид `admin` дополнительно получает `broadcast: [view, send]`. Роль «Админ» с полным видимым CRUD после миграции получает `share` без ручного PATCH — и проходит `is_admin_level`, как только каталог на момент миграции покрыт (после добавления `broadcast` сид `admin` покрыт миграцией; кастомная роль получит `broadcast` только когда оператор отметит новые столбцы — до того `is_admin_level` для неё ложен, если в каталоге уже есть `broadcast`). Чтобы кастомная «Админ» не потеряла admin-уровень из-за нового ключа `broadcast`, backfill **также** добавляет `broadcast: [view, send]` ролям, которые на момент миграции уже владеют полным каталогом **без** `broadcast` (то есть всеми ключами `CATALOG` до этой правки).

### 5. Навигация и UI «Рассылка»

> **Амендмент (ADR-077, 2026-08-17):** утверждение «чекбоксы ролей … (лейбл `{name} (получат: {started_count}, без бота: {not_started_count})`)» более не действует как **видимая** строка — видимы имя роли + бейджи «Получат»/«Без бота»; та же формула остаётся **accessible name** чекбокса. Плоская колонка без карточки более не норма композиции — см. ADR-077 и [08-design-system.md §Страница «Рассылка»](../08-design-system.md#страница-рассылка). Функциональный контракт (API, «Всем», toast, empty/error, без H1) в силе.

Пункт **«Рассылка»** — `#13` плоского ряда, маршрут `/broadcast`, гейт `broadcast:view`, не-full-bleed (`w-full px-6 py-8`). DefaultRoute: `…, documents, broadcast`. Без H1 (как Users/Roles, [ADR-029](ADR-029-ui-login-password-nav-team-form.md)).

Страница: `Textarea` текста; чекбоксы ролей из `GET /api/broadcasts/audience` (**accessible name** `{name} (получат: {started_count}, без бота: {not_started_count})`; видимая строка — [ADR-077](ADR-077-broadcast-page-visual-redesign.md)); чекбокс **«Всем»** (отмечен → чекбоксы ролей `disabled` и игнорируются в теле); кнопка **«Отправить»** (`broadcast:send`, иначе скрыта). Под чекбоксами — сводка «Получат: N · Без бота: M» из счётчиков audience (при «Всем» — `all_*`, иначе сумма выбранных ролей; пользователь в нескольких выбранных ролях в сводке может считаться дважды — это UX-оценка; сервер дедупит по `telegram_user_id`). Успех → toast «Отправлено: N. Не доставлено: K. Без бота: M». `GET /audience` токен **не** проверяет (ошибки 401/403); empty-state **«ИИ-бот не настроен»** — после `503 knowledge_bot_not_configured` на **POST** (страница защитно обрабатывает тот же код и на GET, сервер его на GET не отдаёт). Page-level view-guard — как у остальных.

**Клиентский `is_admin_level` (сверка `frontend/src/features/auth/adminLevel.ts`, `AdminRoute.tsx`, `AppLayout.tsx`).** Каталог (`GET /api/permissions/catalog`) грузится только если `!is_superadmin && role !== "admin" && roles:view ∈ permissions` (`needsPermissionsCatalog`). Пока `needsCatalog && !catalogReady` — `catalogPending=true`: пункт «Пользователи» в ряду — Spinner (не ссылка и не скрытие); `AdminRoute` — Spinner «Загрузка…», **не** заглушка. Ошибка загрузки каталога → `catalogPending=false`, `isAdmin` = покрытие (без каталога — false) → заглушка как обычно.

## Consequences

- (+) Рассылка идёт от знакомого сотрудникам ИИ-бота; mail/sms/notifier не смешиваются.
- (+) «Бот запущен» = проверяемый факт (линк), не догадка по нику.
- (+) Роль «Админ» с полным каталогом получает «Пользователи» и `documents:share` без переименования в `admin`.
- (+) HTTP ИИ-бота не публикуется; write внешнего контура документов не появляется.
- (−) Один и тот же Telegram-токен в двух `.env` (CRM + бот). Ротация — синхронно в обоих деплоях.
- (−) До доработки middleware бота линки не появятся сами: статус у всех «Бот не запущен», рассылка `sent=0`. Контракт соседнего репо обязателен к выкатке **вместе** с CRM.
- (−) Пользователь без `@username` и без mail/sms-линка не бутстрапнется (класс Q-USERS-3 бота) — должен написать с ника, который совпадает с `users.telegram`, либо уже иметь линк другого бота.

## Alternatives

- **CRM вызывает `POST /broadcast` у процесса бота** — отвергнуто: HTTP бота localhost-only и без auth; публикация = дыра на их `/sync`.
- **Бот поллит pending-джобы CRM** — отвергнуто: часовой sync слишком редкий; новый короткий poll — лишняя нагрузка ради одной кнопки.
- **Доставка по mail/sms-линкам** — отверкнуто: другой бот, Telegram вернёт 403.
- **Ключ `users` в каталоге** — отвергнуто: сломало бы замыкание эскалации [ADR-022](ADR-022-teams-nav-categories.md) §4в.
- **Синоним имени `Админ` ≡ `admin`** — отвергнуто: хрупко, не масштабируется.
- **Хранить историю рассылок** — отложено (NFR-1, TD-001).

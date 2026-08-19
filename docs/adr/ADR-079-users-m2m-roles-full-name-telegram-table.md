# ADR-079 — Страница «Пользователи»: несколько ролей (M2M), ФИО, вход только по Telegram, табличный редизайн

- **Статус:** **`implemented`** (сверка состава 2026-08-19: миграции `backend/alembic/versions/0038_user_roles_m2m.py` (`revision="0038_user_roles_m2m"`, `down_revision="0037_knowledge_bot_links"`) и `0039_users_full_name_telegram.py` (`revision="0039_users_full_name_telegram"`, `down_revision="0038_user_roles_m2m"`); `backend/app/models/user_role.py`; `backend/app/models/user.py` — колонки `role_id` нет, `User.roles` = `viewonly` relationship через `secondary="user_roles"`, `lazy="selectin"`, `order_by=(user_roles.c.created_at.asc(), user_roles.c.role_id.asc())`; `backend/app/schemas/auth.py:85` — `MeResponse.roles: list[str]`; `backend/app/schemas/user.py` — `last_name`/`first_name`/`middle_name`, `role_ids`; `frontend/src/pages/UsersPage.tsx`, `frontend/src/components/AddUserModal.tsx`. **Прогоны — по измерениям `qa`/reviewer, architect'ом не перепроверялись:** backend 1854 passed, frontend зелёный, RBAC e2e целевой роли закрыт)
- **Дата:** 2026-08-19
- **Контекст-модули:** [auth](../modules/auth/README.md), [teams](../modules/teams/README.md), [documents](../modules/documents/README.md), [broadcast](../modules/broadcast/README.md)
- **Supersedes:** [ADR-065](ADR-065-users-flat-list-team-chips.md) (плоский список карточек `/users` — отменён целиком)
- **Амендирует** (в каждый добавлена врезка-амендмент): [ADR-021](ADR-021-rbac-users-roles.md) (одна роль на пользователя), [ADR-022](ADR-022-teams-nav-categories.md) §4 (subset-инвариант эскалации считается против **union** прав актора), [ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md) (логин как поле формы; `telegram` опционален и очищается), [ADR-051](ADR-051-superadmin-db-anchor-personal-state.md) §1.5 (гард `is_in_use` — зеркало FK **`user_roles`**), [ADR-055](ADR-055-per-channel-teams-mail-sms.md) §5.1/§5.2 (пример `/me` с `role: str`; прецеденция `POST /api/users`), [ADR-059](ADR-059-documents-module.md) (видимость по `role_ids`), [ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) §4–§6 (предикат admin-уровня; дедуп адресатов), [ADR-078](ADR-078-me-is-admin-level.md) (`MeResponse.role` → `roles`)
- **НЕ амендирует:** [ADR-028](ADR-028-user-status-first-login.md) — производный `UserListItem.status` и `first_login_at` от M2M-ролей и ФИО не зависят, ложных утверждений в нём нет (статус лишь переезжает в колонку таблицы)
- **Миграции:** `0038_user_roles_m2m`, `0039_users_full_name_telegram`

## Context

Модель [ADR-021](ADR-021-rbac-users-roles.md) даёт пользователю **ровно одну** роль (`users.role_id`, FK `ON DELETE RESTRICT`). Владелец запросил **несколько ролей на пользователя**: сотрудник совмещает функции (оператор СМС + редактор документов), и сегодня это требует заводить комбинированную роль под каждую пару — матрица ролей растёт комбинаторно.

Одновременно запрошен **редизайн `/users`**: сегодня страница — плоский список карточек ([ADR-065](ADR-065-users-flat-list-team-chips.md)) с `username` в качестве видимого имени. Владелец хочет **таблицу** с колонкой **ФИО** (Фамилия / Имя / Отчество), а текущий «Логин» — перенести в поле «Имя». Поле **«Логин» из UI убирается**: вход выполняется по Telegram-нику, который уже является вторым допустимым идентификатором ([ADR-025](ADR-025-passwordless-users-login-identifier-open-first-login.md)) и уже реализован в резолвере (`backend/app/services/auth_service.py`, `_resolve_db_user` — сверка 2026-08-19).

Продукт **внутренний**, единственный контур эксплуатации — прод владельца, поэтому контракт ломается сразу, без периода совместимости; фронт и бэк выкатываются одним релизом.

## Decision

### 1. Роли пользователя — M2M через таблицу `user_roles`

`users.role_id` заменяется таблицей связи `user_roles(user_id, role_id, created_at)` — модель [03-data-model.md § Таблица `user_roles`](../03-data-model.md#таблица-user_roles-m2m-adr-079).

- **Миграция `0038_user_roles_m2m`** (`revision = "0038_user_roles_m2m"` — **19 символов** ≤ `VARCHAR(32)`; `down_revision = "0037_knowledge_bot_links"` — фактическая голова цепочки, сверка `backend/alembic/versions/0037_knowledge_bot_links.py:29`; полный концепт и `downgrade()` — [03-data-model.md](../03-data-model.md#миграция-0038_user_roles_m2m-концепт-adr-079)): создать таблицу, скопировать текущие связи `INSERT … SELECT id, role_id FROM users` (**включая системную строку-якорь**, [ADR-051](ADR-051-superadmin-db-anchor-personal-state.md)), затем `ALTER TABLE users DROP COLUMN role_id`. `downgrade()` восстанавливает колонку из роли с `MIN(created_at)`.
- **Новая модель** — `backend/app/models/user_role.py`, `Table` по образцу `backend/app/models/document_node_role.py` (сверка файла 2026-08-19: `Table` + composite PK + `Index` на «правую» колонку). Индекс `ix_user_roles_role_id` обязателен — под `ON DELETE`-гард роли и под обратную выборку «кто в роли».
- **FK ролевой стороны — `ON DELETE RESTRICT`** (не `CASCADE`, в отличие от `document_node_roles`): зеркало сегодняшнего `409 role_in_use`. `CASCADE` молча снял бы роль со всех носителей при удалении роли.
- **«Минимум одна роль» enforce-ится в сервисе (`422`), не в БД.** Табличного способа выразить «≥1 строка в дочерней таблице» без триггера/`DEFERRABLE`-констрейнта нет, а триггеров в проекте нет ни одного. Пустой `role_ids` в `POST`/`PATCH` → `422 unprocessable` (`details[].field="role_ids"`).
- **`RoleRepository.is_in_use` переезжает на `user_roles` БЕЗ фильтра `is_system`** — он обязан остаться зеркалом FK `RESTRICT` ([ADR-051](ADR-051-superadmin-db-anchor-personal-state.md) §1.5). С фильтром `DELETE` роли `admin`, которую держит якорь, дал бы `500 IntegrityError` вместо `409 role_in_use`. `user_count` (`list_all_with_counts`/`count_users`) — наоборот, якорь **исключает** и считает `COUNT(DISTINCT u.id)` (M2M даёт дубли строк).
- **`ensure_superadmin_anchor`** дописывает связь якоря идемпотентно: `INSERT INTO user_roles … ON CONFLICT DO NOTHING` в той же транзакции, что и сам якорь.

### 2. Права = union ролей; `is_admin_level` — по union

```
permissions      = union_permissions(role.permissions for role in user.roles)   # по страницам, дедуп действий
is_admin_level   = is_superadmin OR "admin" ∈ {r.name} OR permissions_subset(full_catalog_permissions(), permissions)
```

- `app/domain/permissions.py`: `+ union_permissions(...)`; протокол `AdminLevelPrincipal.role: str` → **`roles: Sequence[str]`**; ветка сида читается как `"admin" in roles`.
- `Principal` (`app/api/deps.py`): `role: str` → **`roles: tuple[str, ...]`** (порядок — `user_roles.created_at ASC, role_id ASC`), `permissions` = union, `role_id: uuid|None` → **`role_ids: frozenset[uuid]`**. Супер-админ → `roles=("admin",)`, `role_ids=frozenset()`.
- **Инвариант эскалации не меняется** ([ADR-022](ADR-022-teams-nav-categories.md) §4а, [05-security.md](../05-security.md#security-инвариант-эскалации-привилегий-нормативно-adr-022)): `permissions_subset(child, parent)` вызывается с `parent` = **union актора**. Это ослабление в пользу актора математически корректно: актор уже обладает union'ом, значит выдать роли его подмножество — не эскалация. Пункт **(в)** (назначение ролей — только под `require_admin`) остаётся и по-прежнему замыкает эскалацию.
- **Ключ `users` в каталог прав по-прежнему НЕ вводится** (замыкание эскалации, [ADR-022](ADR-022-teams-nav-categories.md) §4в) — отвергнуто повторно.

### 3. JWT-claim `role` остаётся, но становится **информационным**

Выпущенные токены остаются валидными: права грузятся из БД по `uid` на **каждый** запрос ([05-security.md § Enforcement](../05-security.md#enforcement-свежая-загрузка-прав-из-бд)), claim в авторизации не участвует. Точки выпуска (`auth_service`, `mail_telegram_service`, `sms_telegram_link_service`) кладут `role = primary_role_name(user)` — имя **первой** роли по `user_roles.created_at`. Клеймом `role` **запрещено** гейтить что-либо; ветка `"admin" ∈ roles` предиката читается из БД-ролей, а не из claim.

`GET /api/auth/me`: **`role: str` → `roles: list[str]`** (ломающее изменение, фронт и бэк — одним релизом). `is_admin_level` остаётся полем ([ADR-078](ADR-078-me-is-admin-level.md) в силе) и считается по union.

### 4. Видимость документов — по **любой** из ролей

`DocumentScope.role_id: uuid|None` → **`role_ids: frozenset[uuid]`**; предикат видимости узла — `document_node_roles.role_id IN :role_ids` (SQLAlchemy `expanding=True`). **Пустой набор → узел виден только если публичен** (`public-only`), а **не** `500` и не «видно всё»: у супер-админа своих строк в `user_roles` нет, а его доступ обеспечивает отдельный admin-предикат `sees_all_documents`.

Следствие принято осознанно: **union расширяет видимость** — пользователь с ролями `A` и `B` видит узлы, ограниченные `A`, и узлы, ограниченные `B`. Это прямое требование фичи, а не побочный эффект.

### 5. Адресаты Telegram-контуров — join через `user_roles` + DISTINCT

`knowledge_bot_link_repository.audience_by_role` / `recipients_for_roles` и `mail_telegram_link_repository` читают `users` мимо `UserRepository`, поэтому:

- join `users → user_roles → roles` вместо `users.role_id`;
- **дедуп получателей обязателен** (`DISTINCT` по `telegram_user_id`): пользователь с двумя выбранными ролями иначе получит рассылку дважды;
- в счётчиках аудитории пользователь учитывается **в каждой** своей роли (сумма по строкам ≠ числу адресатов) — итоговая сводка «Получат: N» считается по дедуплицированному множеству;
- **`WHERE NOT users.is_system` остаётся обязательным явным условием** во всех fan-out-выборках ([ADR-051](ADR-051-superadmin-db-anchor-personal-state.md), [05-security.md](../05-security.md#системная-строка-якорь-супер-админа-adr-051)) — M2M-join его не заменяет.

### 6. Внешний контракт ИИ-бота — расширяется **аддитивно**, а не переписывается

`ExternalUserAccessResponse` (`GET /api/external/documents/user-access/{id}`, `POST /api/external/knowledge-bot/link`) — контракт **чужого репозитория** (ba-knowledge-base, его [ADR-013](file:///Users/elisejverbickij/Desktop/BA/ba-knowledge-base/docs/adr/ADR-013-crm-knowledge-bot-link.md)). Ломать его синхронно с этим релизом нельзя — вторая команда не участвует в этом цикле.

- **`role_id` / `role_name` СОХРАНЯЮТСЯ** и несут **первую** роль (`primary_role_name`, тот же порядок, что у claim).
- **Добавляется `roles: [{id, name}]`** — полный набор.
- **`sees_all_documents` считается по union** (это уже производное поле, его семантика «видит всё» не меняется, меняется входной набор прав).

Долг «снять `role_id`/`role_name` после перехода бота на `roles[]`» — [TD-084](../100-known-tech-debt.md).

### 7. ФИО: три nullable-колонки, обязательность — на API

**Миграция `0039_users_full_name_telegram`** (`revision = "0039_users_full_name_telegram"` — **29 символов** ≤ `VARCHAR(32)`; `down_revision = "0038_user_roles_m2m"`; DDL и `downgrade()` — [03-data-model.md](../03-data-model.md#миграция-0039_users_full_name_telegram-концепт-adr-079)): `users += last_name text NULL, first_name text NULL, middle_name text NULL`; backfill `UPDATE users SET first_name = username WHERE is_system = false`.

- **В БД — `NULL`-допустимые**: у существующих пользователей фамилии нет, у якоря ФИО нет и не будет никогда. `NOT NULL` потребовал бы заполнить фамилию выдуманным значением.
- **Обязательность `last_name` и `first_name` — на уровне API** (`422`, `details[].field`), как и весь набор правил имени (правило `username` авторитетно на Pydantic — [03-data-model.md](../03-data-model.md#правило-username-кириллица-допускающее-нормативно); то же правило переиспользуется для частей ФИО под именем `validate_name_part`). `middle_name` опционально всегда.
- **Отображаемое имя** — `«{last_name} {first_name} {middle_name}»` со схлопыванием пустых; всё три пустые → фолбэк `username`.

### 8. Telegram обязателен при создании и **не очищается**

- `POST /api/users`: `telegram` — **required** (`422`, если отсутствует/пуст). Формат и нормализация — прежние ([03-data-model.md § Правило `telegram`](../03-data-model.md#правило-telegram-телеграм-ник-нормативно)).
- `PATCH /api/users/{id}`: `telegram` можно **сменить**, но **нельзя очистить** — `null`/`""` → `422 unprocessable`. Прежняя норма «`null`/`""` → убрать телеграм» (04-api.md) **отменена**: с удалённым из UI логином очистка телеграма оставила бы пользователя без единого способа входа.
- **Колонка и `uq_users_telegram` не меняются** (частичный UNIQUE `WHERE telegram IS NOT NULL`): существующие строки с `telegram IS NULL` остаются валидными и продолжают входить по `username` — ветка резолва по `username` в `auth_service` **не трогается**.

### 9. `username` остаётся в БД, но исчезает из UI

Колонка `username` **сохраняется** (`NOT NULL UNIQUE`): она — `sub` в JWT, якорь `is_system` (`superadmin@system`), `Principal.username`, ключ bootstrap-резолва внешнего контура ([ADR-076](ADR-076-knowledge-bot-broadcast-and-admin-level.md) §2 шаг 4). Снос колонки затронул бы пять подсистем ради косметики — отвергнуто.

- **Новым пользователям `username := normalized(telegram)`** (тот же нормализатор: снять `@`, lower-case). Отдельного поля в форме нет.
- **При смене `telegram` через `PATCH` `username` НЕ меняется** — иначе поменялся бы `sub` уже выпущенных токенов и bootstrap-резолв внешнего контура.
- **Коллизия** нормализованного telegram с историческим `username` → **`409 username_taken`**. Форма маппит этот код **на поле «Телеграм»** (поля «Логин» в ней нет).
- `UserListItem.username` **остаётся в ответе** — как фолбэк-отображение для строк без ФИО и как диагностическое значение.

**Прецеденция двух `409` при `POST /api/users` МЕНЯЕТСЯ: `telegram_taken` → `username_taken`** (было наоборот — [ADR-055](ADR-055-per-channel-teams-mail-sms.md) §5.2, [04-api.md](../04-api.md#post-apiusers)).

Обоснование: раньше это были конфликты **двух разных введённых значений** (логин и телеграм — два поля формы), и порядок «сначала логин» просто повторял порядок полей. Теперь **оба конфликта порождены ОДНИМ введённым значением — телеграм-ником**: `username` оператор не вводит, сервис выводит его из `telegram`. Поэтому первой обязана называться **прямая** причина — «такой телеграм уже у другого пользователя» (`telegram_taken`), а `username_taken` («выведенный логин совпал с историческим») — редкий побочный исход того же ввода. Обратный порядок сообщал бы оператору менее вероятную причину и указывал бы на контрол, которого в форме больше нет.

Обе ошибки форма показывает **на поле «Телеграм»**; полная цепочка — «схема (`422`) → существование `role_ids`/`team_ids` (`422`) → `409 telegram_taken` → `409 username_taken`». Для `PATCH` вопрос не возникает: `username` там не пересчитывается, поэтому `username_taken` недостижим.

### 10. Страница `/users` — таблица (отменяет [ADR-065](ADR-065-users-flat-list-team-chips.md))

Норматив состава — [08-design-system.md § Страница «Пользователи»](../08-design-system.md#страница-пользователи). Кратко: сводные плашки (Всего / Активны / Ожидают входа / Активны в боте), клиентский поиск по ФИО+`username`+`telegram`, колонки **ФИО | Роли | Команды | Telegram | Статус | Бот | Действия**, сортировка `fullName.localeCompare('ru')`, клик по «Открыть» → модалка редактирования. Табличная обёртка — **существующий** паттерн `BackendUsersPage.tsx` (карточка `overflow-x-auto` + `<table>`), сводные ячейки — существующий `SummaryCell` (сверка `frontend/src/pages/BackendUsersPage.tsx:184-198`, `:323` — 2026-08-19). **Новых примитивов ДС не вводится.**

Форма: **Фамилия\*, Имя\*, Отчество, Телеграм\*, Пароль (опц.), Роли (MultiSelect, ≥1), Команды, блоки «СМС»/«Почты»**. Поле «Логин» удалено из обеих модалок. Блоки каналов ([ADR-055](ADR-055-per-channel-teams-mail-sms.md)) не меняются.

**Экран входа не меняется:** label «Логин или Телеграм» и плейсхолдер «Логин или @username» ([ADR-041](ADR-041-login-theme-session-ux.md)) **остаются в силе** — существующие пользователи входят по историческому `username`.

## Consequences

- (+) Совмещение функций перестаёт требовать комбинаторных ролей; матрица `/roles` не растёт.
- (+) Выпущенные JWT остаются валидными (принципал собирается по `uid` из БД).
- (−) **Ломающее изменение контрактов** `GET /api/auth/me` (`role`→`roles`) и `GET/POST/PATCH /api/users` (`role_id`/`role_name`→`role_ids`/`roles`, `+ФИО`) — фронт и бэк выкатываются **одним** релизом, миграции применяются entrypoint'ом до старта ([07-deployment.md § Порядок запуска](../07-deployment.md#порядок-запуска)).
- (−) Union расширяет видимость документов и scope каналов — ожидаемое следствие фичи, но при выдаче второй роли доступ растёт **молча** (отдельного подтверждения в форме нет).
- (−) `username` продолжает жить как скрытый идентификатор: пользователь его не видит, но `409 username_taken` при коллизии telegram-ника с историческим логином оператору придётся разрешать вручную (сменить telegram либо переименовать историческую учётку прямым SQL).
- (−) Внешний контракт бота временно несёт **две** формы одной величины (`role_id`/`role_name` и `roles[]`) — [TD-084](../100-known-tech-debt.md).
- Тесты `test_users_roles_api.py`, auth-тесты, `test_documents_visibility.py`, broadcast/knowledge-bot — переписываются; заводятся миграционные тесты `0038`/`0039`. Сценарии — зона `qa`.

## Alternatives

- **Оставить одну роль, разрешив «наследование» ролей** — отвергнуто: иерархия ролей — отдельная модель со своими циклами и порядком разрешения конфликтов; для трёх-четырёх ролей внутреннего продукта это дороже M2M.
- **Хранить роли массивом `uuid[]` в колонке `users.role_ids`** — отвергнуто: нет FK-целостности (удалённая роль осталась бы «висеть» в массиве), гард `409 role_in_use` пришлось бы писать вручную, а зеркало FK — действующий принцип репо ([ADR-051](ADR-051-superadmin-db-anchor-personal-state.md) §1.5).
- **`NOT NULL` на ФИО с backfill `last_name = '—'`** — отвергнуто: выдуманное значение в денормализованном UI-поле неотличимо от настоящего.
- **Снести `username` в той же волне** — отвергнуто: пять подсистем (`sub`, якорь, `Principal`, bootstrap внешнего контура, уникальность) ради поля, которого и так нет в UI. При необходимости — отдельной волной.
- **Ломать внешний контракт бота (`role_id` → `roles`) синхронно** — отвергнуто: вторая команда в этом цикле не участвует; аддитивное расширение + [TD-084](../100-known-tech-debt.md) даёт тот же итог без простоя бота.
- **Считать `is_admin_level` по «главной» роли** — отвергнуто: пользователь с ролями `admin` + узкой второй ролью перестал бы быть админом при неудачном порядке `created_at`; предикат обязан быть монотонным по добавлению ролей.

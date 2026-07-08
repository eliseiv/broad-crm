# Модуль `teams` — CRM-команды (лидер + участники)

Статус: `spec-ready` (Спринт A, [ADR-022](../../adr/ADR-022-teams-nav-categories.md)) · Исполнители: backend, frontend

## Scope

Управление **CRM-командами** — группировка пользователей вокруг лидера: создание, список, редактирование (название/лидер/участники), удаление. Пользователь может входить в **0..N** команд (M2M); у команды ровно один лидер, который **всегда** входит в участники. Модель — [03-data-model.md](../../03-data-model.md#таблицы-teams-и-user_teams-crm-команды), API — [04-api.md](../../04-api.md#teams). Права — через матрицу RBAC (`teams:view/create/edit/delete`, [modules/auth](../auth/README.md), [ADR-021](../../adr/ADR-021-rbac-users-roles.md)).

> **Гейтинг: просмотр — по `teams:view`; управление составом (create/edit) — де-факто admin-only (нормативно, [ADR-022](../../adr/ADR-022-teams-nav-categories.md#3-гейтинг-api-нормативно)).** Серверные гейты `teams:*` не меняются и корректны: `teams:view` даёт полноценный **просмотр** списка команд и навигацию. Но форма создания/редактирования выбирает **лидера** и **участников** из `GET /api/users`, который под `require_admin` (страница «Пользователи» admin-only, §4в замыкания эскалации). Поэтому у не-admin с `teams:create`/`teams:edit` список кандидатов **пуст** → назначение лидера/участников невозможно, и фактическое управление составом доступно только `admin`/супер-админу. Это осознанное следствие зависимости от `/api/users`, а не пробел; UI обрабатывает gracefully — баннер **«Нет пользователей для назначения лидера и участников.»** внутри модалки + disabled **submit-кнопка «Добавить»** модалки ([08-design-system.md](../../08-design-system.md#страница-команды)).

## Дизамбигуация: CRM-команды ≠ mail-«команды» (нормативно)

**Не путать** с «командами» модуля «Почты»: там «Команда» — это `groups` внешнего сервиса `postapp.store` (схема [`MailTeam`](../../04-api.md#схема-mailteam), эндпоинт `GET /api/mail/teams`, дропдаун-фильтр на странице «Почты», [ADR-017](../../adr/ADR-017-dashboard-client-aggregation-mail-server-filters.md)). Это разные сущности:

| | mail-«команды» | CRM-команды (этот модуль) |
|--|----------------|----------------------------|
| API | `/api/mail/teams` | `/api/teams` |
| Хранение | нет (прокси к внешнему) | БД CRM (`teams`+`user_teams`) |
| Id | `integer` (внешний) | `uuid` |
| Смысл | группа почтовых ящиков | группа пользователей вокруг лидера |

Дизамбигуация — [ADR-022](../../adr/ADR-022-teams-nav-categories.md#дизамбигуация-crm-команды--mail-команды-нормативно).

## Out of scope (Этап 1)

- Роль/права внутри команды (лидер vs участник — только организационная метка, не влияет на RBAC; права даёт `roles`, не членство в команде).
- Иерархия/вложенность команд, несколько лидеров.
- Drag-and-drop порядок команд (список сортируется `created_at DESC`).
- Аудит действий с командами ([TD-001](../../100-known-tech-debt.md)).
- Массовые операции над составом (bulk add/remove) — состав задаётся полным набором `member_ids`.

## Инварианты (нормативно)

1. **Лидер ∈ участники.** При `POST`/`PATCH` сервис гарантирует наличие строки `(leader_id, team_id)` в `user_teams`, даже если `leader_id` отсутствует в присланном `member_ids`. Обеспечивает **сервис** (единственная точка записи), БД-триггеров нет ([ADR-022](../../adr/ADR-022-teams-nav-categories.md)).
2. **Уникальность имени.** `teams.name` UNIQUE → дубликат `409 team_name_taken` (детерминированно из `IntegrityError`/предварительной проверки).
3. **Существование ссылок.** `leader_id` и все `member_ids` должны ссылаться на существующих пользователей → иначе `422 unprocessable` (`details[].field` = `leader_id`/`member_ids`).
4. **Удаление лидера-пользователя запрещено.** `teams.leader_id` — `ON DELETE RESTRICT`: попытка `DELETE /api/users/{id}` для пользователя-лидера → `409 user_is_team_leader` ([modules/auth](../auth/README.md), [04-api.md](../../04-api.md#delete-apiusersid)).
5. **Каскад membership.** Удаление команды или пользователя снимает соответствующие строки `user_teams` (`ON DELETE CASCADE`).

## Backend — ТЗ

Слои — как в `servers`/`proxies`/`backends`: router → service → repository (SQLAlchemy async), Pydantic-схемы = контракт. Образцы: `app/api/*`, `app/services/*`, `app/repositories/*`, `app/models/*`, `app/schemas/*`.

### Модель и миграция
- Модель `app/models/team.py` → таблица `teams`; ассоциативная таблица `user_teams` (M2M `users`↔`teams`). **Реэкспорт в `app/models/__init__.py`** (обязательно для автогенерации/видимости Alembic). Модель `team.py` объявляет отношения к `User` через `user_teams` (secondary) — по образцу SQLAlchemy `relationship(secondary=...)`.
- Миграция **`0009_create_teams`** (`down_revision="0008_create_users_roles"`): `teams` (+ `ix_teams_leader_id`) и `user_teams` (составной PK, + `ix_user_teams_team_id`). Рабочий `downgrade()` (`DROP user_teams; DROP teams`) — [03-data-model.md](../../03-data-model.md#миграция-0009_create_teams-концепт), [07-deployment.md](../../07-deployment.md#откат-миграций-бд).

### Endpoints (все под JWT, префикс `/api`, гейт `require("teams", <action>)`)
Контракт, схемы, коды ошибок и прецеденция — строго по [04-api.md](../../04-api.md#teams):
- `GET /api/teams` → `TeamListResponse` (`TeamListItem[]`, сортировка `created_at DESC, id`). Гейт `teams:view`.
- `POST /api/teams {name, leader_id, member_ids?}` → `201 TeamListItem`. Гейт `teams:create`. Лидер добавляется в участники; уникальность `name` → `409 team_name_taken`; несуществующие `leader_id`/`member_ids` → `422`.
- `PATCH /api/teams/{id} {name?, leader_id?, member_ids?}` → `200 TeamListItem`. Гейт `teams:edit`. `member_ids` полностью заменяет состав (лидер всегда включается).
- `DELETE /api/teams/{id}` → `204`. Гейт `teams:delete`. Каскад `user_teams`.

### Репозиторий / сервис (ориентиры, структура — на усмотрение)
1. **Репозиторий** `team_repository.py`: `create(name, leader_id, member_ids)` (в одной транзакции — вставка `teams` + строк `user_teams`, лидер включён); `list_all()` с агрегатами `leader_username` (`JOIN users`), `member_count` и списком `members` (`JOIN user_teams JOIN users`); `get(id)`; `update(...)`; `exists_by_name(name, exclude_id=None)`; `delete(id)`.
2. **Сервис** `team_service.py`: валидация имени (Pydantic + проверка уникальности → `409`), проверка существования `leader_id`/`member_ids` (→ `422`), обеспечение инварианта «лидер ∈ участники», атомарная замена состава при `PATCH`. Маппинг `IntegrityError` (UNIQUE `name`) → `409 team_name_taken`.
3. **API** `api/teams.py` (`prefix="/teams"`, CRUD, `require("teams", action)`), include в `api/router.py`, DI-фабрика в `deps.py`.
4. **Ошибки** в `app/errors.py`: `team_not_found` (404), `team_name_taken` (409). Ошибка `user_is_team_leader` (409) заводится в паре с Users API (срабатывает при `DELETE /api/users/{id}` на `IntegrityError` `RESTRICT` `teams.leader_id`).

### Требования
1. `name` уникален (`UNIQUE`); дубль при `POST`/`PATCH` → `409 team_name_taken`. Прецеденция: схемная валидация (`400`/`422`) → существование ссылок (`422`) → `409`.
2. Инвариант «лидер ∈ участники» соблюдается на всех путях записи (create + update, в т.ч. при смене лидера).
3. `member_ids`/`leader_id` — существующие пользователи, иначе `422` с указанием поля.
4. Каждая Alembic-миграция имеет рабочий `downgrade()` ([07-deployment.md](../../07-deployment.md#откат-миграций-бд)).
5. Замена состава при `PATCH` — атомарна (в одной транзакции; старые строки `user_teams` удаляются, новые вставляются, лидер включён).

## Frontend — ТЗ

Детальный UI-гайд — [08-design-system.md](../../08-design-system.md#страница-команды); русский словарь — [08-design-system.md](../../08-design-system.md#локализация-страниц-пользователи--роли--команды).

### Навигация
- Пункт **«Команды»** (`/teams`) — в категории **«Пользователи»** нового категоризированного меню ([08-design-system.md](../../08-design-system.md#навигация-категории-дропдауны-applayout)). Защищённый маршрут внутри `AppLayout`, не-full-bleed ветка. Page-level view-guard `teams:view` ([ADR-021](../../adr/ADR-021-rbac-users-roles.md) §Последствия, [ADR-022](../../adr/ADR-022-teams-nav-categories.md)).

### Страница `TeamsPage`
- Список команд (`GET /api/teams`): по команде — **Название**, **Лидер** (`leader_username`), «**N участников**» (`member_count`). Кнопка «Добавить команду» — по `useCan('teams','create')`.
- Создание/редактирование (`AddTeamModal`, add+edit): **Название** (`Input`), **Лидер** (`Select` пользователя из `GET /api/users`), **Участники** (мультивыбор пользователей). Лидер автоматически считается участником (UI может подсвечивать/фиксировать его в списке участников). Отправка `POST`/`PATCH /api/teams`.
- **Де-факто admin-only управление составом:** если `GET /api/users` пуст (не-admin, источник кандидатов под `require_admin`) — внутри модалки `AddTeamModal` над полями баннер **«Нет пользователей для назначения лидера и участников.»**, а **submit-кнопка «Добавить» модалки** — disabled (`disabled={noUsers}`). Страничная кнопка «Добавить команду» disabled только при `usersQuery.isLoading` (при пустом списке остаётся активной). Gracefully; серверный контракт `teams:*` не меняется — [08-design-system.md](../../08-design-system.md#страница-команды), [ADR-022](../../adr/ADR-022-teams-nav-categories.md#3-гейтинг-api-нормативно).
- Кнопка **Удалить** (по `teams:delete`) → `DELETE /api/teams/{id}`; подтверждение.
- Ошибки: `409 team_name_taken` → пофилдово под «Название»; `422` (несуществующий лидер/участник) → инлайн; общая → toast.
- Мультивыбор участников — новый UI-примитив (checkbox-список / `MultiSelect`, [08-design-system.md](../../08-design-system.md#компонент-мультивыбор-multiselect)); тот же примитив используется полем «Команды» в форме пользователя.
- Данные/кэш — feature-слой `features/teams` (`api.ts`, `hooks.ts`) на TanStack Query; типы `TeamListItem`/`TeamCreateRequest`/`TeamUpdateRequest`/`TeamListResponse` в `types/api.ts`.

### Состояния UI
Loading (skeleton), empty (только «Добавить команду» + подсказка, read-only-вариант без кнопки при отсутствии `teams:create`), toast «Команда создана»/«Команда обновлена»/«Команда удалена», обработка `409`/`422`/сетевых — по образцу других страниц ([08-design-system.md](../../08-design-system.md#состояния-ui-обязательны)).

## DoD

- [ ] Endpoints и коды ошибок соответствуют [04-api.md](../../04-api.md#teams); гейт `require("teams", action)`.
- [ ] `teams.name` уникален (дубль → `409 team_name_taken`); прецеденция `400`/`422` → `409`.
- [ ] Инвариант «лидер ∈ участники» соблюдается при create и update (в т.ч. смена лидера); подтверждено тестами.
- [ ] `member_ids`/`leader_id` — существующие пользователи, иначе `422` с указанием поля; `member_count` включает лидера.
- [ ] Alembic `0009_create_teams` (`down_revision="0008_create_users_roles"`) с рабочим `downgrade()`; `ix_teams_leader_id`, `ix_user_teams_team_id`.
- [ ] Каскад `user_teams` при удалении команды/пользователя; удаление пользователя-лидера → `409 user_is_team_leader` (совместно с Users API).
- [ ] Frontend: пункт «Команды» в категории «Пользователи», `TeamsPage`, `AddTeamModal` (add+edit), мультивыбор участников, page-guard `teams:view`, все состояния UI, русские строки из словаря.
- [ ] Lint/type-check/format проходят (backend и frontend); coverage по сервису/репозиторию ≥ порога ([06-testing-strategy.md](../../06-testing-strategy.md)).

## Changelog
- 2026-07-08: спецификация создана (architect, [ADR-022](../../adr/ADR-022-teams-nav-categories.md)). Новая доменная сущность CRM-команды (`teams`+`user_teams` M2M, лидер+участники), отдельный неймспейс `/api/teams` (дизамбигуация vs mail-`groups`), права через матрицу `teams:*`, миграция `0009_create_teams`.
